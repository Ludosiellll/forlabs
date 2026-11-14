# Прямое подключение со своим ключом — план работ

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** участник со своим ключом ходит к провайдеру прямо из приложения (без предела 140 с), модель для всех выбирается в админке.

**Architecture:** функция `llm` получает маршруты `/direct` (выдать настройку прямого доступа) и `/usage` (принять расход),
модель для всех хранится в базе и задаётся в админке. Клиент `LLM` спрашивает настройку через `DirectAccess` и
обращается к провайдеру напрямую; без настройки или при недоступности провайдера — прежний путь через функцию.

**Tech Stack:** Python 3.12+ (httpx, pydantic), Supabase Edge Function (Deno/TypeScript), Postgres (Vault), pytest, node --test.

**Spec:** `docs/superpowers/specs/2026-11-14-forlabs-direct-key-design.md`

## Global Constraints

- Прямой путь: тайм-аут запроса 600 с, предел ответа 32 000; серверный путь: 140 с, 16 000.
- Кэш настройки прямого доступа — 600 с; модель из базы в функции кэшируется 60 с.
- GLM (`model.startswith("glm")`): `reasoning_effort` в каждом запросе; обычные шаги `"low"`; к пределу шага +4 000 (не выше предела пути).
- Ключ: только в теле ответа `/direct`, в памяти процесса; не в журналах, файлах, сообщениях об ошибках, `repr`.
- Участник со своим ключом и не в пробном доступе (`trial_labs is null and trial_chat is null`) — без `usage_begin` на любом пути.
- Расход прямого запроса — в `llm_usage` с `key_kind = 'own_direct'`.

## Review Focus

- Подписка закончилась при открытом приложении → через ≤10 мин новые запросы падают с «Подписка закончилась …» (Task 4).
- Админ заменил ключ → первая ошибка 401 у провайдера перезапрашивает настройку и проходит (Task 5).
- Провайдер недоступен из сети студента → после повторов запрос уходит через функцию, лаба не падает (Task 5).
- Отчёт о расходе висит или падает → ответ модели возвращается без задержки (Task 4).
- Старое приложение шлёт `deepseek-…`, админ выбрал `glm-5.3-flash` → функция берёт GLM и подставляет `"low"` (Task 1).

---

### Task 1: Чистая логика функции

**Files:** Modify `supabase/functions/llm/logic.ts`; Test `supabase/functions/llm/logic.test.ts`

**Interfaces — Produces:**
- `isTrial(m: {trial_labs: number|null, trial_chat: number|null}): boolean`
- `directAllowed(m, own: string|null): boolean` — свой ключ есть и не пробный.
- `chooseModel(requested: string, chosen: string|null, models: string[]): string` — выбранная в админке, если в списке; иначе запрошенная, если в списке; иначе `models[0]`.
- `tuneBody(body: Record<string, unknown>, cap: number): Record<string, unknown>` — для `glm*`: `reasoning_effort` по умолчанию `"low"`, `max_tokens = min((max_tokens ?? cap) + 4000, cap)`; прочие модели: `max_tokens = min(max_tokens ?? cap, cap)`.
- `parseUsage(body: unknown, models: string[]): {model, prompt_tokens, completion_tokens, lab: string|null, chat: string|null} | null` — модель из списка, целые 0…1 000 000.

- [ ] Тесты: `chooseModel("deepseek-v4.1-flash-1017", "glm-5.3-flash", ["deepseek-v4.1-flash-1017","glm-5.3-flash"]) === "glm-5.3-flash"`; выбранной нет в списке → запрошенная; запрошенной нет → `models[0]`. `tuneBody({model:"glm-5.3-flash", max_tokens:1500}, 16000)` → `reasoning_effort:"low", max_tokens:5500`; `{model:"glm-5.3", reasoning_effort:"medium"}` сохраняет `"medium"` и даёт 16000; `deepseek` без effort остаётся без effort. `directAllowed({trial_labs:3,trial_chat:null}, "sk")===false`, `({null,null},"sk")===true`, `(…,null)===false`. `parseUsage` отвергает модель не из списка, отрицательные и > 1 000 000, не-целые.
- [ ] Запуск: `node --experimental-strip-types --test supabase/functions/llm/logic.test.ts` → новые тесты падают, затем реализация → все проходят.
- [ ] Commit.

### Task 2: Модель для всех в базе

**Files:** Create `supabase/migrations/0013_llm_model.sql`

**Interfaces — Produces:** RPC `admin_set_model(model text) returns text` (только админ; `^[A-Za-z0-9._:/-]{1,100}$`, иначе `invalid model` 22023), `admin_clear_model() returns void`, `admin_model_info() returns table(model text, set_at timestamptz)`, `model_value() returns text` (только `service_role`). Таблица `private.llm_model(id boolean pk default true check (id), model text, set_at timestamptz)`. Права — как в `0011_shared_key.sql`.

- [ ] Написать миграцию по образцу `0011_shared_key.sql`; проверить синтаксис применением в Task 7.
- [ ] Commit.

### Task 3: Функция `llm`: `/direct`, `/usage`, модель и лимиты

**Files:** Modify `supabase/functions/llm/index.ts`

**Interfaces — Consumes:** Task 1, Task 2 (`model_value`). **Produces:** `POST /direct` → `200 {base_url, model, models, key, access_until, max_tokens: 32000, timeout_s: 600}` | `404 {"error":{"message":"no direct access"}}` | те же 401/403, что у `/chat/completions`; `POST /usage` → `204` | `400 bad usage` | `403 no direct access`.

