-- ForLabs: исправления по ревью пробного доступа и поддержки.

-- 1. Пробный лимит проверяется и учитывается атомарно: блокировка на участника, проверка и запись «брони»
--    запроса в одной транзакции (параллельные запросы не проскакивают мимо лимита). Вызывает только функция llm.
create or replace function public.trial_begin(target uuid, cur_lab text, cur_chat text, max_calls integer, req_model text)
returns table (ok boolean, message text, usage_id bigint)
language plpgsql security definer set search_path = '' as $$
declare
  m record;
  since timestamptz;
  labs_used integer;
  cur_calls integer;
  cur_chats integer;
  seen boolean;
  rid bigint;
begin
  if coalesce(current_setting('request.jwt.claims', true)::jsonb ->> 'role', '') <> 'service_role' then
    raise exception 'only service' using errcode = '42501';
  end if;
  perform pg_advisory_xact_lock(hashtext('forlabs_trial:' || target::text));
  select mb.trial_labs, mb.trial_chat, mb.trial_since, mb.role into m from public.members mb where mb.user_id = target;
  if m.trial_labs is null or m.role = 'admin' then
    return query select true, ''::text, null::bigint;
    return;
  end if;
  if coalesce(btrim(cur_lab), '') = '' then
    return query select false, 'trial needs lab tag'::text, null::bigint;
    return;
  end if;
  since := coalesce(m.trial_since, '-infinity'::timestamptz);
  select count(distinct u.lab) into labs_used from public.llm_usage u
    where u.user_id = target and u.created_at >= since and coalesce(u.lab, '') <> '';
  select count(*), count(distinct u.chat), coalesce(bool_or(u.chat = cur_chat), false)
    into cur_calls, cur_chats, seen
    from public.llm_usage u where u.user_id = target and u.created_at >= since and u.lab = cur_lab;
  if cur_calls = 0 and labs_used >= m.trial_labs then
    return query select false, ('trial labs limit: ' || m.trial_labs)::text, null::bigint;
    return;
  end if;
  if cur_calls >= greatest(coalesce(max_calls, 120), 1) then
    return query select false, 'trial lab calls limit'::text, null::bigint;
    return;
  end if;
  if cur_chat is not null and not seen and cur_chats >= coalesce(m.trial_chat, 0) then
    return query select false, ('trial chat limit: ' || coalesce(m.trial_chat, 0))::text, null::bigint;
    return;
  end if;
  insert into public.llm_usage (user_id, model, lab, chat, key_kind)
  values (target, left(coalesce(req_model, ''), 100), left(cur_lab, 200), left(cur_chat, 64), 'reserved')
  returning id into rid;
  return query select true, ''::text, rid;
end;
$$;
revoke all on function public.trial_begin(uuid, text, text, integer, text) from public, anon, authenticated;
grant execute on function public.trial_begin(uuid, text, text, integer, text) to service_role;

-- 2. Перенос расхода — только с черновика («…/новая-N») на лабу с номером того же профиля и предмета,
--    без слияния с уже существующей меткой: иначе пробный лимит лаб можно было бы «обнулить».
create or replace function public.retag_my_usage(old_lab text, new_lab text) returns void
language plpgsql security definer set search_path = '' as $$
begin
  if coalesce(btrim(new_lab), '') = ''
     or split_part(old_lab, '/', 3) not like 'новая%'
     or split_part(new_lab, '/', 3) like 'новая%'
     or split_part(new_lab, '/', 3) = ''
     or split_part(new_lab, '/', 1) <> split_part(old_lab, '/', 1)
     or split_part(new_lab, '/', 2) <> split_part(old_lab, '/', 2)
     or exists (select 1 from public.llm_usage u where u.user_id = (select auth.uid()) and u.lab = new_lab) then
    return;
  end if;
  update public.llm_usage set lab = left(new_lab, 200) where user_id = (select auth.uid()) and lab = old_lab;
end;
$$;
revoke all on function public.retag_my_usage(text, text) from public, anon;
grant execute on function public.retag_my_usage(text, text) to authenticated;

-- 3. Ответ из Telegram ищется по чату и сообщению (номера сообщений у каждого чата свои).
alter table public.support_messages add column if not exists tg_chat_id bigint;
drop index if exists public.support_messages_tg_idx;
create unique index if not exists support_messages_tg_chat_idx on public.support_messages (tg_chat_id, tg_message_id)
  where tg_message_id is not null;

-- 4. Настройки бота закрыты для всех, кроме функций с правами владельца базы.
revoke all on table private.support_config from public, anon, authenticated;

-- 5. Лишний индекс (повторял llm_usage_user_idx из 0001).
drop index if exists public.llm_usage_user_time_idx;
