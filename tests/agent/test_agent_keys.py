"""SendInput: координаты на нескольких мониторах, Unicode вне BMP, коды клавиш."""

import pytest

from forlabs.agent.core import Blocked
from forlabs.agent.keys import absolute, utf16_units, vkey


def test_absolute_covers_virtual_desktop_with_monitor_on_the_left():
    screen = (-1920, 0, 3840, 1080)  # второй монитор слева от основного
    assert absolute(-1920, 0, screen) == (0, 0)
    assert absolute(1919, 1079, screen) == (65535, 65535)
    x, _ = absolute(0, 500, screen)
    assert 32760 < x < 32790  # левый край основного монитора — середина рабочего стола


def test_utf16_surrogates_and_cyrillic():
    assert utf16_units("Яё") == [0x042F, 0x0451]
    assert utf16_units("😀") == [0xD83D, 0xDE00]


@pytest.mark.parametrize("name, vk", [("s", 0x53), ("5", 0x35), ("f5", 0x74), ("f12", 0x7B), ("enter", 0x0D),
                                      ("pagedown", 0x22), ("/", 0xBF)])
def test_vkeys(name, vk):
    assert vkey(name) == vk


def test_cyrillic_hotkey_letter_is_explained():
    with pytest.raises(Blocked, match="латиницей"):
        vkey("ы")
