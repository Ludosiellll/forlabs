"""Что чат правок может посмотреть сам, прежде чем отвечать: файлы проекта программы — как они лежат в папке проекта
у студента (а если папки нет — из плана), поиск по ним и задание отчёта целиком.

Открыта только папка проекта этой лабы. Всё прочитанное уходит модели на сервер, поэтому остальной диск закрыт:
ни «..», ни абсолютные пути, ни ссылки наружу не читаются."""

from __future__ import annotations

from pathlib import Path

from forlabs.runner.plan import CODE_EXT, DATA_EXT, EXTRA_EXT, ProjectPlan, norm_path

READ_LIMIT = 12_000  # знаков одного файла за раз
FILE_LIMIT = 300 * 1024  # байт: файлы больше не читаются
ANSWER_LIMIT = 30_000  # знаков на всё найденное за один круг
FIND_LINES = 40
LINE_LIMIT = 1000  # знаков одной строки: сжатый в одну строку файл целиком не показывается
MAX_FILES = 300
SKIP_DIRS = {"bin", "obj", "out", "__pycache__", ".vs", ".git", ".idea", "node_modules", "debug", "release"}
TEXT_EXT = (set().union(*CODE_EXT.values(), *EXTRA_EXT.values()) | DATA_EXT
            | {".csproj", ".sln", ".mod", ".vcxproj", ".ini", ".cfg", ".yml", ".yaml"})


def _size(n: int) -> str:
    return f"{n} Б" if n < 1024 else f"{n / 1024:.1f} КБ".replace(".", ",")


