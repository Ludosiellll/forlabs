-- Проверка функций администратора двумя временными пользователями (A — администратор, B — участник).
-- Всё в одной транзакции, которая откатывается в конце (RAISE EXCEPTION): в базе ничего не остаётся.
-- Ожидаемо: b_set_key=blocked b_key_for=blocked 42501 a_set_key_last4=5678 short_key=blocked
--           service_key_ok=true replace_ok=true a_reads_b_labs=1 b_reads_a_labs=0 b_reads_events=0
--           b_self_update=0 b_reads_notes=0 retag_own=1 retag_other=0 events>=3 cleared=true vault_cleared=true
do $$
declare
  a uuid := '00000000-0000-4000-8000-0000000000a2';
  b uuid := '00000000-0000-4000-8000-0000000000b2';
  b_set_key text := 'ALLOWED'; b_key_for text := 'ALLOWED'; short_key text := 'ALLOWED';
  last4 text; service_ok boolean; replace_ok boolean; a_reads_b int; b_reads_a int; b_reads_events int;
  b_self_update int; b_reads_notes int; retag_own int; retag_other int; events int; cleared boolean;
  vault_cleared boolean;
begin
  insert into auth.users (id, instance_id, aud, role, email) values
    (a, '00000000-0000-0000-0000-000000000000', 'authenticated', 'authenticated', 'adm-a@example.invalid'),
    (b, '00000000-0000-0000-0000-000000000000', 'authenticated', 'authenticated', 'adm-b@example.invalid');
  update public.members set approved = true, role = 'admin' where user_id = a;
  update public.members set approved = true, role = 'member' where user_id = b;
  insert into public.llm_usage (user_id, model, prompt_tokens, completion_tokens, lab) values
    (b, 'm', 10, 5, 'adm_b/s/новая-1'), (a, 'm', 10, 5, 'adm_b/s/новая-1');

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
  update public.members set access_until = null, allow_shared_key = true where user_id = b;
  get diagnostics b_self_update = row_count;
  perform public.retag_my_usage('adm_b/s/новая-1', 'adm_b/s/7');

  -- администратор A
  perform set_config('request.jwt.claims', json_build_object('sub', a, 'role', 'authenticated')::text, true);
  insert into public.labs (profile_id, subject_id, number, status, state) values ('adm_a', 's', '1', 'draft', '{}');
  last4 := public.admin_set_key(b, '  sk-test-12345678 ');
  begin
    perform public.admin_set_key(b, 'abc');
  exception when others then short_key := 'blocked';
  end;
  update public.members set access_until = now() + interval '30 days' where user_id = b;
  insert into public.member_notes (user_id, note) values (b, 'оплатил')
    on conflict (user_id) do update set note = excluded.note;
  select count(*) into a_reads_b from public.labs where profile_id = 'adm_b';

  -- снова B: чужие лабы, история и заметки не видны
  perform set_config('request.jwt.claims', json_build_object('sub', b, 'role', 'authenticated')::text, true);
  select count(*) into b_reads_a from public.labs where profile_id = 'adm_a';
  select count(*) into b_reads_events from public.member_events;
  select count(*) into b_reads_notes from public.member_notes;

  -- прокси (service_role) читает ключ; замена ключа
  execute 'reset role';
  perform set_config('request.jwt.claims', '', true);
  execute 'set local role service_role';
  service_ok := public.llm_key_for(b) = 'sk-test-12345678';
  execute 'reset role';
  perform set_config('request.jwt.claims', json_build_object('sub', a, 'role', 'authenticated')::text, true);
  execute 'set local role authenticated';
  perform public.admin_set_key(b, 'sk-test-87654321');
  execute 'reset role';
  perform set_config('request.jwt.claims', '', true);
  replace_ok := public.llm_key_for(b) = 'sk-test-87654321';
  select count(*) into events from public.member_events where user_id = b;
  select count(*) into retag_own from public.llm_usage where user_id = b and lab = 'adm_b/s/7';
  select count(*) into retag_other from public.llm_usage where user_id = a and lab = 'adm_b/s/7';

  -- администратор убирает ключ
  perform set_config('request.jwt.claims', json_build_object('sub', a, 'role', 'authenticated')::text, true);
  execute 'set local role authenticated';
  perform public.admin_clear_key(b);
  execute 'reset role';
  perform set_config('request.jwt.claims', '', true);
  cleared := public.llm_key_for(b) is null;
  vault_cleared := not exists (select 1 from vault.secrets where name = 'forlabs_key_' || b::text);

  raise exception 'ADMIN_RESULT b_set_key=% b_key_for=% a_set_key_last4=% short_key=% service_key_ok=% replace_ok=% a_reads_b_labs=% b_reads_a_labs=% b_reads_events=% b_self_update=% b_reads_notes=% retag_own=% retag_other=% events=% cleared=% vault_cleared=%',
    b_set_key, b_key_for, last4, short_key, service_ok, replace_ok, a_reads_b, b_reads_a, b_reads_events,
    b_self_update, b_reads_notes, retag_own, retag_other, events, cleared, vault_cleared;
end $$;
