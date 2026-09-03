"""
cz_status — read-only статус документа через /doc/list.

Per-profile CzAuthClient + HttpTransport. Не выполняет auth самостоятельно.
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass
from enum import Enum
from typing import Any

from app.chestny.services.cz_auth import (
    CzAuthClient,
    CzAuthError,
    HttpTransport,
    UnauthorizedError,
)


# ═════════════════════════════════════════════════════════════════════════════
#  Document status classification
# ═════════════════════════════════════════════════════════════════════════════


class DocumentStatus(str, Enum):
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    PENDING = "PENDING"
    UNKNOWN = "UNKNOWN"
    NOT_FOUND = "NOT_FOUND"


_STATUS_FINAL: dict[str, DocumentStatus] = {
    "DONE": DocumentStatus.CONFIRMED,
    "COMPLETED": DocumentStatus.CONFIRMED,
    "PROCESSED": DocumentStatus.CONFIRMED,
    "REJECTED": DocumentStatus.FAILED,
    "FAILED": DocumentStatus.FAILED,
    "ERROR": DocumentStatus.FAILED,
}

_PENDING_STATUSES: frozenset[str] = frozenset({
    "IN_PROCESSING", "PROCESSING", "WAITING", "SIGNING",
    "SENT", "ACCEPTED", "CHECKING", "IN_PROGRESS", "PENDING", "IN_WORK",
})


def _classify_status(raw: Any) -> DocumentStatus:
    if not isinstance(raw, str) or not raw.strip():
        return DocumentStatus.UNKNOWN
    upper = raw.strip().upper()
    if upper in _STATUS_FINAL:
        return _STATUS_FINAL[upper]
    if upper in _PENDING_STATUSES:
        return DocumentStatus.PENDING
    return DocumentStatus.UNKNOWN


# ═════════════════════════════════════════════════════════════════════════════
#  Error normalisation
# ═════════════════════════════════════════════════════════════════════════════

_MAX_ERRORS = 10
_MAX_ERROR_STR_LEN = 200


def _normalize_errors(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    result: list[str] = []
    for item in raw:
        if isinstance(item, str):
            result.append(item[:_MAX_ERROR_STR_LEN])
        elif isinstance(item, dict):
            code = item.get("code", "")
            desc = item.get("description", "") or item.get("message", "")
            if isinstance(code, str) and isinstance(desc, str):
                safe_code = code[:_MAX_ERROR_STR_LEN]
                safe_desc = desc[:_MAX_ERROR_STR_LEN]
                result.append(f"[{safe_code}] {safe_desc}" if safe_desc else safe_code)
            elif isinstance(code, str):
                result.append(code[:_MAX_ERROR_STR_LEN])
        if len(result) >= _MAX_ERRORS:
            break
    return tuple(result)


# ═════════════════════════════════════════════════════════════════════════════
#  DTO
# ═════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class DocumentStatusResult:
    """Безопасный результат проверки статуса документа.

    Никаких raw response, токенов или необработанных ошибок.
    """

    status: DocumentStatus
    errors: tuple[str, ...] = ()
    elimination_reason: str | None = None

    def __repr__(self) -> str:
        return f"DocumentStatusResult(status={self.status.value})"


# ═════════════════════════════════════════════════════════════════════════════
#  Exceptions
# ═════════════════════════════════════════════════════════════════════════════


class CzStatusError(CzAuthError):
    """Ошибка проверки статуса документа."""
    pass


# ═════════════════════════════════════════════════════════════════════════════
#  Client
# ═════════════════════════════════════════════════════════════════════════════

_MAX_DOC_ID_LEN = 128


class CzStatusClient:
    """Read-only клиент статуса документа.

    Использует переданный CzAuthClient для получения токена.
    При 401 сбрасывает токен, получает новый и повторяет ровно один раз.
    """

    def __init__(
        self,
        auth_client: CzAuthClient,
        transport: HttpTransport,
    ) -> None:
        self._auth = auth_client
        self._transport = transport

    def check(self, document_id: str) -> DocumentStatusResult:
        """Проверяет статус документа по /doc/list.

        Args:
            document_id: Идентификатор документа (непустая строка).

        Returns:
            DocumentStatusResult с классифицированным статусом.

        Raises:
            ValueError: Невалидный document_id.
            CzStatusError: Ошибка API или парсинга ответа.
            UnauthorizedError: Доступ запрещён после обновления токена.
            Другие CzAuthError: 403, 429, 5xx, timeout, network.
        """
        self._validate_document_id(document_id)

        base = self._auth.credentials.api_base_url.rstrip("/")
        params = urllib.parse.urlencode({
            "pg": "lp",
            "number": document_id,
            "documentStatus": "",
        })
        url = f"{base}/doc/list?{params}"
        headers = {
            "accept": "application/json",
            "Authorization": f"Bearer {self._auth.get_token()}",
        }

        try:
            resp = self._transport.get_json(url, headers=headers, timeout=30)
        except UnauthorizedError:
            # 401: refresh token and retry once
            self._auth.reset_token()
            headers["Authorization"] = f"Bearer {self._auth.get_token()}"
            try:
                resp = self._transport.get_json(url, headers=headers, timeout=30)
            except UnauthorizedError:
                raise CzStatusError(
                    "Доступ запрещён после обновления токена"
                ) from None
        except CzAuthError:
            raise

        return self._parse_response(resp)

    @staticmethod
    def _validate_document_id(doc_id: str) -> None:
        if not isinstance(doc_id, str) or not doc_id.strip():
            raise ValueError("document_id должен быть непустой строкой.")
        if len(doc_id) > _MAX_DOC_ID_LEN:
            raise ValueError(f"document_id слишком длинный (макс. {_MAX_DOC_ID_LEN}).")
        if any(ord(c) < 32 for c in doc_id):
            raise ValueError("document_id содержит недопустимые символы (control chars).")

    @staticmethod
    def _parse_response(resp: Any) -> DocumentStatusResult:
        if not isinstance(resp, dict):
            raise CzStatusError("Ответ /doc/list не является JSON-объектом")

        results = resp.get("results")
        if not isinstance(results, list):
            raise CzStatusError("Поле results должно быть списком")

        if not results:
            return DocumentStatusResult(status=DocumentStatus.NOT_FOUND)

        first = results[0]
        if not isinstance(first, dict):
            raise CzStatusError("Элемент results должен быть объектом")

        raw_status = first.get("status", "")
        status = _classify_status(raw_status)

        raw_errors = first.get("errors")
        errors = _normalize_errors(raw_errors)

        elimination_reason = first.get("elimination_reason")
        if elimination_reason is not None and not isinstance(elimination_reason, str):
            elimination_reason = None

        return DocumentStatusResult(
            status=status,
            errors=errors,
            elimination_reason=elimination_reason,
        )
