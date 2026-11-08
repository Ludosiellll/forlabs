// Запуск: node --experimental-strip-types --test supabase/functions/support/logic.test.ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { cleanText, forOwner, telegramAction } from "./logic.ts";

const OWNER = 555;
const msg = (m: Record<string, unknown>) => ({ update_id: 1, message: { message_id: 10, chat: { id: OWNER }, ...m } });

test("привязка владельца: /start с кодом", () => {
  assert.deepEqual(telegramAction(msg({ chat: { id: 777 }, text: "/start Ab12Cd34" }), null),
    { kind: "bind", code: "Ab12Cd34", chatId: 777 });
});

test("ответ владельца реплаем — студенту", () => {
  assert.deepEqual(telegramAction(msg({ text: "Открою доступ вечером", reply_to_message: { message_id: 42 } }), OWNER),
    { kind: "reply", replyTo: 42, text: "Открою доступ вечером" });
});

test("владелец написал не реплаем — подсказка", () => {
  assert.deepEqual(telegramAction(msg({ text: "привет" }), OWNER), { kind: "hint", chatId: OWNER });
});

test("чужие сообщения и не-текст — игнор", () => {
  assert.deepEqual(telegramAction(msg({ chat: { id: 1 }, text: "привет", reply_to_message: { message_id: 42 } }), OWNER),
    { kind: "ignore" });
  assert.deepEqual(telegramAction({ update_id: 2, edited_message: {} }, OWNER), { kind: "ignore" });
  assert.deepEqual(telegramAction(msg({ photo: [{}], reply_to_message: { message_id: 42 } }), OWNER), { kind: "hint", chatId: OWNER });
});

test("/start без кода от владельца — подсказка, от чужого — игнор", () => {
  assert.deepEqual(telegramAction(msg({ text: "/start" }), OWNER), { kind: "hint", chatId: OWNER });
  assert.deepEqual(telegramAction(msg({ chat: { id: 9 }, text: "/start" }), OWNER), { kind: "ignore" });
});

test("текст сообщения: обрезка, пустое — null", () => {
  assert.equal(cleanText("  привет  "), "привет");
  assert.equal(cleanText("   "), null);
  assert.equal(cleanText(123), null);
  assert.equal(cleanText("я".repeat(5000))?.length, 4000);
});

test("сообщение владельцу: кто пишет и как ответить", () => {
  const s = forOwner("Анна", "anna@example.com", "Не открывается лаба", "пробный: лаб 2 из 3");
  assert.match(s, /Анна \(anna@example\.com\)/);
  assert.match(s, /Не открывается лаба/);
  assert.match(s, /пробный: лаб 2 из 3/);
  assert.match(s, /Ответь на это сообщение/);
});

test("сообщение владельцу укладывается в лимит Telegram 4096", () => {
  const s = forOwner("Анна".repeat(50), "a".repeat(200) + "@example.com", "я".repeat(4000), "пробный доступ");
  assert.ok(s.length <= 4096, String(s.length));
  assert.match(s, /Ответь на это сообщение/);
});
