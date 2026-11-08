import json
import os

import httpx
import pytest
from pydantic import BaseModel

from forlabs.core.llm import LLM, FakeLLM, LLMError
from forlabs.core.settings import Settings


class Answer(BaseModel):
    city: str
    population: int


def _completion(content: str) -> dict:
    return {"id": "x", "object": "chat.completion", "created": 0, "model": "deepseek-v4.1-flash",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}


def make_llm(handler) -> LLM:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return LLM(Settings(base_url="https://example.test/v1"), api_key="k", http_client=client, retry_delays=(0, 0))


def test_json_repair_loop():
    replies = iter(["это не json", json.dumps({"city": "Минск", "population": "много"}),
                    json.dumps({"city": "Минск", "population": 2000000})])
    seen = []

    def handler(req):
        seen.append(json.loads(req.content))
        return httpx.Response(200, json=_completion(next(replies)))

    llm = make_llm(handler)
    res = llm.json([{"role": "user", "content": "Столица?"}], Answer)
    assert res == Answer(city="Минск", population=2000000)
    assert len(seen) == 3
    assert "ошиб" in seen[1]["messages"][-1]["content"].lower()
    assert llm.usage.total_tokens == 45


def test_json_gives_up():
    llm = make_llm(lambda req: httpx.Response(200, json=_completion("нет")))
    with pytest.raises(LLMError) as e:
        llm.json([{"role": "user", "content": "x"}], Answer, repairs=2)
    assert e.value.kind == "bad_response"


@pytest.mark.parametrize("status,body,kind", [
    (401, {"error": {"message": "invalid key"}}, "auth"),
    (403, {"error": {"message": "model_not_allowed"}}, "model"),
    (429, {"error": {"message": "rate limit"}}, "quota"),
    (402, {"error": {"message": "insufficient balance"}}, "quota"),
])
def test_error_mapping(status, body, kind):
    llm = make_llm(lambda req: httpx.Response(status, json=body))
    with pytest.raises(LLMError) as e:
        llm.chat([{"role": "user", "content": "x"}])
    assert e.value.kind == kind
    assert e.value.message_ru


def test_network_error():
    def handler(req):
        raise httpx.ConnectError("нет сети")

    with pytest.raises(LLMError) as e:
        make_llm(handler).chat([{"role": "user", "content": "x"}])
    assert e.value.kind == "network"


def test_fake_llm_interface():
    fake = FakeLLM([{"city": "Гомель", "population": 500000}, "привет"])
    assert fake.json([], Answer).city == "Гомель"
    assert fake.chat([]) == "привет"
    assert len(fake.calls) == 2


@pytest.mark.live
def test_live_check():
    s = Settings(base_url=os.environ.get("FORLABS_BASE_URL", Settings().base_url),
                 model=os.environ.get("FORLABS_MODEL", Settings().model))
    llm = LLM(s, api_key=os.environ["FORLABS_API_KEY"])
    assert "flash" in llm.check()
    res = llm.json([{"role": "user", "content": "Верни JSON: город Минск, население 2000000. Поля city, population."}], Answer)
    assert res.city == "Минск"


@pytest.mark.parametrize("status,msg,needle", [
    (403, "account not approved", "одобр"),
    (503, "LLM_API_KEY is not set", "LLM_API_KEY"),
])
def test_cloud_proxy_errors(status, msg, needle):
    llm = make_llm(lambda req: httpx.Response(status, json={"error": {"message": msg}}))
    with pytest.raises(LLMError) as e:
        llm.chat([{"role": "user", "content": "x"}])
    assert e.value.kind == "access" and needle in e.value.message_ru


def test_api_key_callable_is_called_per_request():
    tokens = iter(["t1", "t2"])
    seen = []

    def handler(req):
        seen.append(req.headers["authorization"])
        return httpx.Response(200, json=_completion("ok"))

    llm = LLM(Settings(base_url="https://x/v1"), api_key=lambda: next(tokens),
              http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    llm.chat([{"role": "user", "content": "a"}]); llm.chat([{"role": "user", "content": "b"}])
    assert seen == ["Bearer t1", "Bearer t2"]


def test_api_key_provider_failure_becomes_llm_error():
    class Expired(Exception):
        message_ru = "Сессия истекла — войди снова."

    def provider():
        raise Expired("refresh failed")

    llm = LLM(Settings(base_url="https://x/v1"), api_key=provider,
              http_client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200))))
    with pytest.raises(LLMError) as e:
        llm.chat([{"role": "user", "content": "x"}])
    assert e.value.kind == "access" and "войди" in e.value.message_ru


