# ForLabs — этап 1 (ядро) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Рабочее локальное приложение, которое по методичке теоретической лабы выдаёт `.docx`, свёрстанный по канону эталонных отчётов, с профилями, проверками стандарта и анти-ИИ, предпросмотром и интерфейсом «Документ».

**Architecture:** Python-пакет `forlabs` (корень репозитория). Конвейер шагов 1–7 из спецификации; LLM вызывается только для разбора, текста и переделки, всё остальное — код. Вёрстка клонирует XML-«образцы» абзацев, снятые с эталонных отчётов, поэтому свойства совпадают байт-в-байт; сверка с каноном — последний шлюз.

**Tech Stack:** Python ≥ 3.11, pydantic 2, python-docx + lxml, pymorphy3, openai SDK, PyMuPDF, FastAPI + Jinja2 + uvicorn, Playwright (Chromium / Edge `channel="msedge"`) + viz.js для схем, keyring, pywin32 (только Windows), pytest.

**Spec:** `docs/superpowers/specs/2026-11-08-forlabs-design.md`, канон: `docs/reference/formatting-canon.md`.

## Global Constraints

- Все надписи интерфейса и сообщения — по-русски, прямо, без лозунгов и эмодзи.
- Канон: A4; поля 30/15/20/20 мм; TNR 14; одинарный интервал; по ширине; отступ первой строки 15 мм (851 twips); код Consolas 12 в рамке `pBdr` single sz=4 (space 1 сверху/снизу, 4 по бокам); подписи без точек; нумерация рисунков/листингов/таблиц с 1 в каждой лабе.
- Ключ API никогда не пишется в файлы репозитория и в логи; в тестах — только переменная `FORLABS_API_KEY`.
- В репозитории и тестах нет реальных персональных данных: вымышленный профиль «Иванов Иван Петрович», «И.П. Иванов», группа «РС02-23».
- Пресеты API: `https://api.aikeysforyou.com/v1` (модели `deepseek-v4.1-flash`, `deepseek-v4-flash`) и `https://api.deepseek.com`.
- Данные приложения: `FORLABS_HOME` или `%LOCALAPPDATA%\ForLabs` / `~/.local/share/forlabs`.
- Windows-only код (Word COM) изолирован в `forlabs/render/word_com.py`, импортируется лениво; на Linux его тесты пропускаются.

## Review Focus

- Методичка с «грязным» текстом PDF (переносы слов «програм-\nма», номера страниц, колонтитулы) → дословная проверка заданий не должна ложно падать: нормализация пробелов/переносов перед сравнением (тест в Task 9/11).
- Цель, начинающаяся не с инфинитива («Цели: формирование навыков…») или с «Научиться» → вывод корректен или явно помечен на подтверждение, без падения (тест в Task 3).
- Код листинга с табами, очень длинными строками и пустыми строками → рамка не рвётся на пустых строках, табы не теряются (тест в Task 7).
- Профиль женского рода → «Выполнила уч-ся», «Изучила», детектор ловит «я разработал» в тексте (тесты в Task 3, 6).
- Модель вернула невалидный JSON / 403 model_not_allowed / сеть недоступна → понятная ошибка на русском, состояние лабы не теряется (тесты в Task 10, 14).

---

### Task 1: Каркас проекта, настройки, пути

**Files:** Create `pyproject.toml`, `forlabs/__init__.py`, `forlabs/core/__init__.py`, `forlabs/core/paths.py`, `forlabs/core/settings.py`, `tests/conftest.py`, `tests/core/test_settings.py`

**Interfaces — Produces:**
- `paths.data_home() -> Path` (учитывает `FORLABS_HOME`), `paths.ensure_dirs() -> None`.
- `Settings(BaseModel)`: `base_url: str = "https://api.aikeysforyou.com/v1"`, `model: str = "deepseek-v4.1-flash"`, `api_key_source: Literal["keyring","env"]`, `word_path: str|None`, `soffice_path: str|None`, `tesseract_path: str|None`, `temperature: float = 0.4`; `load_settings() -> Settings`, `save_settings(s) -> None`; `get_api_key() -> str|None` (keyring → env `FORLABS_API_KEY`), `set_api_key(key) -> None`.
- `conftest.py`: фикстура `tmp_home` (выставляет `FORLABS_HOME` во временную папку).

