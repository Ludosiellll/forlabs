"""CI: запустить команду, показать её вывод, а при сбое — хвост вывода аннотациями ::error (их видно без входа)."""

import subprocess
import sys
from collections import deque


def main(argv: list[str]) -> int:
    title, cmd = argv[0], argv[1:]
    tail: deque[str] = deque(maxlen=60)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,  # noqa: S603
                            encoding="utf-8", errors="replace")
    assert proc.stdout is not None
    for line in proc.stdout:
        sys.stdout.write(line)
        tail.append(line.rstrip())
    code = proc.wait()
    if code:
        for line in tail:
            if line.strip():
                print(f"::error title={title}::{line[:900]}")
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
