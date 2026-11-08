-- Проверка пробного доступа и поддержки двумя временными пользователями (A — администратор, B — участник).
-- Всё в одной транзакции, которая откатывается в конце (RAISE EXCEPTION): в базе ничего не остаётся.
-- Ожидаемо: b_own_rows=2 b_chats_lab1=1 b_reads_a=blocked b_trial_update=0 b_insert_admin_msg=blocked
--           b_reads_a_msgs=0 b_cfg=blocked svc_has_chat=true a_reads_b_msgs=1 a_reply=ok b_mark_read=1 a_status=ok
do $$
declare
  a uuid := '00000000-0000-4000-8000-0000000000a5';
  b uuid := '00000000-0000-4000-8000-0000000000b5';
  b_own_rows int; b_chats_lab1 int; b_reads_a text := 'ALLOWED'; b_trial_update int; b_insert text := 'ALLOWED';
  b_reads_a_msgs int; b_cfg text := 'ALLOWED'; svc_has_chat boolean; a_reads_b_msgs int; a_reply text := 'failed';
  b_mark_read int; a_status text := 'failed';
begin
  insert into auth.users (id, instance_id, aud, role, email) values
    (a, '00000000-0000-0000-0000-000000000000', 'authenticated', 'authenticated', 'trial-a@example.invalid'),
    (b, '00000000-0000-0000-0000-000000000000', 'authenticated', 'authenticated', 'trial-b@example.invalid');
  update public.members set approved = true, role = 'admin' where user_id = a;
  update public.members set approved = true, role = 'member', trial_labs = 3, trial_chat = 5,
         trial_since = now() - interval '1 hour' where user_id = b;
  insert into public.llm_usage (user_id, model, lab, chat, created_at) values
    (b, 'm', 'p/s/1', 'm1', now()), (b, 'm', 'p/s/1', 'm1', now()), (b, 'm', 'p/s/2', null, now()),
    (b, 'm', 'p/s/old', null, now() - interval '2 days'), (a, 'm', 'a/s/1', null, now());
  insert into public.support_messages (user_id, from_admin, text) values (a, false, 'сообщение A'), (b, false, 'сообщение B');

  -- участник B
  perform set_config('request.jwt.claims', json_build_object('sub', b, 'role', 'authenticated')::text, true);
  execute 'set local role authenticated';
  select count(*) into b_own_rows from public.trial_usage(b);
  select chats into b_chats_lab1 from public.trial_usage(b) where lab = 'p/s/1';
  begin
    perform public.trial_usage(a);
  exception when others then b_reads_a := 'blocked';
  end;
  update public.members set trial_labs = 100 where user_id = b;
  get diagnostics b_trial_update = row_count;
  begin
    insert into public.support_messages (user_id, from_admin, text) values (b, true, 'подделка');
  exception when others then b_insert := 'blocked';
  end;
  select count(*) into b_reads_a_msgs from public.support_messages where user_id = a;
  begin
    perform public.support_config_get();
  exception when others then b_cfg := 'blocked';
  end;
  execute 'reset role';

  -- функция (service_role)
  perform set_config('request.jwt.claims', json_build_object('role', 'service_role')::text, true);
  select has_chat into svc_has_chat from public.trial_usage(b, 'm1') where lab = 'p/s/1';

  -- администратор A
  perform set_config('request.jwt.claims', json_build_object('sub', a, 'role', 'authenticated')::text, true);
  execute 'set local role authenticated';
  select count(*) into a_reads_b_msgs from public.support_messages where user_id = b;
  insert into public.support_messages (user_id, from_admin, text) values (b, true, 'ответ');
  a_reply := 'ok';
  perform public.admin_support_status();
  a_status := 'ok';
  execute 'reset role';

  -- B отмечает ответ прочитанным
  perform set_config('request.jwt.claims', json_build_object('sub', b, 'role', 'authenticated')::text, true);
  execute 'set local role authenticated';
  perform public.support_mark_read();
  execute 'reset role';
  select count(*) into b_mark_read from public.support_messages where user_id = b and from_admin and read_at is not null;

  raise exception 'RESULT b_own_rows=% b_chats_lab1=% b_reads_a=% b_trial_update=% b_insert_admin_msg=% b_reads_a_msgs=% b_cfg=% svc_has_chat=% a_reads_b_msgs=% a_reply=% b_mark_read=% a_status=%',
    b_own_rows, b_chats_lab1, b_reads_a, b_trial_update, b_insert, b_reads_a_msgs, b_cfg, svc_has_chat,
    a_reads_b_msgs, a_reply, b_mark_read, a_status;
end $$;