- [ ] Тест `test_settings_roundtrip`: save → load возвращает те же значения; файл `settings.json` в `tmp_home`.
- [ ] Тест `test_api_key_env_fallback`: при отсутствии keyring-записи `get_api_key()` берёт `FORLABS_API_KEY`; ключ не попадает в `settings.json`.
- [ ] Реализация, `pytest tests/core -q` → PASS, коммит.

### Task 2: Профили, предметы, хранилище

**Files:** Create `forlabs/core/models.py`, `forlabs/core/subjects.py`, `forlabs/core/store.py`, `tests/core/test_store.py`

**Interfaces — Produces:**
- `Gender = Literal["m","f"]`; `Enrollment(subject_id, title_subject, teacher, academic_year, variant:int, header_style: Literal["long","short"])`; `Profile(id, full_name, title_name, gender, group, specialty_code="5-04-0612-02", specialty_lines: list[str] = ["«Разработка и сопровождение", " программного обеспечения ", " информационных технологий»"], college_lines: list[str] = ["МИНИСТЕРСТВО ОБРАЗОВАНИЯ РЕСПУБЛИКИ БЕЛАРУСЬ","ФИЛИАЛ БГТУ","«БОБРУЙСКИЙ ГОСУДАРСТВЕННЫЙ ЛЕСОТЕХНИЧЕСКИЙ КОЛЛЕДЖ»"], city="Бобруйск", doc_author: str, output_root: str, enrollments: list[Enrollment])`, `Profile.enrollment(subject_id) -> Enrollment`.
- `Subject(id, name, short_name, kind: Literal["theory","code","1c","network"], default_header_style, default_variant:int)`; `SUBJECTS: dict[str, Subject]` — `ssp`(Современные системы программирования, long, 14), `kpiyap`(Конструирование программ и языки программирования, long, 30), `psip`(Программные средства создания интернет-приложений, short, 14), `vp`(Веб-программирование, long, 14), `trpo`(Технология разработки программного обеспечения, long, 14), `1c`(Программирование 1С, long, 14), `ks`(Компьютерные сети, short, 2); `custom_subjects` хранятся в `subjects.json` и добавляются к встроенным.
- `Store(home)`: `list_profiles()`, `get_profile(id)`, `save_profile(p)`, `delete_profile(id)`, `lab_dir(profile_id, subject_id, lab_number) -> Path`, `save_lab(state)`, `load_lab(profile_id, subject_id, lab_number) -> LabState|None`, `list_labs(profile_id, subject_id) -> list[LabState]`.
- `LabState(profile_id, subject_id, number: str, status: Literal["draft","parsed","confirmed","executing","needs_manual","rendered","final","failed"], spec: LabSpec|None, report: ReportDoc|None, findings: list[Finding], docx_path: str|None, pdf_path: str|None, error: str|None, updated_at)` (LabSpec/ReportDoc/Finding — из Task 4; модели в `forlabs/core/models.py`, импорт без циклов).

- [ ] Тест `test_profile_roundtrip_and_enrollment` (вымышленный профиль, `enrollment("ks").variant == 2`).
- [ ] Тест `test_lab_state_persistence` (save → load, номер «5.2» даёт папку `ЛР5.2`).
- [ ] Реализация, тесты PASS, коммит.

### Task 3: Морфология и пол

**Files:** Create `forlabs/report/__init__.py`, `forlabs/report/morph.py`, `tests/report/test_morph.py`, `tests/fixtures/goal_conclusion_pairs.json` (пары «цель → вывод» из эталонных отчётов, только эти строки)

