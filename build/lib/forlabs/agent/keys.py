"""Клавиши и координаты для SendInput (без вызовов Windows — проверяется тестами в любой системе)."""

from __future__ import annotations

from forlabs.agent.core import Blocked

KEYUP, UNICODE, EXTENDED = 0x2, 0x4, 0x1
VKEYS = {"enter": 0x0D, "tab": 0x09, "esc": 0x1B, "space": 0x20, "backspace": 0x08, "delete": 0x2E, "insert": 0x2D,
         "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27, "home": 0x24, "end": 0x23, "pageup": 0x21,
         "pagedown": 0x22, "apps": 0x5D, "ctrl": 0x11, "shift": 0x10, "alt": 0x12,
         "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD, ";": 0xBA, "'": 0xDE, ",": 0xBC, ".": 0xBE, "/": 0xBF,
         "\\": 0xDC, "`": 0xC0, **{f"f{i}": 0x6F + i for i in range(1, 13)}}
EXTENDED_VK = {0x2E, 0x2D, 0x26, 0x28, 0x25, 0x27, 0x24, 0x23, 0x21, 0x22, 0x5D}


def absolute(x: int, y: int, screen: tuple[int, int, int, int]) -> tuple[int, int]:
    """Пиксели экрана → 0…65535 по всему виртуальному рабочему столу (несколько мониторов, отрицательные x)."""
    vx, vy, vw, vh = screen
    return round((x - vx) * 65535 / max(1, vw - 1)), round((y - vy) * 65535 / max(1, vh - 1))


def utf16_units(text: str) -> list[int]:
    """Символы вне BMP (эмодзи) — двумя половинками UTF-16: так их принимает KEYEVENTF_UNICODE."""
    data = text.encode("utf-16-le")
    return [int.from_bytes(data[i:i + 2], "little") for i in range(0, len(data), 2)]


def vkey(name: str) -> int:
    if name in VKEYS:
        return VKEYS[name]
    if len(name) == 1 and ("a" <= name <= "z" or "0" <= name <= "9"):
        return ord(name.upper())
    raise Blocked(f"клавишу «{name}» указывай латиницей (ctrl+s, а не ctrl+ы)")
