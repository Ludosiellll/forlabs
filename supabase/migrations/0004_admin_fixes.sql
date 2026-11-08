-- ForLabs: исправления по ревью админ-панели.
-- 1) Заметка администратора — в отдельной таблице: участник читает свою строку members целиком,
--    а заметку видеть не должен.
-- 2) Черновик лабы получает номер из методички — расход его разбора переносится на лабу (только свои строки).

create table if not exists public.member_notes (
  user_id uuid primary key references auth.users(id) on delete cascade,
  note text not null default '',
  updated_at timestamptz not null default now()
);
alter table public.member_notes enable row level security;
drop policy if exists member_notes_admin on public.member_notes;
create policy member_notes_admin on public.member_notes for all to authenticated
  using ((select private.is_admin())) with check ((select private.is_admin()));

do $$
begin
  if exists (select 1 from information_schema.columns
             where table_schema = 'public' and table_name = 'members' and column_name = 'note') then
    insert into public.member_notes (user_id, note)
      select user_id, note from public.members where note <> ''
      on conflict (user_id) do nothing;
  end if;
end $$;

create or replace function private.member_notes_audit() returns trigger
language plpgsql security definer set search_path = '' as $$
begin
  if tg_op = 'INSERT' or new.note is distinct from old.note then
    new.updated_at := now();
    insert into public.member_events (user_id, actor, kind, detail)
    values (new.user_id, (select auth.uid()), 'note', jsonb_build_object('note', new.note));
  end if;
  return new;
end;
$$;
revoke all on function private.member_notes_audit() from public, anon, authenticated;
drop trigger if exists member_notes_audit on public.member_notes;
create trigger member_notes_audit before insert or update on public.member_notes
  for each row execute function private.member_notes_audit();

-- история members — без заметки (столбец уходит)
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
  return new;
end;
$$;
alter table public.members drop column if exists note;

-- перенос расхода черновика на лабу: только свои строки
create or replace function public.retag_my_usage(old_lab text, new_lab text) returns void
language sql security definer set search_path = '' as $$
  update public.llm_usage set lab = left(new_lab, 200)
  where user_id = (select auth.uid()) and lab = old_lab
$$;
revoke all on function public.retag_my_usage(text, text) from public, anon;
grant execute on function public.retag_my_usage(text, text) to authenticated;
