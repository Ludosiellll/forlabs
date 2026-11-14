"""Настройка прямого доступа: кэш, отказ, ключ не утекает, отчёт о расходе не задерживает работу."""

import logging
import threading
import time

import httpx

from forlabs.core.direct import DirectAccess

FN = "https://fn.test/functions/v1/llm"
CONFIG = {"base_url": "https://prov.test/v1", "model": "glm-5.3-flash", "models": ["glm-5.3-flash"],
          "key": "sk-secret-member-key", "access_until": None, "max_tokens": 32000, "timeout_s": 600}


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _access(handler, clock=None):
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return DirectAccess(http, FN, token=lambda: "access-token", ttl=600, clock=clock or Clock())


def test_config_is_cached_for_ten_minutes():
    calls = []
    clock = Clock()

    def handler(req):
        calls.append(str(req.url))
        return httpx.Response(200, json=CONFIG)

    d = _access(handler, clock)
    assert d.config().model == "glm-5.3-flash"
    clock.t += 599
    d.config()
    assert len(calls) == 1
    clock.t += 2
    d.config()
    assert len(calls) == 2


def test_no_direct_access_is_remembered():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(404, json={"error": {"message": "no direct access"}})

    d = _access(handler)
    assert d.config() is None and d.config() is None and len(calls) == 1


def test_key_never_appears_in_repr_or_logs(caplog):
    def handler(req):
        if str(req.url).endswith("/usage"):
            return httpx.Response(204)
        return httpx.Response(200, json=CONFIG)

    caplog.set_level(logging.DEBUG)
    d = _access(handler)
    cfg = d.config()
    d.report("glm-5.3-flash", {"prompt_tokens": 1, "completion_tokens": 1}, None, None)
    d.wait()
    assert cfg.key == "sk-secret-member-key"
    assert "sk-secret" not in repr(cfg) and "sk-secret" not in caplog.text


def test_report_does_not_wait_for_a_hanging_server():
    release = threading.Event()

    def handler(req):
        release.wait(5)
        return httpx.Response(204)

    d = _access(handler)
    t = time.monotonic()
    d.report("glm-5.3-flash", {"prompt_tokens": 10, "completion_tokens": 5}, "lab", None)
    assert time.monotonic() - t < 0.5
    release.set()
    d.wait()


def test_down_direct_path_is_skipped_until_the_next_refresh():
    clock = Clock()
    d = _access(lambda req: httpx.Response(200, json=CONFIG), clock)
    assert d.config() is not None
    d.mark_down()
    assert d.config() is None
    clock.t += 601
    assert d.config() is not None
