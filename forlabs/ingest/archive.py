"""ZIP от преподавателя → обычная папка: без путей наружу, без мусора архиваторов, с русскими именами."""

from __future__ import annotations

import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath

MAX_ENTRIES = 2000
MAX_TOTAL = 2_000_000_000  # байт после распаковки на весь архив
MAX_RATIO = 200  # во сколько раз файл может быть сжат (для файлов больше 10 МБ) — иначе «бомба»
JUNK = {".ds_store", "thumbs.db", "desktop.ini"}


def _name(info: zipfile.ZipInfo) -> str:
    """Архиваторы Windows пишут имена в cp866 без флага UTF-8; zipfile читает их как cp437."""
    if info.flag_bits & 0x800:
        return info.filename
    try:
        return info.filename.encode("cp437").decode("cp866")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return info.filename


def unpack(zip_path: Path, dest: Path) -> list[Path]:
    """Распаковать в dest (создаётся). → распакованные файлы. ValueError — с причиной по-русски."""
    try:
        z = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as e:
        raise ValueError("это не ZIP-архив или он повреждён") from e
    dest = Path(dest)
    out: list[Path] = []
    with z:
        infos = z.infolist()
        if len(infos) > MAX_ENTRIES:
            raise ValueError(f"в архиве больше {MAX_ENTRIES} файлов")
        if sum(i.file_size for i in infos) > MAX_TOTAL:
            raise ValueError("архив после распаковки больше 2 ГБ")
        plan = []
        for info in infos:
            name = _name(info).replace("\\", "/")
            parts = PurePosixPath(name).parts
            if info.is_dir() or not parts:
                continue
            if name.startswith("/") or ".." in parts or ":" in parts[0]:
                raise ValueError(f"в архиве есть файл вне папки архива («{name}»)")
            if parts[0] == "__MACOSX" or parts[-1].lower() in JUNK or parts[-1].startswith("._"):
                continue
            if stat.S_ISLNK(info.external_attr >> 16):
                continue  # ссылки не распаковываются
            if info.file_size > 10 * 1024 * 1024 and info.file_size > MAX_RATIO * max(info.compress_size, 1):
                raise ValueError(f"файл «{name}» подозрительно сильно сжат — архив не распакован")
            plan.append((info, parts))
        dest.mkdir(parents=True, exist_ok=True)
        root = dest.resolve()
        for info, parts in plan:
            target = dest.joinpath(*parts)
            if not str(target.resolve()).startswith(str(root)):
                raise ValueError(f"в архиве есть файл вне папки архива («{'/'.join(parts)}»)")
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with z.open(info) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
            except (RuntimeError, NotImplementedError) as e:  # пароль, Deflate64 и прочие способы сжатия
                raise ValueError("архив защищён паролем или сжат способом, который ForLabs не поддерживает — "
                                 "пересохрани его обычным ZIP без пароля") from e
            out.append(target)
    return out
