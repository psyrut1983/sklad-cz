"""
Тесты packaging: новый builder для LK_RECEIPT.

Все внешние вызовы замоканы. Никакой реальной сети/подписи/ЧЗ.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import pytest

from app.chestny.services.packaging import (
    CONFIRMED,
    FAILED,
    PENDING,
    UNKNOWN,
    Package,
    PackageBuilder,
    PackageBuilderError,
    PackageCreateError,
    PackageItem,
    PackageStore,
    PackageStoreError,
)
from app.chestny.services.active_imports import ActiveImport, ExpiredError, NotFoundError
from app.services.excel_import import AcceptedRow, ExcludedRow, ImportResult, ImportSummary

# ═════════════════════════════════════════════════════════════════════════════
#  Фикстуры
# ═════════════════════════════════════════════════════════════════════════════


def _make_import(
    profile_id: str = "org-sinyavin",
    token: str = "test-token-12345",
    accepted_count: int = 3,
    expires_in: float = 1800,
) -> ActiveImport:
    accepted = [
        AcceptedRow(
            row_index=i,
            ki=f"010123456789099921GOODS{i:03d}ABCDE",
            check_number=f"CHK{i:03d}",
            fn_number=f"FN{i:03d}",
            cost_kopecks=100 * (i + 1),
            date=f"2026-09-0{i+1}",
        )
        for i in range(1, accepted_count + 1)
    ]
    summary = ImportSummary(
        total_rows=accepted_count,
        accepted=accepted_count,
        excluded=0,
        by_reason={},
    )
    return ActiveImport(
        token=token,
        profile_id=profile_id,
        accepted=tuple(accepted),
        excluded=(),
        summary=summary,
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc).replace(hour=23, minute=59, second=59),
    )


def _make_expired_import(profile_id: str = "org-sinyavin") -> ActiveImport:
    accepted = (
        AcceptedRow(row_index=1, ki="010123456789099921GOODS1ABCDE", check_number="CHK001", fn_number="FN001", cost_kopecks=100, date="2026-09-01"),
    )
    summary = ImportSummary(total_rows=1, accepted=1, excluded=0, by_reason={})
    return ActiveImport(
        token="expired-token",
        profile_id=profile_id,
        accepted=accepted,
        excluded=(),
        summary=summary,
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc),
    )


def _make_settings(profile_id: str = "org-sinyavin") -> dict[str, Any]:
    return {
        "id": profile_id,
        "inn": "123456789012",
        "certificate_thumbprint": "AABBCCDDEEFF00112233445566778899AABBCCDD",
        "fias_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
        "api_base_url": "https://markirovka.crpt.ru/api/v3/true-api",
    }


class FakeSigner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def __call__(self, data: str, thumbprint: str) -> str:
        self.calls.append((data, thumbprint))
        return "fake-signature"


class FakeTransport:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self._response: dict[int, Any] = {}

    def set_response(self, status: int, json_data: Any = None, text: str = "") -> None:
        self._response = {"status": status, "json": json_data, "text": text}

    def __call__(self, method: str, url: str, **kwargs: Any) -> Any:
        self.requests.append({"method": method, "url": url, **kwargs})
        resp = self._response
        # Return a mock response object
        class MockResponse:
            def __init__(self, status: int, json_data: Any, text: str):
                self.status_code = status
                self._json_data = json_data
                self.text = text

            def json(self) -> Any:
                if self._json_data is not None:
                    return self._json_data
                raise ValueError("No JSON")

        return MockResponse(resp.get("status", 200), resp.get("json"), resp.get("text", ""))


@pytest.fixture
def auth_client() -> Any:
    """Mock auth client."""
    client = type("MockAuth", (), {})()
    client._token = "mock-token-abc123"
    client.get_token = lambda: client._token
    client.reset_token = lambda: setattr(client, "_token", "mock-token-xyz789")
    return client


@pytest.fixture
def signer() -> FakeSigner:
    return FakeSigner()


@pytest.fixture
def transport() -> FakeTransport:
    return FakeTransport()


@pytest.fixture
def builder(auth_client: Any, transport: FakeTransport, signer: FakeSigner) -> PackageBuilder:
    return PackageBuilder(auth_client, transport, signer, hmac_key=b"test-hmac-key-32-bytes!")


# ═════════════════════════════════════════════════════════════════════════════
#  Тесты Package
# ═════════════════════════════════════════════════════════════════════════════


class TestPackage:
    def test_create(self) -> None:
        pkg = Package(id="p1", profile_id="org-sinyavin", import_token="tok1")
        assert pkg.id == "p1"
        assert pkg.profile_id == "org-sinyavin"
        assert pkg.status == PENDING
        assert pkg.document_id is None

    def test_repr_no_secrets(self) -> None:
        pkg = Package(id="p1", profile_id="org-sinyavin", import_token="tok1")
        r = repr(pkg)
        assert "p1" in r
        assert "org-sinyavin" in r
        assert "tok1" not in r


# ═════════════════════════════════════════════════════════════════════════════
#  Тесты PackageItem
# ═════════════════════════════════════════════════════════════════════════════


class TestPackageItem:
    def test_create(self) -> None:
        item = PackageItem(ki31="010123456789099921GOODS1ABCDE", hmac="abc123", mask="0101****CDE", check="CHK001", fn="FN001", cost_kopecks=100, date="2026-09-01")
        assert item.ki31 == "010123456789099921GOODS1ABCDE"
        assert item.hmac == "abc123"
        assert item.mask == "0101****CDE"
        assert item.cost_kopecks == 100


# ═════════════════════════════════════════════════════════════════════════════
#  Тесты PackageBuilder
# ═════════════════════════════════════════════════════════════════════════════


class TestCreatePackage:
    def test_create_success(self, builder: PackageBuilder, transport: FakeTransport) -> None:
        transport.set_response(200, json_data={"documentId": "doc-12345-uuid"})
        imp = _make_import()
        settings = _make_settings()
        pkg = builder.create(imp, settings, action_date="2026-09-03", document_number="DOC-001", document_date="2026-09-03")
        assert pkg.status == CONFIRMED
        assert pkg.document_id == "doc-12345-uuid"
        assert pkg.profile_id == "org-sinyavin"
        assert pkg.import_token == imp.token
        assert len(transport.requests) == 1
        req = transport.requests[0]
        assert req["method"] == "POST"
        assert "lk/documents/create" in req["url"]
        assert "pg=lp" in req["url"]
        assert "Authorization" in req["headers"]
        assert "Bearer" in req["headers"]["Authorization"]

    def test_expired_import(self, builder: PackageBuilder) -> None:
        imp = _make_expired_import()
        settings = _make_settings()
        with pytest.raises(PackageBuilderError, match="импорта истёк"):
            builder.create(imp, settings, action_date="2026-09-03", document_number="DOC-001", document_date="2026-09-03")

    def test_wrong_profile(self, builder: PackageBuilder) -> None:
        imp = _make_import(profile_id="org-sinyavin")
        settings = _make_settings(profile_id="org-krasikova")
        with pytest.raises(PackageBuilderError, match="другому профилю"):
            builder.create(imp, settings, action_date="2026-09-03", document_number="DOC-001", document_date="2026-09-03")

    def test_items_mapping(self, builder: PackageBuilder, transport: FakeTransport) -> None:
        transport.set_response(200, json_data={"documentId": "doc-uuid-12345"})
        imp = _make_import(accepted_count=2)
        settings = _make_settings()
        pkg = builder.create(imp, settings, action_date="2026-09-03", document_number="DOC-001", document_date="2026-09-03")
        assert pkg.status == CONFIRMED
        assert pkg.summary is not None
        assert pkg.summary.accepted == 2

    def test_json_contract_format(self, builder: PackageBuilder, transport: FakeTransport) -> None:
        transport.set_response(200, json_data={"documentId": "doc-uuid-12345"})
        imp = _make_import(accepted_count=1)
        settings = _make_settings()
        builder.create(imp, settings, action_date="2026-09-03", document_number="DOC-001", document_date="2026-09-03")
        req = transport.requests[0]
        outer = req.get("json", {})
        assert outer.get("document_format") == "MANUAL"
        assert outer.get("type") == "LK_RECEIPT"
        assert "product_document" in outer
        assert "signature" in outer
        # Проверяем, что product_document — валидный base64
        import base64
        inner_json = base64.b64decode(outer["product_document"]).decode("utf-8")
        inner = json.loads(inner_json)
        assert inner["inn"] == settings["inn"]
        assert inner["action"] == "DISTANCE"
        assert inner["document_type"] == "OTHER"
        assert inner["fias_id"] == settings["fias_id"]
        assert len(inner["products"]) == 1
        assert inner["products"][0]["cis"] == imp.accepted[0].ki
        assert inner["products"][0]["product_cost"] == imp.accepted[0].cost_kopecks

    def test_plain_text_uuid_response(self, builder: PackageBuilder, transport: FakeTransport) -> None:
        doc_id = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        transport.set_response(200, text=doc_id)
        imp = _make_import()
        settings = _make_settings()
        pkg = builder.create(imp, settings, action_date="2026-09-03", document_number="DOC-001", document_date="2026-09-03")
        assert pkg.document_id == doc_id

    def test_401_retry_success(self, builder: PackageBuilder, transport: FakeTransport, auth_client: Any) -> None:
        """401 → reset token → повтор → успех."""
        transport.set_response(401, json_data={"error": "unauthorized"})
        # На второй вызов ответ будет 200
        original_token = auth_client._token

        # We need a smarter mock that changes response after first call
        call_count = [0]

        class SmartTransport:
            def __call__(self, method, url, **kwargs):
                call_count[0] += 1
                if call_count[0] == 1:
                    return type("R", (), {"status_code": 401, "text": "", "json": lambda self: {"error": "unauthorized"}})()
                return type("R", (), {"status_code": 200, "text": "", "json": lambda self: {"documentId": "doc-retry-uuid-12345"}})()

        builder._transport = SmartTransport()
        imp = _make_import()
        settings = _make_settings()
        pkg = builder.create(imp, settings, action_date="2026-09-03", document_number="DOC-001", document_date="2026-09-03")
        assert pkg.document_id == "doc-retry-uuid-12345"
        assert auth_client._token != original_token  # token was reset
        assert call_count[0] == 2

    def test_401_retry_fails_twice(self, builder: PackageBuilder, transport: FakeTransport, auth_client: Any) -> None:
        """401 → reset → повтор 401 → ошибка."""
        transport.set_response(401, json_data={"error": "unauthorized"})
        imp = _make_import()
        settings = _make_settings()
        with pytest.raises(PackageCreateError, match="создать документ"):
            builder.create(imp, settings, action_date="2026-09-03", document_number="DOC-001", document_date="2026-09-03")

    def test_403_no_retry(self, builder: PackageBuilder, transport: FakeTransport) -> None:
        """403 → ошибка без повтора."""
        transport.set_response(403, json_data={"error": "forbidden"})
        imp = _make_import()
        settings = _make_settings()
        with pytest.raises(PackageCreateError):
            builder.create(imp, settings, action_date="2026-09-03", document_number="DOC-001", document_date="2026-09-03")

    def test_500_no_retry(self, builder: PackageBuilder, transport: FakeTransport) -> None:
        """500 → ошибка без повтора."""
        transport.set_response(500, json_data={"error": "server error"})
        imp = _make_import()
        settings = _make_settings()
        with pytest.raises(PackageCreateError):
            builder.create(imp, settings, action_date="2026-09-03", document_number="DOC-001", document_date="2026-09-03")

    def test_signer_called(self, builder: PackageBuilder, transport: FakeTransport, signer: FakeSigner) -> None:
        transport.set_response(200, json_data={"documentId": "doc-uuid"})
        imp = _make_import()
        settings = _make_settings()
        builder.create(imp, settings, action_date="2026-09-03", document_number="DOC-001", document_date="2026-09-03")
        assert len(signer.calls) == 1
        data, tp = signer.calls[0]
        assert tp == settings["certificate_thumbprint"]
        # data should be the inner JSON string
        inner = json.loads(data)
        assert inner["inn"] == settings["inn"]


# ═════════════════════════════════════════════════════════════════════════════
#  Тесты PackageStore
# ═════════════════════════════════════════════════════════════════════════════


class TestPackageStore:
    def test_save_and_get(self) -> None:
        store = PackageStore()
        pkg = Package(id="p1", profile_id="org-sinyavin", import_token="tok1")
        store.save(pkg)
        assert store.get("p1") is pkg
        assert store.get("nonexistent") is None

    def test_list_by_profile(self) -> None:
        store = PackageStore()
        store.save(Package(id="p1", profile_id="org-sinyavin", import_token="tok1"))
        store.save(Package(id="p2", profile_id="org-krasikova", import_token="tok2"))
        store.save(Package(id="p3", profile_id="org-sinyavin", import_token="tok3"))
        sinyavin = store.list_by_profile("org-sinyavin")
        assert len(sinyavin) == 2
        assert {p.id for p in sinyavin} == {"p1", "p3"}

    def test_list_by_status(self) -> None:
        store = PackageStore()
        store.save(Package(id="p1", profile_id="org-sinyavin", import_token="tok1", status=PENDING))
        store.save(Package(id="p2", profile_id="org-sinyavin", import_token="tok2", status=CONFIRMED))
        store.save(Package(id="p3", profile_id="org-sinyavin", import_token="tok3", status=FAILED))
        assert len(store.list_by_status(PENDING)) == 1
        assert len(store.list_by_status(CONFIRMED)) == 1
        assert len(store.list_by_status(FAILED)) == 1

    def test_update_status(self) -> None:
        store = PackageStore()
        store.save(Package(id="p1", profile_id="org-sinyavin", import_token="tok1"))
        store.update_status("p1", CONFIRMED, document_id="doc-123")
        pkg = store.get("p1")
        assert pkg is not None
        assert pkg.status == CONFIRMED
        assert pkg.document_id == "doc-123"

    def test_update_status_not_found(self) -> None:
        store = PackageStore()
        with pytest.raises(PackageStoreError, match="не найден"):
            store.update_status("nonexistent", CONFIRMED)

    def test_clear(self) -> None:
        store = PackageStore()
        store.save(Package(id="p1", profile_id="org-sinyavin", import_token="tok1"))
        store.clear()
        assert len(store) == 0

    def test_len(self) -> None:
        store = PackageStore()
        assert len(store) == 0
        store.save(Package(id="p1", profile_id="org-sinyavin", import_token="tok1"))
        assert len(store) == 1
