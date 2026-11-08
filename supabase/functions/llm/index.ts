// ForLabs: прокси к модели. Ключ API хранится только в секретах Supabase (LLM_API_KEY).
// Пускает только вошедших и одобренных пользователей, разрешает модели из списка, пишет расход токенов.
import { createClient } from "npm:@supabase/supabase-js@2.45.4";

const BASE = (Deno.env.get("LLM_BASE_URL") ?? "https://api.aikeysforyou.com/v1").replace(/\/$/, "");
const KEY = Deno.env.get("LLM_API_KEY") ?? "";
const MODELS = (Deno.env.get("LLM_MODELS") ?? "deepseek-v4.1-flash,deepseek-v4-flash").split(",").map((s) => s.trim());
const admin = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!, {
  auth: { persistSession: false },
});

function err(status: number, message: string) {
  return new Response(JSON.stringify({ error: { message } }), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

Deno.serve(async (req: Request) => {
  if (req.method !== "POST") return err(405, "only POST");
  const path = new URL(req.url).pathname;
  if (!path.endsWith("/chat/completions")) return err(404, "only /chat/completions");

  const token = (req.headers.get("Authorization") ?? "").replace(/^Bearer\s+/i, "");
  if (!token) return err(401, "not signed in");
  const { data: userData, error: userErr } = await admin.auth.getUser(token);
  const user = userData?.user;
  if (userErr || !user) return err(401, "not signed in");

  const { data: member } = await admin.from("members").select("approved").eq("user_id", user.id).maybeSingle();
  if (!member?.approved) return err(403, "account not approved");
  if (!KEY) return err(503, "LLM_API_KEY is not set");

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

  const upstream = await fetch(`${BASE}/chat/completions`, {
    method: "POST",
    headers: { Authorization: `Bearer ${KEY}`, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const text = await upstream.text();
  if (upstream.ok) {
    try {
      const usage = JSON.parse(text).usage ?? {};
      await admin.from("llm_usage").insert({
        user_id: user.id,
        model,
        prompt_tokens: usage.prompt_tokens ?? 0,
        completion_tokens: usage.completion_tokens ?? 0,
      });
    } catch (_) {
      // учёт расхода не должен ломать ответ
    }
  }
  return new Response(text, { status: upstream.status, headers: { "Content-Type": "application/json" } });
});
