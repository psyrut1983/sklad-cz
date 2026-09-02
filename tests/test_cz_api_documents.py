"""
Тесты create_receipt_document и check_document_status_by_id.

Проверяет:
- Успешные ответы: JSON с полями документа, plain-text UUID
- HTTP-ошибки: 400, 401 (с повторной попыткой), 429, 500
- Не-JSON ответы, тайм-ауты
- Структуру payload: type=LK_RECEIPT, pg=lp, base64 product_document, signature
- URL/params для status: /doc/list, pg=lp, number=doc_id
"""

import json
import pytest
from unittest.mock import MagicMock

# ===== Helpers =====

FAKE_SETTINGS = {
    "cz_api_url": "https://markirovka.crpt.ru/api/v3/true-api",
    "cz_cert_thumbprint": "AABBCCDDEEFF0011223344556677889900AABBCC",
    "cz_inn": "7712345678",
    "product_group": "1",
    "default_disposal_fias_id": "test-fias-id-12345",
}

FAKE_SIGNATURE = "FAKE_CADES_SIGNATURE_BASE64_STRING"
FAKE_TOKEN = "test-jwt-token-abc123"


def _make_response(status_code=200, json_data=None, text=None):
    """Создать mock-ответ requests."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = text if text is not None else json.dumps(json_data or {})
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = None
    resp.raise_for_status = MagicMock()
    return resp


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


@pytest.fixture
def mock_uuid_token(monkeypatch):
    """Подменяем get_uuid_token, чтобы не зависеть от реального ЧЗ."""
    monkeypatch.setattr("app.cz_api.get_uuid_token", lambda thumbprint=None: FAKE_TOKEN)


# ===== create_receipt_document =====

@pytest.mark.usefixtures("mock_sign_data", "mock_uuid_token")
class TestCreateReceiptDocumentSuccess:
    """Успешные сценарии create_receipt_document."""

    def test_json_success_with_document_id(self, monkeypatch):
        """JSON-ответ с documentId."""
        import app.cz_api as cz

        api_response = {
            "documentId": "doc-12345",
            "number": "ВЫВОД-20240902120000",
            "value": "some-value",
        }

        captured = {}

        def _mock_post(url, **kw):
            captured["url"] = url
            captured["params"] = kw.get("params")
            captured["json"] = kw.get("json")
            captured["headers"] = kw.get("headers")
            return _make_response(200, api_response)

        monkeypatch.setattr(cz.requests, "post", _mock_post)

        result = cz.create_receipt_document(
            cz_codes=["010463003759346121SjFg6nX5bGS91oMA"],
        )

        assert result["success"] is True
        assert result["data"]["documentId"] == "doc-12345"
        assert result["data"]["number"] == "ВЫВОД-20240902120000"
        assert result["data"]["value"] == "some-value"

    def test_json_success_with_id_field(self, monkeypatch):
        """JSON-ответ с полем id (альтернативный формат)."""
        import app.cz_api as cz

        api_response = {"id": "123456", "document_id": "654321"}

        def _mock_post(url, **kw):
            return _make_response(200, api_response)

        monkeypatch.setattr(cz.requests, "post", _mock_post)

        result = cz.create_receipt_document(
            cz_codes=["010463003759346121SjFg6nX5bGS91oMA"],
        )

        assert result["success"] is True
        assert result["data"]["id"] == "123456"
        assert result["data"]["document_id"] == "654321"

    def test_plain_text_uuid_success(self, monkeypatch):
        """200 OK с plain-text UUID (не JSON)."""
        import app.cz_api as cz

        def _mock_post(url, **kw):
            resp = _make_response(200, {})
            resp.json.side_effect = json.JSONDecodeError("Expecting value", "plain", 0)
            resp.text = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
            return resp

        monkeypatch.setattr(cz.requests, "post", _mock_post)

        result = cz.create_receipt_document(
            cz_codes=["010463003759346121SjFg6nX5bGS91oMA"],
        )

        assert result["success"] is True
        assert result["data"]["document_id"] == "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        assert "raw_response" in result

    def test_plain_text_201_success(self, monkeypatch):
        """201 Created с plain-text UUID."""
        import app.cz_api as cz

        def _mock_post(url, **kw):
            resp = _make_response(201, {})
            resp.json.side_effect = json.JSONDecodeError("Expecting value", "plain", 0)
            resp.text = "uuid-from-201-response"
            return resp

        monkeypatch.setattr(cz.requests, "post", _mock_post)

        result = cz.create_receipt_document(
            cz_codes=["010463003759346121SjFg6nX5bGS91oMA"],
        )

        assert result["success"] is True
        assert result["data"]["document_id"] == "uuid-from-201-response"


@pytest.mark.usefixtures("mock_sign_data", "mock_uuid_token")
class TestCreateReceiptDocumentPayload:
    """Проверка структуры отправляемого payload."""

    def test_payload_type_and_format(self, monkeypatch):
        """Проверка type=LK_RECEIPT, document_format=MANUAL, signature."""
        import app.cz_api as cz

        captured = {}

        def _mock_post(url, **kw):
            captured["json"] = kw.get("json")
            captured["params"] = kw.get("params")
            captured["headers"] = kw.get("headers")
            return _make_response(200, {"documentId": "d1"})

        monkeypatch.setattr(cz.requests, "post", _mock_post)

        cz.create_receipt_document(
            cz_codes=["010463003759346121SjFg6nX5bGS91oMA"],
            pg="lp",
        )

        payload = captured["json"]
        assert payload["type"] == "LK_RECEIPT"
        assert payload["document_format"] == "MANUAL"
        assert payload["signature"] == FAKE_SIGNATURE
        assert "product_document" in payload

    def test_payload_product_document_is_base64(self, monkeypatch):
        """product_document — валидный base64 от JSON."""
        import app.cz_api as cz
        import base64

        captured = {}

        def _mock_post(url, **kw):
            captured["json"] = kw.get("json")
            captured["params"] = kw.get("params")
            return _make_response(200, {"documentId": "d1"})

        monkeypatch.setattr(cz.requests, "post", _mock_post)

        cz.create_receipt_document(
            cz_codes=["010463003759346121SjFg6nX5bGS91oMA"],
            pg="lp",
        )

        b64 = captured["json"]["product_document"]
        # Декодируем и проверяем, что это JSON с ожидаемыми полями
        decoded = base64.b64decode(b64).decode("utf-8")
        inner = json.loads(decoded)
        assert inner["inn"] == "7712345678"
        assert len(inner["products"]) == 1
        assert inner["products"][0]["cis"] is not None

    def test_payload_pg_param(self, monkeypatch):
        """pg передаётся как query-параметр."""
        import app.cz_api as cz

        captured = {}

        def _mock_post(url, **kw):
            captured["params"] = kw.get("params")
            return _make_response(200, {"documentId": "d1"})

        monkeypatch.setattr(cz.requests, "post", _mock_post)

        cz.create_receipt_document(
            cz_codes=["010463003759346121SjFg6nX5bGS91oMA"],
            pg="lp",
        )

        assert captured["params"] == {"pg": "lp"}

    def test_payload_pg_from_settings(self, monkeypatch):
        """pg берётся из настроек (product_group=1 → lp)."""
        import app.cz_api as cz

        captured = {}

        def _mock_post(url, **kw):
            captured["params"] = kw.get("params")
            return _make_response(200, {"documentId": "d1"})

        monkeypatch.setattr(cz.requests, "post", _mock_post)

        cz.create_receipt_document(
            cz_codes=["010463003759346121SjFg6nX5bGS91oMA"],
        )

        assert captured["params"] == {"pg": "lp"}

    def test_payload_url(self, monkeypatch):
        """URL запроса: /lk/documents/create."""
        import app.cz_api as cz

        captured = {}

        def _mock_post(url, **kw):
            captured["url"] = url
            return _make_response(200, {"documentId": "d1"})

        monkeypatch.setattr(cz.requests, "post", _mock_post)

        cz.create_receipt_document(
            cz_codes=["010463003759346121SjFg6nX5bGS91oMA"],
        )

        assert "/lk/documents/create" in captured["url"]

    def test_payload_headers(self, monkeypatch):
        """Headers: Content-Type, Authorization, accept."""
        import app.cz_api as cz

        captured = {}

        def _mock_post(url, **kw):
            captured["headers"] = kw.get("headers")
            return _make_response(200, {"documentId": "d1"})

        monkeypatch.setattr(cz.requests, "post", _mock_post)

        cz.create_receipt_document(
            cz_codes=["010463003759346121SjFg6nX5bGS91oMA"],
        )

        h = captured["headers"]
        assert h["Content-Type"] == "application/json"
        assert h["Authorization"] == f"Bearer {FAKE_TOKEN}"
        assert h["accept"] == "application/json"


@pytest.mark.usefixtures("mock_sign_data", "mock_uuid_token")
class TestCreateReceiptDocumentErrors:
    """Обработка HTTP-ошибок create_receipt_document."""

    def test_400_error(self, monkeypatch):
        """400 Bad Request."""
        import app.cz_api as cz

        def _mock_post(url, **kw):
            return _make_response(400, {"error": "bad request"}, text='{"error": "bad request"}')

        monkeypatch.setattr(cz.requests, "post", _mock_post)

        result = cz.create_receipt_document(
            cz_codes=["010463003759346121SjFg6nX5bGS91oMA"],
        )

        assert result["success"] is False
        assert result["error_code"] == 400

    def test_401_retry(self, monkeypatch):
        """401 → сброс токена + повторный запрос."""
        import app.cz_api as cz

        call_count = {"first": False, "second": False}

        def _mock_post(url, **kw):
            if not call_count["first"]:
                call_count["first"] = True
                return _make_response(401, {"error": "unauthorized"})
            call_count["second"] = True
            return _make_response(200, {"documentId": "retried-ok"})

        monkeypatch.setattr(cz.requests, "post", _mock_post)

        result = cz.create_receipt_document(
            cz_codes=["010463003759346121SjFg6nX5bGS91oMA"],
        )

        assert call_count["first"] is True
        assert call_count["second"] is True
        assert result["success"] is True
        assert result["data"]["documentId"] == "retried-ok"

    def test_429_error(self, monkeypatch):
        """429 Too Many Requests."""
        import app.cz_api as cz

        def _mock_post(url, **kw):
            return _make_response(429, {"error": "too many requests"})

        monkeypatch.setattr(cz.requests, "post", _mock_post)

        result = cz.create_receipt_document(
            cz_codes=["010463003759346121SjFg6nX5bGS91oMA"],
        )

        assert result["success"] is False
        assert result["error_code"] == 429

    def test_500_error(self, monkeypatch):
        """500 Internal Server Error."""
        import app.cz_api as cz

        def _mock_post(url, **kw):
            return _make_response(500, {"error": "internal error"})

        monkeypatch.setattr(cz.requests, "post", _mock_post)

        result = cz.create_receipt_document(
            cz_codes=["010463003759346121SjFg6nX5bGS91oMA"],
        )

        assert result["success"] is False
        assert result["error_code"] == 500

    def test_non_json_failure(self, monkeypatch):
        """Non-JSON ответ с HTTP-ошибкой (не 200/201)."""
        import app.cz_api as cz

        def _mock_post(url, **kw):
            resp = _make_response(502, {})
            resp.json.side_effect = json.JSONDecodeError("Expecting value", "<html>", 0)
            resp.text = "<html>502 Bad Gateway</html>"
            return resp

        monkeypatch.setattr(cz.requests, "post", _mock_post)

        result = cz.create_receipt_document(
            cz_codes=["010463003759346121SjFg6nX5bGS91oMA"],
        )

        assert result["success"] is False
        assert result["error_code"] == 502
        assert "502" in result["error_message"]

    def test_connect_timeout(self, monkeypatch):
        """ConnectTimeout — не перехвачен (requests.post вне try/except)."""
        import app.cz_api as cz
        import requests

        def _mock_post(url, **kw):
            raise requests.exceptions.ConnectTimeout("Connection timed out")

        monkeypatch.setattr(cz.requests, "post", _mock_post)

        with pytest.raises((requests.exceptions.ConnectTimeout, Exception)):
            cz.create_receipt_document(
                cz_codes=["010463003759346121SjFg6nX5bGS91oMA"],
            )

    def test_read_timeout(self, monkeypatch):
        """ReadTimeout — не перехвачен (requests.post вне try/except)."""
        import app.cz_api as cz
        import requests

        def _mock_post(url, **kw):
            raise requests.exceptions.ReadTimeout("Read timed out")

        monkeypatch.setattr(cz.requests, "post", _mock_post)

        with pytest.raises((requests.exceptions.ReadTimeout, Exception)):
            cz.create_receipt_document(
                cz_codes=["010463003759346121SjFg6nX5bGS91oMA"],
            )

    def test_missing_thumbprint(self, monkeypatch):
        """Нет thumbprint ни в аргументе, ни в настройках → исключение."""
        import app.cz_api as cz

        def _settings_no_tp():
            return {"cz_api_url": "https://example.com/api"}

        monkeypatch.setattr(cz, "load_settings", _settings_no_tp)

        with pytest.raises(Exception) as exc:
            cz.create_receipt_document(
                cz_codes=["010463003759346121SjFg6nX5bGS91oMA"],
            )
        assert "thumbprint" in str(exc.value).lower()


# ===== check_document_status_by_id =====

@pytest.mark.usefixtures("mock_uuid_token")
class TestCheckDocumentStatusSuccess:
    """Успешные сценарии check_document_status_by_id."""

    def test_success_with_status(self, monkeypatch):
        """Успешный ответ с status, errors, eliminationReason."""
        import app.cz_api as cz

        api_results = {
            "results": [
                {
                    "status": "DONE",
                    "errors": [],
                    "eliminationReason": None,
                    "number": "ВЫВОД-20240902120000",
                }
            ]
        }

        captured = {}

        def _mock_get(url, **kw):
            captured["url"] = url
            captured["params"] = kw.get("params")
            captured["headers"] = kw.get("headers")
            return _make_response(200, api_results)

        monkeypatch.setattr(cz.requests, "get", _mock_get)

        result = cz.check_document_status_by_id(doc_id="doc-12345")

        assert result["success"] is True
        assert result["data"]["status"] == "DONE"
        assert result["data"]["errors"] == []
        assert result["data"]["eliminationReason"] is None

    def test_success_with_errors_list(self, monkeypatch):
        """Успешный ответ с непустым списком errors."""
        import app.cz_api as cz

        api_results = {
            "results": [
                {
                    "status": "ERROR",
                    "errors": [
                        {"code": "CIS_NOT_FOUND", "message": "Код не найден"}
                    ],
                    "eliminationReason": "Неверный код маркировки",
                }
            ]
        }

        def _mock_get(url, **kw):
            return _make_response(200, api_results)

        monkeypatch.setattr(cz.requests, "get", _mock_get)

        result = cz.check_document_status_by_id(doc_id="doc-12345")

        assert result["success"] is True
        assert result["data"]["status"] == "ERROR"
        assert len(result["data"]["errors"]) == 1
        assert result["data"]["errors"][0]["code"] == "CIS_NOT_FOUND"

    def test_document_not_found(self, monkeypatch):
        """Пустой список results → Document not found."""
        import app.cz_api as cz

        api_results = {"results": []}

        def _mock_get(url, **kw):
            return _make_response(200, api_results)

        monkeypatch.setattr(cz.requests, "get", _mock_get)

        result = cz.check_document_status_by_id(doc_id="nonexistent")

        assert result["success"] is False
        assert result["error_message"] == "Document not found"


@pytest.mark.usefixtures("mock_uuid_token")
class TestCheckDocumentStatusRequest:
    """Проверка URL и параметров запроса status."""

    def test_url_and_params(self, monkeypatch):
        """URL /doc/list, params pg=lp, number=doc_id."""
        import app.cz_api as cz

        captured = {}

        def _mock_get(url, **kw):
            captured["url"] = url
            captured["params"] = kw.get("params")
            return _make_response(200, {"results": [{"status": "DONE"}]})

        monkeypatch.setattr(cz.requests, "get", _mock_get)

        cz.check_document_status_by_id(doc_id="test-doc-001")

        assert "/doc/list" in captured["url"]
        assert captured["params"]["pg"] == "lp"
        assert captured["params"]["number"] == "test-doc-001"
        assert captured["params"]["documentStatus"] == ""

    def test_pg_from_settings(self, monkeypatch):
        """pg берётся из настроек (product_group=1 → lp)."""
        import app.cz_api as cz

        captured = {}

        def _mock_get(url, **kw):
            captured["params"] = kw.get("params")
            return _make_response(200, {"results": [{"status": "DONE"}]})

        monkeypatch.setattr(cz.requests, "get", _mock_get)

        cz.check_document_status_by_id(doc_id="test-doc-001")

        assert captured["params"]["pg"] == "lp"

    def test_explicit_pg(self, monkeypatch):
        """Явно указанный pg переопределяет настройки."""
        import app.cz_api as cz

        captured = {}

        def _mock_get(url, **kw):
            captured["params"] = kw.get("params")
            return _make_response(200, {"results": [{"status": "DONE"}]})

        monkeypatch.setattr(cz.requests, "get", _mock_get)

        cz.check_document_status_by_id(doc_id="test-doc-001", pg="toys")

        assert captured["params"]["pg"] == "toys"

    def test_headers(self, monkeypatch):
        """Headers: accept и Authorization."""
        import app.cz_api as cz

        captured = {}

        def _mock_get(url, **kw):
            captured["headers"] = kw.get("headers")
            return _make_response(200, {"results": [{"status": "DONE"}]})

        monkeypatch.setattr(cz.requests, "get", _mock_get)

        cz.check_document_status_by_id(doc_id="test-doc-001")

        h = captured["headers"]
        assert h["accept"] == "application/json"
        assert h["Authorization"] == f"Bearer {FAKE_TOKEN}"


@pytest.mark.usefixtures("mock_uuid_token")
class TestCheckDocumentStatusErrors:
    """Обработка ошибок check_document_status_by_id."""

    def test_401_retry(self, monkeypatch):
        """401 → сброс токена + повторный запрос."""
        import app.cz_api as cz

        call_count = {"first": False, "second": False}

        def _mock_get(url, **kw):
            if not call_count["first"]:
                call_count["first"] = True
                return _make_response(401, {"error": "unauthorized"})
            call_count["second"] = True
            return _make_response(200, {"results": [{"status": "DONE"}]})

        monkeypatch.setattr(cz.requests, "get", _mock_get)

        result = cz.check_document_status_by_id(doc_id="test-doc-001")

        assert call_count["first"] is True
        assert call_count["second"] is True
        assert result["success"] is True
        assert result["data"]["status"] == "DONE"

    def test_429_error(self, monkeypatch):
        """429 Too Many Requests."""
        import app.cz_api as cz

        def _mock_get(url, **kw):
            return _make_response(429, {"error": "too many requests"})

        monkeypatch.setattr(cz.requests, "get", _mock_get)

        result = cz.check_document_status_by_id(doc_id="test-doc-001")

        assert result["success"] is False
        assert result["error_code"] == 429

    def test_500_error(self, monkeypatch):
        """500 Internal Server Error."""
        import app.cz_api as cz

        def _mock_get(url, **kw):
            return _make_response(500, {"error": "internal error"})

        monkeypatch.setattr(cz.requests, "get", _mock_get)

        result = cz.check_document_status_by_id(doc_id="test-doc-001")

        assert result["success"] is False
        assert result["error_code"] == 500

    def test_non_json_response(self, monkeypatch):
        """Non-JSON ответ."""
        import app.cz_api as cz

        def _mock_get(url, **kw):
            resp = _make_response(200, {})
            resp.json.side_effect = json.JSONDecodeError("Expecting value", "plain text", 0)
            resp.text = "plain text response"
            return resp

        monkeypatch.setattr(cz.requests, "get", _mock_get)

        result = cz.check_document_status_by_id(doc_id="test-doc-001")

        assert result["success"] is False
        assert result["error_code"] == 0
        assert "Non-JSON" in result["error_message"]

    def test_connect_timeout(self, monkeypatch):
        """ConnectTimeout."""
        import app.cz_api as cz
        import requests

        def _mock_get(url, **kw):
            raise requests.exceptions.ConnectTimeout("Connection timed out")

        monkeypatch.setattr(cz.requests, "get", _mock_get)

        result = cz.check_document_status_by_id(doc_id="test-doc-001")

        assert result["success"] is False
        assert result["error_code"] == 0
        assert "timed out" in result["error_message"].lower() or "timeout" in result["error_message"].lower()

    def test_read_timeout(self, monkeypatch):
        """ReadTimeout."""
        import app.cz_api as cz
        import requests

        def _mock_get(url, **kw):
            raise requests.exceptions.ReadTimeout("Read timed out")

        monkeypatch.setattr(cz.requests, "get", _mock_get)

        result = cz.check_document_status_by_id(doc_id="test-doc-001")

        assert result["success"] is False
        assert result["error_code"] == 0
        assert "timed out" in result["error_message"].lower() or "timeout" in result["error_message"].lower()

    def test_missing_thumbprint(self, monkeypatch):
        """Нет thumbprint → исключение."""
        import app.cz_api as cz

        def _settings_no_tp():
            return {"cz_api_url": "https://example.com/api"}

        monkeypatch.setattr(cz, "load_settings", _settings_no_tp)

        with pytest.raises(Exception) as exc:
            cz.check_document_status_by_id(doc_id="test-doc-001")
        assert "thumbprint" in str(exc.value).lower()