**Interfaces — Produces:**
- `goal_to_conclusion(goal: str, gender: Gender) -> ConclusionResult(text: str, needs_confirm: bool, note: str|None)`: убирает префикс «Цель работы:»/«Цель:»/«Цели:», переводит первый инфинитив (в т.ч. возвратный) в прошедшее время ед. ч. рода профиля с заглавной буквы, остальное дословно, точка в конце; если первое слово не инфинитив — `needs_confirm=True`, текст = цель как есть с точкой; если дальше есть инфинитивы после « и »/«, » на верхнем уровне — `needs_confirm=True` и `note` с вариантом.
- `past_tense(infinitive: str, gender: Gender) -> str`.
- `performed_word(gender) -> str` («Выполнил»/«Выполнила»).
- `agree_short_participle(base: str, topic: str) -> str`: согласует «представлен/приведен/показан» с главным словом темы (род/число по первому существительному в им. п.): «Схема … » → «представлена», «Код …» → «представлен», «Результаты …» → «представлены», «Окно …» → «представлено».
- `wrong_gender_verbs(text: str, gender: Gender) -> list[str]`: бессубъектные глаголы прошедшего времени ед. ч. противоположного рода (глагол стоит в начале предложения или после «я»).

- [ ] Тест `test_reference_pairs`: для каждой пары фикстуры `goal_to_conclusion(goal,"m").text` == вывод (нормализация пробелов/точки).
- [ ] Тест `test_female_forms`: «Исследовать функциональность…» → «Исследовала функциональность….», «Научиться настраивать сеть» → «Научилась настраивать сеть.», «Сформировать умения создавать…» → «Сформировала умения создавать….».
- [ ] Тест `test_non_infinitive_goal_needs_confirm`: «Формирование навыков работы» → `needs_confirm is True`, без исключения.
- [ ] Тест `test_participle_agreement` (4 случая выше) и `test_wrong_gender_verbs` («Разработал программу.» при `f` → `["Разработал"]`; «Программа разработана.» → `[]`).
- [ ] Реализация (pymorphy3, `pymorphy3-dicts-ru`), тесты PASS, коммит.

### Task 4: Модель отчёта, нумерация, подводки

**Files:** Create `forlabs/core/models.py` (дополнить), `forlabs/report/derive.py`, `tests/report/test_derive.py`

**Interfaces — Produces (в `forlabs/core/models.py`):**
- `Task(n:int, text:str, kind: Literal["main","extra"])`; `LabSpec(subject_id, number:str, date:str /*ДД.ММ.ГГГГ*/, topic, goal, equipment:str|None, assignment:str, tasks:list[Task], variant:int|None, variant_text:str|None, questions:list[str], source_text:str)`.
- Блоки (discriminated union по `type`): `Paragraph(text)`, `DashList(items:list[str])`, `NumberedList(items)`, `Figure(topic, image_path:str|None, slot:str|None, width_cm:float|None)`, `Listing(topic, code, language="")`, `Table(topic, rows:list[list[str]], header_rows:int=1)`.
- `TaskSection(n, title, blocks)`, `QA(n, question, answer)`, `ReportDoc(lab_number:str, header: dict[str,str] /*метка→значение по порядку*/, sections, questions:list[QA], conclusion:str)`.
- `Finding(stage, rule_id, severity: Literal["error","warn"], location:str, text:str, message:str, suggestion:str|None=None)`.
- `derive.number_objects(doc) -> NumberedDoc`: проходит блоки по порядку, назначает рисункам/листингам/таблицам номера 1..N; `figure_intro(topic, n) -> str` («<Тема> представлен(а/о/ы) на рисунке n»), `listing_intro(topic,n)` («… в листинге n»), `table_intro(topic,n)` («В таблице n представлены <тема с маленькой буквы>»), `caption(kind, n, topic)` («Рисунок n – Тема»). Тема без точки на конце (точка срезается).
- `build_header(spec, enrollment, group) -> dict[str,str]` по стилю: long → «Номер учебной группы», «Дата выполнения работы», «Тема работы», «Цель работы», [«Оснащение»], «Задание»; short → «Номер учебной группы», «Дата выполнения работы», «Тема», «Цель», «Задание», «Оснащение работы».

- [ ] Тест `test_numbering_per_lab` (2 рисунка, 1 листинг, 1 таблица → 1,2 / 1 / 1).
- [ ] Тест `test_intro_caption_consistency` (подпись = тема; подводка начинается с темы и без точки).
- [ ] Тест `test_header_styles` (long/short порядок меток как выше).
- [ ] Реализация, тесты PASS, коммит.

