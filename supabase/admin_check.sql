-- Проверка функций администратора двумя временными пользователями (A — администратор, B — участник).
-- Всё в одной транзакции, которая откатывается в конце (RAISE EXCEPTION): в базе ничего не остаётся.
-- Ожидаемо: b_set_key=blocked b_key_for=blocked 42501 a_set_key_last4=5678 short_key=blocked
--           service_key_ok=true a_reads_b_labs=1 b_reads_a_labs=0 b_reads_events=0 events>=2 cleared=true
do $$
declare
  a uuid := '00000000-0000-4000-8000-0000000000a2';
  b uuid := '00000000-0000-4000-8000-0000000000b2';
  b_set_key text := 'ALLOWED'; b_key_for text := 'ALLOWED'; short_key text := 'ALLOWED';
  last4 text; service_ok boolean; a_reads_b int; b_reads_a int; b_reads_events int; events int; cleared boolean;
begin
  insert into auth.users (id, instance_id, aud, role, email) values
    (a, '00000000-0000-0000-0000-000000000000', 'authenticated', 'authenticated', 'adm-a@example.invalid'),
    (b, '00000000-0000-0000-0000-000000000000', 'authenticated', 'authenticated', 'adm-b@example.invalid');
  update public.members set approved = true, role = 'admin' where user_id = a;
  update public.members set approved = true, role = 'member' where user_id = b;

  -- участник B
  perform set_config('request.jwt.claims', json_build_object('sub', b, 'role', 'authenticated')::text, true);
  execute 'set local role authenticated';
  insert into public.labs (profile_id, subject_id, number, status, state) values ('adm_b', 's', '1', 'draft', '{}');
  begin
    perform public.admin_set_key(a, 'sk-other-99999999');
  exception when others then b_set_key := 'blocked';
  end;
  begin
    perform public.llm_key_for(b);
  exception when others then b_key_for := 'blocked ' || sqlstate;
  end;

  -- администратор A
  perform set_config('request.jwt.claims', json_build_object('sub', a, 'role', 'authenticated')::text, true);
  insert into public.labs (profile_id, subject_id, number, status, state) values ('adm_a', 's', '1', 'draft', '{}');
  last4 := public.admin_set_key(b, '  sk-test-12345678 ');
  begin
    perform public.admin_set_key(b, 'abc');
  exception when others then short_key := 'blocked';
  end;
  update public.members set access_until = now() + interval '30 days', note = 'оплатил' where user_id = b;
  select count(*) into a_reads_b from public.labs where profile_id = 'adm_b';

  -- снова B: чужие лабы и история не видны
  perform set_config('request.jwt.claims', json_build_object('sub', b, 'role', 'authenticated')::text, true);
  select count(*) into b_reads_a from public.labs where profile_id = 'adm_a';
  select count(*) into b_reads_events from public.member_events;

  -- прокси (service_role) читает ключ
  execute 'reset role';
  perform set_config('request.jwt.claims', '', true);
  execute 'set local role service_role';
  service_ok := public.llm_key_for(b) = 'sk-test-12345678';
  execute 'reset role';
  select count(*) into events from public.member_events where user_id = b;

  -- администратор убирает ключ
  perform set_config('request.jwt.claims', json_build_object('sub', a, 'role', 'authenticated')::text, true);
  execute 'set local role authenticated';
  perform public.admin_clear_key(b);
  execute 'reset role';
  perform set_config('request.jwt.claims', '', true);
  cleared := public.llm_key_for(b) is null;

  raise exception 'ADMIN_RESULT b_set_key=% b_key_for=% a_set_key_last4=% short_key=% service_key_ok=% a_reads_b_labs=% b_reads_a_labs=% b_reads_events=% events=% cleared=%',
    b_set_key, b_key_for, last4, short_key, service_ok, a_reads_b, b_reads_a, b_reads_events, events, cleared;
end $$;
