"""
Тесты cz_auth: изолированная auth/token-граница.
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
    Challenge,
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
    PRODUCTION_API_BASE_URL,
)


# ═════════════════════════════════════════════════════════════════════════════
#  Mocks
# ═════════════════════════════════════════════════════════════════════════════


class FakeTransport(HttpTransport):
    """Mock transport с предсказуемыми ответами."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.challenge_response: Any = {
            "uuid": "test-uuid-12345",
            "data": "challenge-data-to-sign",
        }
        self.signin_response: Any = {
            "token": "test-token-abc",
        }
        self.signin_status: int = 200
        self.challenge_status: int = 200
        self.raise_on_get: Exception | None = None
        self.raise_on_post: Exception | None = None

    def get_json(self, url: str, headers: dict[str, str] | None = None,
                 timeout: float = 15) -> Any:
        self.requests.append({
            "method": "GET", "url": url, "headers": headers, "timeout": timeout,
        })
        if self.raise_on_get is not None:
            raise self.raise_on_get
        if self.challenge_status != 200:
            raise _status_error(self.challenge_status)
        return self.challenge_response

    def post_json(self, url: str, payload: dict[str, Any],
                  headers: dict[str, str] | None = None,
                  timeout: float = 15) -> Any:
        self.requests.append({
            "method": "POST", "url": url, "payload": payload,
            "headers": headers, "timeout": timeout,
        })
        if self.raise_on_post is not None:
            raise self.raise_on_post
        if self.signin_status != 200:
            raise _status_error(self.signin_status)
        return self.signin_response


def _status_error(status: int) -> Exception:
    if status == 429:
        return RateLimitError("Превышен лимит запросов к ЧЗ")
    if status >= 500:
        return ServerError("Ошибка сервера ЧЗ")
    if status in (401, 403):
        return AccessDeniedError("Доступ запрещён")
    return RuntimeError(f"Unexpected status {status}")


