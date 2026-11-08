// ForLabs: прокси к модели. Ключи — только на сервере: свой ключ участника в Vault (выдаёт администратор)
// или общий секрет LLM_API_KEY. Пускает только вошедших, одобренных и с действующей подпиской (в пробном
// доступе — в пределах лаб и сообщений чата правок), разрешает модели из списка, пишет расход по лабам. /balance — остаток на ключах у провайдера.
import { createClient } from "npm:@supabase/supabase-js@2.45.4";
import { decideAccess, decideTrial, labTag, ownKeyError, parseBalance, pickKey, type TrialRow } from "./logic.ts";

const BASE = (Deno.env.get("LLM_BASE_URL") ?? "https://api.aikeysforyou.com/v1").replace(/\/$/, "");
const KEY = Deno.env.get("LLM_API_KEY") ?? "";
// первая — модель по умолчанию; остальные разрешены как запасные (клиент сам переходит, если ключ не пускает)
const MODELS = (Deno.env.get("LLM_MODELS") ?? "deepseek-v4.1-flash-1017,deepseek-v4.1-flash,deepseek-v4-flash")
  .split(",").map((s) => s.trim());
// пробный доступ: потолок запросов на одну лабу (перезапуски), сверх лимита лаб и сообщений чата
const TRIAL_CALLS_PER_LAB = Number(Deno.env.get("TRIAL_CALLS_PER_LAB") ?? "120") || 120;
const admin = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!, {
  auth: { persistSession: false },
});

type Member = {
  approved: boolean; role: string; access_until: string | null; allow_shared_key: boolean | null;
  trial_labs: number | null; trial_chat: number | null;
};

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function err(status: number, message: string) {
  return json(status, { error: { message } });
}

async function ownKey(userId: string): Promise<string | null> {
  const { data, error } = await admin.rpc("llm_key_for", { target: userId });
  if (error) throw new Error(`llm_key_for: ${error.message}`);
  return typeof data === "string" && data ? data : null;
}

async function providerBalance(key: string): Promise<{ balance: number | null; error?: string }> {
  try {
    const r = await fetch(`${BASE}/balance`, {
      headers: { Authorization: `Bearer ${key}` },
      signal: AbortSignal.timeout(10_000),
    });
    if (!r.ok) return { balance: null, error: `provider ${r.status}` };
    const balance = parseBalance(await r.json().catch(() => null));
    return balance === null ? { balance, error: "no balance in answer" } : { balance };
  } catch (e) {
    return { balance: null, error: String((e as Error).message ?? e).slice(0, 120) };
  }
}

async function balances(req: Request, userId: string, member: Member): Promise<Response> {
  const body = await req.json().catch(() => ({})) as { user_ids?: unknown; shared?: unknown };
  const isAdmin = member.role === "admin";
  const ids = isAdmin && Array.isArray(body.user_ids)
    ? body.user_ids.filter((x): x is string => typeof x === "string").slice(0, 200)
    : [userId];
  const result: Record<string, number | null> = {};
  const errors: Record<string, string> = {};
  await Promise.all(ids.map(async (id) => {
    try {
      const key = await ownKey(id);
      if (!key) {
        result[id] = null;
        errors[id] = "no key";
        return;
      }
      const b = await providerBalance(key);
      result[id] = b.balance;
      if (b.error) errors[id] = b.error;
    } catch (e) {
      result[id] = null;
      errors[id] = String((e as Error).message ?? e).slice(0, 120);
    }
  }));
  if (isAdmin && body.shared && KEY) {
    const b = await providerBalance(KEY);
    result.shared = b.balance;
    if (b.error) errors.shared = b.error;
  }
  return json(200, { balances: result, errors });
}

