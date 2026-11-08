// Чистая логика прокси: без сети и Supabase, чтобы проверять тестами Node.

export type MemberAccess = { approved: boolean; role: string; access_until: string | null };
export type Decision = { ok: true } | { ok: false; status: number; message: string };

/** Пускать ли к модели: одобрен и (администратор, без срока или срок не прошёл). */
export function decideAccess(m: MemberAccess, now: Date): Decision {
  if (!m.approved) return { ok: false, status: 403, message: "account not approved" };
  if (m.role === "admin" || !m.access_until) return { ok: true };
  const until = new Date(m.access_until);
  if (Number.isNaN(until.getTime())) return { ok: true };
  if (until.getTime() <= now.getTime()) {
    return { ok: false, status: 403, message: `subscription expired: ${until.toISOString()}` };
  }
  return { ok: true };
}

/** Свой ключ участника, иначе общий (если разрешён), иначе ничего. */
export function pickKey(own: string | null, shared: string, allowShared: boolean): { key: string; kind: "own" | "shared" } | null {
  if (own && own.trim()) return { key: own.trim(), kind: "own" };
  if (allowShared && shared) return { key: shared, kind: "shared" };
  return null;
}

/** Остаток токенов из ответа провайдера: token_balance, иначе balance. */
export function parseBalance(body: unknown): number | null {
  if (!body || typeof body !== "object") return null;
  const b = body as Record<string, unknown>;
  for (const field of ["token_balance", "balance"]) {
    const v = b[field];
    const n = typeof v === "number" ? v : typeof v === "string" && v.trim() !== "" ? Number(v) : NaN;
    if (Number.isFinite(n)) return n;
  }
  return null;
}

/** Метка лабы из заголовка X-ForLabs-Lab (URL-кодирована). */
export function labTag(header: string | null): string | null {
  if (!header) return null;
  try {
    const s = decodeURIComponent(header).trim();
    return s ? s.slice(0, 200) : null;
  } catch {
    return null;
  }
}

/** Провайдер отказал по личному ключу участника: участнику нужен администратор, а не «настройки».
 *  Временная перегрузка (429 без слов о квоте) и чужие ошибки проходят как есть. */
export function ownKeyError(kind: "own" | "shared", status: number, body: string): { status: number; message: string } | null {
  if (kind !== "own") return null;
  const low = body.toLowerCase();
  if (low.includes("model_not_allowed") || low.includes("model not")) return null;
  const money = /balance|quota|insufficient|credit/.test(low);
  if (status === 402 || ((status === 429 || status === 403) && money)) {
    return { status: 402, message: `member key balance: ${status}` };
  }
  if (status === 401 || status === 403) return { status: 402, message: `member key rejected: ${status}` };
  return null;
}

export type TrialLimits = { trial_labs: number | null; trial_chat: number | null };
/** Расход пробного периода по лабе: запросов к модели и разных сообщений чата правок. */
export type TrialRow = { lab: string; calls: number; chats: number };

/** Пробный доступ: не больше trial_labs разных лаб, trial_chat сообщений чата правок на лабу и (скрыто)
 *  maxCalls запросов на лабу — чтобы перезапусками не съесть лимиты. chatSeen — у этого сообщения уже были запросы. */
export function decideTrial(m: TrialLimits, usage: TrialRow[], lab: string | null, chat: string | null,
                            chatSeen: boolean, maxCalls: number): Decision {
  if (m.trial_labs === null || m.trial_labs === undefined) return { ok: true };
  if (!lab) return { ok: false, status: 403, message: "trial needs lab tag" };
  const labs = usage.filter((r) => r.lab);
  const cur = labs.find((r) => r.lab === lab);
  if (!cur && labs.length >= m.trial_labs) {
    return { ok: false, status: 403, message: `trial labs limit: ${m.trial_labs}` };
  }
  if (cur && cur.calls >= maxCalls) return { ok: false, status: 403, message: "trial lab calls limit" };
  const chatLimit = m.trial_chat ?? 0;
  if (chat && !chatSeen && (cur?.chats ?? 0) >= chatLimit) {
    return { ok: false, status: 403, message: `trial chat limit: ${chatLimit}` };
  }
  return { ok: true };
}
