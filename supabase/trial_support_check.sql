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

-- Пробный лимит атомарно (usage_begin, миграция 0007) и защита переноса расхода (retag_my_usage).
-- Ожидаемо: lab3=true lab4=trial labs limit: 3 old_lab=true chat5=true chat6=trial chat limit: 5 chat5_again=true
--           calls_cap=trial lab calls limit no_tag=trial needs lab tag member_call=blocked admin=true
--           to_empty=0 p_s_1_kept=2 (строка + бронь проверки) draft_moved=1
do $$
declare
  a uuid := '00000000-0000-4000-8000-0000000000a6';
  b uuid := '00000000-0000-4000-8000-0000000000b6';
  r record; lab3 text; lab4 text; old_lab text; chat5 text; chat6 text; chat5_again text; calls_cap text; no_tag text;
  member_call text := 'ALLOWED'; adm text; to_empty int; kept int; moved int;
begin
  insert into auth.users (id, instance_id, aud, role, email) values
    (a, '00000000-0000-0000-0000-000000000000', 'authenticated', 'authenticated', 'tb-a@example.invalid'),
    (b, '00000000-0000-0000-0000-000000000000', 'authenticated', 'authenticated', 'tb-b@example.invalid');
  update public.members set approved = true, role = 'admin', trial_labs = 1, trial_chat = 0 where user_id = a;
  update public.members set approved = true, role = 'member', trial_labs = 3, trial_chat = 5,
         trial_since = now() - interval '1 hour' where user_id = b;
  insert into public.llm_usage (user_id, model, lab, chat) values
    (b, 'm', 'p/s/1', null), (b, 'm', 'p/s/2', null), (b, 'm', 'p/s/2', 'c1'), (b, 'm', 'p/s/2', 'c2'),
    (b, 'm', 'p/s/2', 'c3'), (b, 'm', 'p/s/2', 'c4');
  perform set_config('request.jwt.claims', json_build_object('role', 'service_role')::text, true);
  select * into r from public.usage_begin(b, 'p/s/3', null, 'm', 1000000, 300, 120); lab3 := r.ok::text;
  select * into r from public.usage_begin(b, 'p/s/4', null, 'm', 1000000, 300, 120); lab4 := coalesce(nullif(r.message, ''), r.ok::text);
  select * into r from public.usage_begin(b, 'p/s/1', null, 'm', 1000000, 300, 120); old_lab := r.ok::text;
  select * into r from public.usage_begin(b, 'p/s/2', 'c5', 'm', 1000000, 300, 120); chat5 := r.ok::text;
  select * into r from public.usage_begin(b, 'p/s/2', 'c6', 'm', 1000000, 300, 120); chat6 := coalesce(nullif(r.message, ''), r.ok::text);
  select * into r from public.usage_begin(b, 'p/s/2', 'c5', 'm', 1000000, 300, 120); chat5_again := r.ok::text;
  select * into r from public.usage_begin(b, 'p/s/2', null, 'm', 1000000, 300, 5); calls_cap := coalesce(nullif(r.message, ''), r.ok::text);
  select * into r from public.usage_begin(b, '', null, 'm', 1000000, 300, 120); no_tag := coalesce(nullif(r.message, ''), r.ok::text);
  select * into r from public.usage_begin(a, 'x/y/1', 'c', 'm', 1000000, 300, 120); adm := r.ok::text;
  insert into public.llm_usage (user_id, model, lab) values (b, 'm', 'p/s/новая-1');

  perform set_config('request.jwt.claims', json_build_object('sub', b, 'role', 'authenticated')::text, true);
  execute 'set local role authenticated';
  begin
    perform public.usage_begin(b, 'p/s/9', null, 'm', 1000000, 300, 120);
  exception when others then member_call := 'blocked';
  end;
  perform public.retag_my_usage('p/s/1', '');           -- обнулить лабу — нельзя
  perform public.retag_my_usage('p/s/1', 'p/s/2');      -- слить две лабы — нельзя
  perform public.retag_my_usage('p/s/новая-1', 'p/s/7');  -- черновик → номер — можно
  execute 'reset role';
  select count(*) into to_empty from public.llm_usage where user_id = b and lab = '';
  select count(*) into kept from public.llm_usage where user_id = b and lab = 'p/s/1';
  select count(*) into moved from public.llm_usage where user_id = b and lab = 'p/s/7';
  raise exception 'RESULT lab3=% lab4=% old_lab=% chat5=% chat6=% chat5_again=% calls_cap=% no_tag=% member_call=% admin=% to_empty=% p_s_1_kept=% draft_moved=%',
    lab3, lab4, old_lab, chat5, chat6, chat5_again, calls_cap, no_tag, member_call, adm, to_empty, kept, moved;
