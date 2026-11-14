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

type Trial = { trial_labs: number | null; trial_chat: number | null };

/** Пробный доступ: заданы лимиты лаб или сообщений чата. */
export function isTrial(m: Trial): boolean {
  return m.trial_labs !== null && m.trial_labs !== undefined || m.trial_chat !== null && m.trial_chat !== undefined;
}

/** Прямой доступ к провайдеру: у участника свой ключ и он не в пробном доступе. */
export function directAllowed(m: Trial, own: string | null): boolean {
  return !!own && own.trim() !== "" && !isTrial(m);
}

/** Модель запроса: выбранная в админке (если она в списке), иначе запрошенная (если в списке), иначе первая. */
export function chooseModel(requested: string, chosen: string | null, models: string[]): string {
  if (chosen && models.includes(chosen)) return chosen;
  if (models.includes(requested)) return requested;
  return models[0];
}

const MODEL_NAME = /^[A-Za-z0-9._:/-]{1,100}$/;

/** Модель запроса: у своего ключа — модель из карточки участника (её выбрал администратор под этот ключ, список
 * LLM_MODELS её не ограничивает); у общего ключа и своего без модели — модель по умолчанию (chooseModel). */
export function modelFor(
  kind: "own" | "shared", keyModel: string | null, requested: string, chosen: string | null, models: string[],
): string {
  const mine = (keyModel ?? "").trim();
  if (kind === "own" && MODEL_NAME.test(mine)) return mine;
  return chooseModel(requested, chosen, models);
}

/** Предел ответа не выше потолка; GLM рассуждает всегда — короткое рассуждение по умолчанию и запас 4000 на него. */
export function tuneBody(body: Record<string, unknown>, cap: number): Record<string, unknown> {
  const out = { ...body };
  const asked = Number(out.max_tokens ?? cap);
  const base = Number.isFinite(asked) && asked > 0 ? asked : cap;
  if (String(out.model ?? "").startsWith("glm")) {
    if (!out.reasoning_effort) out.reasoning_effort = "low";
    out.max_tokens = Math.min(base + 4000, cap);
  } else {
    out.max_tokens = Math.min(base, cap);
  }
  return out;
}

export type UsageReport = {
  model: string; prompt_tokens: number; completion_tokens: number; lab: string | null; chat: string | null;
};

/** Расход прямого запроса от приложения: модель из списка, целые числа 0…1 000 000. */
export function parseUsage(body: unknown, models: string[]): UsageReport | null {
  if (!body || typeof body !== "object") return null;
  const b = body as Record<string, unknown>;
  const model = String(b.model ?? "");
  if (!models.includes(model)) return null;
  const ok = (v: unknown) => typeof v === "number" && Number.isInteger(v) && v >= 0 && v <= 1_000_000;
  if (!ok(b.prompt_tokens) || !ok(b.completion_tokens)) return null;
  const tag = (v: unknown) => (typeof v === "string" ? labTag(v) : null);
  return {
    model, prompt_tokens: b.prompt_tokens as number, completion_tokens: b.completion_tokens as number,
    lab: tag(b.lab), chat: tag(b.chat),
  };
}
