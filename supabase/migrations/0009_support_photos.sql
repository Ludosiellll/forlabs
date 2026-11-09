-- ForLabs: фото в поддержке. Файлы — в приватном бакете support (папка = id студента), в сообщении — их пути.
-- Ответ владельца реплаем на любое из сообщений бота (текст или фото) находит студента через support_tg.

alter table public.support_messages add column if not exists images text[] not null default '{}';
alter table public.support_messages drop constraint if exists support_messages_text_check;
alter table public.support_messages add constraint support_messages_text_check
  check (length(text) <= 4000 and (length(text) >= 1 or cardinality(images) > 0));

create table if not exists public.support_tg (
  tg_chat_id bigint not null,
  tg_message_id bigint not null,
  user_id uuid not null references auth.users(id) on delete cascade,
  created_at timestamptz not null default now(),
  primary key (tg_chat_id, tg_message_id)
);
alter table public.support_tg enable row level security;  -- политик нет: читает и пишет только функция support

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('support', 'support', false, 10485760, array['image/jpeg', 'image/png'])
on conflict (id) do nothing;

drop policy if exists support_files_select on storage.objects;
create policy support_files_select on storage.objects for select to authenticated
  using (bucket_id = 'support' and ((storage.foldername(name))[1] = (select auth.uid())::text
                                    or (select private.is_admin())));
drop policy if exists support_files_insert on storage.objects;
create policy support_files_insert on storage.objects for insert to authenticated
  with check (bucket_id = 'support' and ((storage.foldername(name))[1] = (select auth.uid())::text
                                         or (select private.is_admin())));
