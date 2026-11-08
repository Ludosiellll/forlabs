// Чистая логика поддержки: без сети и Supabase, чтобы проверять тестами Node.

export type Action =
  | { kind: "ignore" }
  | { kind: "bind"; code: string; chatId: number }
  | { kind: "reply"; replyTo: number; text: string }
  | { kind: "hint"; chatId: number };

type TgMessage = {
  message_id?: number;
  chat?: { id?: number };
  text?: string;
  reply_to_message?: { message_id?: number };
};

/** Что делать с обновлением Telegram: привязать владельца по коду, передать его ответ студенту, подсказать. */
export function telegramAction(update: { message?: TgMessage } & Record<string, unknown>, ownerChatId: number | null): Action {
  const m = update.message;
  const chatId = m?.chat?.id;
  if (!m || typeof chatId !== "number") return { kind: "ignore" };
  const text = typeof m.text === "string" ? m.text.trim() : "";
  const start = /^\/start(?:@\w+)?\s+([A-Za-z0-9]{6,32})$/.exec(text);
  if (start) return { kind: "bind", code: start[1], chatId };
  if (chatId !== ownerChatId) return { kind: "ignore" };
  const replyTo = m.reply_to_message?.message_id;
  if (text && typeof replyTo === "number") return { kind: "reply", replyTo, text: text.slice(0, 4000) };
  return { kind: "hint", chatId };
}

export function cleanText(v: unknown): string | null {
  if (typeof v !== "string") return null;
  const s = v.trim();
  return s ? s.slice(0, 4000) : null;
}

/** Сообщение студента владельцу в Telegram. */
export function forOwner(name: string, email: string, text: string, status: string): string {
  const who = email ? `${name} (${email})` : name;
  return `ForLabs · ${who}${status ? ` · ${status}` : ""}\n\n${text}\n\nОтветь на это сообщение (Reply) — ответ придёт студенту в ForLabs.`;
}
