"""
chestny.services.certificates — диагностика локальных сертификатов.

Тонкая обёртка над app.cz_api.list_certificates.
Санитизирует исключения: не пропускает пути/COM stack в ответ.
Не вызывает _sign_data/get_uuid_token/requests напрямую.
"""

from __future__ import annotations

import unicodedata
from typing import Any

from app.chestny.validation import normalize_thumbprint


def _strip_controls(value: str) -> str:
    """Удаляет все Unicode control-символы (категория C)."""
    return "".join(c for c in value if unicodedata.category(c)[0] != "C")


def _sanitize_str(value: Any, max_len: int = 500) -> str:
    """Приводит к строке, None/не-string → ''; удаляет control-символы; обрезает."""
    if not isinstance(value, str):
        return ""
    return _strip_controls(value)[:max_len]


def list_local_certificates() -> list[dict[str, Any]]:
    """
    Возвращает список локальных сертификатов с безопасными полями.

    Каждый сертификат: thumbprint, subject, issuer, has_private_key, store.
    Санитизирует каждую запись: только dict, нормализация thumbprint,
    subject/issuer/store — строки без управляющих символов, с ограничением длины.
    Некорректные записи пропускаются.
    """
    try:
        from app.cz_api import list_certificates
        raw = list_certificates()
    except Exception:
        raise CertificateBackendError(
            "Не удалось получить список сертификатов"
        ) from None

    if not isinstance(raw, (list, tuple)):
        raise CertificateBackendError(
            "Не удалось получить список сертификатов"
        ) from None

    safe: list[dict[str, Any]] = []
    for c in raw:
        if not isinstance(c, dict):
            continue

        try:
            tp_raw = c.get("thumbprint", "")
            thumbprint = normalize_thumbprint(str(tp_raw))
            if thumbprint is None:
                continue
        except Exception:
            continue

        try:
            subject = _sanitize_str(c.get("subject"), 500)
            issuer = _sanitize_str(c.get("issuer"), 500)
            store = _sanitize_str(c.get("store"), 100)

            hpk = c.get("has_private_key", False)
            has_private_key = isinstance(hpk, bool) and hpk
        except Exception:
            continue

        safe.append({
            "thumbprint": thumbprint,
            "subject": subject,
            "issuer": issuer,
            "has_private_key": has_private_key,
            "store": store,
        })

    return safe


def diagnose_profile_certificate(thumbprint: str | None) -> dict[str, Any]:
    """
    Диагностика сертификата для профиля.

    Возвращает шаги: configured, found, has_private_key.
    При ошибке backend бросает CertificateBackendError.
    """
    result: dict[str, Any] = {
        "configured": bool(thumbprint),
        "found": False,
        "has_private_key": False,
    }

    if not thumbprint:
        return result

    certs = list_local_certificates()

    for c in certs:
        if c["thumbprint"].upper() == thumbprint.upper():
            result["found"] = True
            result["has_private_key"] = c.get("has_private_key", False)
            result["subject"] = c.get("subject", "")
            result["issuer"] = c.get("issuer", "")
            result["store"] = c.get("store", "")
            break

    return result


class CertificateBackendError(Exception):
    """Ошибка backend диагностики сертификатов (безопасное сообщение)."""
    pass