### Task 5: Проверки стандарта РС02-23

**Files:** Create `forlabs/report/standard.py`, `tests/report/test_standard.py`

**Interfaces — Produces:** `check_standard(doc: ReportDoc, spec: LabSpec, gender: Gender) -> list[Finding]` с `rule_id`: `header.missing`, `header.date_format`, `task.sequence`, `task.title_mismatch` (заголовок ≠ текст задания из spec после нормализации), `caption.trailing_dot`, `list.lowercase`, `list.punctuation`, `list.reference_inside`, `qa.not_in_spec`, `qa.answer_too_long` (>22 слов или >165 символов), `conclusion.formula` (≠ `goal_to_conclusion`), `text.markdown`, `text.emoji`, `text.english_quotes`, `text.gender` (через `wrong_gender_verbs`).

- [ ] Тест `test_clean_doc_passes` (корректный документ → `[]`).
- [ ] По одному тесту на каждый `rule_id` с испорченным документом → ровно этот `rule_id`.
- [ ] Реализация, тесты PASS, коммит.

### Task 6: Анти-ИИ детекторы

**Files:** Create `forlabs/antiai/__init__.py`, `forlabs/antiai/text_ru.py`, `forlabs/antiai/code.py`, `forlabs/antiai/design.py`, `forlabs/antiai/similarity.py`, `tests/antiai/test_*.py`

