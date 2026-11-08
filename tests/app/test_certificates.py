"""Сеть колледжа или антивирус проверяет HTTPS: понятное сообщение и системные сертификаты Windows."""

import httpx
import pytest

from forlabs.cloud.auth import CloudAuth, CloudError
from forlabs.cloud.config import CloudConfig
from forlabs.core.llm import LLM, LLMError
from forlabs.core.settings import Settings

SSL_ERR = "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate"


def handler(req):
    raise httpx.ConnectError(SSL_ERR)


def test_cloud_certificate_problem_is_explained(tmp_home):
    auth = CloudAuth(CloudConfig(url="https://p.supabase.test", publishable_key="k"),
                     http=httpx.Client(transport=httpx.MockTransport(handler)), use_keyring=False)
    with pytest.raises(CloudError) as e:
        auth.sign_in("a@b.c", "x")
    assert "сертификат" in e.value.message_ru


def test_model_certificate_problem_is_explained():
    llm = LLM(Settings(base_url="https://example.test/v1"), api_key="k",
              http_client=httpx.Client(transport=httpx.MockTransport(handler)), retry_delays=(0, 0))
    with pytest.raises(LLMError) as e:
        llm.chat([{"role": "user", "content": "x"}])
    assert "сертификат" in e.value.message_ru


def test_system_certificates_are_used():
    import truststore

    from forlabs.core.netcert import use_system_certificates

    try:
        assert use_system_certificates() is True
    finally:
        truststore.extract_from_ssl()  # не меняем SSL остальным тестам
