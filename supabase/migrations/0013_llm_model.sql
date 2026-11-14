-- ForLabs: модель для всех выбирается в админке. Не выбрана — функция llm берёт первую из LLM_MODELS.
-- Список допустимых моделей по-прежнему задаёт переменная LLM_MODELS функции.

create table if not exists private.llm_model (
  id boolean primary key default true check (id),
  model text not null,
  set_at timestamptz not null default now()
);
revoke all on table private.llm_model from public, anon, authenticated;

create or replace function public.admin_set_model(model text) returns text
language plpgsql security definer set search_path = '' as $$
declare
  m text := btrim(coalesce(model, ''));
begin
  if not (select private.is_admin()) then
    raise exception 'only admin' using errcode = '42501';
  end if;
  if m !~ '^[A-Za-z0-9._:/-]{1,100}$' then
    raise exception 'invalid model' using errcode = '22023';
  end if;
  insert into private.llm_model (id, model, set_at) values (true, m, now())
  on conflict (id) do update set model = excluded.model, set_at = excluded.set_at;
  return m;
end;
$$;

create or replace function public.admin_clear_model() returns void
language plpgsql security definer set search_path = '' as $$
begin
  if not (select private.is_admin()) then
    raise exception 'only admin' using errcode = '42501';
  end if;
  delete from private.llm_model where id;
end;
$$;

create or replace function public.admin_model_info() returns table (model text, set_at timestamptz)
language plpgsql stable security definer set search_path = '' as $$
begin
  if not (select private.is_admin()) then
    raise exception 'only admin' using errcode = '42501';
  end if;
  return query select lm.model, lm.set_at from private.llm_model lm where lm.id;
end;
$$;

-- только для функции llm (service_role)
create or replace function public.model_value() returns text
language sql stable security definer set search_path = '' as $$
  select lm.model from private.llm_model lm where lm.id
$$;

revoke all on function public.admin_set_model(text) from public, anon;
revoke all on function public.admin_clear_model() from public, anon;
revoke all on function public.admin_model_info() from public, anon;
grant execute on function public.admin_set_model(text) to authenticated;
grant execute on function public.admin_clear_model() to authenticated;
grant execute on function public.admin_model_info() to authenticated;
revoke all on function public.model_value() from public, anon, authenticated;
grant execute on function public.model_value() to service_role;
