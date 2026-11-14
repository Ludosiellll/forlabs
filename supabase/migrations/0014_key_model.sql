-- ForLabs: модель своего ключа участника. Администратор выдаёт ключи под разные модели (общий — DeepSeek, свои —
-- GLM): модель хранится рядом с ключом и уходит вместе с ним. Не выбрана — модель по умолчанию (0013, LLM_MODELS).

alter table private.member_keys add column if not exists model text;

create or replace function public.admin_set_key_model(target uuid, model text) returns text
language plpgsql security definer set search_path = '' as $$
declare
  m text := btrim(coalesce(model, ''));
begin
  if not (select private.is_admin()) then
    raise exception 'only admin' using errcode = '42501';
  end if;
  if m <> '' and m !~ '^[A-Za-z0-9._:/-]{1,100}$' then
    raise exception 'invalid model' using errcode = '22023';
  end if;
  if not exists (select 1 from private.member_keys mk where mk.user_id = target) then
    raise exception 'no key' using errcode = '22023';
  end if;
  update private.member_keys set model = nullif(m, '') where user_id = target;
  insert into public.member_events (user_id, actor, kind, detail)
  values (target, (select auth.uid()), 'key_model', jsonb_build_object('model', nullif(m, '')));
  return m;
end;
$$;

-- список ключей для админки — теперь и с моделью (тип результата меняется: функция пересоздаётся)
drop function if exists public.admin_keys();
create function public.admin_keys() returns table (user_id uuid, last4 text, set_at timestamptz, model text)
language plpgsql stable security definer set search_path = '' as $$
begin
  if not (select private.is_admin()) then
    raise exception 'only admin' using errcode = '42501';
  end if;
  return query select mk.user_id, mk.last4, mk.set_at, mk.model from private.member_keys mk;
end;
$$;

-- только для функции llm (service_role): модель своего ключа участника
create or replace function public.llm_key_model_for(target uuid) returns text
language sql stable security definer set search_path = '' as $$
  select mk.model from private.member_keys mk where mk.user_id = target
$$;

revoke all on function public.admin_set_key_model(uuid, text) from public, anon;
grant execute on function public.admin_set_key_model(uuid, text) to authenticated;
revoke all on function public.admin_keys() from public, anon;
grant execute on function public.admin_keys() to authenticated;
revoke all on function public.llm_key_model_for(uuid) from public, anon, authenticated;
grant execute on function public.llm_key_model_for(uuid) to service_role;
