# Чат «Правки» — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** У каждой лабы чат: точечные правки по словам преподавателя, вопросы, «запомнить для следующих лаб», версии и откат.

**Architecture:** Модели `ChatMessage`/`LabVersion` в `LabState`; движок `forlabs/pipeline/chat.py` (план правки → точечная переделка заданий и ответов → проверка → вёрстка); `Pipeline.chat`/`Pipeline.revert`; требования преподавателя — отдельная запись памяти `<предмет>.teacher`; панель в левой колонке экрана лабы.

**Tech Stack:** Python 3.13, pydantic, FastAPI/Jinja2; FakeLLM в тестах; живой прогон — DeepSeek через провайдера.

**Spec:** `docs/superpowers/specs/2026-11-08-forlabs-lab-chat-design.md`

## Global Constraints

- Нетронутые блоки переносятся без изменений (`keep`), вместе со скриншотами (`image_path`).
- Версий хранится не больше 10; сбой посреди правки возвращает разбор и отчёт к версии до неё.
- Требования преподавателя не обрезаются вместе с выжимкой лаб (`MAX_CHARS` к ним не относится).
- Тексты интерфейса — по-русски, без ИИ-штампов; ответы ИИ в чате — коротко, по делу.

## Review Focus

1. Модель вернула `keep` с несуществующим номером блока → блок пропускается, правка не падает (Task 2 `test_unknown_keep_is_skipped`).
2. Модель предложила правку задания, которого нет (`n=9`) → игнор, остальное применяется (Task 2 `test_plan_ignores_unknown_task`).
3. Пустое сообщение или только пробелы → ничего не делаем, сообщение не добавляется (Task 4 `test_empty_message_refused`).
4. Откат к версии, которой уже нет (вытеснена) → понятная ошибка, отчёт не меняется (Task 3 `test_revert_unknown_version`).
5. Правка лабы на другом компьютере (скриншоты в облаке) → `keep` сохраняет относительные пути, вёрстка докачивает (Task 3 `test_keep_preserves_relative_image_path`).

---

### Task 1: Модели

**Files:** Modify `forlabs/core/models.py`; Test `tests/core/test_models_chat.py`.

**Produces:** `ChatMessage(id: str, role: Literal["user","assistant"], text: str, at: str, remember: bool=False, kind: Literal["edit","answer","question","failed","revert","note"]="note", changed: list[str]=[], retake: list[str]=[], version: int|None=None)`; `LabVersion(id: int, at: str, label: str, spec: LabSpec, report: ReportDoc)`; `LabState.chat: list[ChatMessage]`, `LabState.versions: list[LabVersion]`; `push_version(st, label) -> int` (≤ 10, id растёт).

- [ ] Тесты: старое состояние без полей читается; `push_version` 12 раз → 10 версий, id 3..12.
- [ ] RED → реализация → GREEN → commit.

### Task 2: Движок правки

**Files:** Create `forlabs/pipeline/chat.py`; Modify `forlabs/pipeline/prompts.py` (CHAT_PLAN_SYSTEM, CHAT_REVISE_SYSTEM, CHAT_ANSWERS_SYSTEM), `forlabs/pipeline/write.py` (`BlockDraft.keep: str|None`, `BlockDraft.retake: bool`); Test `tests/pipeline/test_chat.py`.

**Produces:**
- `EditPlan(kind: Literal["edit","answer","question"], reply: str, tasks: list[TaskEdit(n:int, instruction:str)], questions: list[QuestionEdit(n:int, instruction:str)], variant: int|None, variant_text: str|None, conclusion: str|None, remember: str|None)`.
- `plan_edit(st: LabState, message: str, profile, subject, llm, rules, teacher: str) -> EditPlan` — в промпте: задания, оглавление отчёта (блоки кратко), вопросы, вариант, вывод, последние 6 сообщений чата, текст методички (до 30 000 символов) для варианта; неизвестные `n` отбрасываются.
- `revise_section(spec, section: TaskSection, instruction: str, profile, subject, llm, rules, assets_dir, memory: str, render_diagrams: bool) -> tuple[TaskSection, list[str]]` — блоки показываются модели с метками `b1…bN`; ответ — блоки, где `{"keep": "b3"}` переносит исходный блок как есть, `retake: true` у рисунка-скриншота снимает `image_path` и даёт место под новый снимок; возвращает темы рисунков к пересъёмке.
- `revise_answers(spec, qas: list[QA], edits: list[QuestionEdit], profile, subject, llm, rules) -> list[QA]`.

- [ ] Тесты: `test_plan_edit_question` (kind=answer → без правок), `test_plan_ignores_unknown_task`, `test_revise_keeps_blocks_and_screenshot`, `test_retake_clears_image`, `test_unknown_keep_is_skipped`, `test_revise_answers_only_listed`.
- [ ] RED → реализация → GREEN → commit.

### Task 3: Конвейер: правка, откат, память

**Files:** Modify `forlabs/pipeline/run.py`, `forlabs/pipeline/memory.py`; Test `tests/pipeline/test_chat_run.py`.

**Produces:** `Pipeline.chat(st, message: str, remember: bool=False) -> LabState`; `Pipeline.revert(st, version_id: int) -> LabState`; `SubjectMemory.teacher(profile_id, subject_id) -> str`, `SubjectMemory.add_teacher(profile_id, subject_id, rule: str) -> str` (без дублей, ≤ 30 требований); `Pipeline._memory_text` включает блок «Требования преподавателя (соблюдай всегда)».

- [ ] Тесты: правка задания 2 → задание 1 без изменений, версия сохранена, ответ ассистента с `changed`; смена варианта и вывода; вопрос → отчёт тот же, версия не создана; `remember` → требование в памяти и в промпте следующей правки; сбой модели на середине → разбор и отчёт как до правки, сообщение `failed`; `test_revert_unknown_version`; откат → отчёт как в версии, сообщение `revert`; `test_keep_preserves_relative_image_path`.
- [ ] RED → реализация → GREEN → commit.

### Task 4: Интерфейс

**Files:** Modify `forlabs/ui/app.py` (POST `/labs/{p}/{s}/{n}/chat`, POST `/labs/{p}/{s}/{n}/chat/revert/{vid}`), `forlabs/ui/templates/lab.html`, `forlabs/ui/static/app.css`, `forlabs/ui/static/app.js`; Test `tests/ui/test_chat_routes.py`.

- [ ] Тесты: отправка → правка применена, в ленте оба сообщения; `test_empty_message_refused`; занятая лаба → 303 с ошибкой; откат по кнопке; до выполнения (нет отчёта) — подсказка вместо поля ввода.
- [ ] RED → реализация → GREEN → commit; скриншоты панели, проходы по виду.

### Task 5: Живой прогон и сдача

- [ ] Живой прогон: ССП ЛР4 + две правки (код и вывод) + вопрос; проверка `compare()` и замечаний.
- [ ] Полный набор тестов; ревью ветки свежим субагентом; исправления; слияние в main, push.
