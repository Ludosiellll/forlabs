// Запуск: node --experimental-strip-types --test supabase/functions/llm/logic.test.ts
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  chooseModel, decideAccess, directAllowed, isTrial, labTag, modelFor, ownKeyError, parseBalance, parseUsage, pickKey,
  tuneBody,
} from "./logic.ts";

const NOW = new Date("2026-11-08T12:00:00Z");

test("одобренный участник без срока — доступ есть", () => {
  assert.deepEqual(decideAccess({ approved: true, role: "member", access_until: null }, NOW), { ok: true });
});

test("неодобренный — 403 account not approved", () => {
  const r = decideAccess({ approved: false, role: "member", access_until: null }, NOW);
  assert.equal(r.ok, false);
  if (!r.ok) {
    assert.equal(r.status, 403);
    assert.equal(r.message, "account not approved");
  }
});

test("подписка закончилась — 403 с датой", () => {
  const r = decideAccess({ approved: true, role: "member", access_until: "2026-11-01T00:00:00Z" }, NOW);
  assert.equal(r.ok, false);
  if (!r.ok) {
    assert.equal(r.status, 403);
    assert.equal(r.message, "subscription expired: 2026-11-01T00:00:00.000Z");
  }
});

test("подписка ещё идёт — доступ есть", () => {
  assert.equal(decideAccess({ approved: true, role: "member", access_until: "2026-12-01T00:00:00Z" }, NOW).ok, true);
});

test("администратор датой не ограничен", () => {
  assert.equal(decideAccess({ approved: true, role: "admin", access_until: "2020-01-01T00:00:00Z" }, NOW).ok, true);
});

test("выбор ключа", () => {
  assert.deepEqual(pickKey("OWN", "SHARED", true), { key: "OWN", kind: "own" });
  assert.deepEqual(pickKey(null, "SHARED", true), { key: "SHARED", kind: "shared" });
  assert.equal(pickKey(null, "SHARED", false), null);
  assert.equal(pickKey(null, "", true), null);
  assert.equal(pickKey("", "", true), null);
});

test("остаток у провайдера", () => {
  assert.equal(parseBalance({ object: "balance", token_balance: 92011485 }), 92011485);
  assert.equal(parseBalance({ balance: "1500" }), 1500);
  assert.equal(parseBalance("x"), null);
  assert.equal(parseBalance({ token_balance: "много" }), null);
  assert.equal(parseBalance(null), null);
});

test("метка лабы", () => {
  assert.equal(labTag("ivanov%2Fssp%2F4"), "ivanov/ssp/4");
  assert.equal(labTag("%E0"), null);
  assert.equal(labTag(null), null);
  assert.equal(labTag(""), null);
  assert.equal(labTag("a".repeat(500))?.length, 200);
});

test("ошибки личного ключа участника — понятный текст вместо «проверь ключ в настройках»", () => {
  assert.deepEqual(ownKeyError("own", 401, "invalid key"), { status: 402, message: "member key rejected: 401" });
  assert.deepEqual(ownKeyError("own", 402, "insufficient balance"), { status: 402, message: "member key balance: 402" });
  assert.deepEqual(ownKeyError("own", 429, "token quota exceeded"), { status: 402, message: "member key balance: 429" });
  assert.equal(ownKeyError("own", 429, "rate limit, slow down"), null); // временная перегрузка — повтор на клиенте
  assert.equal(ownKeyError("own", 403, "model_not_allowed: x"), null);
  assert.equal(ownKeyError("shared", 401, "invalid key"), null); // общий ключ — дело администратора, как раньше
  assert.equal(ownKeyError("own", 500, "upstream"), null);
});

const MODELS = ["deepseek-v4.1-flash-1017", "glm-5.3-flash"];

test("модель: выбранная в админке, если она в списке", () => {
  assert.equal(chooseModel("deepseek-v4.1-flash-1017", "glm-5.3-flash", MODELS), "glm-5.3-flash");
});

test("модель: выбранной нет в списке — запрошенная; запрошенной нет — первая", () => {
  assert.equal(chooseModel("glm-5.3-flash", "kimi-k3", MODELS), "glm-5.3-flash");
  assert.equal(chooseModel("gpt-x", null, MODELS), "deepseek-v4.1-flash-1017");
  assert.equal(chooseModel("gpt-x", "kimi-k3", MODELS), "deepseek-v4.1-flash-1017");
});

