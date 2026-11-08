"""События выполнения лабы для интерфейса (журнал и прогресс)."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class Event:
    seq: int
    text: str
    kind: str = "info"  # info | step | warn | error | done
    step: str | None = None
    at: float = field(default_factory=time.time)


class EventBus:
    def __init__(self, keep: int = 500):
        self._events: dict[str, list[Event]] = {}
        self._cond = threading.Condition()
        self._seq = 0
        self.keep = keep

    def publish(self, key: str, text: str, kind: str = "info", step: str | None = None) -> Event:
        with self._cond:
            self._seq += 1
            ev = Event(self._seq, text, kind, step)
            lst = self._events.setdefault(key, [])
            lst.append(ev)
            del lst[:-self.keep]
            self._cond.notify_all()
            return ev

    def history(self, key: str, after: int = 0) -> list[Event]:
        with self._cond:
            return [e for e in self._events.get(key, []) if e.seq > after]

    def wait(self, key: str, after: int, timeout: float = 15.0) -> list[Event]:
        deadline = time.time() + timeout
        with self._cond:
            while True:
                evs = [e for e in self._events.get(key, []) if e.seq > after]
                left = deadline - time.time()
                if evs or left <= 0:
                    return evs
                self._cond.wait(left)
