"""LibreOffice без интерфейса: .docx → PDF для предпросмотра, когда нет Word."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

WINDOWS_CANDIDATES = [r"C:\Program Files\LibreOffice\program\soffice.exe",
                      r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"]


def find_soffice(configured: str | None = None) -> str | None:
    if configured and Path(configured).exists():
        return configured
    found = shutil.which("soffice") or shutil.which("libreoffice")
    if found:
        return found
    if sys.platform == "win32":
        for c in WINDOWS_CANDIDATES:
            if Path(c).exists():
                return c
    return None


def to_pdf(docx: Path, outdir: Path, soffice: str | None = None, timeout: int = 240) -> Path:
    exe = find_soffice(soffice)
    if not exe:
        raise RuntimeError("LibreOffice не найден")
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="forlabs-lo-") as profile:
        uri = Path(profile).as_uri()
        cmd = [exe, f"-env:UserInstallation={uri}", "--headless", "--convert-to", "pdf", "--outdir", str(outdir),
               str(docx)]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                             env={**os.environ, "HOME": profile} if sys.platform != "win32" else None)
    pdf = outdir / (Path(docx).stem + ".pdf")
    if res.returncode != 0 or not pdf.exists():
        raise RuntimeError(f"LibreOffice не смог сделать PDF: {res.stderr.strip()[:300]}")
    return pdf
