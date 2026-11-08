-- ForLabs: админ-панель. Подписка по дате, ключ модели у каждого участника (в Supabase Vault),
-- учёт расхода по лабам, история действий администратора, чтение данных участников администратором.

-- участники: подписка, заметка, общий ключ, последняя активность
alter table public.members add column if not exists access_until timestamptz;
alter table public.members add column if not exists note text not null default '';
alter table public.members add column if not exists allow_shared_key boolean not null default true;
alter table public.members add column if not exists last_seen_at timestamptz;

alter table public.labs add column if not exists created_at timestamptz not null default now();

alter table public.llm_usage add column if not exists lab text;
alter table public.llm_usage add column if not exists key_kind text;
create index if not exists llm_usage_lab_idx on public.llm_usage (user_id, lab);

-- история действий: пишут только триггер и функции администратора, читает администратор
create table if not exists public.member_events (
  id bigint generated always as identity primary key,
  user_id uuid not null references auth.users(id) on delete cascade,
  actor uuid,
  kind text not null,
  detail jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
create index if not exists member_events_user_idx on public.member_events (user_id, created_at desc);
alter table public.member_events enable row level security;
drop policy if exists member_events_admin_read on public.member_events;
create policy member_events_admin_read on public.member_events for select to authenticated
  using ((select private.is_admin()));

create or replace function private.members_audit() returns trigger
language plpgsql security definer set search_path = '' as $$
declare
  who uuid := (select auth.uid());
begin
  if new.approved is distinct from old.approved then
    insert into public.member_events (user_id, actor, kind, detail)
    values (new.user_id, who, 'approve', jsonb_build_object('approved', new.approved));
  end if;
  if new.access_until is distinct from old.access_until then
    insert into public.member_events (user_id, actor, kind, detail)
    values (new.user_id, who, 'access', jsonb_build_object('from', old.access_until, 'until', new.access_until));
  end if;
  if new.note is distinct from old.note then
    insert into public.member_events (user_id, actor, kind, detail)
    values (new.user_id, who, 'note', jsonb_build_object('note', new.note));
  end if;
  if new.display_name is distinct from old.display_name then
    insert into public.member_events (user_id, actor, kind, detail)
    values (new.user_id, who, 'name', jsonb_build_object('name', new.display_name));
  end if;
  if new.allow_shared_key is distinct from old.allow_shared_key then
    insert into public.member_events (user_id, actor, kind, detail)
    values (new.user_id, who, 'shared_key', jsonb_build_object('allowed', new.allow_shared_key));
  end if;
  return new;
end;
$$;
revoke all on function private.members_audit() from public, anon, authenticated;
drop trigger if exists members_audit on public.members;
create trigger members_audit after update on public.members
  for each row execute function private.members_audit();

-- ключи участников: в private (не видно через API), сам ключ — в Vault
create table if not exists private.member_keys (
  user_id uuid primary key references auth.users(id) on delete cascade,
  secret_id uuid not null,
  last4 text not null,
  set_at timestamptz not null default now()
);
revoke all on private.member_keys from public, anon, authenticated;

create or replace function private.member_keys_drop_secret() returns trigger
language plpgsql security definer set search_path = '' as $$
begin
  delete from vault.secrets where id = old.secret_id;
  return old;
end;
$$;
revoke all on function private.member_keys_drop_secret() from public, anon, authenticated;
drop trigger if exists member_keys_drop_secret on private.member_keys;
create trigger member_keys_drop_secret after delete on private.member_keys
  for each row execute function private.member_keys_drop_secret();

create or replace function public.admin_set_key(target uuid, api_key text) returns text
language plpgsql security definer set search_path = '' as $$
declare
  k text := btrim(coalesce(api_key, ''));
  existing uuid;
  sid uuid;
  tail text;
begin
  if not (select private.is_admin()) then
    raise exception 'only admin' using errcode = '42501';
  end if;
  if length(k) < 8 or k ~ '\s' then
    raise exception 'key too short' using errcode = '22023';
  end if;
  if not exists (select 1 from public.members m where m.user_id = target) then
    raise exception 'no such member' using errcode = '22023';
  end if;
  tail := right(k, 4);
  select mk.secret_id into existing from private.member_keys mk where mk.user_id = target;
  if existing is not null then
    perform vault.update_secret(existing, k);
    update private.member_keys set last4 = tail, set_at = now() where user_id = target;
  else
    delete from vault.secrets where name = 'forlabs_key_' || target::text;
    sid := vault.create_secret(k, 'forlabs_key_' || target::text, 'ForLabs: ключ модели участника');
    insert into private.member_keys (user_id, secret_id, last4) values (target, sid, tail);
  end if;
  insert into public.member_events (user_id, actor, kind, detail)
  values (target, (select auth.uid()), 'key_set', jsonb_build_object('last4', tail));
  return tail;
end;
$$;

create or replace function public.admin_clear_key(target uuid) returns void
language plpgsql security definer set search_path = '' as $$
begin
  if not (select private.is_admin()) then
    raise exception 'only admin' using errcode = '42501';
  end if;
  if exists (select 1 from private.member_keys where user_id = target) then
    delete from private.member_keys where user_id = target;
    insert into public.member_events (user_id, actor, kind, detail)
    values (target, (select auth.uid()), 'key_clear', '{}'::jsonb);
  end if;
end;
$$;

create or replace function public.admin_keys() returns table (user_id uuid, last4 text, set_at timestamptz)
language plpgsql stable security definer set search_path = '' as $$
begin
  if not (select private.is_admin()) then
    raise exception 'only admin' using errcode = '42501';
  end if;
  return query select mk.user_id, mk.last4, mk.set_at from private.member_keys mk;
end;
$$;

-- только для прокси (service_role): расшифрованный ключ участника
create or replace function public.llm_key_for(target uuid) returns text
language sql stable security definer set search_path = '' as $$
  select ds.decrypted_secret
  from private.member_keys mk join vault.decrypted_secrets ds on ds.id = mk.secret_id
  where mk.user_id = target
$$;

-- расход: работают через RLS (администратор видит всех, участник — себя); дни — по Минску
create or replace function public.admin_usage_daily(days integer) returns table (day date, user_id uuid, tokens bigint)
language sql stable security invoker set search_path = '' as $$
  select (u.created_at at time zone 'Europe/Minsk')::date as day, u.user_id,
         sum(u.prompt_tokens + u.completion_tokens)::bigint as tokens
  from public.llm_usage u
  where u.created_at >= now() - make_interval(days => least(greatest(coalesce(days, 30), 1), 366))
  group by 1, 2
  order by 1, 2
$$;

create or replace function public.admin_usage_by_lab(target uuid)
returns table (lab text, tokens bigint, calls bigint, last_at timestamptz)
language sql stable security invoker set search_path = '' as $$
  select coalesce(u.lab, ''), sum(u.prompt_tokens + u.completion_tokens)::bigint, count(*)::bigint, max(u.created_at)
  from public.llm_usage u
  where u.user_id = target
  group by 1
  order by 4 desc
$$;

revoke all on function public.admin_set_key(uuid, text) from public, anon;
revoke all on function public.admin_clear_key(uuid) from public, anon;
revoke all on function public.admin_keys() from public, anon;
revoke all on function public.admin_usage_daily(integer) from public, anon;
revoke all on function public.admin_usage_by_lab(uuid) from public, anon;
grant execute on function public.admin_set_key(uuid, text) to authenticated;
grant execute on function public.admin_clear_key(uuid) to authenticated;
grant execute on function public.admin_keys() to authenticated;
grant execute on function public.admin_usage_daily(integer) to authenticated;
grant execute on function public.admin_usage_by_lab(uuid) to authenticated;
revoke all on function public.llm_key_for(uuid) from public, anon, authenticated;
grant execute on function public.llm_key_for(uuid) to service_role;

-- администратор читает (не меняет) лабы, профили и файлы участников
drop policy if exists labs_admin_read on public.labs;
create policy labs_admin_read on public.labs for select to authenticated
  using ((select private.is_admin()));
drop policy if exists student_profiles_admin_read on public.student_profiles;
create policy student_profiles_admin_read on public.student_profiles for select to authenticated
  using ((select private.is_admin()));
drop policy if exists labs_files_admin_read on storage.objects;
create policy labs_files_admin_read on storage.objects for select to authenticated
  using (bucket_id = 'labs' and (select private.is_admin()));