def test_transient_errors_are_retried():
    replies = iter([httpx.Response(503, text="overloaded"),
                    httpx.Response(429, json={"error": {"message": "rate limit reached"}}),
                    httpx.Response(200, json=_completion("ok"))])
    calls = []

    def handler(req):
        calls.append(req)
        return next(replies)

    assert make_llm(handler).chat([{"role": "user", "content": "x"}]) == "ok"
    assert len(calls) == 3


def test_network_error_retried_then_reported():
    calls = []

    def handler(req):
        calls.append(req)
        raise httpx.ConnectError("нет сети")

    with pytest.raises(LLMError) as e:
        make_llm(handler).chat([{"role": "user", "content": "x"}])
    assert e.value.kind == "network" and len(calls) == 3


@pytest.mark.parametrize("status,body", [(402, {"error": {"message": "insufficient balance"}}),
                                         (401, {"error": {"message": "invalid key"}}),
                                         (429, {"error": {"message": "You exceeded your current quota"}})])
def test_permanent_errors_not_retried(status, body):
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(status, json=body)

    with pytest.raises(LLMError):
        make_llm(handler).chat([{"role": "user", "content": "x"}])
    assert len(calls) == 1


def test_subscription_expired_message():
    llm = make_llm(lambda req: httpx.Response(403, json={"error": {"message": "subscription expired: 2026-12-12T00:00:00.000Z"}}))
    with pytest.raises(LLMError) as e:
        llm.chat([{"role": "user", "content": "x"}])
    assert e.value.kind == "access"
    assert "12.12.2026" in e.value.message_ru and "администратор" in e.value.message_ru


def test_no_key_assigned_message():
    llm = make_llm(lambda req: httpx.Response(503, json={"error": {"message": "no key assigned"}}))
    with pytest.raises(LLMError) as e:
        llm.chat([{"role": "user", "content": "x"}])
    assert e.value.kind == "access" and "ещё не выдал" in e.value.message_ru


def test_with_headers_sends_lab():
    seen = []

    def handler(req):
        seen.append(req.headers.get("x-forlabs-lab"))
        return httpx.Response(200, json=_completion("ok"))

    base = make_llm(handler)
    tagged = base.with_headers({"X-ForLabs-Lab": "ivanov%2Fssp%2F4"})
    assert tagged.chat([{"role": "user", "content": "x"}]) == "ok"
    base.chat([{"role": "user", "content": "x"}])
    assert seen == ["ivanov%2Fssp%2F4", None]
    assert base.usage.requests == 2  # общий счётчик расхода


def test_subscription_expired_date_is_minsk():
    # 22:30 UTC 15.11 — это уже 16.11 по Минску, как в шапке приложения
    llm = make_llm(lambda req: httpx.Response(403, json={"error": {"message": "subscription expired: 2026-11-15T22:30:00.000Z"}}))
    with pytest.raises(LLMError) as e:
        llm.chat([{"role": "user", "content": "x"}])
    assert "16.11.2026" in e.value.message_ru


def test_not_approved_points_to_admin_section():
    llm = make_llm(lambda req: httpx.Response(403, json={"error": {"message": "account not approved"}}))
    with pytest.raises(LLMError) as e:
        llm.chat([{"role": "user", "content": "x"}])
    assert "«Админ»" in e.value.message_ru and "Пользователи" not in e.value.message_ru


@pytest.mark.parametrize("message,needle", [("member key rejected: 401", "не принят"),
                                            ("member key balance: 402", "закончился баланс")])
def test_own_key_problems_point_to_admin(message, needle):
    llm = make_llm(lambda req: httpx.Response(402, json={"error": {"message": message}}))
    with pytest.raises(LLMError) as e:
        llm.chat([{"role": "user", "content": "x"}])
    assert e.value.kind == "access" and needle in e.value.message_ru and "администратор" in e.value.message_ru