test("GLM: короткое рассуждение по умолчанию и запас на рассуждение", () => {
  const b = tuneBody({ model: "glm-5.3-flash", max_tokens: 1500 }, 16000);
  assert.equal(b.reasoning_effort, "low");
  assert.equal(b.max_tokens, 5500);
});

test("GLM: заданный уровень сохраняется, предел не выше потолка", () => {
  const b = tuneBody({ model: "glm-5.3", reasoning_effort: "medium" }, 16000);
  assert.equal(b.reasoning_effort, "medium");
  assert.equal(b.max_tokens, 16000);
  assert.equal(tuneBody({ model: "glm-5.3", max_tokens: 15000 }, 16000).max_tokens, 16000);
});

test("не GLM: рассуждение не добавляется, предел не выше потолка", () => {
  const b = tuneBody({ model: "deepseek-v4.1-flash-1017", max_tokens: 50000 }, 16000);
  assert.equal(b.reasoning_effort, undefined);
  assert.equal(b.max_tokens, 16000);
  assert.equal(tuneBody({ model: "deepseek-v4.1-flash-1017" }, 16000).max_tokens, 16000);
});

test("пробный доступ и прямой доступ", () => {
  assert.equal(isTrial({ trial_labs: 3, trial_chat: null }), true);
  assert.equal(isTrial({ trial_labs: null, trial_chat: 5 }), true);
  assert.equal(isTrial({ trial_labs: null, trial_chat: null }), false);
  assert.equal(directAllowed({ trial_labs: 3, trial_chat: null }, "sk-own"), false);
  assert.equal(directAllowed({ trial_labs: null, trial_chat: null }, "sk-own"), true);
  assert.equal(directAllowed({ trial_labs: null, trial_chat: null }, null), false);
  assert.equal(directAllowed({ trial_labs: null, trial_chat: null }, "  "), false);
});

test("расход прямого запроса: проверка полей", () => {
  const ok = parseUsage({ model: "glm-5.3-flash", prompt_tokens: 1200, completion_tokens: 300, lab: "a%2Fb", chat: null }, MODELS);
  assert.deepEqual(ok, { model: "glm-5.3-flash", prompt_tokens: 1200, completion_tokens: 300, lab: "a/b", chat: null });
  assert.equal(parseUsage({ model: "gpt-x", prompt_tokens: 1, completion_tokens: 1 }, MODELS), null);
  assert.equal(parseUsage({ model: "glm-5.3-flash", prompt_tokens: -1, completion_tokens: 1 }, MODELS), null);
  assert.equal(parseUsage({ model: "glm-5.3-flash", prompt_tokens: 1_000_001, completion_tokens: 1 }, MODELS), null);
  assert.equal(parseUsage({ model: "glm-5.3-flash", prompt_tokens: 1.5, completion_tokens: 1 }, MODELS), null);
  assert.equal(parseUsage("x", MODELS), null);
});

test("свой ключ — модель из карточки участника, общий — модель по умолчанию", () => {
  const MODELS = ["deepseek-v4.1-flash-1017", "deepseek-v4-flash"];
  // свой ключ с моделью в карточке: она, даже если её нет в LLM_MODELS
  assert.equal(modelFor("own", "glm-5.3-flash", "deepseek-v4.1-flash-1017", null, MODELS), "glm-5.3-flash");
  // общий ключ модель из карточки не берёт
  assert.equal(modelFor("shared", "glm-5.3-flash", "deepseek-v4.1-flash-1017", null, MODELS), "deepseek-v4.1-flash-1017");
  // без модели в карточке — как раньше: модель по умолчанию из админки или первая из списка
  assert.equal(modelFor("own", null, "gpt-x", "deepseek-v4-flash", MODELS), "deepseek-v4-flash");
  assert.equal(modelFor("own", "", "gpt-x", null, MODELS), "deepseek-v4.1-flash-1017");
  // кривое значение в карточке не уходит провайдеру
  assert.equal(modelFor("own", "glm 5; drop", "gpt-x", null, MODELS), "deepseek-v4.1-flash-1017");
});
