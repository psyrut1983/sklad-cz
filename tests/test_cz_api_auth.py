"""
Тесты аутентификации API Честного Знака (get_uuid_token).

Проверяет challenge → sign → token flow:
- URL/headers/payload формирования запросов
- Диспетчеризация подписи: Windows (win32com) vs Linux (pycades)
- Обработка ошибок: 401, 429, 500, connect timeout, read timeout, non-JSON
- Маппинг товарной группы: 1 → lp
"""

import json
import sys

import pytest
from unittest.mock import MagicMock, patch, call


# ===== Helpers =====

FAKE_SETTINGS = {
    "cz_api_url": "https://markirovka.crpt.ru/api/v3/true-api",
    "cz_cert_thumbprint": "AABBCCDDEEFF0011223344556677889900AABBCC",
    "cz_inn": "7712345678",
}

FAKE_AUTH_KEY_RESPONSE = {
    "uuid": "test-uuid-12345",
    "data": "test-data-to-sign",
}

FAKE_SIGNATURE = "FAKE_CADES_SIGNATURE_BASE64_ENCODED_STRING"

FAKE_TOKEN = "test-jwt-token-abc123"


def _make_response(status_code=200, json_data=None, text=None):
    """Создать mock-ответ requests."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = text or json.dumps(json_data or {})
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = None
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = _http_error(status_code, resp)
    return resp


def _http_error(status_code, response=None):
    """Создать HTTPError."""
    import requests
    err = requests.exceptions.HTTPError(response=response)
    err.response = response
    err.response.status_code = status_code
    return err


# ===== Fixtures =====

@pytest.fixture(autouse=True)
def reset_cz_token():
    """Сбрасываем глобальный _uuid_token перед каждым тестом."""
    from app.cz_api import reset_token
    reset_token()
    yield
    reset_token()


@pytest.fixture(autouse=True)
def mock_settings(monkeypatch):
    """Подменяем load_settings тестовыми данными."""
    def _fake_settings():
        return dict(FAKE_SETTINGS)
    monkeypatch.setattr("app.cz_api.load_settings", _fake_settings)


@pytest.fixture
def mock_sign_data(monkeypatch):
    """Подменяем _sign_data, чтобы не зависеть от реальной КриптоПро."""
    monkeypatch.setattr("app.cz_api._sign_data", lambda data, tp: FAKE_SIGNATURE)


# ===== Маппинг 1 → lp =====

class TestProductGroupMapping:
    """Проверка маппинга числовых ID товарных групп в строковые коды.

    Тесты удалены — app.utils.py был удалён в Этапе 7.
    Функциональность перенесена в app.chestny.services.cz_auth.
    """
    pass


# ===== Успешная аутентификация =====

@pytest.mark.usefixtures("mock_sign_data")
class TestSuccessfulAuth:
    """Полный успешный сценарий: challenge → sign → token."""

    def test_full_flow_returns_token(self, monkeypatch):
        """Полный цикл: GET /auth/key → sign → POST /auth/simpleSignIn → токен."""
        import app.cz_api as cz

        get_responses = iter([
            _make_response(200, FAKE_AUTH_KEY_RESPONSE),
        ])
        post_responses = iter([
            _make_response(200, {"token": FAKE_TOKEN}),
        ])

        original_get = cz.requests.get
        original_post = cz.requests.post

        def _mock_get(url, **kw):
            resp = next(get_responses)
            resp.url = url
            return resp

        def _mock_post(url, **kw):
            resp = next(post_responses)
            resp.url = url
            # Сохраняем payload для проверки
            _mock_post._last_url = url
            _mock_post._last_json = kw.get("json")
            _mock_post._last_headers = kw.get("headers")
            return resp

        monkeypatch.setattr(cz.requests, "get", _mock_get)
        monkeypatch.setattr(cz.requests, "post", _mock_post)

        token = cz.get_uuid_token()

        assert token == FAKE_TOKEN

    def test_request_url_and_headers(self, monkeypatch):
        """Проверка URL и headers запросов."""
        import app.cz_api as cz

        captured = {}

        def _mock_get(url, **kw):
            captured["get_url"] = url
            captured["get_headers"] = kw.get("headers")
            return _make_response(200, FAKE_AUTH_KEY_RESPONSE)

        def _mock_post(url, **kw):
            captured["post_url"] = url
            captured["post_headers"] = kw.get("headers")
            captured["post_json"] = kw.get("json")
            return _make_response(200, {"token": FAKE_TOKEN})

        monkeypatch.setattr(cz.requests, "get", _mock_get)
        monkeypatch.setattr(cz.requests, "post", _mock_post)

        cz.get_uuid_token()

        # GET /auth/key
        assert "auth/key" in captured["get_url"]
        assert captured["get_headers"] == {"accept": "application/json"}

        # POST /auth/simpleSignIn
        assert "auth/simpleSignIn" in captured["post_url"]
        assert captured["post_headers"] == {
            "Content-Type": "application/json",
            "accept": "application/json",
        }

    def test_payload_structure(self, monkeypatch):
        """Проверка структуры payload в POST запросе."""
        import app.cz_api as cz

        def _mock_get(url, **kw):
            return _make_response(200, FAKE_AUTH_KEY_RESPONSE)

        payload = {}

        def _mock_post(url, **kw):
            payload.update(kw.get("json", {}))
            return _make_response(200, {"token": FAKE_TOKEN})

        monkeypatch.setattr(cz.requests, "get", _mock_get)
        monkeypatch.setattr(cz.requests, "post", _mock_post)

        cz.get_uuid_token()

        assert payload["uuid"] == "test-uuid-12345"
        assert payload["data"] == FAKE_SIGNATURE
        assert payload["unitedToken"] is True
        assert payload["inn"] == "7712345678"

    def test_token_without_inn(self, monkeypatch):
        """Если ИНН не указан, поле inn не передаётся в payload."""
        import app.cz_api as cz

        def _settings_no_inn():
            s = dict(FAKE_SETTINGS)
            s.pop("cz_inn", None)
            return s

        monkeypatch.setattr(cz, "load_settings", _settings_no_inn)

        def _mock_get(url, **kw):
            return _make_response(200, FAKE_AUTH_KEY_RESPONSE)

        payload = {}

        def _mock_post(url, **kw):
            payload.update(kw.get("json", {}))
            return _make_response(200, {"token": FAKE_TOKEN})

        monkeypatch.setattr(cz.requests, "get", _mock_get)
        monkeypatch.setattr(cz.requests, "post", _mock_post)

        cz.get_uuid_token()

        assert "inn" not in payload

    def test_uuid_token_field_fallback(self, monkeypatch):
        """Если поле 'token' отсутствует, подхватываем 'uuidToken'."""
        import app.cz_api as cz

        def _mock_get(url, **kw):
            return _make_response(200, FAKE_AUTH_KEY_RESPONSE)

        def _mock_post(url, **kw):
            return _make_response(200, {"uuidToken": "fallback-token-xyz"})

        monkeypatch.setattr(cz.requests, "get", _mock_get)
        monkeypatch.setattr(cz.requests, "post", _mock_post)

        token = cz.get_uuid_token()
        assert token == "fallback-token-xyz"

    def test_cached_token(self, monkeypatch):
        """Повторный вызов возвращает кэшированный токен без запросов."""
        import app.cz_api as cz

        call_count = {"get": 0, "post": 0}

        def _mock_get(url, **kw):
            call_count["get"] += 1
            return _make_response(200, FAKE_AUTH_KEY_RESPONSE)

        def _mock_post(url, **kw):
            call_count["post"] += 1
            return _make_response(200, {"token": FAKE_TOKEN})

        monkeypatch.setattr(cz.requests, "get", _mock_get)
        monkeypatch.setattr(cz.requests, "post", _mock_post)

        # Первый вызов — делает запросы
        token1 = cz.get_uuid_token()
        assert token1 == FAKE_TOKEN
        assert call_count["get"] == 1
        assert call_count["post"] == 1

        # Второй вызов — возвращает кэш, без запросов
        token2 = cz.get_uuid_token()
        assert token2 == FAKE_TOKEN
        assert call_count["get"] == 1
        assert call_count["post"] == 1

    def test_thumbprint_from_argument(self, monkeypatch):
        """Передача thumbprint аргументом, а не из настроек."""
        import app.cz_api as cz

        captured_tp = []

        def _fake_sign(data, tp):
            captured_tp.append(tp)
            return FAKE_SIGNATURE

        monkeypatch.setattr(cz, "_sign_data", _fake_sign)

        def _mock_get(url, **kw):
            return _make_response(200, FAKE_AUTH_KEY_RESPONSE)

        def _mock_post(url, **kw):
            return _make_response(200, {"token": FAKE_TOKEN})

        monkeypatch.setattr(cz.requests, "get", _mock_get)
        monkeypatch.setattr(cz.requests, "post", _mock_post)

        cz.get_uuid_token(thumbprint="EXPLICIT_THUMBPRINT")
        assert captured_tp == ["EXPLICIT_THUMBPRINT"]


# ===== Диспетчеризация подписи =====

class TestSignDispatch:
    """Проверка, что _sign_data вызывает правильную платформенную реализацию."""

    def test_windows_dispatches_to_win(self, monkeypatch):
        """На win32 → _sign_data_win."""
        import app.cz_api as cz
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(cz, "_platform", "win32")

        called = {"win": False, "linux": False}

        def _win(data, tp):
            called["win"] = True
            return FAKE_SIGNATURE

        def _linux(data, tp):
            called["linux"] = True
            return FAKE_SIGNATURE

        monkeypatch.setattr(cz, "_sign_data_win", _win)
        monkeypatch.setattr(cz, "_sign_data_linux", _linux)

        result = cz._sign_data("hello", "tp123")
        assert called["win"] is True
        assert called["linux"] is False
        assert result == FAKE_SIGNATURE

    def test_linux_dispatches_to_linux(self, monkeypatch):
        """На linux → _sign_data_linux."""
        import app.cz_api as cz
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(cz, "_platform", "linux")

        called = {"win": False, "linux": False}

        def _win(data, tp):
            called["win"] = True
            return FAKE_SIGNATURE

        def _linux(data, tp):
            called["linux"] = True
            return FAKE_SIGNATURE

        monkeypatch.setattr(cz, "_sign_data_win", _win)
        monkeypatch.setattr(cz, "_sign_data_linux", _linux)

        result = cz._sign_data("hello", "tp123")
        assert called["win"] is False
        assert called["linux"] is True
        assert result == FAKE_SIGNATURE

    def test_other_platform_dispatches_to_linux(self, monkeypatch):
        """Не-win32 (darwin, etc) → _sign_data_linux (fallback)."""
        import app.cz_api as cz
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(cz, "_platform", "darwin")

        called = {"win": False, "linux": False}

        def _win(data, tp):
            called["win"] = True
            return FAKE_SIGNATURE

        def _linux(data, tp):
            called["linux"] = True
            return FAKE_SIGNATURE

        monkeypatch.setattr(cz, "_sign_data_win", _win)
        monkeypatch.setattr(cz, "_sign_data_linux", _linux)

        result = cz._sign_data("hello", "tp123")
        assert called["win"] is False
        assert called["linux"] is True
        assert result == FAKE_SIGNATURE


# ===== Ошибки HTTP =====

@pytest.mark.usefixtures("mock_sign_data")
class TestAuthErrors:
    """Обработка HTTP-ошибок при аутентификации."""

    def test_401_raises(self, monkeypatch):
        """401 Unauthorised → raise_for_status."""
        import app.cz_api as cz

        def _mock_get(url, **kw):
            return _make_response(200, FAKE_AUTH_KEY_RESPONSE)

        def _mock_post(url, **kw):
            return _make_response(401, {"error": "unauthorized"})

        monkeypatch.setattr(cz.requests, "get", _mock_get)
        monkeypatch.setattr(cz.requests, "post", _mock_post)

        with pytest.raises(Exception) as exc:
            cz.get_uuid_token()
        # HTTPError or derivative
        assert "401" in str(exc.value) or "HTTP" in str(type(exc.value).__name__)

    def test_429_raises(self, monkeypatch):
        """429 Too Many Requests → raise_for_status."""
        import app.cz_api as cz

        def _mock_get(url, **kw):
            return _make_response(200, FAKE_AUTH_KEY_RESPONSE)

        def _mock_post(url, **kw):
            return _make_response(429, {"error": "too many requests"})

        monkeypatch.setattr(cz.requests, "get", _mock_get)
        monkeypatch.setattr(cz.requests, "post", _mock_post)

        with pytest.raises(Exception) as exc:
            cz.get_uuid_token()
        assert "429" in str(exc.value) or "HTTP" in str(type(exc.value).__name__)

    def test_500_raises(self, monkeypatch):
        """500 Internal Server Error → raise_for_status."""
        import app.cz_api as cz

        def _mock_get(url, **kw):
            return _make_response(200, FAKE_AUTH_KEY_RESPONSE)

        def _mock_post(url, **kw):
            return _make_response(500, {"error": "internal error"})

        monkeypatch.setattr(cz.requests, "get", _mock_get)
        monkeypatch.setattr(cz.requests, "post", _mock_post)

        with pytest.raises(Exception) as exc:
            cz.get_uuid_token()
        assert "500" in str(exc.value) or "HTTP" in str(type(exc.value).__name__)

    def test_401_on_get_key_raises(self, monkeypatch):
        """GET /auth/key возвращает 401 → raise_for_status."""
        import app.cz_api as cz

        def _mock_get(url, **kw):
            return _make_response(401, {"error": "unauthorized"})

        monkeypatch.setattr(cz.requests, "get", _mock_get)

        with pytest.raises(Exception) as exc:
            cz.get_uuid_token()
        assert "401" in str(exc.value) or "HTTP" in str(type(exc.value).__name__)


# ===== Тайм-ауты =====

@pytest.mark.usefixtures("mock_sign_data")
class TestTimeouts:
    """Обработка тайм-аутов соединения и чтения."""

    def test_connect_timeout_on_key(self, monkeypatch):
        """ConnectTimeout на GET /auth/key."""
        import app.cz_api as cz
        import requests

        def _mock_get(url, **kw):
            raise requests.exceptions.ConnectTimeout(f"Connection to {url} timed out")

        monkeypatch.setattr(cz.requests, "get", _mock_get)

        with pytest.raises((requests.exceptions.ConnectTimeout, Exception)):
            cz.get_uuid_token()

    def test_read_timeout_on_key(self, monkeypatch):
        """ReadTimeout на GET /auth/key."""
        import app.cz_api as cz
        import requests

        def _mock_get(url, **kw):
            raise requests.exceptions.ReadTimeout(f"Read from {url} timed out")

        monkeypatch.setattr(cz.requests, "get", _mock_get)

        with pytest.raises((requests.exceptions.ReadTimeout, Exception)):
            cz.get_uuid_token()

    def test_connect_timeout_on_signin(self, monkeypatch):
        """ConnectTimeout на POST /auth/simpleSignIn."""
        import app.cz_api as cz
        import requests

        def _mock_get(url, **kw):
            return _make_response(200, FAKE_AUTH_KEY_RESPONSE)

        def _mock_post(url, **kw):
            raise requests.exceptions.ConnectTimeout(f"Connection to {url} timed out")

        monkeypatch.setattr(cz.requests, "get", _mock_get)
        monkeypatch.setattr(cz.requests, "post", _mock_post)

        with pytest.raises((requests.exceptions.ConnectTimeout, Exception)):
            cz.get_uuid_token()


# ===== Non-JSON =====

@pytest.mark.usefixtures("mock_sign_data")
class TestNonJSON:
    """Обработка non-JSON ответов."""

    def test_get_key_non_json(self, monkeypatch):
        """GET /auth/key возвращает не-JSON (например, HTML)."""
        import app.cz_api as cz

        def _mock_get(url, **kw):
            resp = _make_response(200, {})
            resp.json.side_effect = json.JSONDecodeError("Expecting value", "<html>", 0)
            resp.text = "<html>502 Bad Gateway</html>"
            return resp

        monkeypatch.setattr(cz.requests, "get", _mock_get)

        with pytest.raises(Exception):
            cz.get_uuid_token()

    def test_post_signin_non_json(self, monkeypatch):
        """POST /auth/simpleSignIn возвращает не-JSON (например, plain text)."""
        import app.cz_api as cz

        def _mock_get(url, **kw):
            return _make_response(200, FAKE_AUTH_KEY_RESPONSE)

        def _mock_post(url, **kw):
            resp = _make_response(200, {})
            resp.json.side_effect = json.JSONDecodeError("Expecting value", "OK", 0)
            resp.text = "OK"
            return resp

        monkeypatch.setattr(cz.requests, "get", _mock_get)
        monkeypatch.setattr(cz.requests, "post", _mock_post)

        with pytest.raises(Exception) as exc:
            cz.get_uuid_token()
        assert "non-JSON" in str(exc.value)

    def test_post_signin_empty_response(self, monkeypatch):
        """POST /auth/simpleSignIn возвращает пустой ответ."""
        import app.cz_api as cz

        def _mock_get(url, **kw):
            return _make_response(200, FAKE_AUTH_KEY_RESPONSE)

        def _mock_post(url, **kw):
            resp = _make_response(200, {})
            resp.json.return_value = {}  # пустой JSON
            resp.text = "{}"
            return resp

        monkeypatch.setattr(cz.requests, "get", _mock_get)
        monkeypatch.setattr(cz.requests, "post", _mock_post)

        with pytest.raises(Exception) as exc:
            cz.get_uuid_token()
        assert "no token fields" in str(exc.value).lower()


# ===== Missing configuration =====

class TestMissingConfig:
    """Обработка отсутствующей конфигурации."""

    def test_no_thumbprint_in_settings(self, monkeypatch):
        """Если thumbprint не задан ни в аргументе, ни в настройках → исключение."""
        import app.cz_api as cz

        def _settings_no_tp():
            return {"cz_api_url": "https://example.com/api"}

        monkeypatch.setattr(cz, "load_settings", _settings_no_tp)

        with pytest.raises(Exception) as exc:
            cz.get_uuid_token()
        assert "thumbprint" in str(exc.value).lower()


# ===== 403 Signature / Access =====

@pytest.mark.usefixtures("mock_sign_data")
class Test403Errors:
    """Обработка 403 с разными сообщениями."""

    def test_403_signature_invalid(self, monkeypatch):
        """403 + 'Подпись невалидна' → понятное исключение."""
        import app.cz_api as cz

        def _mock_get(url, **kw):
            return _make_response(200, FAKE_AUTH_KEY_RESPONSE)

        def _mock_post(url, **kw):
            resp = _make_response(403, {"error_message": "Подпись невалидна"})
            return resp

        monkeypatch.setattr(cz.requests, "get", _mock_get)
        monkeypatch.setattr(cz.requests, "post", _mock_post)

        with pytest.raises(Exception) as exc:
            cz.get_uuid_token()
        msg = str(exc.value)
        assert "невалидна" in msg or "invalid" in msg.lower() or "Signature" in msg

    def test_403_access_denied(self, monkeypatch):
        """403 + 'Отсутствует доступ' → понятное исключение."""
        import app.cz_api as cz

        def _mock_get(url, **kw):
            return _make_response(200, FAKE_AUTH_KEY_RESPONSE)

        def _mock_post(url, **kw):
            resp = _make_response(403, {"error_message": "Отсутствует доступ"})
            return resp

        monkeypatch.setattr(cz.requests, "get", _mock_get)
        monkeypatch.setattr(cz.requests, "post", _mock_post)

        with pytest.raises(Exception) as exc:
            cz.get_uuid_token()
        msg = str(exc.value)
        assert "доступ" in msg or "access" in msg.lower() or "403" in msg