- [ ] Маршрутизация по окончанию пути (`/direct`, `/usage`, `/balance`, `/chat/completions`); проверки входа и `decideAccess` — общие для `/direct` и `/chat/completions`.
- [ ] `chosenModel()` — `model_value` с кэшем 60 с; в `/chat/completions` модель = `chooseModel(body.model, chosen, MODELS)`, тело = `tuneBody(body, MAX_TOKENS_CAP)`.
- [ ] `usage_begin` пропускается, если `picked.kind === "own" && !isTrial(member)`.
- [ ] `/usage`: только при `directAllowed`; строка `llm_usage` с `key_kind: "own_direct"`, `last_seen_at`.
- [ ] Ключ в журнал функции не выводится.
- [ ] Commit.

### Task 4: `DirectAccess` в приложении

**Files:** Create `forlabs/core/direct.py`; Test `tests/core/test_direct.py`

**Interfaces — Produces:**
- `@dataclass DirectConfig(base_url: str, model: str, models: list[str], key: str = field(repr=False), max_tokens: int = 32000, timeout_s: float = 600)`
- `class DirectAccess(http: httpx.Client, function_url: str, token: Callable[[], str], ttl: float = 600, clock=time.monotonic)`:
  `config() -> DirectConfig | None` (кэш `ttl`, 404 → `None` в кэше, ошибки доступа → `LLMError` с теми же `message_ru`, что у `LLM`), `invalidate() -> None`, `mark_down() -> None` (прямой путь не используется `ttl` секунд), `report(model: str, usage: dict, lab: str|None, chat: str|None) -> None` (фоновый поток, ошибки глотаются).
- В `forlabs/core/llm.py` — вынести разбор ошибочного ответа в `error_for(status: int, detail: str, retry_after: str|None) -> LLMError` и использовать в `LLM._post_once` и `DirectAccess`.

- [ ] Тесты (httpx.MockTransport): настройка берётся один раз за `ttl`, по истечении — снова; 404 → `None` и прямой путь не пробуется до истечения кэша; 403 `subscription expired: 2026-12-01T00:00:00Z` → `LLMError.message_ru` начинается с «Подписка закончилась»; `repr(cfg)` и журнал (caplog) не содержат ключа; `report` при зависшем сервере возвращается < 0,5 с.
- [ ] Реализация, тесты зелёные, весь `tests/core` зелёный.
- [ ] Commit.

### Task 5: Прямой путь в клиенте `LLM`

**Files:** Modify `forlabs/core/llm.py`; Test `tests/core/test_llm_direct.py`

**Interfaces — Consumes:** Task 4. **Produces:** `LLM.direct: DirectAccess | None = None`.

- [ ] Тесты: есть настройка → запрос уходит на `base_url` провайдера с ключом из настройки, моделью из настройки, без заголовков `X-ForLabs-*`, `server["max_tokens"] == 32000`; после ответа `report` получает модель, usage, метки лабы/чата из `extra_headers` (раскодированные); 401 провайдера → `invalidate` + повтор с новой настройкой → успех; второй 401 → «Ключ не работает — напиши администратору.»; 402 или «balance» → «На ключе закончились токены — напиши администратору.»; сетевые ошибки после всех повторов → `mark_down` и тот же запрос через функцию → успех; GLM: на обычном шаге `reasoning_effort == "low"` и `max_tokens == шаг + 4000`, на шаге с размышлением на прямом пути — `"medium"` при настройке `"low"`; GLM `finish_reason=length` → один повтор с пределом пути и `"low"`, повтора «без размышления» нет.
- [ ] Реализация: маршрут выбирается в `_post_once`; тайм-аут прямого запроса — `timeout_s` настройки; на прямом пути `think` не гасится пределом сервера.
- [ ] `pytest tests/core -q` зелёный. Commit.

### Task 6: Подключение в приложении и поле «Модель» в админке

**Files:** Modify `forlabs/ui/context.py`, `forlabs/cloud/store.py`, `forlabs/ui/admin_routes.py`, `forlabs/ui/templates/admin.html`; Test `tests/ui/test_admin.py`

- [ ] `context.llm()` в облаке: `llm.direct = self._direct()` — один `DirectAccess` на процесс (`CLOUD.llm_base_url`, `self.auth.access_token`).
- [ ] `CloudStore.admin_model_info() -> tuple[str, str|None] | None`, `admin_set_model(model: str) -> str`, `admin_clear_model() -> None` (RPC Task 2).
- [ ] `POST /admin/model` (action `set|clear`, поле `model`) → редирект на `/admin` с сообщением; форма на странице админки рядом с общим ключом, подсказка «модель должна быть в списке LLM_MODELS на сервере», варианты `glm-5.3-flash`, `deepseek-v4.1-flash-1017`, `deepseek-v4-pro`.
- [ ] Тест маршрута с поддельным хранилищем по образцу `tests/ui/test_admin.py`. Полный `pytest` зелёный. Commit.

### Task 7: Выпуск

- [ ] Версия 1.0.19 (`forlabs/version.py`), полный `pytest`, слияние в `main`, сборка и релиз на GitHub, проверка `latest.json` и контрольной суммы.
- [ ] Применить `0013_llm_model.sql` и выложить функцию `llm` (Management API, как для v14); проверить: `/direct` без входа → 401.
- [ ] Живая проверка прямого пути: `LLM` с `DirectAccess` на поддельной функции и настоящим провайдером (ключ aikeysforyou, `glm-5.3-flash`) — короткий и длинный запрос.
