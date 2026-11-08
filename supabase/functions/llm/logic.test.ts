// Запуск: node --experimental-strip-types --test supabase/functions/llm/logic.test.ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { decideAccess, labTag, parseBalance, pickKey } from "./logic.ts";

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