end $$;


-- Лимиты для всех (usage_begin, миграция 0008): токены в день, запросы на лабу, пробный — как раньше.
-- Ожидаемо: day_ok=true day_over=daily token limit: 1000000 own_limit=true lab_cap=lab calls limit: 3 no_tag_full=true
--           trial_lab4=trial labs limit: 1 member_call=blocked admin=true reserved=1
do $$
declare
  a uuid := '00000000-0000-4000-8000-0000000000a7';
  b uuid := '00000000-0000-4000-8000-0000000000b7';
  c uuid := '00000000-0000-4000-8000-0000000000c7';
  r record; day_ok text; day_over text; own_limit text; lab_cap text; no_tag_full text; trial_lab4 text;
  member_call text := 'ALLOWED'; adm text; reserved int;
begin
  insert into auth.users (id, instance_id, aud, role, email) values
    (a, '00000000-0000-0000-0000-000000000000', 'authenticated', 'authenticated', 'ub-a@example.invalid'),
    (b, '00000000-0000-0000-0000-000000000000', 'authenticated', 'authenticated', 'ub-b@example.invalid'),
    (c, '00000000-0000-0000-0000-000000000000', 'authenticated', 'authenticated', 'ub-c@example.invalid');
  update public.members set approved = true, role = 'admin' where user_id = a;
  update public.members set approved = true, role = 'member' where user_id = b;
  update public.members set approved = true, role = 'member', trial_labs = 1, trial_chat = 5, trial_since = now() where user_id = c;
  perform set_config('request.jwt.claims', json_build_object('role', 'service_role')::text, true);
  select * into r from public.usage_begin(b, 'p/s/1', null, 'm', 1000000, 300, 120); day_ok := r.ok::text;
  select count(*) into reserved from public.llm_usage where id = r.usage_id and key_kind = 'reserved';
  insert into public.llm_usage (user_id, model, lab, prompt_tokens, completion_tokens) values (b, 'm', 'p/s/1', 600000, 400000);
  select * into r from public.usage_begin(b, 'p/s/1', null, 'm', 1000000, 300, 120); day_over := coalesce(nullif(r.message, ''), r.ok::text);
  update public.members set daily_tokens = 5000000 where user_id = b;
  select * into r from public.usage_begin(b, 'p/s/1', null, 'm', 1000000, 300, 120); own_limit := r.ok::text;
  select * into r from public.usage_begin(b, 'p/s/1', null, 'm', 1000000, 3, 120); lab_cap := coalesce(nullif(r.message, ''), r.ok::text);
  select * into r from public.usage_begin(b, null, null, 'm', 1000000, 300, 120); no_tag_full := r.ok::text;
  select * into r from public.usage_begin(c, 'q/s/1', null, 'm', 1000000, 300, 120);
  select * into r from public.usage_begin(c, 'q/s/2', null, 'm', 1000000, 300, 120); trial_lab4 := coalesce(nullif(r.message, ''), r.ok::text);
  update public.llm_usage set prompt_tokens = 99999999 where user_id = a;
  select * into r from public.usage_begin(a, 'x/y/1', null, 'm', 1, 1, 1); adm := r.ok::text;
  perform set_config('request.jwt.claims', json_build_object('sub', b, 'role', 'authenticated')::text, true);
  execute 'set local role authenticated';
  begin
    perform public.usage_begin(b, 'p/s/9', null, 'm', 999999999, 300, 120);
  exception when others then member_call := 'blocked';
  end;
  execute 'reset role';
  raise exception 'RESULT day_ok=% day_over=% own_limit=% lab_cap=% no_tag_full=% trial_lab4=% member_call=% admin=% reserved=%',
    day_ok, day_over, own_limit, lab_cap, no_tag_full, trial_lab4, member_call, adm, reserved;
