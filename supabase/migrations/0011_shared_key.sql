-- ForLabs: общий ключ модели задаётся в админке (в Supabase Vault, зашифрованно), а не секретом LLM_API_KEY.
-- Секрет LLM_API_KEY остаётся запасным: функция llm берёт ключ из Vault, если он задан, иначе секрет.

create table if not exists private.shared_key (
  id boolean primary key default true check (id),
  secret_id uuid,
  last4 text,
  set_at timestamptz
);
revoke all on table private.shared_key from public, anon, authenticated;

create or replace function public.admin_set_shared_key(api_key text) returns text
language plpgsql security definer set search_path = '' as $$
declare
  k text := btrim(coalesce(api_key, ''));
  existing uuid;
  sid uuid;
begin
  if not (select private.is_admin()) then
    raise exception 'only admin' using errcode = '42501';
  end if;
  if length(k) < 8 or k ~ '\s' then
    raise exception 'key too short' using errcode = '22023';
  end if;
  select sk.secret_id into existing from private.shared_key sk where sk.id;
  if existing is not null then
    perform vault.update_secret(existing, k);
    update private.shared_key set last4 = right(k, 4), set_at = now() where id;
  else
    delete from vault.secrets where name = 'forlabs_shared_key';
    sid := vault.create_secret(k, 'forlabs_shared_key', 'ForLabs: общий ключ модели');
    insert into private.shared_key (id, secret_id, last4, set_at) values (true, sid, right(k, 4), now())
    on conflict (id) do update set secret_id = excluded.secret_id, last4 = excluded.last4, set_at = excluded.set_at;
  end if;
  return right(k, 4);
end;
$$;

create or replace function public.admin_clear_shared_key() returns void
language plpgsql security definer set search_path = '' as $$
declare
  sid uuid;
begin
  if not (select private.is_admin()) then
    raise exception 'only admin' using errcode = '42501';
  end if;
  select sk.secret_id into sid from private.shared_key sk where sk.id;
  delete from private.shared_key where id;
  if sid is not null then
    delete from vault.secrets where id = sid;
  end if;
end;
$$;

create or replace function public.admin_shared_key_info() returns table (last4 text, set_at timestamptz)
language plpgsql stable security definer set search_path = '' as $$
begin
  if not (select private.is_admin()) then
    raise exception 'only admin' using errcode = '42501';
  end if;
  return query select sk.last4, sk.set_at from private.shared_key sk where sk.id;
end;
$$;

-- только для функции llm (service_role): расшифрованный общий ключ
create or replace function public.shared_key_value() returns text
language sql stable security definer set search_path = '' as $$
  select ds.decrypted_secret from private.shared_key sk join vault.decrypted_secrets ds on ds.id = sk.secret_id
  where sk.id
$$;

revoke all on function public.admin_set_shared_key(text) from public, anon;
revoke all on function public.admin_clear_shared_key() from public, anon;
revoke all on function public.admin_shared_key_info() from public, anon;
grant execute on function public.admin_set_shared_key(text) to authenticated;
grant execute on function public.admin_clear_shared_key() to authenticated;
grant execute on function public.admin_shared_key_info() to authenticated;
revoke all on function public.shared_key_value() from public, anon, authenticated;
grant execute on function public.shared_key_value() to service_role;
