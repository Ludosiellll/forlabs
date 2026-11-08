-- Служебные функции проверки доступа — в закрытую схему, не видимую через API.
create schema if not exists private;
revoke all on schema private from public, anon;
grant usage on schema private to authenticated, service_role;

create or replace function private.is_approved() returns boolean
language sql stable security definer set search_path = '' as $$
  select coalesce((select m.approved from public.members m where m.user_id = (select auth.uid())), false)
$$;

create or replace function private.is_admin() returns boolean
language sql stable security definer set search_path = '' as $$
  select coalesce((select m.approved and m.role = 'admin' from public.members m where m.user_id = (select auth.uid())), false)
$$;

revoke all on function private.is_approved() from public, anon;
revoke all on function private.is_admin() from public, anon;
grant execute on function private.is_approved() to authenticated, service_role;
grant execute on function private.is_admin() to authenticated, service_role;

drop policy if exists members_select on public.members;
create policy members_select on public.members for select to authenticated
  using (user_id = (select auth.uid()) or (select private.is_admin()));
drop policy if exists members_admin_update on public.members;
create policy members_admin_update on public.members for update to authenticated
  using ((select private.is_admin())) with check ((select private.is_admin()));

do $$
declare t text;
begin
  foreach t in array array['student_profiles', 'labs', 'subject_memory', 'rule_overrides', 'custom_subjects'] loop
    execute format('drop policy if exists %I on public.%I', t || '_own', t);
    execute format(
      'create policy %I on public.%I for all to authenticated '
      'using (owner = (select auth.uid()) and (select private.is_approved())) '
      'with check (owner = (select auth.uid()) and (select private.is_approved()))', t || '_own', t);
  end loop;
end $$;

drop policy if exists llm_usage_select on public.llm_usage;
create policy llm_usage_select on public.llm_usage for select to authenticated
  using (user_id = (select auth.uid()) or (select private.is_admin()));

drop policy if exists labs_files_select on storage.objects;
create policy labs_files_select on storage.objects for select to authenticated
  using (bucket_id = 'labs' and (storage.foldername(name))[1] = (select auth.uid())::text and (select private.is_approved()));
drop policy if exists labs_files_insert on storage.objects;
create policy labs_files_insert on storage.objects for insert to authenticated
  with check (bucket_id = 'labs' and (storage.foldername(name))[1] = (select auth.uid())::text and (select private.is_approved()));
drop policy if exists labs_files_update on storage.objects;
create policy labs_files_update on storage.objects for update to authenticated
  using (bucket_id = 'labs' and (storage.foldername(name))[1] = (select auth.uid())::text and (select private.is_approved()));
drop policy if exists labs_files_delete on storage.objects;
create policy labs_files_delete on storage.objects for delete to authenticated
  using (bucket_id = 'labs' and (storage.foldername(name))[1] = (select auth.uid())::text and (select private.is_approved()));

drop function if exists public.is_approved();
drop function if exists public.is_admin();

-- администратор не может случайно снять одобрение с самого себя
create or replace function private.keep_self_admin() returns trigger
language plpgsql security definer set search_path = '' as $$
begin
  if old.user_id = (select auth.uid()) and (new.approved is distinct from old.approved or new.role is distinct from old.role) then
    raise exception 'Нельзя менять свою роль и одобрение';
  end if;
  return new;
end;
$$;
revoke all on function private.keep_self_admin() from public, anon, authenticated;
drop trigger if exists members_keep_self on public.members;
create trigger members_keep_self before update on public.members
  for each row execute function private.keep_self_admin();
