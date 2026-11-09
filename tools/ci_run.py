"""CI: запустить команду, показать её вывод, а при сбое — хвост вывода ОДНОЙ аннотацией ::error (GitHub показывает
не больше 10 аннотаций на шаг; без входа видны только они). Шумные предупреждения в хвост не попадают."""

import subprocess
import sys
from collections import deque

NOISE = ("Duplicate data file", "Nuitka-Progress", "Nuitka-Inclusion:WARNING")


def main(argv: list[str]) -> int:
    title, cmd = argv[0], argv[1:]
    tail: deque[str] = deque(maxlen=45)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,  # noqa: S603
                            encoding="utf-8", errors="replace")
    assert proc.stdout is not None
    for line in proc.stdout:
        sys.stdout.write(line)
        if line.strip() and not any(n in line for n in NOISE):
            tail.append(line.rstrip()[:400])
    code = proc.wait()
    if code:
        text = "%0A".join(s.replace("%", "%25").replace("\r", "") for s in tail)
        print(f"::error title={title} (код {code})::{text}")
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