end $$;

-- Фото в поддержке (миграция 0009): студент кладёт файлы только в свою папку и видит только свои,
-- администратор видит все; сообщение только с фото (без текста) допустимо, пустое — нет.
-- Ожидаемо: b_own_insert=ok b_foreign_insert=blocked b_sees_own=1 b_sees_foreign=0 a_sees_all=2 photo_only=ok empty=blocked
do $$
declare
  a uuid := '00000000-0000-4000-8000-0000000000a8';
  b uuid := '00000000-0000-4000-8000-0000000000b8';
  c uuid := '00000000-0000-4000-8000-0000000000c8';
  b_own text := 'failed'; b_foreign text := 'ALLOWED'; b_sees_own int; b_sees_foreign int; a_sees int;
  photo_only text := 'failed'; empty text := 'ALLOWED';
begin
  insert into auth.users (id, instance_id, aud, role, email) values
    (a, '00000000-0000-0000-0000-000000000000', 'authenticated', 'authenticated', 'ph-a@example.invalid'),
    (b, '00000000-0000-0000-0000-000000000000', 'authenticated', 'authenticated', 'ph-b@example.invalid'),
    (c, '00000000-0000-0000-0000-000000000000', 'authenticated', 'authenticated', 'ph-c@example.invalid');
  update public.members set approved = true, role = 'admin' where user_id = a;
  update public.members set approved = true, role = 'member' where user_id in (b, c);
  insert into storage.objects (bucket_id, name, owner) values ('support', c::text || '/c1.jpg', c);
  perform set_config('request.jwt.claims', json_build_object('sub', b, 'role', 'authenticated')::text, true);
  execute 'set local role authenticated';
  insert into storage.objects (bucket_id, name, owner) values ('support', b::text || '/b1.jpg', b);
  b_own := 'ok';
  begin
    insert into storage.objects (bucket_id, name, owner) values ('support', c::text || '/evil.jpg', b);
  exception when others then b_foreign := 'blocked';
  end;
  select count(*) into b_sees_own from storage.objects where bucket_id = 'support' and name like b::text || '/%';
  select count(*) into b_sees_foreign from storage.objects where bucket_id = 'support' and name like c::text || '/%';
  execute 'reset role';
  perform set_config('request.jwt.claims', json_build_object('sub', a, 'role', 'authenticated')::text, true);
  execute 'set local role authenticated';
  select count(*) into a_sees from storage.objects where bucket_id = 'support' and (name like b::text || '/%' or name like c::text || '/%');
  insert into public.support_messages (user_id, from_admin, text, images) values (b, true, '', array[b::text || '/b1.jpg']);
  photo_only := 'ok';
  begin
    insert into public.support_messages (user_id, from_admin, text, images) values (b, true, '', '{}');
  exception when others then empty := 'blocked';
  end;
  execute 'reset role';
  raise exception 'RESULT b_own_insert=% b_foreign_insert=% b_sees_own=% b_sees_foreign=% a_sees_all=% photo_only=% empty=%',
    b_own, b_foreign, b_sees_own, b_sees_foreign, a_sees, photo_only, empty;
end $$;
