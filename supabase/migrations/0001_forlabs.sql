-- ForLabs: многопользовательский режим.
-- Каждый видит только свои данные и только после одобрения администратором.
-- Первый зарегистрированный пользователь становится администратором.

create table if not exists public.members (
  user_id uuid primary key references auth.users(id) on delete cascade,
  email text not null default '',
  display_name text not null default '',
  role text not null default 'member' check (role in ('admin', 'member')),
  approved boolean not null default false,
  created_at timestamptz not null default now()
);
alter table public.members enable row level security;

create or replace function public.is_approved() returns boolean
language sql stable security definer set search_path = '' as $$
  select coalesce((select m.approved from public.members m where m.user_id = (select auth.uid())), false)
$$;

create or replace function public.is_admin() returns boolean
language sql stable security definer set search_path = '' as $$
  select coalesce((select m.approved and m.role = 'admin' from public.members m where m.user_id = (select auth.uid())), false)
$$;

create or replace function public.handle_new_user() returns trigger
language plpgsql security definer set search_path = '' as $$
declare
  is_first boolean;
begin
  perform pg_advisory_xact_lock(4242);
  select not exists (select 1 from public.members) into is_first;
  insert into public.members (user_id, email, display_name, role, approved)
  values (new.id, coalesce(new.email, ''), coalesce(new.raw_user_meta_data ->> 'name', ''),
          case when is_first then 'admin' else 'member' end, is_first);
  return new;
end;
$$;
revoke execute on function public.handle_new_user() from public, anon, authenticated;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created after insert on auth.users
  for each row execute function public.handle_new_user();

drop policy if exists members_select on public.members;
create policy members_select on public.members for select to authenticated
  using (user_id = (select auth.uid()) or (select public.is_admin()));
drop policy if exists members_admin_update on public.members;
create policy members_admin_update on public.members for update to authenticated
  using ((select public.is_admin())) with check ((select public.is_admin()));

-- данные пользователя
create table if not exists public.student_profiles (
  owner uuid not null default auth.uid() references auth.users(id) on delete cascade,
  id text not null,
  data jsonb not null,
  updated_at timestamptz not null default now(),
  primary key (owner, id)
);

create table if not exists public.labs (
  owner uuid not null default auth.uid() references auth.users(id) on delete cascade,
  profile_id text not null,
  subject_id text not null,
  number text not null,
  status text not null default 'draft',
  state jsonb not null,
  updated_at timestamptz not null default now(),
  primary key (owner, profile_id, subject_id, number)
);

create table if not exists public.subject_memory (
  owner uuid not null default auth.uid() references auth.users(id) on delete cascade,
  profile_id text not null,
  subject_id text not null,
  text text not null default '',
  updated_at timestamptz not null default now(),
  primary key (owner, profile_id, subject_id)
);

create table if not exists public.rule_overrides (
  owner uuid not null default auth.uid() references auth.users(id) on delete cascade,
  path text not null,
  text text not null,
  updated_at timestamptz not null default now(),
  primary key (owner, path)
);

create table if not exists public.custom_subjects (
  owner uuid not null default auth.uid() references auth.users(id) on delete cascade,
  id text not null,
  data jsonb not null,
  primary key (owner, id)
);

do $$
declare t text;
begin
  foreach t in array array['student_profiles', 'labs', 'subject_memory', 'rule_overrides', 'custom_subjects'] loop
    execute format('alter table public.%I enable row level security', t);
    execute format('drop policy if exists %I on public.%I', t || '_own', t);
    execute format(
      'create policy %I on public.%I for all to authenticated '
      'using (owner = (select auth.uid()) and (select public.is_approved())) '
      'with check (owner = (select auth.uid()) and (select public.is_approved()))', t || '_own', t);
  end loop;
end $$;

-- расход токенов: пишет только функция-прокси (service role), читают свои строки, админ — все
create table if not exists public.llm_usage (
  id bigint generated always as identity primary key,
  user_id uuid not null references auth.users(id) on delete cascade,
  model text not null,
  prompt_tokens integer not null default 0,
  completion_tokens integer not null default 0,
  created_at timestamptz not null default now()
);
create index if not exists llm_usage_user_idx on public.llm_usage (user_id, created_at);
alter table public.llm_usage enable row level security;
drop policy if exists llm_usage_select on public.llm_usage;
create policy llm_usage_select on public.llm_usage for select to authenticated
  using (user_id = (select auth.uid()) or (select public.is_admin()));

-- файлы: приватный bucket, папка = id пользователя
insert into storage.buckets (id, name, public, file_size_limit)
values ('labs', 'labs', false, 52428800)
on conflict (id) do nothing;

drop policy if exists labs_files_select on storage.objects;
create policy labs_files_select on storage.objects for select to authenticated
  using (bucket_id = 'labs' and (storage.foldername(name))[1] = (select auth.uid())::text and (select public.is_approved()));
drop policy if exists labs_files_insert on storage.objects;
create policy labs_files_insert on storage.objects for insert to authenticated
  with check (bucket_id = 'labs' and (storage.foldername(name))[1] = (select auth.uid())::text and (select public.is_approved()));
drop policy if exists labs_files_update on storage.objects;
create policy labs_files_update on storage.objects for update to authenticated
  using (bucket_id = 'labs' and (storage.foldername(name))[1] = (select auth.uid())::text and (select public.is_approved()));
drop policy if exists labs_files_delete on storage.objects;
create policy labs_files_delete on storage.objects for delete to authenticated
  using (bucket_id = 'labs' and (storage.foldername(name))[1] = (select auth.uid())::text and (select public.is_approved()));
