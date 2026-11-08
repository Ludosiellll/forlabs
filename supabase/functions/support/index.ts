// ForLabs: поддержка. Развёртывается с verify_jwt = false: Telegram зовёт /telegram без JWT (проверяется
// секрет вебхука), а вход студента и администратора функция проверяет сама, как llm.
//   POST /support/send      {text}  — студент пишет; сообщение уходит владельцу в Telegram
//   POST /support/setup             — администратор: включить вебхук и получить ссылку привязки владельца
//   POST /support/telegram          — вебхук Telegram: привязка владельца и его ответы реплаем
// Секреты: TELEGRAM_BOT_TOKEN (от @BotFather), TELEGRAM_WEBHOOK_SECRET (любая длинная случайная строка).
import { createClient } from "npm:@supabase/supabase-js@2.45.4";
import { cleanText, forOwner, telegramAction } from "./logic.ts";

const BOT = Deno.env.get("TELEGRAM_BOT_TOKEN") ?? "";
const SECRET = Deno.env.get("TELEGRAM_WEBHOOK_SECRET") ?? "";
const PER_HOUR = 20;
const admin = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!, {
  auth: { persistSession: false },
});

type Config = { owner_chat_id: number | null; bot_username: string | null; bind_code: string | null; bind_expires: string | null };

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}
const err = (status: number, message: string) => json(status, { error: { message } });

async function tg(method: string, body: unknown): Promise<Record<string, unknown> | null> {
  if (!BOT) return null;
  try {
    const r = await fetch(`https://api.telegram.org/bot${BOT}/${method}`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      signal: AbortSignal.timeout(10_000),
    });
    return await r.json().catch(() => null);
  } catch {
    return null;
  }
}

async function config(): Promise<Config> {
  const { data } = await admin.rpc("support_config_get");
  return ((data ?? [])[0] ?? { owner_chat_id: null, bot_username: null, bind_code: null, bind_expires: null }) as Config;
}

async function signedIn(req: Request) {
  const token = (req.headers.get("Authorization") ?? "").replace(/^Bearer\s+/i, "");
  if (!token) return null;
  const { data } = await admin.auth.getUser(token);
  if (!data?.user) return null;
  const { data: member } = await admin.from("members")
    .select("*").eq("user_id", data.user.id).maybeSingle();
  return member ? { user: data.user, member } : null;
}

async function send(req: Request): Promise<Response> {
  const who = await signedIn(req);
  if (!who) return err(401, "not signed in");
  const body = await req.json().catch(() => ({})) as { text?: unknown };
  const text = cleanText(body.text);
  if (!text) return err(400, "empty message");
  const since = new Date(Date.now() - 3600_000).toISOString();
  const { count } = await admin.from("support_messages").select("id", { count: "exact", head: true })
    .eq("user_id", who.user.id).eq("from_admin", false).gte("created_at", since);
  if ((count ?? 0) >= PER_HOUR) return err(429, "support rate limit");
  const { data: row, error } = await admin.from("support_messages")
    .insert({ user_id: who.user.id, from_admin: false, text }).select("id").single();
  if (error) return err(500, `insert: ${error.message}`);
  const cfg = await config();
  let delivered = false;
  if (cfg.owner_chat_id) {
    const m = who.member as { email: string; display_name: string; approved: boolean; trial_labs: number | null };
    const status = !m.approved ? "ждёт одобрения" : m.trial_labs !== null ? "пробный доступ" : "";
    const r = await tg("sendMessage", { chat_id: cfg.owner_chat_id, text: forOwner(m.display_name || m.email, m.email, text, status) });
    const mid = (r?.result as { message_id?: number } | undefined)?.message_id;
    if (typeof mid === "number") {
      delivered = true;
      await admin.from("support_messages").update({ tg_message_id: mid }).eq("id", row.id);
    }
  }
  return json(200, { ok: true, id: row.id, telegram: delivered });
}

async function setup(req: Request): Promise<Response> {
  const who = await signedIn(req);
  if (!who || who.member.role !== "admin" || !who.member.approved) return err(403, "only admin");
  if (!BOT || !SECRET) return err(503, "telegram secrets missing");
  const me = await tg("getMe", {});
  const username = (me?.result as { username?: string } | undefined)?.username;
  if (!username) return err(502, "telegram token rejected");
  const url = `${Deno.env.get("SUPABASE_URL")}/functions/v1/support/telegram`;
  const hook = await tg("setWebhook", { url, secret_token: SECRET, allowed_updates: ["message"] });
  if (!hook?.ok) return err(502, "telegram webhook rejected");
  const bytes = crypto.getRandomValues(new Uint8Array(12));
  const code = Array.from(bytes, (b) => "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"[b % 56]).join("");
  const cfg = await config();
  await admin.rpc("support_config_set", { owner: cfg.owner_chat_id, bot: username, code,
    expires: new Date(Date.now() + 30 * 60_000).toISOString() });
  return json(200, { bot: username, link: `https://t.me/${username}?start=${code}`, bound: cfg.owner_chat_id !== null });
}

async function telegram(req: Request): Promise<Response> {
  if (!SECRET || req.headers.get("X-Telegram-Bot-Api-Secret-Token") !== SECRET) return err(403, "bad secret");
  const update = await req.json().catch(() => ({}));
  const cfg = await config();
  const action = telegramAction(update, cfg.owner_chat_id);
  if (action.kind === "bind") {
    const fresh = cfg.bind_code && action.code === cfg.bind_code && cfg.bind_expires && new Date(cfg.bind_expires) > new Date();
    if (fresh) {
      await admin.rpc("support_config_set", { owner: action.chatId, bot: cfg.bot_username, code: null, expires: null });
      await tg("sendMessage", { chat_id: action.chatId, text: "Готово: сюда будут приходить сообщения поддержки ForLabs. Отвечай на них реплаем." });
    }
  } else if (action.kind === "reply") {
    const { data: orig } = await admin.from("support_messages").select("user_id").eq("tg_message_id", action.replyTo).maybeSingle();
    if (orig) {
      await admin.from("support_messages").insert({ user_id: orig.user_id, from_admin: true, text: action.text });
      await admin.from("support_messages").update({ read_at: new Date().toISOString() })
        .eq("user_id", orig.user_id).eq("from_admin", false).is("read_at", null);
      await tg("setMessageReaction", { chat_id: cfg.owner_chat_id, message_id: (update as { message: { message_id: number } }).message.message_id,
        reaction: [{ type: "emoji", emoji: "👌" }] });
    } else {
      await tg("sendMessage", { chat_id: cfg.owner_chat_id, text: "Не нашёл, кому это ответ: отвечай реплаем на сообщение студента." });
    }
  } else if (action.kind === "hint") {
    await tg("sendMessage", { chat_id: action.chatId, text: "Чтобы ответить студенту, ответь (Reply) на его сообщение." });
  }
  return json(200, { ok: true });  // Telegram повторяет запросы при ошибке — отвечаем 200 всегда
}

Deno.serve(async (req: Request) => {
  if (req.method !== "POST") return err(405, "only POST");
  const path = new URL(req.url).pathname;
  if (path.endsWith("/send")) return send(req);
  if (path.endsWith("/setup")) return setup(req);
  if (path.endsWith("/telegram")) return telegram(req);
  return err(404, "only /send, /setup, /telegram");
});
