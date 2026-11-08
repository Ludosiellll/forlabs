"""Сети с проверкой HTTPS (колледж, антивирус): системные сертификаты Windows и понятное сообщение."""

from __future__ import annotations

CERT_MESSAGE = ("Защищённое соединение не прошло проверку: сеть (например, колледжа) или антивирус подменяет "
                "сертификаты. Попробуй другую сеть или добавь ForLabs в исключения антивируса.")
OFFLINE_MESSAGE = "Нет связи с облаком. Проверь интернет."


def is_cert_error(e: BaseException) -> bool:
    text = str(e).lower()
    return "certificate_verify_failed" in text or "certificate verify failed" in text


def offline_message(e: BaseException) -> str:
    return CERT_MESSAGE if is_cert_error(e) else OFFLINE_MESSAGE


def use_system_certificates() -> bool:
    """Доверять сертификатам из хранилища Windows (туда антивирус и сеть колледжа добавляют свои)."""
    try:
        import truststore

        truststore.inject_into_ssl()
        return True
    except Exception:
        return False
