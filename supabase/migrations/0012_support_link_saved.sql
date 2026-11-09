-- Код ссылки Telegram не сохранялся, и бот на любую свежую ссылку отвечал «Ссылка устарела».
-- Причина: Supabase не выполняет UPDATE без WHERE в запросах через API (расширение safeupdate), даже внутри функции,
-- а support_config_set обновляла единственную строку настроек без условия. Из SQL Editor она работала.
create or replace function public.support_config_set(owner bigint, bot text, code text, expires timestamptz)
returns void language sql security definer set search_path = '' as $$
  update private.support_config
     set owner_chat_id = owner, bot_username = bot, bind_code = code, bind_expires = expires
   where id
$$;
revoke all on function public.support_config_set(bigint, text, text, timestamptz) from public, anon, authenticated;
grant execute on function public.support_config_set(bigint, text, text, timestamptz) to service_role;
