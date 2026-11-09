-- ForLabs: поддержка. Студент пишет из приложения (через функцию support: она же шлёт владельцу в Telegram),
-- владелец отвечает в Telegram реплаем или из админки. Участник видит только свою переписку.

create table if not exists public.support_messages (
  id bigint generated always as identity primary key,
  user_id uuid not null references auth.users(id) on delete cascade,  -- чья переписка
  from_admin boolean not null default false,
  text text not null check (length(text) between 1 and 4000),
  created_at timestamptz not null default now(),
  tg_message_id bigint,   -- сообщение у владельца в Telegram: его реплай находит переписку
  read_at timestamptz     -- прочитано другой стороной
);
create index if not exists support_messages_user_idx on public.support_messages (user_id, created_at);
create unique index if not exists support_messages_tg_idx on public.support_messages (tg_message_id)
  where tg_message_id is not null;
alter table public.support_messages enable row level security;

drop policy if exists support_select on public.support_messages;
create policy support_select on public.support_messages for select to authenticated
  using (user_id = (select auth.uid()) or (select private.is_admin()));
-- студент пишет только через функцию support (лимит частоты, Telegram); администратор отвечает и из админки
drop policy if exists support_admin_insert on public.support_messages;
create policy support_admin_insert on public.support_messages for insert to authenticated
  with check ((select private.is_admin()) and from_admin);
drop policy if exists support_admin_update on public.support_messages;
create policy support_admin_update on public.support_messages for update to authenticated
  using ((select private.is_admin())) with check ((select private.is_admin()));

-- студент отмечает ответы прочитанными (только read_at своих входящих)
create or replace function public.support_mark_read() returns void
language sql security definer set search_path = '' as $$
  update public.support_messages set read_at = now()
  where user_id = (select auth.uid()) and from_admin and read_at is null
$$;
revoke all on function public.support_mark_read() from public, anon;
grant execute on function public.support_mark_read() to authenticated;

-- настройки бота: чей это Telegram (владелец привязывается кодом из админки)
create table if not exists private.support_config (
  id boolean primary key default true check (id),
  owner_chat_id bigint,
  bot_username text,
  bind_code text,
  bind_expires timestamptz
);
insert into private.support_config (id) values (true) on conflict (id) do nothing;

create or replace function public.admin_support_status() returns table (telegram boolean, bot_username text)
language plpgsql stable security definer set search_path = '' as $$
begin
  if not (select private.is_admin()) then
    raise exception 'only admin' using errcode = '42501';
  end if;
  return query select c.owner_chat_id is not null, c.bot_username from private.support_config c;
end;
$$;
revoke all on function public.admin_support_status() from public, anon;
grant execute on function public.admin_support_status() to authenticated;

-- для функции support (service_role): чтение и запись настроек бота
create or replace function public.support_config_get() returns table (owner_chat_id bigint, bot_username text,
                                                                     bind_code text, bind_expires timestamptz)
language sql stable security definer set search_path = '' as $$
  select c.owner_chat_id, c.bot_username, c.bind_code, c.bind_expires from private.support_config c
$$;
create or replace function public.support_config_set(owner bigint, bot text, code text, expires timestamptz)
returns void language sql security definer set search_path = '' as $$
  update private.support_config set owner_chat_id = owner, bot_username = bot, bind_code = code, bind_expires = expires
$$;
revoke all on function public.support_config_get() from public, anon, authenticated;
revoke all on function public.support_config_set(bigint, text, text, timestamptz) from public, anon, authenticated;
grant execute on function public.support_config_get() to service_role;
grant execute on function public.support_config_set(bigint, text, text, timestamptz) to service_role;
