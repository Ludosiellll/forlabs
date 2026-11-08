# Админ-панель ForLabs — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Раздел «Админ» в ForLabs: участники, их ключи (Vault), подписка по дате, остатки у провайдера, расход и лабы, графики, история.

**Architecture:** База — миграция `0003_admin.sql` (столбцы, Vault, функции, политики чтения для администратора, триггер истории). Прокси `llm` получает чистый модуль `logic.ts` (подписка, выбор ключа, баланс). Приложение — методы `CloudStore`, SVG-графики `forlabs/ui/charts.py`, страницы `/admin` и `/admin/u/{uid}`, строка подписки у участника.

**Tech Stack:** Python 3.13, FastAPI/Jinja2, httpx; Supabase (Postgres 15, Vault 0.3.1, Edge Functions на Deno); Node 22 `--experimental-strip-types` для тестов `logic.ts`.

**Spec:** `docs/superpowers/specs/2026-11-08-forlabs-admin-design.md`

## Global Constraints

- Проект Supabase `iebwndcikwnnvjvvdcyu`; миграции и проверки — через Management API `/v1/projects/{ref}/database/query` (токен подставляет прокси сессии); функция — `POST /v1/projects/{ref}/functions/deploy?slug=llm`, `verify_jwt=false`.
- Ключ модели участника никогда не возвращается в приложение: только `last4`.
- Подписка ограничивает только вызовы модели; администратор датой не ограничивается.
- Текст интерфейса — по-русски, без ИИ-штампов (правила `rules/anti-ai/design.md`); графики — встроенный SVG, без CDN.
- Личных данных в репозитории нет; проверки базы — только в транзакции, которая заканчивается `raise exception` (откат).

## Review Focus

1. Участник открывает `/admin` или `/admin/u/…` → переход на главную, данных нет (тест в Task 5).
2. Провайдер не отвечает на `/balance` для одного ключа → остальные остатки есть, в ячейке «нет связи» (тест `logic.ts` в Task 2 и `test_admin_balances_partial` в Task 4).
3. Подписка закончилась → понятное сообщение с датой, а не «нет доступа» (Task 3 `test_subscription_expired_message`).
4. Администратор пытается забрать подписку у себя → кнопок нет, сервер не режет администратора (Task 2 `decideAccess` для admin, Task 5 шаблон).
5. Пустой ключ / короче 8 символов → отказ с сообщением, ключ не меняется (Task 1 SQL-проверка, Task 5 `test_admin_set_key_validates`).

---

### Task 1: Миграция и проверка базы

**Files:** Create `supabase/migrations/0003_admin.sql`, `supabase/admin_check.sql`.

**Produces (SQL API):** столбцы `members.access_until|note|allow_shared_key|last_seen_at`, `labs.created_at`, `llm_usage.lab|key_kind`; `private.member_keys`; `public.member_events(id, user_id, actor, kind, detail jsonb, created_at)`; функции `public.admin_set_key(target uuid, api_key text) returns text`, `public.admin_clear_key(target uuid) returns void`, `public.admin_keys() returns table(user_id uuid, last4 text, set_at timestamptz)`, `public.admin_usage_daily(days int) returns table(day date, user_id uuid, tokens bigint)`, `public.admin_usage_by_lab(target uuid) returns table(lab text, tokens bigint, calls bigint, last_at timestamptz)`, `public.llm_key_for(target uuid) returns text` (только service_role); политики `*_admin_read` на `labs`, `student_profiles`, `storage.objects` (bucket labs); триггер `members_audit` → `member_events` с kind `access|approve|note|name|shared_key`; функции ключей пишут `key_set|key_clear`.

