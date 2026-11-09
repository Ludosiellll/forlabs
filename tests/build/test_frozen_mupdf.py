"""Собранная программа: обёртка PyMuPDF (mupdf.py) не компилируется и подгружается из папки программы как модуль
верхнего уровня — запасной путь, которым её ищет сам pymupdf."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_preload_mupdf_makes_pymupdf_work_from_files():
    import pymupdf

    base = Path(pymupdf.__file__).parent
    code = (
        "import sys; from pathlib import Path\n"
        "from forlabs.frozen_fix import preload_mupdf\n"
        f"assert preload_mupdf(Path(r'{base}'))\n"
        "assert 'mupdf' in sys.modules and '_mupdf' in sys.modules\n"
        "import pymupdf\n"
        "doc = pymupdf.open(); doc.new_page().insert_text((72, 72), 'ok'); assert doc.tobytes()\n"
        "print('OK')\n"
    )
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and "OK" in r.stdout, r.stderr[-1500:]


def test_preload_mupdf_without_files_is_noop(tmp_path):
    from forlabs.frozen_fix import preload_mupdf

    assert preload_mupdf(tmp_path) is False
