# ForLabs exe — план

**Spec:** docs/superpowers/specs/2026-11-08-forlabs-exe-design.md. Исполнение inline, TDD, ledger в `.superpowers/sdd/exe/`.

## Global Constraints
- Python 3.12 в CI (Nuitka), исходники совместимы с 3.11+. Без прав администратора: всё в HKCU и `%LOCALAPPDATA%`.
- Папка программы `%LOCALAPPDATA%\Programs\ForLabs`; данные (`%LOCALAPPDATA%\ForLabs`) установщик не трогает,
  удаление спрашивает про них отдельно.
- Без подписи кода. Никаких секретов в exe и в репозитории.
- Стиль установщика и обучения — 2.6 (Fluent): цвета #202020/#272727, акцент #60CDFF, кривые и длительности Fluent 2.

## T1 — пакет к сборке (готово)
`forlabs/rules/`, `rules.builtin_rules()/rules_source()`, `tools/embed_rules.py write_module(rules_dir, out)`,
`forlabs/version.py (__version__, frozen())`, `/health → {"app","version"}`, `run_forlabs.py main(argv) -> int`,
`selfcheck.build_checks() -> list[Check]`, `selfcheck.main(argv) -> int` (1 при поломке сборки).

## T2 — ядро установщика `forlabs/installer/`
- `core.py`: `Shell` (протокол: `create_shortcut(lnk, target, args, workdir, icon)`, `set_uninstall_entry(dict)`,
  `remove_uninstall_entry()`, `start_menu_dir()`, `desktop_dir()`, `running_pids(exe)`), `WindowsShell`, `FakeShell` в тестах.
  `install(payload: Path, target: Path, *, desktop: bool, shell, progress: Callable[[int,int,str],None],
  cancelled: Callable[[],bool]) -> InstallResult` — распаковка в `target.new`, затем замена `target`
  (старое → `target.old`, удаляется), ярлыки, запись в реестр (DisplayName, DisplayVersion, Publisher,
  InstallLocation, DisplayIcon, UninstallString `"…\ForLabs.exe" --uninstall`, QuietUninstallString, EstimatedSize,
  NoModify, NoRepair). Ошибки — `InstallError(message_ru)`: занят файл, нет места, нет прав, отмена.
- `uninstall.py`: `main(argv) -> int` — вопрос (MessageBox) «Удалить ForLabs?» + «и данные?»; `--silent`
  без вопросов, `--remove-data`; ярлыки, реестр, файлы (exe удаляет себя через отложенную команду).
- `payload`: `tools/make_payload.py dist_dir out.zip` (+ manifest с размером).
## T3 — окно установщика
`forlabs/installer/web/setup.html` (2.6, без внешних ресурсов) + `forlabs/installer/app.py` (`Api` для pywebview:
`defaults()`, `browse()`, `start(target, desktop, launch)`, `poll()`, `cancel()`, `launch()`, `close()`),
`run_setup.py` (`--silent --target D [--no-desktop]`). Нет WebView2 → запуск бутстрапа Microsoft, затем повтор;
не вышло — установка с вопросом через MessageBox.
## T4 — обучение
`forlabs/ui/static/tour.js` + стили в `app.css`, якоря `data-tour="…"` в base.html/index; флаг `Settings.tour_done`;
POST `/tour/done`, POST `/tour/restart` (кнопка в «Настройках»). Без JS — ничего не ломается.
## T6 — CI
`.github/workflows/build-windows.yml`: windows-latest, Python 3.12, `pip install .[windows] nuitka`, embed_rules,
Nuitka standalone `run_forlabs.py` → `ForLabs.dist`, `--selfcheck`, payload, Nuitka onefile `run_setup.py`,
`ForLabsSetup.exe --silent`, установленный `--selfcheck`, `--uninstall --silent`; при сбое — `::error::` с хвостом
отчёта (видно в аннотациях без входа в GitHub). Тег `v*` — релиз.
## T7 — README, docs/windows-checklist.md.

## Review Focus
1. Повторная установка поверх работающего ForLabs — понятная просьба закрыть, старая версия цела.
2. Обрыв установки посередине — старая версия цела, повтор дописывает.
3. Папка установки с кириллицей и пробелами (ярлыки, реестр, кавычки в UninstallString).
4. Удаление не трогает данные без согласия и не оставляет пустую папку программы.
5. Обучение не мешает работе: пропуск, повторный показ, страницы без якорей.