**Interfaces — Produces:**
- `scan_text(text: str, location: str) -> list[Finding]`: правила `ai.banned` (сгенерир-, генерац-, нейросет-, «ИИ», «искусственн… интеллект», chatgpt, «бот», «ассистент», «языковая модель», «на оценку», «повышенного уровня сложности», «Пожалуйста»), `ai.cliche` (≥ 40 штампов: «играет важную роль», «является ключевым», «в современном мире», «стоит отметить», «важно отметить», «следует подчеркнуть», «широкий спектр», «не только …, но и», «в данной работе рассмотрен», «таким образом,», «позволяет эффективно», «комплексный подход», «мощный инструмент», «неотъемлемой частью», «в рамках данной», «давайте», «ключевую роль», «уникальная возможность», «инновационн», «оптимальн решени», «эффективн и удобн», «благодаря чему», «в заключение», «подводя итог» …).
- `scan_paragraph_openings(paragraphs: list[str]) -> list[Finding]` (`ai.same_opening`: ≥ 3 абзаца начинаются с одних и тех же 2 слов).
- `scan_code(code: str, language: str, location) -> list[Finding]`: `code.comment_density` (> 25 % строк — комментарии), `code.english_comment_bsl`, `code.todo`, `code.separator` (строки/литералы из 5+ `=`/`-`/`*`), `code.emoji`.
- `scan_design(html_or_css: str, location) -> list[Finding]`: `design.ai_gradient` (#667eea/#764ba2 и фиолетово-синие `linear-gradient`), `design.glass` (`backdrop-filter`), `design.neon` (цветные `box-shadow` с blur ≥ 20px), `design.emoji_icons`, `design.default_font` (Inter/Poppins), `design.slogan` («Откройте для себя», «Добро пожаловать в будущее»).
- `similarity(a: str, b: str) -> float` (Жаккар по 5-словным шинглам), `scan_similarity(text, others: dict[str,str], threshold=0.35) -> list[Finding]`.

- [ ] Тесты: каждый `rule_id` ловится на плохом примере; эталонный текст ССП ЛР8 из фикстуры не даёт `ai.banned`; `similarity` одинаковых = 1.0, разных < 0.1.
- [ ] Реализация, PASS, коммит.

### Task 7: Шаблон, образцы абзацев, сборка .docx, сверка с каноном

**Files:** Create `tools/extract_exemplars.py`, `forlabs/render/__init__.py`, `forlabs/render/assets/base.docx`, `forlabs/render/assets/exemplars.xml`, `forlabs/render/assets/title.xml`, `forlabs/render/assets/toc.xml`, `forlabs/render/canon.py`, `forlabs/render/docx_builder.py`, `forlabs/render/conformance.py`, `tests/render/test_builder.py`, `tests/render/test_conformance.py`

**Interfaces — Produces:**
- `tools/extract_exemplars.py <ref.docx>...`: классифицирует абзацы (классификатор из `conformance.py`), для каждой роли берёт самый частый вариант эффективного форматирования (с учётом `pBdr`) и сохраняет `pPr` и `rPr` (для «метка: значение» — оба `rPr`) без `rsid`; `base.docx` = пакет эталона с пустым телом (только `sectPr`), свойства документа обнулены; `title.xml` — абзацы титула с плейсхолдерами `{{COLLEGE1..3}}`, `{{SPECIALTY_CODE}}`, `{{SPEC1..3}}`, `{{SUBJECT}}`, `{{PERFORMED}}`, `{{GROUP}}`, `{{NAME}}`, `{{TEACHER}}`, `{{CITY}}`, `{{YEAR}}`; `toc.xml` — блок «Содержание» (sdt) с плейсхолдером записей. Личные данные в ассеты не попадают (тест).
- `canon.ROLES: tuple[str,...]` = `lab_heading, header_field, results_title, task_heading, body, dash_item, numbered_item, figure_ref, image, figure_caption, listing_ref, listing_caption, code, code_blank, continuation, table_ref, table_caption, table_cell, cq_title, cq_question, cq_answer, conclusion, page_break, toc_title, toc_entry`; `canon.exemplar(role) -> Exemplar(ppr: Element, rpr: Element, label_rpr: Element|None)`.
- `docx_builder.build_lab(doc: ReportDoc, out: Path) -> Path`; `build_semester(docs: list[ReportDoc], profile: Profile, enrollment: Enrollment, out: Path, toc_pages: dict[str,int]|None=None) -> Path`; рисунок: ширина = min(16.5 см, натуральная при 96 dpi), высота ≤ 22 см; слот без картинки — абзац-заглушка «[Вставить скриншот: …]» роли `body` (в финальную выдачу не допускается — Task 14 блокирует); листинг: каждая строка (включая пустые) — абзац роли `code` с рамкой, табы → 4 пробела, текст строки хранится как есть; таблица: `tblPr`/границы/шрифт 12 из эталона, строка заголовка повторяется.
- `conformance.profile_docx(path) -> dict[role, Counter[key]]`, `conformance.compare(path) -> list[Finding]` (`render.canon_mismatch` с ролью, ожидаемым и фактическим).

- [ ] Тест `test_assets_have_no_personal_data` (в ассетах нет «Лабынцев», «Корчагина», «Подгорная», «Довнар»).
- [ ] Тест `test_lab_docx_conforms`: документ-образец (ССП ЛР8 без личных данных: шапка, 2 задания, список, листинг с пустой строкой и табом, рисунок PNG, таблица, 3 вопроса, вывод) → `compare()` == `[]`.
- [ ] Тест `test_semester_docx_conforms_and_title`: 2 лабы → титул содержит «Выполнила уч-ся» для `f`, «Содержание», 2 Heading 1, `compare()` == `[]`.
- [ ] Тест `test_listing_frame_continuous`: все абзацы листинга (включая пустые) имеют одинаковый `pBdr`.
- [ ] Тест `test_libreoffice_renders_pdf` (skip без soffice): `.docx` → PDF, страниц ≥ 1.
- [ ] Реализация, PASS, коммит.

### Task 8: Доводка и предпросмотр

**Files:** Create `forlabs/render/libreoffice.py`, `forlabs/render/word_com.py`, `forlabs/render/finalize.py`, `forlabs/render/preview.py`, `tests/render/test_finalize.py`

**Interfaces — Produces:**
- `libreoffice.find_soffice() -> str|None`, `libreoffice.to_pdf(docx, outdir) -> Path`.
- `preview.pdf_to_pngs(pdf, outdir, zoom=1.5) -> list[Path]`; `preview.heading_pages(pdf, headings: list[str]) -> dict[str,int]` (номер страницы, где впервые встречается строка «Лабораторная работа №N» вне содержания).
- `word_com.available() -> bool`; `word_com.finalize(docx: Path, pdf_out: Path, author: str) -> FinalizeReport` (обновить TOC → цикл «Продолжение листинга N»/«Продолжение таблицы N» до стабилизации, ≤ 10 итераций → снова TOC → свойства → сохранить → экспорт PDF).
- `finalize.finalize(docx, profile, kind: Literal["lab","semester"]) -> FinalizeReport(engine: Literal["word","libreoffice"], pdf: Path, pages: list[Path], continuations: int, warnings: list[str])`: Word, если доступен; иначе LibreOffice: PDF → номера страниц лаб → пересборка содержания со статичными номерами (`build_semester(..., toc_pages=...)`) → повторный PDF; предупреждение «Продолжения листингов расставит Word: открой на Windows и нажми „Доводка в Word“».

- [ ] Тест `test_finalize_libreoffice_semester` (skip без soffice): в содержании номера страниц совпадают с `heading_pages`.
- [ ] Тест `test_word_com_skipped_off_windows`: `word_com.available()` is False на Linux.
- [ ] Реализация, PASS, коммит.

### Task 9: Чтение методички

**Files:** Create `forlabs/ingest/__init__.py`, `forlabs/ingest/extract.py`, `tests/ingest/test_extract.py`

**Interfaces — Produces:** `SourceText(text: str, pages: int, warnings: list[str])`; `extract_text(path: Path) -> SourceText` (PDF через PyMuPDF; DOCX — абзацы и таблицы; TXT/MD; PNG/JPG — Tesseract при наличии, иначе `warnings`); `normalize(text) -> str` (склейка переносов «слово-\nслово», схлопывание пробелов, удаление строк-номеров страниц); `normalize_for_compare(s) -> str` (нижний регистр, ё→е, только буквы/цифры через пробел).

- [ ] Тест `test_pdf_and_docx_roundtrip` (создать PDF через PyMuPDF и DOCX через python-docx с текстом с переносом «програм-\nмы» → в тексте «программы»).
- [ ] Тест `test_image_without_tesseract_warns`.
- [ ] Реализация, PASS, коммит.

### Task 10: LLM-клиент

**Files:** Create `forlabs/core/llm.py`, `tests/core/test_llm.py`

**Interfaces — Produces:** `LLMError(kind: Literal["auth","model","quota","network","bad_response"], message_ru: str)`; `LLM(settings, api_key)`: `chat(messages, temperature=None, max_tokens=None) -> str`, `json(messages, schema: type[BaseModel], repairs=3) -> BaseModel` (`response_format={"type":"json_object"}`, при ошибке валидации — повтор с текстом ошибки), `check() -> str` (короткий запрос, возвращает имя модели), `usage: UsageCounter`; `FakeLLM(responses: list[str|dict])` с тем же интерфейсом для тестов. Ошибки: 401 → auth «Ключ не принят», 403 model_not_allowed → model «Эта модель не входит в ключ», 429 → quota, сеть → network.

- [ ] Тест `test_json_repair_loop` (FakeLLM: сначала невалидный JSON, потом валидный → объект; 4 подряд невалидных → `LLMError(kind="bad_response")`).
- [ ] Тест `test_error_mapping` (мок-ответы 401/403/429).
- [ ] Тест `test_live_check` (`@pytest.mark.live`, нужен `FORLABS_API_KEY`).
- [ ] Реализация, PASS, коммит.

### Task 11: Разбор методички (LabSpec)

**Files:** Create `forlabs/spec/__init__.py`, `forlabs/spec/extract.py`, `forlabs/spec/prompts.py`, `tests/spec/test_extract.py`, `tests/fixtures/methodology_psip_lr7.txt` (текст методички ПСИП ЛР7 без личных данных)

**Interfaces — Produces:** `extract_spec(src: SourceText, subject: Subject, enrollment: Enrollment, llm, lab_number: str|None, date: str) -> tuple[LabSpec, list[Finding]]`: LLM возвращает `RawSpec(number, topic, goal, equipment, assignment, main_tasks[str], extra_tasks[str], variant_table_text|None, questions[str])`; код: сквозная нумерация (main, затем extra), подстановка варианта профиля, `verify_verbatim(spec, src) -> list[Finding]` (`spec.not_verbatim` для задания/вопроса, которого нет в нормализованном источнике).

- [ ] Тест `test_extract_with_fake_llm` (FakeLLM возвращает 6 заданий ПСИП ЛР7 → `tasks[5].n == 6`, findings пусты).
- [ ] Тест `test_verbatim_detects_paraphrase` (одно задание перефразировано → `spec.not_verbatim`).
- [ ] Тест `test_extract_live` (`live`): реальный разбор фикстуры даёт тему «Создание визуальных эффектов и анимации на HTML странице» и 6 заданий.
- [ ] Реализация, PASS, коммит.

### Task 12: Правила для модели

**Files:** Create `rules/common/standard.md`, `rules/common/voice.md`, `rules/anti-ai/text.md`, `rules/anti-ai/code.md`, `rules/anti-ai/design.md`, `rules/subjects/{ssp,kpiyap,psip,vp,trpo,1c,ks,_template}.md`, `forlabs/core/rules.py`, `tests/core/test_rules.py`

**Interfaces — Produces:** `load_rules(stage: Literal["spec","write","repair","code","design"], subject_id: str) -> str` (поставляемые файлы + пользовательские правки из `<home>/rules/` с тем же путём имеют приоритет); `list_rule_files() -> list[RuleFile(path, text, overridden: bool)]`, `save_rule_override(path, text)`, `reset_rule_override(path)`.

- [ ] Тест `test_override_wins_and_reset`.
- [ ] Тексты правил из источников `forlabsnotes` (стандарт, голос, анти-ИИ, по предметам: варианты, особенности папок, «продолжение темы» ПСИП/ТРПО, 1С-накопительность), коммит.

### Task 13: Написание отчёта и переделка

**Files:** Create `forlabs/executors/__init__.py`, `forlabs/executors/theory.py`, `forlabs/executors/diagrams.py`, `forlabs/executors/assets/viz-standalone.js`, `forlabs/pipeline/__init__.py`, `forlabs/pipeline/write.py`, `forlabs/pipeline/repair.py`, `tests/pipeline/test_write.py`

**Interfaces — Produces:**
- `diagrams.render_dot(dot: str, out_png: Path) -> Path|None` (viz.js в Chromium/Edge через Playwright; нейтральный стиль: белый фон, чёрные линии, Times New Roman 14; None, если браузер недоступен).
- `theory.write_section(spec, task: Task, profile, subject, memory: str, llm) -> TaskSection` (LLM → `SectionDraft(blocks: list[BlockDraft])`, `BlockDraft` = блок без номеров + для рисунков `kind: Literal["diagram","screenshot"]`, `dot`, `instruction`); рисунок-схема рендерится, скриншот → `slot`.
- `theory.answer_questions(spec, profile, llm) -> list[QA]`.
- `write.write_report(spec, profile, enrollment, subject, llm, on_event) -> ReportDoc` (шапка, секции по заданиям, вопросы, вывод через `goal_to_conclusion`).
- `repair.check_all(doc, spec, profile, others) -> list[Finding]` (стандарт + анти-ИИ текст/код + сходство); `repair.repair(doc, findings, llm, rules) -> ReportDoc` (переписываются только блоки с замечаниями, до 3 раундов).

- [ ] Тест `test_write_report_fake_llm` (2 задания → 2 секции, рисунок-скриншот стал `slot`, вывод по формуле, `check_all` == `[]`).
- [ ] Тест `test_repair_fixes_cliche` (FakeLLM переписывает абзац со штампом → замечание исчезает; блоки без замечаний не меняются).
- [ ] Тест `test_render_dot` (skip без браузера): PNG создан, ширина > 100 px.
- [ ] Реализация, PASS, коммит.

### Task 14: Конвейер и состояние лабы

**Files:** Create `forlabs/pipeline/run.py`, `forlabs/pipeline/events.py`, `forlabs/pipeline/memory.py`, `tests/pipeline/test_run.py`

**Interfaces — Produces:**
- `events.EventBus`: `publish(lab_key, Event(kind, text, step, progress))`, `subscribe(lab_key) -> AsyncIterator[Event]`; журнал дублируется в `<lab_dir>/journal.log`.
- `run.parse(state, source_path) -> LabState` (→ `parsed`); `run.confirm(state, spec) -> LabState` (→ `confirmed`); `run.execute(state, llm, bus) -> LabState` (write → check/repair → `needs_manual`, если есть пустые слоты, иначе render+finalize → `rendered`); `run.attach_screenshot(state, figure_ref: tuple[int,int], image_bytes) -> LabState` (сохраняет PNG, перерисовывает, при отсутствии слотов → `rendered`); `run.redo_task(state, n, llm, bus)`; `run.mark_final(state)` (только без `error`-замечаний и слотов); `run.build_semester(profile, subject_id, numbers: list[str]) -> Path`. Ошибка LLM → `status="failed"`, `error` = русское сообщение, прежние данные целы.
- `memory.update(profile_id, subject_id, spec, report, llm)`, `memory.read(...) -> str`.
- Выход в папку сдачи: `<output_root>/<subject.short_name>/ЛР<N>/ЛР<N>_<short_name>.docx` + картинки не копируются (они внутри .docx).

- [ ] Тест `test_full_pipeline_fake_llm` (draft → parsed → confirmed → needs_manual → attach → rendered; `.docx` проходит `compare()`).
- [ ] Тест `test_llm_failure_keeps_state` (FakeLLM кидает `LLMError(kind="network")` → `failed`, `spec` сохранён).
- [ ] Реализация, PASS, коммит.

### Task 15: Интерфейс (вариант B «Документ»)

**Files:** Create `forlabs/ui/__init__.py`, `forlabs/ui/app.py`, `forlabs/ui/routes_*.py`, `forlabs/ui/templates/*.html`, `forlabs/ui/static/app.css`, `forlabs/ui/static/app.js`, `forlabs/__main__.py`, `tests/ui/test_routes.py`, `tools/ui_screens.py`

**Interfaces — Produces:** `create_app(store, settings) -> FastAPI`; страницы: `/` (лабы профиля по предметам), `/profiles`, `/profiles/{id}`, `/subjects`, `/labs/new`, `/labs/{p}/{s}/{n}/confirm`, `/labs/{p}/{s}/{n}` (экран B + вкладка «Журнал»), `/labs/{p}/{s}/{n}/events` (SSE), `/labs/{p}/{s}/{n}/page/{i}.png`, `/semester`, `/rules`, `/settings` (+ «Проверить подключение»); POST-эндпоинты для действий; `python -m forlabs` запускает uvicorn на 127.0.0.1 и открывает окно (`pywebview`, иначе браузер). Шрифты: IBM Plex Sans/Mono и Tinos — локальные файлы в `static/fonts` (OFL), без CDN.

- [ ] Тест `test_routes_smoke` (TestClient: все GET-страницы 200 на вымышленном профиле; создание профиля и лабы через POST).
- [ ] Визуальная проверка `tools/ui_screens.py` (Playwright, ширины 1280 и 390): скриншоты всех экранов; 5 проходов «смотрю → правлю» по виду и удобству; замечания и исправления фиксируются в коммитах.
- [ ] Коммит.

### Task 16: Запуск на Windows, документация

**Files:** Create `Установка.bat`, `Запуск.bat`, `README.md`, `docs/windows-checklist.md`, `forlabs/selfcheck.py`

**Interfaces — Produces:** `python -m forlabs.selfcheck` печатает: Python, зависимости, Word (COM), LibreOffice, Edge/Chromium для схем, Tesseract, доступ к API — каждое «есть/нет» с подсказкой.

- [ ] `Установка.bat`: проверка Python 3.11+, `py -m venv .venv`, `pip install -e .[windows]`; `Запуск.bat`: `.venv\Scripts\python -m forlabs`.
- [ ] Коммит.

### Task 17: Живой прогон и доводка качества

- [ ] С ключом из окружения: методичка ПСИП ЛР7 (фикстура) → разбор → текст → проверки → `.docx` → PDF (LibreOffice) → `compare()` == `[]`, визуальный осмотр страниц.
- [ ] Повторить на теоретической методичке (ССП ЛР1-подобной) и с женским профилем.
- [ ] Исправить найденное, `pytest -q` полностью зелёный, коммит и push.
