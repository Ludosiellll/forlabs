-- ForLabs: пробный доступ — N лаб и M сообщений чата правок на каждую лабу (срок — access_until, как у подписки).
-- Считает и проверяет прокси llm на сервере; участник свои лимиты видит, но изменить не может.

alter table public.members add column if not exists trial_labs integer;
alter table public.members add column if not exists trial_chat integer;
alter table public.members add column if not exists trial_since timestamptz;
do $$ begin
  alter table public.members add constraint members_trial_range
    check ((trial_labs is null or trial_labs between 0 and 100) and (trial_chat is null or trial_chat between 0 and 100));
exception when duplicate_object then null;
end $$;

alter table public.llm_usage add column if not exists chat text;  -- id сообщения чата правок (заголовок X-ForLabs-Chat)
create index if not exists llm_usage_user_time_idx on public.llm_usage (user_id, created_at);

-- расход пробного периода по лабам: запросы, разные сообщения чата, было ли уже сообщение cur_chat.
-- Прокси (service_role) — для любого участника, участник — для себя, администратор — для любого.
create or replace function public.trial_usage(target uuid, cur_chat text default null)
returns table (lab text, calls bigint, chats bigint, has_chat boolean)
language plpgsql stable security definer set search_path = '' as $$
declare
  since timestamptz;
begin
  if not (coalesce(current_setting('request.jwt.claims', true)::jsonb ->> 'role', '') = 'service_role'
          or target = (select auth.uid()) or (select private.is_admin())) then
    raise exception 'not allowed' using errcode = '42501';
  end if;
  select m.trial_since into since from public.members m where m.user_id = target;
  return query
    select coalesce(u.lab, '')::text, count(*)::bigint, count(distinct u.chat)::bigint,
           coalesce(bool_or(u.chat = cur_chat), false)
    from public.llm_usage u
    where u.user_id = target and u.created_at >= coalesce(since, '-infinity'::timestamptz)
    group by 1;
end;
$$;
revoke all on function public.trial_usage(uuid, text) from public, anon;
grant execute on function public.trial_usage(uuid, text) to authenticated, service_role;

-- история: выдача и снятие пробного доступа
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
  if new.display_name is distinct from old.display_name then
    insert into public.member_events (user_id, actor, kind, detail)
    values (new.user_id, who, 'name', jsonb_build_object('name', new.display_name));
  end if;
  if new.allow_shared_key is distinct from old.allow_shared_key then
    insert into public.member_events (user_id, actor, kind, detail)
    values (new.user_id, who, 'shared_key', jsonb_build_object('allowed', new.allow_shared_key));
  end if;
  if new.trial_labs is distinct from old.trial_labs or new.trial_chat is distinct from old.trial_chat then
    insert into public.member_events (user_id, actor, kind, detail)
    values (new.user_id, who, 'trial', jsonb_build_object('labs', new.trial_labs, 'chat', new.trial_chat));
  end if;
  return new;
end;
$$;
