-- Проверка RLS двумя временными пользователями. Всё выполняется в одной транзакции, которая
-- откатывается в конце (RAISE EXCEPTION): в базе ничего не остаётся. Запуск: SQL Editor в Supabase
-- или Management API /database/query. Ожидаемо: seen_a=1 seen_b=0 upd_b=0 del_b=0, обе вставки blocked 42501.
do $$
declare
  a uuid := '00000000-0000-4000-8000-0000000000a1';
  b uuid := '00000000-0000-4000-8000-0000000000b1';
  seen_a int; seen_b int; upd_b int; del_b int; files_b int;
  b_as_a text := 'blocked'; unapproved text := 'blocked';
begin
  insert into auth.users (id, instance_id, aud, role, email) values
    (a, '00000000-0000-0000-0000-000000000000', 'authenticated', 'authenticated', 'rls-a@example.invalid'),
    (b, '00000000-0000-0000-0000-000000000000', 'authenticated', 'authenticated', 'rls-b@example.invalid');
  update public.members set approved = true where user_id in (a, b);
  perform set_config('request.jwt.claims', json_build_object('sub', a, 'role', 'authenticated')::text, true);
  execute 'set local role authenticated';
  insert into public.labs (profile_id, subject_id, number, status, state) values ('p', 's', '1', 'draft', '{}');
  select count(*) into seen_a from public.labs where profile_id = 'p';
  perform set_config('request.jwt.claims', json_build_object('sub', b, 'role', 'authenticated')::text, true);
  select count(*) into seen_b from public.labs where profile_id = 'p';
  update public.labs set status = 'final' where profile_id = 'p';
  get diagnostics upd_b = row_count;
  delete from public.labs where profile_id = 'p';
  get diagnostics del_b = row_count;
  begin
    insert into public.labs (owner, profile_id, subject_id, number, status, state) values (a, 'x', 's', '1', 'draft', '{}');
    b_as_a := 'ALLOWED';
  exception when others then b_as_a := 'blocked ' || sqlstate;
  end;
  select count(*) into files_b from storage.objects where bucket_id = 'labs' and name like a::text || '/%';
  execute 'reset role';
  perform set_config('request.jwt.claims', '', true);
  update public.members set approved = false where user_id = b;
  perform set_config('request.jwt.claims', json_build_object('sub', b, 'role', 'authenticated')::text, true);
  execute 'set local role authenticated';
  begin
    insert into public.labs (profile_id, subject_id, number, status, state) values ('q', 's', '1', 'draft', '{}');
    unapproved := 'ALLOWED';
  exception when others then unapproved := 'blocked ' || sqlstate;
  end;
  raise exception 'RLS_RESULT seen_a=% seen_b=% upd_b=% del_b=% b_insert_as_a=% files_b=% unapproved_insert=%',
    seen_a, seen_b, upd_b, del_b, b_as_a, files_b, unapproved;
end $$;
