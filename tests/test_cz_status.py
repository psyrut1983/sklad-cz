"""
Тесты cz_status: read-only статус документа через /doc/list.
Все внешние вызовы замоканы. Никакой реальной сети/подписи.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from app.chestny.services.cz_auth import (
    CzAuthClient,
    CzAuthRegistry,
    CredentialsSnapshot,
    HttpTransport,
    Signer,
    ChallengeError,
    SignatureInvalidError,
    AccessDeniedError,
    TokenParseError,
    RateLimitError,
    ServerError,
    TimeoutError,
    NetworkError,
    SigningError,
    UnauthorizedError,
    PRODUCTION_API_BASE_URL,
)
from app.chestny.services.cz_status import (
    CzStatusClient,
    CzStatusError,
    DocumentStatus,
    DocumentStatusResult,
)


# ═════════════════════════════════════════════════════════════════════════════
#  Mocks (reused from test_cz_auth, adapted for status)
# ═════════════════════════════════════════════════════════════════════════════


class FakeTransport(HttpTransport):
    """Mock transport для статус-запросов."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.get_response: Any = {"results": [{"status": "DONE"}]}
        self.get_status: int = 200
        self.raise_on_get: Exception | None = None
        self.call_count: int = 0

    def get_json(self, url: str, headers: dict[str, str] | None = None,
                 timeout: float = 15) -> Any:
        self.call_count += 1
        self.requests.append({
            "method": "GET", "url": url, "headers": headers, "timeout": timeout,
        })
        if self.raise_on_get is not None:
            raise self.raise_on_get
        if self.get_status != 200:
            raise _status_error(self.get_status)
        return self.get_response

    def post_json(self, url: str, payload: dict[str, Any],
                  headers: dict[str, str] | None = None,
                  timeout: float = 15) -> Any:
        raise NotImplementedError("Status client does not POST")