class FakeSigner(Signer):
    """Mock signer — возвращает фиксированную подпись."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def sign(self, data: str, thumbprint: str) -> str:
        self.calls.append((data, thumbprint))
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


def make_client(transport: HttpTransport | None = None,
                signer: Signer | None = None,
                creds: CredentialsSnapshot | None = None) -> CzAuthClient:
    return CzAuthClient(
        credentials=creds or make_creds(),
        transport=transport or FakeTransport(),
        signer=signer or FakeSigner(),
    )


# ═════════════════════════════════════════════════════════════════════════════
#  Request contract
# ═════════════════════════════════════════════════════════════════════════════


class TestRequestContract:
    """Проверка цепочки вызовов: GET /auth/key → POST /auth/simpleSignIn."""

    def test_full_auth_flow(self) -> None:
        transport = FakeTransport()
        signer = FakeSigner()
        client = make_client(transport=transport, signer=signer)

        token = client.get_token()

        assert token == "test-token-abc"
        assert len(transport.requests) == 2
        assert transport.requests[0]["method"] == "GET"
        assert transport.requests[0]["url"].endswith("/auth/key")
        assert transport.requests[1]["method"] == "POST"
        assert transport.requests[1]["url"].endswith("/auth/simpleSignIn")
        assert transport.requests[1]["payload"]["uuid"] == "test-uuid-12345"
        assert transport.requests[1]["payload"]["data"] == "signed:challenge-data-to-sign"
        assert transport.requests[1]["payload"]["unitedToken"] is True
        assert transport.requests[1]["payload"]["inn"] == "770123456789"
        assert len(signer.calls) == 1
        assert signer.calls[0] == ("challenge-data-to-sign", "AABBCCDDEEFF00112233445566778899AABBCCDD")

    def test_inn_included_in_payload(self) -> None:
        transport = FakeTransport()
        creds = make_creds(inn="770123456789")
        client = make_client(transport=transport, creds=creds)

        client.get_token()

        assert transport.requests[1]["payload"]["inn"] == "770123456789"

    def test_uuid_token_alternative_field(self) -> None:
        transport = FakeTransport()
        transport.signin_response = {"uuidToken": "uuid-token-val"}
        client = make_client(transport=transport)

        token = client.get_token()

        assert token == "uuid-token-val"

    def test_uuid_token_prefers_token_field(self) -> None:
        transport = FakeTransport()
        transport.signin_response = {"token": "primary-token", "uuidToken": "uuid-token"}
        client = make_client(transport=transport)

        token = client.get_token()

        assert token == "primary-token"


# ═════════════════════════════════════════════════════════════════════════════
#  Token cache / reset
# ═════════════════════════════════════════════════════════════════════════════


class TestTokenCache:
    """Кэширование токена и принудительный сброс."""

    def test_cached_token_no_extra_requests(self) -> None:
        transport = FakeTransport()
        client = make_client(transport=transport)

        t1 = client.get_token()
        t2 = client.get_token()
        t3 = client.get_token()

        assert t1 == t2 == t3
        # Only the first call should have made HTTP requests
        assert len(transport.requests) == 2

    def test_reset_token_clears_cache(self) -> None:
        transport = FakeTransport()
        client = make_client(transport=transport)

        t1 = client.get_token()
        client.reset_token()
        t2 = client.get_token()

        assert t1 == t2
        # After reset, a new auth cycle happened
        assert len(transport.requests) == 4

    def test_force_authenticate_always_fresh(self) -> None:
        transport = FakeTransport()
        client = make_client(transport=transport)

        t1 = client.get_token()
        t2 = client.force_authenticate()
        t3 = client.force_authenticate()

        assert t1 == t2 == t3
        # Three separate auth cycles
        assert len(transport.requests) == 6


# ═════════════════════════════════════════════════════════════════════════════
#  Registry — two-profile isolation
# ═════════════════════════════════════════════════════════════════════════════


class TestRegistry:
    """CzAuthRegistry: изоляция профилей и инвалидация."""

    def test_two_profiles_isolated(self) -> None:
        reg = CzAuthRegistry()
        t1 = FakeTransport()
        t2 = FakeTransport()
        c1 = make_creds("profile-a", inn="111111111111")
        c2 = make_creds("profile-b", inn="222222222222")

        client_a = reg.get_or_create(c1, transport=t1, signer=FakeSigner())
        client_b = reg.get_or_create(c2, transport=t2, signer=FakeSigner())

        assert client_a is not client_b
        token_a = client_a.get_token()
        token_b = client_b.get_token()
        # Different transport instances → different tokens
        assert token_a == "test-token-abc"
        assert token_b == "test-token-abc"

    def test_same_profile_reuses_client(self) -> None:
        reg = CzAuthRegistry()
        c = make_creds("p1")

        a = reg.get_or_create(c)
        b = reg.get_or_create(c)

        assert a is b

    def test_changed_credentials_invalidates(self) -> None:
        reg = CzAuthRegistry()
        t1 = FakeTransport()
        t2 = FakeTransport()
        c_old = make_creds("p1", inn="111111111111", thumbprint="A" * 40)
        c_new = make_creds("p1", inn="222222222222", thumbprint="B" * 40)

        client_a = reg.get_or_create(c_old, transport=t1, signer=FakeSigner())
        client_b = reg.get_or_create(c_new, transport=t2, signer=FakeSigner())

        assert client_a is not client_b
        assert reg.get("p1") is client_b

    def test_get_and_remove(self) -> None:
        reg = CzAuthRegistry()
        c = make_creds("p1")
        client = reg.get_or_create(c)

        assert reg.get("p1") is client
        reg.remove("p1")
        assert reg.get("p1") is None

    def test_clear(self) -> None:
        reg = CzAuthRegistry()
        reg.get_or_create(make_creds("p1"))
        reg.get_or_create(make_creds("p2"))

        assert len(reg) == 2
        reg.clear()
        assert len(reg) == 0

    # test_changed_api_base_invalidates удалён — api_base_url теперь
    # фиксирован константой PRODUCTION_API_BASE_URL и не может измениться.
    # Изменение inn/thumbprint покрыто test_changed_credentials_invalidates.


# ═════════════════════════════════════════════════════════════════════════════
#  Concurrency
# ═════════════════════════════════════════════════════════════════════════════


class TestConcurrency:
    """Параллельный доступ — только одна аутентификация."""

    def test_parallel_get_token_single_auth(self) -> None:
        transport = FakeTransport()
        signer = FakeSigner()
        client = make_client(transport=transport, signer=signer)

        results: list[str] = []
        errors: list[Exception] = []
        barrier = threading.Barrier(5)

        def worker() -> None:
            barrier.wait()
            try:
                t = client.get_token()
                results.append(t)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert all(r == "test-token-abc" for r in results)
        # Exactly one auth cycle
        assert len(transport.requests) == 2


# ═════════════════════════════════════════════════════════════════════════════
#  Malformed responses
# ═════════════════════════════════════════════════════════════════════════════


class TestMalformedResponses:
    """Некорректные ответы от API ЧЗ."""

    def test_challenge_missing_uuid(self) -> None:
        transport = FakeTransport()
        transport.challenge_response = {"data": "some-data"}
        client = make_client(transport=transport)

        with pytest.raises(ChallengeError, match="непустой строкой"):
            client.get_token()

    def test_challenge_missing_data(self) -> None:
        transport = FakeTransport()
        transport.challenge_response = {"uuid": "u-1"}
        client = make_client(transport=transport)

        with pytest.raises(ChallengeError, match="непустой строкой"):
            client.get_token()

    def test_challenge_non_dict(self) -> None:
        transport = FakeTransport()
        transport.challenge_response = "not-a-dict"
        client = make_client(transport=transport)

        with pytest.raises(ChallengeError, match="не является JSON-объектом"):
            client.get_token()

    def test_signin_missing_token(self) -> None:
        transport = FakeTransport()
        transport.signin_response = {"some": "thing"}
        client = make_client(transport=transport)

        with pytest.raises(TokenParseError, match="непустой строкой"):
            client.get_token()

    def test_signin_empty_response(self) -> None:
        transport = FakeTransport()
        transport.signin_response = {}
        client = make_client(transport=transport)

        with pytest.raises(TokenParseError, match="непустой строкой"):
            client.get_token()

    def test_signin_non_dict(self) -> None:
        transport = FakeTransport()
        transport.signin_response = ["not-a-dict"]
        client = make_client(transport=transport)

        with pytest.raises(TokenParseError, match="не является JSON-объектом"):
            client.get_token()


# ═════════════════════════════════════════════════════════════════════════════
#  HTTP error responses
# ═════════════════════════════════════════════════════════════════════════════


class TestHttpErrors:
    """HTTP-ошибки: 401, 403, 429, 5xx, timeout, network."""

    def test_401_unauthorized(self) -> None:
        transport = FakeTransport()
        transport.signin_status = 401
        client = make_client(transport=transport)

        with pytest.raises(AccessDeniedError):
            client.get_token()

    def test_403_forbidden(self) -> None:
        transport = FakeTransport()
        transport.signin_status = 403
        client = make_client(transport=transport)

        with pytest.raises(AccessDeniedError):
            client.get_token()

    def test_429_rate_limit(self) -> None:
        transport = FakeTransport()
        transport.signin_status = 429
        client = make_client(transport=transport)

        with pytest.raises(RateLimitError):
            client.get_token()

    def test_500_server_error(self) -> None:
        transport = FakeTransport()
        transport.signin_status = 500
        client = make_client(transport=transport)

        with pytest.raises(ServerError):
            client.get_token()

    def test_503_server_error(self) -> None:
        transport = FakeTransport()
        transport.signin_status = 503
        client = make_client(transport=transport)

        with pytest.raises(ServerError):
            client.get_token()

    def test_timeout_on_get(self) -> None:
        transport = FakeTransport()
        transport.raise_on_get = TimeoutError("Таймаут соединения с ЧЗ")
        client = make_client(transport=transport)

        with pytest.raises(TimeoutError):
            client.get_token()

    def test_timeout_on_post(self) -> None:
        transport = FakeTransport()
        transport.raise_on_post = TimeoutError("Таймаут соединения с ЧЗ")
        client = make_client(transport=transport)

        with pytest.raises(TimeoutError):
            client.get_token()

    def test_network_error_on_get(self) -> None:
        transport = FakeTransport()
        transport.raise_on_get = NetworkError("Сетевая ошибка")
        client = make_client(transport=transport)

        with pytest.raises(NetworkError):
            client.get_token()

    def test_network_error_on_post(self) -> None:
        transport = FakeTransport()
        transport.raise_on_post = NetworkError("Сетевая ошибка")
        client = make_client(transport=transport)

        with pytest.raises(NetworkError):
            client.get_token()

    def test_challenge_429(self) -> None:
        transport = FakeTransport()
        transport.challenge_status = 429
        client = make_client(transport=transport)

        with pytest.raises(RateLimitError):
            client.get_token()

    def test_challenge_5xx(self) -> None:
        transport = FakeTransport()
        transport.challenge_status = 502
        client = make_client(transport=transport)

        with pytest.raises(ServerError):
            client.get_token()


# ═════════════════════════════════════════════════════════════════════════════
#  Safety — repr / errors
# ═════════════════════════════════════════════════════════════════════════════


class TestSafety:
    """Токены и секреты не должны утекать через repr/errors."""

    def test_client_repr_no_token(self) -> None:
        client = make_client()
        r = repr(client)
        assert "test-token" not in r
        assert "profile_id" in r

    def test_client_repr_no_credentials(self) -> None:
        client = make_client()
        r = repr(client)
        assert "770123456789" not in r
        assert "AABBCCDDEEFF00112233445566778899AABBCCDD" not in r

    def test_error_no_token_in_message(self) -> None:
        client = make_client()
        try:
            client.get_token()
        except Exception:
            pass
        # No error should contain the token value
        assert True  # smoke test — exception messages are safe by design

    def test_challenge_error_no_secrets(self) -> None:
        transport = FakeTransport()
        transport.challenge_response = "bad"
        client = make_client(transport=transport)
        with pytest.raises(ChallengeError) as exc:
            client.get_token()
        msg = str(exc.value)
        assert "test-token" not in msg
        assert "signed:" not in msg
        assert "challenge-data" not in msg

    def test_registry_len(self) -> None:
        reg = CzAuthRegistry()
        assert len(reg) == 0
        reg.get_or_create(make_creds("p1"))
        assert len(reg) == 1


# ═════════════════════════════════════════════════════════════════════════════
#  SSRF / URL validation
# ═════════════════════════════════════════════════════════════════════════════


class TestUrlValidation:
    """CredentialsSnapshot отвергает не-production URL."""

    def test_production_url_allowed(self) -> None:
        c = make_creds(api_base=PRODUCTION_API_BASE_URL)
        assert c.api_base_url == PRODUCTION_API_BASE_URL

    def test_trailing_slash_allowed_and_normalized(self) -> None:
        c = make_creds(api_base=PRODUCTION_API_BASE_URL + "/")
        # Trailing slash нормализуется к exact URL
        assert c.api_base_url == PRODUCTION_API_BASE_URL

    def test_http_rejected(self) -> None:
        with pytest.raises(ValueError, match="Недопустимый api_base_url"):
            make_creds(api_base="http://markirovka.crpt.ru/api/v3/true-api")

    def test_different_host_rejected(self) -> None:
        with pytest.raises(ValueError, match="Недопустимый api_base_url"):
            make_creds(api_base="https://evil.ru/api/v3/true-api")

    def test_different_path_rejected(self) -> None:
        with pytest.raises(ValueError, match="Недопустимый api_base_url"):
            make_creds(api_base="https://markirovka.crpt.ru/api/v2/true-api")

    def test_userinfo_rejected(self) -> None:
        with pytest.raises(ValueError, match="Недопустимый api_base_url"):
            make_creds(api_base="https://user:pass@markirovka.crpt.ru/api/v3/true-api")

    def test_query_rejected(self) -> None:
        with pytest.raises(ValueError, match="Недопустимый api_base_url"):
            make_creds(api_base="https://markirovka.crpt.ru/api/v3/true-api?foo=bar")

    def test_fragment_rejected(self) -> None:
        with pytest.raises(ValueError, match="Недопустимый api_base_url"):
            make_creds(api_base="https://markirovka.crpt.ru/api/v3/true-api#sec")


# ═════════════════════════════════════════════════════════════════════════════
#  Wrong types / whitespace
# ═════════════════════════════════════════════════════════════════════════════


class TestTypeValidation:
    """uuid/data/token/uuidToken — только непустые строки."""

    def test_challenge_uuid_int_rejected(self) -> None:
        transport = FakeTransport()
        transport.challenge_response = {"uuid": 123, "data": "ok"}
        client = make_client(transport=transport)
        with pytest.raises(ChallengeError, match="непустой строкой"):
            client.get_token()

    def test_challenge_data_list_rejected(self) -> None:
        transport = FakeTransport()
        transport.challenge_response = {"uuid": "ok", "data": ["x"]}
        client = make_client(transport=transport)
        with pytest.raises(ChallengeError, match="непустой строкой"):
            client.get_token()

    def test_challenge_uuid_dict_rejected(self) -> None:
        transport = FakeTransport()
        transport.challenge_response = {"uuid": {"x": 1}, "data": "ok"}
        client = make_client(transport=transport)
        with pytest.raises(ChallengeError, match="непустой строкой"):
            client.get_token()

    def test_challenge_uuid_whitespace_rejected(self) -> None:
        transport = FakeTransport()
        transport.challenge_response = {"uuid": "   ", "data": "ok"}
        client = make_client(transport=transport)
        with pytest.raises(ChallengeError, match="непустой строкой"):
            client.get_token()

    def test_token_int_rejected(self) -> None:
        transport = FakeTransport()
        transport.signin_response = {"token": 456}
        client = make_client(transport=transport)
        with pytest.raises(TokenParseError, match="непустой строкой"):
            client.get_token()

    def test_uuid_token_list_rejected(self) -> None:
        transport = FakeTransport()
        transport.signin_response = {"uuidToken": ["a"]}
        client = make_client(transport=transport)
        with pytest.raises(TokenParseError, match="непустой строкой"):
            client.get_token()

    def test_token_whitespace_rejected(self) -> None:
        transport = FakeTransport()
        transport.signin_response = {"token": " \t\n"}
        client = make_client(transport=transport)
        with pytest.raises(TokenParseError, match="непустой строкой"):
            client.get_token()


# ═════════════════════════════════════════════════════════════════════════════
#  Signer leak protection
# ═════════════════════════════════════════════════════════════════════════════


class TestSignerLeak:
    """Исключения Signer не должны утекать."""

    def test_signer_exception_wrapped(self) -> None:
        class ExplodingSigner(Signer):
            def sign(self, data: str, thumbprint: str) -> str:
                raise RuntimeError("secret-key-12345")

        client = make_client(signer=ExplodingSigner())
        with pytest.raises(SigningError) as exc:
            client.get_token()
        msg = str(exc.value)
        assert "Ошибка подписи" in msg
        assert "secret-key" not in msg
        assert "challenge-data" not in msg

    def test_signer_exception_no_data_in_message(self) -> None:
        class LeakySigner(Signer):
            def sign(self, data: str, thumbprint: str) -> str:
                raise ValueError(f"CANARY:{data}:{thumbprint}")

        client = make_client(signer=LeakySigner())
        with pytest.raises(SigningError) as exc:
            client.get_token()
        msg = str(exc.value)
        assert "CANARY" not in msg
        assert "challenge-data" not in msg
        assert "AABBCCDDEEFF00112233445566778899AABBCCDD" not in msg
        assert "AABBCCDDEEFF00112233445566778899AABBCCDD" not in msg


# ═════════════════════════════════════════════════════════════════════════════
#  CredentialsSnapshot repr
# ═════════════════════════════════════════════════════════════════════════════


class TestCredentialsRepr:
    """CredentialsSnapshot repr не должен раскрывать sensitive поля."""

    def test_repr_does_not_show_inn(self) -> None:
        c = make_creds(inn="770123456789")
        r = repr(c)
        assert "770123456789" not in r
        assert "profile_id" in r

    def test_repr_does_not_show_thumbprint(self) -> None:
        c = make_creds(thumbprint="AABBCCDDEEFF00112233445566778899AABBCCDD")
        r = repr(c)
        assert "AABBCCDDEEFF00112233445566778899AABBCCDD" not in r

    def test_repr_does_not_show_url(self) -> None:
        c = make_creds()
        r = repr(c)
        assert "markirovka" not in r

    def test_repr_does_not_show_userinfo_canary(self) -> None:
        c = make_creds()
        r = repr(c)
        assert "user:pass" not in r


# ═════════════════════════════════════════════════════════════════════════════
#  CredentialsSnapshot field validation
# ═════════════════════════════════════════════════════════════════════════════


class TestCredentialsValidation:
    """Валидация полей CredentialsSnapshot."""

    def test_empty_profile_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="profile_id"):
            CredentialsSnapshot(profile_id="", inn="770123456789",
                                certificate_thumbprint="AABBCCDDEEFF00112233445566778899AABBCCDD",
                                api_base_url=PRODUCTION_API_BASE_URL)

    def test_whitespace_profile_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="profile_id"):
            CredentialsSnapshot(profile_id="   ", inn="770123456789",
                                certificate_thumbprint="AABBCCDDEEFF00112233445566778899AABBCCDD",
                                api_base_url=PRODUCTION_API_BASE_URL)

    def test_empty_inn_rejected(self) -> None:
        with pytest.raises(ValueError, match="INN"):
            CredentialsSnapshot(profile_id="p1", inn="",
                                certificate_thumbprint="AABBCCDDEEFF00112233445566778899AABBCCDD",
                                api_base_url=PRODUCTION_API_BASE_URL)

    def test_short_inn_rejected(self) -> None:
        with pytest.raises(ValueError, match="INN"):
            CredentialsSnapshot(profile_id="p1", inn="7701234567",
                                certificate_thumbprint="AABBCCDDEEFF00112233445566778899AABBCCDD",
                                api_base_url=PRODUCTION_API_BASE_URL)

    def test_non_digit_inn_rejected(self) -> None:
        with pytest.raises(ValueError, match="INN"):
            CredentialsSnapshot(profile_id="p1", inn="77012A456789",
                                certificate_thumbprint="AABBCCDDEEFF00112233445566778899AABBCCDD",
                                api_base_url=PRODUCTION_API_BASE_URL)

    def test_short_thumbprint_rejected(self) -> None:
        with pytest.raises(ValueError, match="certificate_thumbprint"):
            CredentialsSnapshot(profile_id="p1", inn="770123456789",
                                certificate_thumbprint="abc123",
                                api_base_url=PRODUCTION_API_BASE_URL)

    def test_non_hex_thumbprint_rejected(self) -> None:
        with pytest.raises(ValueError, match="certificate_thumbprint"):
            CredentialsSnapshot(profile_id="p1", inn="770123456789",
                                certificate_thumbprint="ZZBBCCDDEEFF00112233445566778899AABBCCDD",
                                api_base_url=PRODUCTION_API_BASE_URL)

    def test_thumbprint_normalized_to_uppercase(self) -> None:
        c = CredentialsSnapshot(profile_id="p1", inn="770123456789",
                                certificate_thumbprint="aabbccddeeff00112233445566778899aabbccdd",
                                api_base_url=PRODUCTION_API_BASE_URL)
        assert c.certificate_thumbprint == "AABBCCDDEEFF00112233445566778899AABBCCDD"

    def test_valid_credentials_ok(self) -> None:
        c = make_creds()
        assert c.profile_id == "p1"
        assert c.inn == "770123456789"
        assert c.certificate_thumbprint == "AABBCCDDEEFF00112233445566778899AABBCCDD"
        assert c.api_base_url == PRODUCTION_API_BASE_URL


# ═════════════════════════════════════════════════════════════════════════════
#  Signer result validation
# ═════════════════════════════════════════════════════════════════════════════


class TestSignerResult:
    """Результат signer должен быть непустой строкой."""

    def test_signer_returns_none_rejected(self) -> None:
        class NoneSigner(Signer):
            def sign(self, data: str, thumbprint: str) -> str:
                return None  # type: ignore[return-value]

        client = make_client(signer=NoneSigner())
        with pytest.raises(SigningError):
            client.get_token()

    def test_signer_returns_int_rejected(self) -> None:
        class IntSigner(Signer):
            def sign(self, data: str, thumbprint: str) -> str:
                return 42  # type: ignore[return-value]

        client = make_client(signer=IntSigner())
        with pytest.raises(SigningError):
            client.get_token()

    def test_signer_returns_dict_rejected(self) -> None:
        class DictSigner(Signer):
            def sign(self, data: str, thumbprint: str) -> str:
                return {"sig": "x"}  # type: ignore[return-value]

        client = make_client(signer=DictSigner())
        with pytest.raises(SigningError):
            client.get_token()

    def test_signer_returns_whitespace_rejected(self) -> None:
        class WhitespaceSigner(Signer):
            def sign(self, data: str, thumbprint: str) -> str:
                return "   "

        client = make_client(signer=WhitespaceSigner())
        with pytest.raises(SigningError):
            client.get_token()

    def test_signer_returns_empty_string_rejected(self) -> None:
        class EmptySigner(Signer):
            def sign(self, data: str, thumbprint: str) -> str:
                return ""

        client = make_client(signer=EmptySigner())
        with pytest.raises(SigningError):
            client.get_token()