- [ ] Step 1: `admin_check.sql` (по образцу `rls_check.sql`): два временных пользователя (A — администратор: `role='admin', approved`; B — участник), затем `raise exception 'ADMIN_RESULT …'` с полями: `b_set_key` (ожидаемо `blocked`), `b_key_for` (`blocked 42501`), `a_set_key_last4` (`5678` для ключа `sk-test-12345678`), `service_key_ok` (`true` — `llm_key_for` под `service_role` вернул ключ), `short_key` (`blocked`), `a_reads_b_labs` (`1`), `b_reads_a_labs` (`0`), `events` (`>=2`: `key_set`, `access`).
- [ ] Step 2: Запустить до миграции → ошибка «function admin_set_key does not exist» (RED).
- [ ] Step 3: Написать `0003_admin.sql` (идемпотентно: `if not exists`, `create or replace`, `drop policy if exists`); ключ в Vault через `vault.create_secret(api_key, 'forlabs_key_'||target)` / `vault.update_secret`; удаление ключа — `delete from vault.secrets`; проверка `private.is_admin()` внутри функций администратора (`raise exception 'only admin' using errcode = '42501'`); `llm_key_for` — `revoke all … from public, anon, authenticated; grant execute … to service_role`.
- [ ] Step 4: Применить миграцию, повторить проверку → `ADMIN_RESULT` с ожидаемыми значениями; проверить, что временных строк не осталось (0).
- [ ] Step 5: Commit.

### Task 2: Прокси `llm` — логика, тесты, развёртывание

**Files:** Create `supabase/functions/llm/logic.ts`, `supabase/functions/llm/logic.test.ts`; Modify `supabase/functions/llm/index.ts`.

**Produces (`logic.ts`):**
- `decideAccess(m: {approved: boolean, role: string, access_until: string|null}, now: Date): {ok: true} | {ok: false, status: number, message: string}` — 403 `account not approved`; 403 `subscription expired: <ISO>` (не для admin).
- `pickKey(own: string|null, shared: string, allowShared: boolean): {key: string, kind: "own"|"shared"} | null`.
- `parseBalance(body: unknown): number|null` — `token_balance`, иначе `balance`, иначе null.
- `labTag(header: string|null): string|null` — decodeURIComponent, обрезка до 200, мусор → null.

- [ ] Step 1: `logic.test.ts` (node:test): истёкшая подписка участника → 403 с датой; администратор с прошедшей датой → ok; `access_until=null` → ok; `pickKey(null, "", true)` → null; `pickKey(null, "S", false)` → null; `pickKey("O","S",true).kind` → `own`; `parseBalance({token_balance: 92011485})` → 92011485; `parseBalance("x")` → null; `labTag("ivanov%2Fssp%2F4")` → `ivanov/ssp/4`; `labTag("%E0")` → null.
- [ ] Step 2: `node --experimental-strip-types --test supabase/functions/llm/logic.test.ts` → FAIL (нет модуля).
- [ ] Step 3: Реализовать `logic.ts`; в `index.ts`: чтение `approved, role, access_until, allow_shared_key`; `rpc('llm_key_for')`; 503 `no key assigned`; запись `lab`, `key_kind`; `last_seen_at`; маршрут `/balance` (участник — свой; администратор — `user_ids[]` и `shared`), запросы к провайдеру параллельно, тайм-аут 10 с на ключ.
- [ ] Step 4: Тесты → PASS. Развернуть (две части `file`), проверить без входа: `/chat/completions` и `/balance` → 401.
- [ ] Step 5: Commit.

### Task 3: Клиент модели и метка лабы

**Files:** Modify `forlabs/core/llm.py`, `forlabs/pipeline/run.py`; Test `tests/core/test_llm.py`, `tests/pipeline/test_run.py`.

**Produces:** `LLM.with_headers(extra: dict[str, str]) -> LLM` (копия, тот же http-клиент и счётчик); `FakeLLM.with_headers` → `self`, сохраняет заголовки в `self.headers`; `Pipeline._llm(st) -> object` — фабрика + `X-ForLabs-Lab: quote(key)`.

- [ ] Step 1: Тесты: `test_subscription_expired_message` (403 `subscription expired: 2026-12-12T00:00:00Z` → `kind=="access"`, `"12.12.2026" in message_ru`); `test_no_key_assigned_message` (503 `no key assigned` → «ещё не выдал»); `test_with_headers_sends_lab` (MockTransport видит заголовок); `test_pipeline_tags_llm_calls` (FakeLLM.headers содержит `X-ForLabs-Lab` = `ivanov%2Fpsip%2F7`).
- [ ] Step 2: RED. Step 3: реализация. Step 4: GREEN + весь набор. Step 5: Commit.

