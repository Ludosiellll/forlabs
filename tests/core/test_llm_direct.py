"""Прямой путь к провайдеру со своим ключом участника: настройка с сервера, ключ участника, откат на сервер."""

import json

import httpx
import pytest

from forlabs.core.direct import DirectAccess
from forlabs.core.llm import LLM, LLMError
from forlabs.core.settings import Settings

FN = "https://fn.test/functions/v1/llm"
PROV = "https://prov.test/v1"


def _completion(content="ok", finish="stop", model="glm-5.3-flash"):
    return {"model": model, "choices": [{"index": 0, "finish_reason": finish,
                                         "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}}


def _config(key="sk-member-1", model="glm-5.3-flash"):
    return {"base_url": PROV, "model": model, "models": [model, "deepseek-v4.1-flash-1017"], "key": key,
            "access_until": None, "max_tokens": 32000, "timeout_s": 600}


class World:
    """Поддельные функция llm и провайдер: что пришло, что ответить."""

    def __init__(self, configs=None, provider=None, proxy=None):
        self.configs = list(configs or [(200, _config())])
        self.provider = list(provider or [(200, _completion())])
        self.proxy = list(proxy or [(200, _completion(model="deepseek-v4.1-flash-1017"))])
        self.calls: list[tuple[str, dict, dict]] = []

    def __call__(self, req: httpx.Request):
        body = json.loads(req.content) if req.content else {}
        url = str(req.url)
        self.calls.append((url, dict(req.headers), body))
        if url == f"{FN}/direct":
            status, data = self.configs.pop(0) if len(self.configs) > 1 else self.configs[0]
        elif url == f"{FN}/usage":
            return httpx.Response(204)
        elif url == f"{PROV}/chat/completions":
            item = self.provider.pop(0) if len(self.provider) > 1 else self.provider[0]
            if isinstance(item, Exception):
                raise item
            status, data = item
        elif url == f"{FN}/chat/completions":
            status, data = self.proxy.pop(0) if len(self.proxy) > 1 else self.proxy[0]
        else:
            raise AssertionError(url)
        return httpx.Response(status, json=data) if isinstance(data, dict) else httpx.Response(status, text=data)

    def to(self, url):
        return [c for c in self.calls if c[0] == url]


def make(world: World, reasoning="low") -> tuple[LLM, DirectAccess]:
    http = httpx.Client(transport=httpx.MockTransport(world))
    llm = LLM(Settings(mode="cloud", reasoning=reasoning), api_key=lambda: "access-token", http_client=http,
              base_url=FN, retry_delays=(0, 0))
    llm.accepts_lab_tag = True
    direct = DirectAccess(http, FN, token=lambda: "access-token")
    llm.direct = direct
    return llm.with_headers({"X-ForLabs-Lab": "ivanov%2Fpsip%2F7", "X-ForLabs-Chat": "c1"}), direct


MSG = [{"role": "user", "content": "Привет"}]


def test_request_goes_to_provider_with_member_key_and_usage_is_reported():
    world = World()
    llm, direct = make(world)
    assert llm.chat(MSG) == "ok"
    url, headers, body = world.to(f"{PROV}/chat/completions")[0]
    assert headers["authorization"] == "Bearer sk-member-1" and body["model"] == "glm-5.3-flash"
    assert not any(h.startswith("x-forlabs") for h in headers)
    assert llm.server["max_tokens"] == 32000
    direct.wait()
    _, uh, usage = world.to(f"{FN}/usage")[0]
    assert uh["authorization"] == "Bearer access-token"
    assert usage == {"model": "glm-5.3-flash", "prompt_tokens": 100, "completion_tokens": 20,
                     "lab": "ivanov/psip/7", "chat": "c1"}


def test_without_direct_access_the_server_path_is_used():
    world = World(configs=[(404, {"error": {"message": "no direct access"}})])
    llm, _ = make(world)
    llm.chat(MSG)
    url, headers, _ = world.to(f"{FN}/chat/completions")[0]
    assert headers["authorization"] == "Bearer access-token" and headers["x-forlabs-lab"] == "ivanov%2Fpsip%2F7"
    assert not world.to(f"{PROV}/chat/completions")


def test_rejected_key_fetches_the_new_one_once():
    world = World(configs=[(200, _config("sk-old")), (200, _config("sk-new"))],
                  provider=[(401, {"error": {"message": "invalid api key"}}), (200, _completion())])
    llm, _ = make(world)
    assert llm.chat(MSG) == "ok"
    keys = [h["authorization"] for _, h, _ in world.to(f"{PROV}/chat/completions")]
    assert keys == ["Bearer sk-old", "Bearer sk-new"] and len(world.to(f"{FN}/direct")) == 2


def test_key_rejected_again_tells_to_write_to_admin():
    world = World(provider=[(401, {"error": {"message": "invalid api key"}})])
    llm, _ = make(world)
    with pytest.raises(LLMError) as e:
        llm.chat(MSG)
    assert e.value.message_ru == "Ключ не работает — напиши администратору."


def test_empty_balance_tells_to_write_to_admin():
    world = World(provider=[(402, {"error": {"message": "insufficient balance"}})])
    llm, _ = make(world)
    with pytest.raises(LLMError) as e:
        llm.chat(MSG)
    assert e.value.message_ru == "На ключе закончились токены — напиши администратору."


def test_unreachable_provider_falls_back_to_the_server():
    world = World(provider=[httpx.ConnectError("blocked by college network")])
    llm, direct = make(world)
    assert llm.chat(MSG) == "ok"
    assert len(world.to(f"{PROV}/chat/completions")) == 3 and len(world.to(f"{FN}/chat/completions")) == 1
    assert direct.config() is None  # прямой путь отложен до следующего обновления настройки


def test_expired_subscription_stops_work():
    world = World(configs=[(403, {"error": {"message": "subscription expired: 2026-12-01T00:00:00Z"}})])
    llm, _ = make(world)
    with pytest.raises(LLMError) as e:
        llm.chat(MSG)
    assert e.value.message_ru.startswith("Подписка закончилась")
    assert not world.to(f"{PROV}/chat/completions") and not world.to(f"{FN}/chat/completions")


def test_glm_normal_step_reasons_briefly_with_headroom():
    world = World()
    llm, _ = make(world)
    llm.chat(MSG, max_tokens=1500)
    body = world.to(f"{PROV}/chat/completions")[0][2]
    assert body["reasoning_effort"] == "low" and body["max_tokens"] == 5500


def test_glm_hard_step_reasons_medium_on_the_direct_path():
    world = World()
    llm, _ = make(world, reasoning="low")
    llm.chat(MSG, think=True)
    body = world.to(f"{PROV}/chat/completions")[0][2]
    assert body["reasoning_effort"] == "medium" and body["max_tokens"] == 20000


def test_glm_cut_by_length_is_retried_once_with_the_path_cap():
    world = World(provider=[(200, _completion("", finish="length")), (200, _completion("готово"))])
    llm, _ = make(world)
    assert llm.chat(MSG, max_tokens=1500) == "готово"
    bodies = [b for _, _, b in world.to(f"{PROV}/chat/completions")]
    assert len(bodies) == 2 and bodies[1]["max_tokens"] == 32000 and bodies[1]["reasoning_effort"] == "low"
