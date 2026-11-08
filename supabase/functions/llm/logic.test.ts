// Запуск: node --experimental-strip-types --test supabase/functions/llm/logic.test.ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { decideAccess, labTag, ownKeyError, parseBalance, pickKey } from "./logic.ts";

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

// ---- пробный доступ: N лаб, M сообщений чата правок на лабу, скрытый потолок запросов на лабу ----
import { decideTrial, type TrialRow } from "./logic.ts";

const TRIAL = { trial_labs: 3, trial_chat: 5 };
const rows = (...r: [string, number, number][]): TrialRow[] => r.map(([lab, calls, chats]) => ({ lab, calls, chats }));

test("без пробного режима — без ограничений", () => {
  assert.deepEqual(decideTrial({ trial_labs: null, trial_chat: null }, rows(["a", 999, 99]), "x", null, false, 120), { ok: true });
});

test("новая лаба, пока не исчерпаны 3 — можно", () => {
  assert.deepEqual(decideTrial(TRIAL, rows(["a", 10, 0], ["b", 10, 0]), "c", null, false, 120), { ok: true });
});

test("четвёртая лаба — 403 trial labs limit", () => {
  const r = decideTrial(TRIAL, rows(["a", 10, 0], ["b", 10, 0], ["c", 10, 0]), "d", null, false, 120);
  assert.equal(r.ok, false);
  if (!r.ok) { assert.equal(r.status, 403); assert.equal(r.message, "trial labs limit: 3"); }
});

test("продолжение уже начатой лабы при исчерпанных лабах — можно", () => {
  assert.deepEqual(decideTrial(TRIAL, rows(["a", 10, 0], ["b", 10, 0], ["c", 10, 0]), "b", null, false, 120), { ok: true });
});

test("пятое сообщение чата можно, шестое — нет; запросы уже начатого сообщения — можно", () => {
  assert.deepEqual(decideTrial(TRIAL, rows(["a", 10, 4]), "a", "m5", false, 120), { ok: true });
  const r = decideTrial(TRIAL, rows(["a", 10, 5]), "a", "m6", false, 120);
  assert.equal(r.ok, false);
  if (!r.ok) assert.equal(r.message, "trial chat limit: 5");
  assert.deepEqual(decideTrial(TRIAL, rows(["a", 10, 5]), "a", "m5", true, 120), { ok: true });
});

test("потолок запросов на лабу (перезапуски) — 403", () => {
  const r = decideTrial(TRIAL, rows(["a", 120, 0]), "a", null, false, 120);
  assert.equal(r.ok, false);
  if (!r.ok) assert.equal(r.message, "trial lab calls limit");
});

test("без метки лабы в пробном режиме — просьба обновить программу", () => {
  const r = decideTrial(TRIAL, rows(), null, null, false, 120);
  assert.equal(r.ok, false);
  if (!r.ok) assert.equal(r.message, "trial needs lab tag");
});

test("пустая метка в истории не считается лабой", () => {
  assert.deepEqual(decideTrial(TRIAL, rows(["", 50, 0], ["a", 1, 0], ["b", 1, 0]), "c", null, false, 120), { ok: true });
});

test("лимит правок 0 — чат закрыт, работа над лабой — нет", () => {
  const t = { trial_labs: 1, trial_chat: 0 };
  assert.equal(decideTrial(t, rows(), "a", "m1", false, 120).ok, false);
  assert.equal(decideTrial(t, rows(), "a", null, false, 120).ok, true);
});
