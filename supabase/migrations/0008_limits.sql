-- ForLabs: лимиты для всех участников (не только пробных) — проверяются на сервере атомарно.
--  * токенов в день на человека: members.daily_tokens, пусто — общий (секрет DAILY_TOKENS функции llm, 1 000 000);
--  * запросов на одну лабу (секрет LAB_CALLS_LIMIT, 300; на пробном — TRIAL_CALLS_PER_LAB, 120);
--  * пробный доступ (лабы и сообщения правок) — как в 0007.
-- Администратор без лимитов. День — по Минску.

alter table public.members add column if not exists daily_tokens integer;
do $$ begin
  alter table public.members add constraint members_daily_tokens_range
    check (daily_tokens is null or daily_tokens between 1000 and 1000000000);
exception when duplicate_object then null;
end $$;

create or replace function public.usage_begin(target uuid, cur_lab text, cur_chat text, req_model text,
                                              default_daily integer, lab_calls integer, trial_calls integer)
returns table (ok boolean, message text, usage_id bigint)
language plpgsql security definer set search_path = '' as $$
declare
  m record;
  day_start timestamptz := (date_trunc('day', now() at time zone 'Europe/Minsk')) at time zone 'Europe/Minsk';
  daily integer;
  used_today bigint;
  since timestamptz;
  labs_used integer;
  cur_calls integer;
  cur_chats integer;
  seen boolean;
  all_calls integer;
  rid bigint;
  v_lab text := nullif(btrim(coalesce(cur_lab, '')), '');
begin
  if coalesce(current_setting('request.jwt.claims', true)::jsonb ->> 'role', '') <> 'service_role' then
    raise exception 'only service' using errcode = '42501';
  end if;
  perform pg_advisory_xact_lock(hashtext('forlabs_trial:' || target::text));
  select mb.role, mb.trial_labs, mb.trial_chat, mb.trial_since, mb.daily_tokens into m
    from public.members mb where mb.user_id = target;
  if m.role = 'admin' then
    return query select true, ''::text, null::bigint;
    return;
  end if;
  daily := coalesce(m.daily_tokens, default_daily, 1000000);
  select coalesce(sum(u.prompt_tokens + u.completion_tokens), 0) into used_today
    from public.llm_usage u where u.user_id = target and u.created_at >= day_start;
  if used_today >= daily then
    return query select false, ('daily token limit: ' || daily)::text, null::bigint;
    return;
  end if;
  if v_lab is not null then
    select count(*) into all_calls from public.llm_usage u where u.user_id = target and u.lab = v_lab;
    if all_calls >= greatest(coalesce(lab_calls, 300), 1) then
      return query select false, ('lab calls limit: ' || greatest(coalesce(lab_calls, 300), 1))::text, null::bigint;
      return;
    end if;
  end if;
  if m.trial_labs is not null then
    if v_lab is null then
      return query select false, 'trial needs lab tag'::text, null::bigint;
      return;
    end if;
    since := coalesce(m.trial_since, '-infinity'::timestamptz);
    select count(distinct u.lab) into labs_used from public.llm_usage u
      where u.user_id = target and u.created_at >= since and coalesce(u.lab, '') <> '';
    select count(*), count(distinct u.chat), coalesce(bool_or(u.chat = cur_chat), false)
      into cur_calls, cur_chats, seen
      from public.llm_usage u where u.user_id = target and u.created_at >= since and u.lab = v_lab;
    if cur_calls = 0 and labs_used >= m.trial_labs then
      return query select false, ('trial labs limit: ' || m.trial_labs)::text, null::bigint;
      return;
    end if;
    if cur_calls >= greatest(coalesce(trial_calls, 120), 1) then
      return query select false, 'trial lab calls limit'::text, null::bigint;
      return;
    end if;
    if cur_chat is not null and not seen and cur_chats >= coalesce(m.trial_chat, 0) then
      return query select false, ('trial chat limit: ' || coalesce(m.trial_chat, 0))::text, null::bigint;
      return;
    end if;
  end if;
  insert into public.llm_usage (user_id, model, lab, chat, key_kind)
  values (target, left(coalesce(req_model, ''), 100), left(v_lab, 200), left(cur_chat, 64), 'reserved')
  returning id into rid;
  return query select true, ''::text, rid;
end;
$$;
revoke all on function public.usage_begin(uuid, text, text, text, integer, integer, integer) from public, anon, authenticated;
grant execute on function public.usage_begin(uuid, text, text, text, integer, integer, integer) to service_role;

-- trial_begin (0007) заменён на usage_begin
drop function if exists public.trial_begin(uuid, text, text, integer, text);

-- история: смена дневного лимита
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
  if new.daily_tokens is distinct from old.daily_tokens then
    insert into public.member_events (user_id, actor, kind, detail)
    values (new.user_id, who, 'daily', jsonb_build_object('tokens', new.daily_tokens));
  end if;
  return new;
end;
$$;
