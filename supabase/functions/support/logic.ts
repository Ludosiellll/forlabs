// Чистая логика поддержки: без сети и Supabase, чтобы проверять тестами Node.

export type Action =
  | { kind: "ignore" }
  | { kind: "bind"; code: string; chatId: number }
  | { kind: "reply"; replyTo: number; text: string; photo: string | null }
  | { kind: "hint"; chatId: number };

type TgMessage = {
  message_id?: number;
  chat?: { id?: number };
  text?: string;
  caption?: string;
  photo?: { file_id?: string; file_size?: number; width?: number; height?: number }[];
  reply_to_message?: { message_id?: number };
};

/** Самый крупный вариант фото из сообщения Telegram. */
function largestPhoto(m: TgMessage): string | null {
  const sizes = (m.photo ?? []).filter((p) => typeof p?.file_id === "string");
  if (!sizes.length) return null;
  const area = (p: { file_size?: number; width?: number; height?: number }) =>
    p.file_size ?? (p.width ?? 0) * (p.height ?? 0);
  return sizes.reduce((a, b) => (area(b) >= area(a) ? b : a)).file_id as string;
}

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
  const photo = largestPhoto(m);
  const body = (text || (typeof m.caption === "string" ? m.caption.trim() : "")).slice(0, 4000);
  if ((body || photo) && typeof replyTo === "number") {
    if (photo) return { kind: "reply", replyTo, text: body, photo };
    return { kind: "reply", replyTo, text: body, photo: null };
  }
  return { kind: "hint", chatId };
}

export function cleanText(v: unknown): string | null {
  if (typeof v !== "string") return null;
  const s = v.trim();
  return s ? s.slice(0, 4000) : null;
}

/** Сообщение студента владельцу в Telegram. */
export function forOwner(name: string, email: string, text: string, status: string): string {
  const who = (email ? `${name} (${email})` : name).slice(0, 200);
  const head = `ForLabs · ${who}${status ? ` · ${status}` : ""}\n\n`;
  const tail = "\n\nОтветь на это сообщение (Reply) — ответ придёт студенту в ForLabs.";
  const room = 4096 - head.length - tail.length;  // лимит длины сообщения в Telegram
  const body = text.length > room ? text.slice(0, room - 1) + "…" : text;
  return head + body + tail;
}
