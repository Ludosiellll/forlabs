"""Точка входа собранной программы ForLabs.exe (Nuitka).

    ForLabs.exe                         окно ForLabs
    ForLabs.exe --browser               в браузере вместо окна
    ForLabs.exe --selfcheck [--out f]   самопроверка сборки (CI, поддержка)
    ForLabs.exe --uninstall [--silent]  удаление (строка «Параметры → Приложения»)
"""

from __future__ import annotations

import multiprocessing
import sys


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--uninstall" not in argv:  # удалению PDF не нужен
        from forlabs import frozen_fix

        frozen_fix.apply()
    if "--selfcheck" in argv:
        from forlabs import selfcheck

        return selfcheck.main(argv)
    if "--uninstall" in argv:
        from forlabs.installer import uninstall

        return uninstall.main(argv)
    from forlabs import __main__ as app

    app.main()
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()  # дочерний процесс Word в собранной программе
    raise SystemExit(main())