class ProjectView:
    def __init__(self, plan: ProjectPlan | None = None, folder: Path | str | None = None, st=None):
        self.plan = plan
        self.folder = Path(folder) if folder else None
        self.st = st

    # ---------- файлы ----------

    def _on_disk(self) -> dict[str, Path]:
        """Текстовые файлы папки проекта (без результатов сборки): путь от папки → файл."""
        root = self.folder
        if root is None or not root.is_dir():
            return {}
        out: dict[str, Path] = {}
        try:
            real = root.resolve()
            for f in sorted(root.rglob("*")):
                rel = f.relative_to(root)
                if {p.lower() for p in rel.parts[:-1]} & SKIP_DIRS or f.suffix.lower() not in TEXT_EXT:
                    continue
                try:
                    inside = not f.is_symlink() and f.is_file() and f.resolve().is_relative_to(real)
                    if not inside or f.stat().st_size > FILE_LIMIT:
                        continue
                except OSError:
                    continue
                out[rel.as_posix()] = f
                if len(out) >= MAX_FILES:
                    break
        except OSError:
            return out
        return out

    def files(self) -> dict[str, int]:
        """Файлы проекта: путь → размер в байтах. Сначала то, что лежит у студента, затем то, что есть только в плане."""
        out = {}
        for path, f in self._on_disk().items():
            try:
                out[path] = f.stat().st_size
            except OSError:
                continue
        for f in self.plan.files if self.plan else []:
            if f.path.lower() not in {p.lower() for p in out}:
                out[f.path] = len(f.content.encode("utf-8"))
        return out

    def _text(self, path: str, disk: dict[str, Path] | None = None) -> tuple[str, bool] | None:
        """(текст файла, с диска ли он). None — такого файла в проекте нет или читать его нельзя."""
        raw = str(path or "").strip()
        key = norm_path(raw)
        if not key or key.startswith("/") or ":" in key or ".." in key.split("/"):
            return None
        disk = self._on_disk() if disk is None else disk
        f = disk.get(key) or next((v for k, v in disk.items() if k.lower() == key.lower()), None)
        if f is not None:
            try:
                return f.read_bytes().decode("utf-8-sig", "replace"), True
            except OSError:
                pass
        mine = next((x for x in (self.plan.files if self.plan else []) if x.path.lower() == key.lower()), None)
        return (mine.content, False) if mine is not None else None

    def read(self, path: str, line: int = 1) -> str:
        got = self._text(path)
        if got is None:
            names = ", ".join(list(self.files())[:40]) or "файлов нет"
            return f"Файла «{path}» нет в проекте. Файлы проекта: {names}."
        text, disk = got
        lines = text.splitlines()
        start = max(1, int(line or 1))
        body, used, stop = [], 0, None
        for i, ln in enumerate(lines, start=1):
            if i < start:
                continue
            row = f"{i}: {ln}" if len(ln) <= LINE_LIMIT else f"{i}: {ln[:LINE_LIMIT]}… (строка обрезана)"
            if used + len(row) + 1 > READ_LIMIT:
                stop = i
                break
            body.append(row)
            used += len(row) + 1
        head = f"Файл {norm_path(path)}" + (" (как лежит в папке проекта у студента)" if disk else "") \
            + f", строк: {len(lines)}:"
        tail = f"\n… обрезано на строке {stop} из {len(lines)}: дальше — тем же запросом с \"line\": {stop}" if stop else ""
        return head + "\n" + "\n".join(body) + tail

    def find(self, text: str) -> str:
        needle = str(text or "").strip().lower()
        if not needle:
            return "Что искать — не сказано."
        hits = []
        disk = self._on_disk()  # папка обходится один раз на весь поиск
        names = list(disk) + [f.path for f in (self.plan.files if self.plan else [])
                              if f.path.lower() not in {p.lower() for p in disk}]
        for path in names:
            got = self._text(path, disk)
            for i, ln in enumerate(got[0].splitlines() if got else [], start=1):
                if needle in ln.lower():
                    hits.append(f"{path}:{i}: {ln.strip()[:200]}")
                    if len(hits) >= FIND_LINES:
                        return f"«{text}» в файлах проекта (первые {FIND_LINES}):\n" + "\n".join(hits)
        return (f"«{text}» в файлах проекта:\n" + "\n".join(hits)) if hits else f"«{text}» в файлах проекта не встречается."

    # ---------- отчёт ----------

    def task(self, n: int) -> str:
        from forlabs.pipeline.chat import describe_block

        sec = next((s for s in (self.st.report.sections if self.st and self.st.report else []) if s.n == n), None)
        if sec is None:
            return f"Задания {n} в отчёте нет."
        blocks = "\n".join(describe_block(f"b{i}", b) for i, b in enumerate(sec.blocks, start=1))
        return f"Задание {sec.n}. {sec.title} — блоки целиком:\n{blocks}"

    # ---------- для запроса ----------

    def summary(self) -> str:
        """Состояние программы лабы для чата: собрана ли, какие в ней программы и файлы, ошибки последней сборки."""
        pr = self.st.project if self.st is not None else None
        files = self.files()
        need = self._apps_needed()
        if pr is None and not files and not need:
            return "Программа лабы: проекта нет (в отчёте нет кода, который ForLabs собирает сам)."
        parts = []
        if pr is not None:
            state = ("собрана" if pr.built else
                     f"не собрана — нет программ на компьютере: {', '.join(pr.missing)}" if pr.missing else
                     f"не запускалась — {pr.blocked[0]}" if pr.blocked else "не собралась")
            parts.append(f"Программа лабы: «{pr.name}», язык {pr.language}. Состояние: {state}.")
            if pr.programs:
                parts.append("Программ в проекте несколько, у каждой своя папка: " + ", ".join(pr.programs) + ".")
        if self.plan is not None and self.plan.programs:
            from forlabs.pipeline.project import programs_text

            parts.append(programs_text(self.plan))
        if pr is not None and pr.missing:
            from forlabs.runner import tools

            parts.append("Не хватает программ на этом компьютере (поэтому программа не собрана):\n"
                         + "\n".join("- " + (tools.how_to_get(k) or k) for k in pr.missing))
        if need:
            from forlabs.agent import apps

            parts.append("Для снимков в программах не хватает:\n" + "\n".join("- " + apps.how_to_get(k) for k in need))
        listed = ", ".join(f"{p} ({_size(n)})" for p, n in list(files.items())[:60])
        parts.append(f"Файлы проекта: {listed or 'нет'}." + (" …" if len(files) > 60 else ""))
        if pr is not None and not pr.built and pr.build_log.strip():
            parts.append(f"Ошибки последней сборки:\n<<<\n{pr.build_log.strip()}\n>>>")
        return "\n".join(parts)

    def _apps_needed(self) -> list[str]:
        """Программы студента, без которых не сделать снимки (Visual Studio и т.п.)."""
        out: list[str] = []
        for sec in (self.st.report.sections if self.st is not None and self.st.report else []):
            for b in sec.blocks:
                tool = getattr(b, "need_tool", None) or ""
                if tool.startswith("app:") and not getattr(b, "image_path", None) and tool[4:] not in out:
                    out.append(tool[4:])
        return out

    def answer(self, looks: list) -> str:
        out = []
        for look in looks[:8]:
            if getattr(look, "file", ""):
                out.append(self.read(look.file, getattr(look, "line", 1) or 1))
            if getattr(look, "find", ""):
                out.append(self.find(look.find))
            if getattr(look, "task", None) is not None:
                out.append(self.task(look.task))
        text = "\n\n".join(out) or "Ничего не запрошено."
        return text if len(text) <= ANSWER_LIMIT else text[:ANSWER_LIMIT] + "\n… (обрезано)"