### Task 4: `CloudStore` — методы администратора

**Files:** Modify `forlabs/cloud/store.py`; Test `tests/cloud/test_cloud.py` (FakeSupabase: `/rest/v1/rpc/<fn>`, PATCH, `/functions/v1/llm/balance`).

**Produces:**
- `Member` + поля `access_until: str|None`, `note: str`, `allow_shared_key: bool`, `last_seen_at: str|None`; свойства `access_state -> Literal["pending","active","expiring","expired","unlimited"]` (expiring — ≤3 дней) и `days_left -> int|None`.
- `admin_overview() -> AdminOverview` (members, keys: dict uid→KeyInfo(last4,set_at), usage_30d: dict uid→int, daily: list[(date, uid, tokens)], labs: list[LabRow(owner, profile_id, subject_id, number, status, created_at, updated_at)], profiles: dict uid→list[Profile]).
- `admin_member(uid) -> MemberCard` (member, key, profiles, labs, usage_by_lab: dict lab→(tokens, calls), daily: list[(date,int)], events: list[MemberEvent]).
- `admin_update_member(uid, **fields)`; `admin_extend(uid, days: int)` (от большего из «сейчас» и текущей даты); `admin_set_until(uid, iso: str|None)`; `admin_revoke(uid)` (`access_until=now`); `admin_set_key(uid, key) -> str`; `admin_clear_key(uid)`; `balances(user_ids: list[str]) -> dict[str, int|None]` (+ `"shared"`); `my_balance() -> int|None`; `download_member_report(uid, profile_id, subject_id, number, local) -> bool`.
- [ ] Step 1: Тесты на каждый метод против FakeSupabase, включая `test_admin_extend_from_future_date`, `test_admin_balances_partial` (один ключ null), `test_member_access_state` (pending/active/expiring/expired/unlimited).
- [ ] Steps 2–5: RED → реализация → GREEN → Commit.

### Task 5: Графики и страницы

**Files:** Create `forlabs/ui/charts.py`, `forlabs/ui/templates/admin.html`, `forlabs/ui/templates/admin_member.html`; Modify `forlabs/ui/app.py`, `forlabs/ui/templates/base.html`, `forlabs/ui/templates/settings.html`, `forlabs/ui/static/app.css`, `forlabs/ui/static/app.js`; Delete `forlabs/ui/templates/users.html`; Test `tests/ui/test_charts.py`, `tests/ui/test_admin.py`.

**Produces:** `stacked_bars(days: list[date], series: list[tuple[str, list[int]]], height=220) -> Markup`; `bars(labels: list[str], values: list[int], height=180) -> Markup` — SVG с `<title>` у каждого столбика (подсказка), подписи осей, пустое состояние «Пока нет данных». Маршруты: `GET /admin`, `GET /admin/u/{uid}`, `GET /admin/balances` (JSON), `POST /admin/u/{uid}/access` (`action=extend7|extend30|until|unlimited|revoke|approve|disable`, `until`), `POST /admin/u/{uid}/key` (`action=set|clear|shared_on|shared_off`, `api_key`), `POST /admin/u/{uid}/note` (`display_name`, `note`), `GET /admin/u/{uid}/lab/{profile}/{subject}/{number}.docx`; `/users` → 303 `/admin`.

- [ ] Step 0: загрузить навык dataviz перед кодом графиков.
- [ ] Step 1: Тесты: SVG-графики (число столбиков, подсказки, пустое состояние, без `<script>`); страницы с фейковым облачным контекстом (админ видит участников, остатки JSON, действия вызывают нужные методы и пишут сообщение); участник получает 303 на главную; `test_admin_set_key_validates` (`"  abc "` → ошибка, метод не вызван); строка подписки в шапке у участника (active/expiring/expired).
- [ ] Steps 2–5: RED → реализация → GREEN → Commit.

### Task 6: Вид и проверка

- [ ] Скриншоты `/admin`, карточки, шапки участника (1440 и 1024 px), 5 проходов по виду с исправлениями.
- [ ] Полный набор тестов, ruff F; ревью ветки свежим субагентом; исправления; слияние в main, push.