Deno.serve(async (req: Request) => {
  if (req.method !== "POST") return err(405, "only POST");
  const path = new URL(req.url).pathname;

  const token = (req.headers.get("Authorization") ?? "").replace(/^Bearer\s+/i, "");
  if (!token) return err(401, "not signed in");
  const { data: userData, error: userErr } = await admin.auth.getUser(token);
  const user = userData?.user;
  if (userErr || !user) return err(401, "not signed in");

  const { data: member } = await admin.from("members")
    .select("approved, role, access_until, allow_shared_key, trial_labs, trial_chat").eq("user_id", user.id).maybeSingle();
  if (!member?.approved) return err(403, "account not approved");

  if (path.endsWith("/balance")) return balances(req, user.id, member as Member);
  if (!path.endsWith("/chat/completions")) return err(404, "only /chat/completions and /balance");

  const decision = decideAccess(member as Member, new Date());
  if (!decision.ok) return err(decision.status, decision.message);

  const lab = labTag(req.headers.get("x-forlabs-lab"));
  const chat = labTag(req.headers.get("x-forlabs-chat"));
  if (member.role !== "admin" && member.trial_labs !== null && member.trial_labs !== undefined) {
    const { data: rows, error: usageErr } = await admin.rpc("trial_usage", { target: user.id, cur_chat: chat });
    if (usageErr) return err(500, `trial_usage: ${usageErr.message}`);
    const usage = (rows ?? []) as (TrialRow & { has_chat: boolean })[];
    const cur = usage.find((r) => r.lab === lab);
    const trial = decideTrial(member as Member, usage.map((r) => ({ lab: r.lab, calls: Number(r.calls), chats: Number(r.chats) })),
      lab, chat, Boolean(cur?.has_chat), TRIAL_CALLS_PER_LAB);
    if (!trial.ok) return err(trial.status, trial.message);
  }

  let own: string | null;
  try {
    own = await ownKey(user.id);
  } catch (e) {
    return err(500, String((e as Error).message ?? e));
  }
  const picked = pickKey(own, KEY, member.allow_shared_key ?? true);
  if (!picked) return err(503, "no key assigned");

  let body: Record<string, unknown>;
  try {
    body = await req.json();
  } catch {
    return err(400, "bad json");
  }
  const model = String(body.model ?? MODELS[0]);
  if (!MODELS.includes(model)) return err(403, `model_not_allowed: ${model}`);
  body.model = model;
  body.stream = false;

  let upstream: Response;
  let text: string;
  try {
    // шлюз Supabase обрывает функцию на ~150 с: отвечаем раньше и понятно, а не обрывом соединения
    upstream = await fetch(`${BASE}/chat/completions`, {
      method: "POST",
      headers: { Authorization: `Bearer ${picked.key}`, "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(140_000),
    });
    text = await upstream.text();
  } catch (e) {
    const timeout = (e as Error)?.name === "TimeoutError" || (e as Error)?.name === "AbortError";
    return err(timeout ? 504 : 502, timeout ? "provider timeout" : "provider unreachable");
  }
  if (!upstream.ok) {
    const own = ownKeyError(picked.kind, upstream.status, text);
    if (own) return err(own.status, own.message);
  }
  if (upstream.ok) {
    try {
      const usage = JSON.parse(text).usage ?? {};
      const row: Record<string, unknown> = {
        user_id: user.id,
        model,
        prompt_tokens: usage.prompt_tokens ?? 0,
        completion_tokens: usage.completion_tokens ?? 0,
        lab,
        chat,
        key_kind: picked.kind,
      };
      const { error: insertErr } = await admin.from("llm_usage").insert(row);
      if (insertErr && chat !== null) {  // база ещё без столбца chat (миграция 0005 не применена) — без него
        delete row.chat;
        await admin.from("llm_usage").insert(row);
      }
      await admin.from("members").update({ last_seen_at: new Date().toISOString() }).eq("user_id", user.id);
    } catch (_) {
      // учёт расхода не должен ломать ответ
    }
  }
  return new Response(text, { status: upstream.status, headers: { "Content-Type": "application/json" } });
});