class AuthFakeTransport(HttpTransport):
    """Mock transport для auth-запросов (возвращает challenge/token)."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.challenge_response: Any = {
            "uuid": "test-uuid-12345",
            "data": "challenge-data-to-sign",
        }
        self.token_response: Any = {"token": "test-token-abc"}
        self.get_status: int = 200
        self.post_status: int = 200
        self.raise_on_get: Exception | None = None
        self.raise_on_post: Exception | None = None
        self.call_count: int = 0

    def get_json(self, url: str, headers: dict[str, str] | None = None,
                 timeout: float = 15) -> Any:
        self.call_count += 1
        self.requests.append({"method": "GET", "url": url, "headers": headers})
        if self.raise_on_get is not None:
            raise self.raise_on_get
        if self.get_status != 200:
            raise _status_error(self.get_status)
        return self.challenge_response

    def post_json(self, url: str, payload: dict[str, Any],
                  headers: dict[str, str] | None = None,
                  timeout: float = 15) -> Any:
        self.call_count += 1
        self.requests.append({"method": "POST", "url": url, "payload": payload})
        if self.raise_on_post is not None:
            raise self.raise_on_post
        if self.post_status != 200:
            raise _status_error(self.post_status)
        return self.token_response


def _status_error(status: int) -> Exception:
    if status == 429:
        return RateLimitError("Превышен лимит запросов к ЧЗ")
    if status >= 500:
        return ServerError("Ошибка сервера ЧЗ")
    if status == 403:
        return AccessDeniedError("Доступ запрещён")
    if status == 401:
        return UnauthorizedError("Токен недействителен")
    return RuntimeError(f"Unexpected status {status}")


class FakeSigner(Signer):
    """Mock signer."""
    def sign(self, data: str, thumbprint: str) -> str:
        return f"signed:{data}"


# ═════════════════════════════════════════════════════════════════════════════
#  Helpers
# ═════════════════════════════════════════════════════════════════════════════


def make_creds(profile_id: str = "p1", inn: str = "770123456789",
               thumbprint: str = "AABBCCDDEEFF00112233445566778899AABBCCDD",
               api_base: str = PRODUCTION_API_BASE_URL) -> CredentialsSnapshot:
    return CredentialsSnapshot(
        profile_id=profile_id,
        inn=inn,
        certificate_thumbprint=thumbprint,
        api_base_url=api_base,
    )


def make_auth_client(transport: HttpTransport | None = None,
                     signer: Signer | None = None) -> CzAuthClient:
    """Создаёт CzAuthClient с транспортом, возвращающим challenge."""
    if transport is None:
        transport = AuthFakeTransport()
    return CzAuthClient(
        credentials=make_creds(),
        transport=transport,
        signer=signer or FakeSigner(),
    )


def make_status_client(transport: HttpTransport | None = None,
                       auth_client: CzAuthClient | None = None) -> CzStatusClient:
    return CzStatusClient(
        auth_client=auth_client or make_auth_client(),
        transport=transport or FakeTransport(),
    )


# ═════════════════════════════════════════════════════════════════════════════
#  Request contract
# ═════════════════════════════════════════════════════════════════════════════


class TestRequestContract:
    """Проверка URL, параметров, заголовков запроса."""

    def test_url_and_params(self) -> None:
        transport = FakeTransport()
        client = make_status_client(transport=transport)

        client.check("DOC-001")

        assert len(transport.requests) == 1
        req = transport.requests[0]
        assert req["method"] == "GET"
        assert "doc/list" in req["url"]
        assert "pg=lp" in req["url"]
        assert "number=DOC-001" in req["url"]
        assert "documentStatus=" in req["url"]

    def test_authorization_header(self) -> None:
        transport = FakeTransport()
        client = make_status_client(transport=transport)

        client.check("DOC-001")

        auth = transport.requests[0]["headers"].get("Authorization", "")
        assert auth.startswith("Bearer ")
        assert len(auth) > 20  # non-empty token

    def test_accept_header(self) -> None:
        transport = FakeTransport()
        client = make_status_client(transport=transport)

        client.check("DOC-001")

        assert transport.requests[0]["headers"].get("accept") == "application/json"

    def test_timeout_30(self) -> None:
        transport = FakeTransport()
        client = make_status_client(transport=transport)

        client.check("DOC-001")

        assert transport.requests[0]["timeout"] == 30

    def test_url_encoding(self) -> None:
        transport = FakeTransport()
        client = make_status_client(transport=transport)

        client.check("DOC with spaces")

        # urllib.parse.urlencode uses + for spaces
        assert "DOC+with+spaces" in transport.requests[0]["url"]
        assert " " not in transport.requests[0]["url"]


# ═════════════════════════════════════════════════════════════════════════════
#  Status classification
# ═════════════════════════════════════════════════════════════════════════════


class TestClassification:
    """Маппинг сырых статусов в DocumentStatus."""

    @pytest.mark.parametrize("raw,expected", [
        ("DONE", DocumentStatus.CONFIRMED),
        ("COMPLETED", DocumentStatus.CONFIRMED),
        ("PROCESSED", DocumentStatus.CONFIRMED),
        ("done", DocumentStatus.CONFIRMED),
        ("Done", DocumentStatus.CONFIRMED),
        ("REJECTED", DocumentStatus.FAILED),
        ("FAILED", DocumentStatus.FAILED),
        ("ERROR", DocumentStatus.FAILED),
        ("rejected", DocumentStatus.FAILED),
        ("IN_PROCESSING", DocumentStatus.PENDING),
        ("PROCESSING", DocumentStatus.PENDING),
        ("WAITING", DocumentStatus.PENDING),
        ("SIGNING", DocumentStatus.PENDING),
        ("SENT", DocumentStatus.PENDING),
        ("ACCEPTED", DocumentStatus.PENDING),
        ("CHECKING", DocumentStatus.PENDING),
        ("IN_PROGRESS", DocumentStatus.PENDING),
        ("PENDING", DocumentStatus.PENDING),
        ("IN_WORK", DocumentStatus.PENDING),
        ("SOME_UNKNOWN_STATUS", DocumentStatus.UNKNOWN),
        ("", DocumentStatus.UNKNOWN),
        (None, DocumentStatus.UNKNOWN),
        (123, DocumentStatus.UNKNOWN),
    ])
    def test_classification(self, raw: Any, expected: DocumentStatus) -> None:
        transport = FakeTransport()
        transport.get_response = {"results": [{"status": raw}]}
        client = make_status_client(transport=transport)

        result = client.check("DOC-001")

        assert result.status == expected


# ═════════════════════════════════════════════════════════════════════════════
#  Found / Not found / Malformed
# ═════════════════════════════════════════════════════════════════════════════


class TestResponseParsing:
    """Парсинг ответа /doc/list."""

    def test_found(self) -> None:
        transport = FakeTransport()
        transport.get_response = {
            "results": [{"status": "DONE", "errors": [], "elimination_reason": None}],
        }
        client = make_status_client(transport=transport)

        result = client.check("DOC-001")

        assert result.status == DocumentStatus.CONFIRMED
        assert result.errors == ()
        assert result.elimination_reason is None

    def test_not_found_empty_results(self) -> None:
        transport = FakeTransport()
        transport.get_response = {"results": []}
        client = make_status_client(transport=transport)

        result = client.check("DOC-001")

        assert result.status == DocumentStatus.NOT_FOUND

    def test_not_found_missing_results(self) -> None:
        transport = FakeTransport()
        transport.get_response = {}
        client = make_status_client(transport=transport)

        with pytest.raises(CzStatusError, match="results"):
            client.check("DOC-001")

    def test_results_not_list(self) -> None:
        transport = FakeTransport()
        transport.get_response = {"results": "not-a-list"}
        client = make_status_client(transport=transport)

        with pytest.raises(CzStatusError, match="results"):
            client.check("DOC-001")

    def test_element_not_dict(self) -> None:
        transport = FakeTransport()
        transport.get_response = {"results": ["not-a-dict"]}
        client = make_status_client(transport=transport)

        with pytest.raises(CzStatusError, match="results"):
            client.check("DOC-001")

    def test_response_not_dict(self) -> None:
        transport = FakeTransport()
        transport.get_response = ["not-a-dict"]
        client = make_status_client(transport=transport)

        with pytest.raises(CzStatusError, match="JSON-объектом"):
            client.check("DOC-001")


# ═════════════════════════════════════════════════════════════════════════════
#  401 retry logic
# ═════════════════════════════════════════════════════════════════════════════


class TestRetryOn401:
    """401 → reset_token + retry ровно один раз."""

    def test_retry_succeeds_with_new_token(self) -> None:
        # Status transport: first call 401, second call 200
        class RetryTransport(FakeTransport):
            def get_json(self, url, headers=None, timeout=15):
                self.call_count += 1
                self.requests.append({"method": "GET", "url": url, "headers": headers, "timeout": timeout})
                if self.call_count == 1:
                    raise UnauthorizedError("Токен недействителен")
                return self.get_response

        transport = RetryTransport()
        auth = make_auth_client()  # AuthFakeTransport handles challenge/token
        client = make_status_client(transport=transport, auth_client=auth)

        result = client.check("DOC-001")

        assert result.status == DocumentStatus.CONFIRMED
        assert transport.call_count == 2  # original + retry
        assert auth._transport.call_count == 4  # GET challenge(×2) + POST sign-in(×2)

    def test_second_401_raises_status_error(self) -> None:
        # Status transport: both calls 401
        class Double401Transport(FakeTransport):
            def get_json(self, url, headers=None, timeout=15):
                self.call_count += 1
                self.requests.append({"method": "GET", "url": url, "headers": headers, "timeout": timeout})
                raise UnauthorizedError("Токен недействителен")

        transport = Double401Transport()
        auth = make_auth_client()
        client = make_status_client(transport=transport, auth_client=auth)

        with pytest.raises(CzStatusError, match="обновления токена"):
            client.check("DOC-001")

        assert transport.call_count == 2  # original + retry
        assert auth._transport.call_count == 4  # GET challenge(×2) + POST sign-in(×2)

    def test_non_401_no_retry(self) -> None:
        """403 не вызывает retry."""
        class NoRetryTransport(FakeTransport):
            def get_json(self, url, headers=None, timeout=15):
                self.call_count += 1
                raise AccessDeniedError("Доступ запрещён")

        transport = NoRetryTransport()
        client = make_status_client(transport=transport)

        with pytest.raises(AccessDeniedError):
            client.check("DOC-001")

        assert transport.call_count == 1  # no retry


# ═════════════════════════════════════════════════════════════════════════════
#  Other errors — no retry
# ═════════════════════════════════════════════════════════════════════════════


class TestNoRetry:
    """403, 429, 5xx, timeout, network — без повторных попыток."""

    @pytest.mark.parametrize("status,exc_cls", [
        (403, AccessDeniedError),
        (429, RateLimitError),
        (500, ServerError),
        (503, ServerError),
    ])
    def test_error_no_retry(self, status: int, exc_cls: type) -> None:
        transport = FakeTransport()
        client = make_status_client(transport=transport)

        transport.get_status = status

        with pytest.raises(exc_cls):
            client.check("DOC-001")

        assert transport.call_count == 1  # no retry

    def test_timeout_no_retry(self) -> None:
        transport = FakeTransport()
        client = make_status_client(transport=transport)

        transport.raise_on_get = TimeoutError("Таймаут")

        with pytest.raises(TimeoutError):
            client.check("DOC-001")

        assert transport.call_count == 1

    def test_network_error_no_retry(self) -> None:
        transport = FakeTransport()
        client = make_status_client(transport=transport)

        transport.raise_on_get = NetworkError("Сетевая ошибка")

        with pytest.raises(NetworkError):
            client.check("DOC-001")

        assert transport.call_count == 1


# ═════════════════════════════════════════════════════════════════════════════
#  Errors normalization
# ═════════════════════════════════════════════════════════════════════════════


class TestErrorsNormalization:
    """Нормализация поля errors из ответа."""

    def test_errors_empty_list(self) -> None:
        transport = FakeTransport()
        transport.get_response = {"results": [{"status": "FAILED", "errors": []}]}
        client = make_status_client(transport=transport)

        result = client.check("DOC-001")
        assert result.errors == ()

    def test_errors_string_list(self) -> None:
        transport = FakeTransport()
        transport.get_response = {
            "results": [{"status": "FAILED", "errors": ["err1", "err2"]}],
        }
        client = make_status_client(transport=transport)

        result = client.check("DOC-001")
        assert result.errors == ("err1", "err2")

    def test_errors_dict_list(self) -> None:
        transport = FakeTransport()
        transport.get_response = {
            "results": [{
                "status": "REJECTED",
                "errors": [
                    {"code": "E001", "description": "Bad data"},
                    {"code": "E002", "message": "Invalid format"},
                ],
            }],
        }
        client = make_status_client(transport=transport)

        result = client.check("DOC-001")
        assert "[E001] Bad data" in result.errors
        assert "[E002] Invalid format" in result.errors

    def test_errors_not_list(self) -> None:
        transport = FakeTransport()
        transport.get_response = {
            "results": [{"status": "FAILED", "errors": "not-a-list"}],
        }
        client = make_status_client(transport=transport)

        result = client.check("DOC-001")
        assert result.errors == ()

    def test_errors_missing(self) -> None:
        transport = FakeTransport()
        transport.get_response = {"results": [{"status": "DONE"}]}
        client = make_status_client(transport=transport)

        result = client.check("DOC-001")
        assert result.errors == ()

    def test_errors_truncated(self) -> None:
        transport = FakeTransport()
        long_errors = [f"error-{i}" * 100 for i in range(20)]
        transport.get_response = {"results": [{"status": "FAILED", "errors": long_errors}]}
        client = make_status_client(transport=transport)

        result = client.check("DOC-001")
        assert len(result.errors) == 10  # capped at _MAX_ERRORS


# ═════════════════════════════════════════════════════════════════════════════
#  Document ID validation
# ═════════════════════════════════════════════════════════════════════════════


class TestDocumentIdValidation:
    """Валидация document_id."""

    def test_empty_string_rejected(self) -> None:
        client = make_status_client()
        with pytest.raises(ValueError, match="непустой строкой"):
            client.check("")

    def test_whitespace_rejected(self) -> None:
        client = make_status_client()
        with pytest.raises(ValueError, match="непустой строкой"):
            client.check("   ")

    def test_too_long_rejected(self) -> None:
        client = make_status_client()
        with pytest.raises(ValueError, match="слишком длинный"):
            client.check("X" * 200)

    def test_control_chars_rejected(self) -> None:
        client = make_status_client()
        with pytest.raises(ValueError, match="недопустимые символы"):
            client.check("doc\n001")

    def test_valid_accepted(self) -> None:
        transport = FakeTransport()
        client = make_status_client(transport=transport)

        result = client.check("valid-doc-id-123")
        assert result.status == DocumentStatus.CONFIRMED


# ═════════════════════════════════════════════════════════════════════════════
#  Safety — repr / exceptions
# ═════════════════════════════════════════════════════════════════════════════


class TestSafety:
    """Никаких токенов, document_id, raw errors в repr/exception."""

    def test_result_repr_no_token(self) -> None:
        result = DocumentStatusResult(status=DocumentStatus.CONFIRMED)
        r = repr(result)
        assert "token" not in r
        assert "CONFIRMED" in r

    def test_result_repr_no_document_id(self) -> None:
        result = DocumentStatusResult(status=DocumentStatus.FAILED)
        r = repr(result)
        assert "DOC-001" not in r

    def test_result_repr_no_raw_errors(self) -> None:
        result = DocumentStatusResult(
            status=DocumentStatus.FAILED,
            errors=("err1", "err2"),
        )
        r = repr(result)
        assert "err1" not in r
        assert "err2" not in r

    def test_error_no_token_in_message(self) -> None:
        transport = FakeTransport()
        transport.get_response = {"results": "bad"}
        client = make_status_client(transport=transport)

        with pytest.raises(CzStatusError) as exc:
            client.check("DOC-001")
        msg = str(exc.value)
        assert "Bearer" not in msg
        assert "test-token" not in msg

    def test_error_no_raw_body(self) -> None:
        transport = FakeTransport()
        transport.get_response = {"results": [{"status": "DONE", "secret": "sensitive"}]}
        client = make_status_client(transport=transport)

        result = client.check("DOC-001")
        # The raw response is not returned
        assert hasattr(result, "status")
        # The secret field is not accessible
        assert not hasattr(result, "secret")


# ═════════════════════════════════════════════════════════════════════════════
#  Elimination reason
# ═════════════════════════════════════════════════════════════════════════════


class TestEliminationReason:
    """Поле elimination_reason."""

    def test_present(self) -> None:
        transport = FakeTransport()
        transport.get_response = {
            "results": [{"status": "REJECTED", "elimination_reason": "Bad signature"}],
        }
        client = make_status_client(transport=transport)

        result = client.check("DOC-001")
        assert result.elimination_reason == "Bad signature"

    def test_none(self) -> None:
        transport = FakeTransport()
        transport.get_response = {
            "results": [{"status": "DONE", "elimination_reason": None}],
        }
        client = make_status_client(transport=transport)

        result = client.check("DOC-001")
        assert result.elimination_reason is None

    def test_non_string_ignored(self) -> None:
        transport = FakeTransport()
        transport.get_response = {
            "results": [{"status": "FAILED", "elimination_reason": 123}],
        }
        client = make_status_client(transport=transport)

        result = client.check("DOC-001")
        assert result.elimination_reason is None
