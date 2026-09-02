"""
Тесты-характеризация маршрута create_receipt_document_route (массовый вывод из оборота).

Фиксирует текущее поведение БЕЗ исправления известных дефектов:

 1) Обе единицы передаются в cz_codes, но unit_data берётся только из первой
    (строка 626: unit_data = unit_data_list[0] if unit_data_list else None).
 2) document_id извлекается из вложенного data.data.documentId.
 3) product_group='1' в настройках приводит к pg='lp'.
 4) Пустой unit_ids → 400.
 5) Единицы без cz_code → 400, create_receipt_document не вызывается.

Все внешние вызовы замоканы. Production-код не изменяется.
"""

import json
import pytest
from unittest.mock import MagicMock


# ===== Фикстуры =====

FAKE_CZ_CODES = [
    "010463003759346121SjFg6nX5bGS91oMA",
    "010463003759346122KkHg7oY6cHT92pNB",
]

FAKE_DOC_ID = "doc-test-12345"


class FakeUnit:
    """Минимальный фейковый Unit — ровно те поля, которые читает маршрут."""
    def __init__(self, id, cz_code, disposal_price, disposal_doc_number,
                 disposal_doc_date, disposal_fias_id,
                 disposal_type="shipment", disposal_reason="remote_sale",
                 disposal_doc_type="other", disposal_doc_name="",
                 disposal_address="", buyer_inn=""):
        self.id = id
        self.cz_code = cz_code
        self.disposal_type = disposal_type
        self.disposal_reason = disposal_reason
        self.disposal_doc_type = disposal_doc_type
        self.disposal_doc_name = disposal_doc_name
        self.disposal_doc_number = disposal_doc_number
        self.disposal_doc_date = disposal_doc_date
        self.disposal_address = disposal_address
        self.disposal_fias_id = disposal_fias_id
        self.disposal_price = disposal_price
        self.disposal_status = 0
        self.buyer_inn = buyer_inn
        self.sku = MagicMock()


@pytest.fixture(autouse=True)
def _app_context():
    """Создаём Flask-приложение и контекст для тестового клиента."""
    import os
    from app import create_app, db as _db

    app = create_app()
    app.config["TESTING"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["SERVER_NAME"] = "127.0.0.1:5000"

    with app.app_context():
        _db.create_all()
        yield app
        _db.drop_all()


@pytest.fixture
def client(_app_context):
    with _app_context.test_client() as c:
        yield c


@pytest.fixture(autouse=True)
def _common_mocks(monkeypatch):
    """
    Общие моки для всех тестов маршрута.
    - db.session.commit — молча принимает вызов.
    - load_settings/get_product_group_code — базовые (могут переопределяться).
    reset_token импортируется только внутри тела функции, мокаем на app.cz_api.
    """
    monkeypatch.setattr("app.routes.import_export.db.session.commit", MagicMock())

    # По умолчанию — нейтральные настройки (переопределяются в конкретных тестах)
    # load_settings/get_product_group_code импортируются внутри тела функции
    # через from app.utils import ..., поэтому мокаем в app.utils
    monkeypatch.setattr(
        "app.utils.load_settings",
        lambda: {"product_group": "27"}
    )
    monkeypatch.setattr(
        "app.utils.get_product_group_code",
        lambda x: "toys"
    )
    yield


# =====================================================================
# Тест 1. Известный дефект — unit_data только от первой позиции
# =====================================================================

def test_defect_first_unit_data_used_for_all(client, monkeypatch):
    """
    Характеризация дефекта (строка 626):
    обе единицы передаются в cz_codes, но unit_data для
    create_receipt_document берётся ТОЛЬКО от первой Unit.
    Цена, номер/дата документа и FIAS второй единицы игнорируются.
    """
    # Две единицы с РАЗНЫМИ реквизитами
    units = [
        FakeUnit(id=1, cz_code=FAKE_CZ_CODES[0],
                 disposal_price=100.0, disposal_doc_number="DOC-001",
                 disposal_doc_date="2024-01-15", disposal_fias_id="FIAS-001"),
        FakeUnit(id=2, cz_code=FAKE_CZ_CODES[1],
                 disposal_price=200.0, disposal_doc_number="DOC-002",
                 disposal_doc_date="2024-02-20", disposal_fias_id="FIAS-002"),
    ]

    # Мокаем цепочку Unit.query.options(...).filter(...)
    fake_query = MagicMock()
    fake_query.options.return_value.filter.return_value = units
    monkeypatch.setattr("app.routes.import_export.Unit.query", fake_query)

    # Перехватываем вызов create_receipt_document
    captured = {}

    def fake_create(cz_codes, document_format="MANUAL", unit_data=None):
        captured["cz_codes"] = list(cz_codes)
        captured["unit_data"] = unit_data
        return {"success": True, "data": {"documentId": FAKE_DOC_ID}}

    monkeypatch.setattr(
        "app.cz_api.create_receipt_document", fake_create
    )

    # Статус-чек — заглушка
    monkeypatch.setattr(
        "app.cz_api.check_document_status_by_id",
        lambda doc_id, pg=None, thumbprint=None: {
            "success": True, "data": {"status": "DONE"}
        }
    )

    resp = client.post(
        "/api/cz/receipt/create",
        json={"unit_ids": [1, 2], "document_format": "MANUAL"}
    )

    assert resp.status_code == 200
    body = resp.get_json()

    # 1) Оба КИЗ переданы
    assert len(captured["cz_codes"]) == 2
    assert captured["cz_codes"] == FAKE_CZ_CODES

    # 2) unit_data — от первой единицы (ДЕФЕКТ)
    ud = captured["unit_data"]
    assert ud["disposal_price"] == 100.0, \
        f"Ожидалась цена от Unit 1 (100.0), получено {ud['disposal_price']}"
    assert ud["disposal_doc_number"] == "DOC-001"
    assert ud["disposal_doc_date"] == "2024-01-15"
    assert ud["disposal_fias_id"] == "FIAS-001"

    # 3) units_processed = 2
    assert body["units_processed"] == 2


# =====================================================================
# Тест 2. document_id из вложенного data.data.documentId
# =====================================================================

def test_success_extracts_document_id_from_nested_data(client, monkeypatch):
    """
    Успешный ответ API с вложенным data → document_id извлекается
    из data['data']['documentId'], возвращается units_processed=2.
    """
    units = [
        FakeUnit(id=1, cz_code=FAKE_CZ_CODES[0],
                 disposal_price=50.0, disposal_doc_number="DOC-A",
                 disposal_doc_date="2024-03-01", disposal_fias_id="FIAS-A"),
        FakeUnit(id=2, cz_code=FAKE_CZ_CODES[1],
                 disposal_price=75.0, disposal_doc_number="DOC-B",
                 disposal_doc_date="2024-03-05", disposal_fias_id="FIAS-B"),
    ]

    fake_query = MagicMock()
    fake_query.options.return_value.filter.return_value = units
    monkeypatch.setattr("app.routes.import_export.Unit.query", fake_query)

    # create_receipt_document возвращает вложенный data
    monkeypatch.setattr(
        "app.cz_api.create_receipt_document",
        lambda cz_codes, document_format="MANUAL", unit_data=None: {
            "success": True,
            "data": {
                "data": {
                    "documentId": "nested-doc-999"
                }
            }
        }
    )

    monkeypatch.setattr(
        "app.cz_api.check_document_status_by_id",
        lambda doc_id, pg=None, thumbprint=None: {
            "success": True, "data": {"status": "DONE"}
        }
    )

    resp = client.post(
        "/api/cz/receipt/create",
        json={"unit_ids": [1, 2]}
    )

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["document_id"] == "nested-doc-999"
    assert body["units_processed"] == 2


# =====================================================================
# Тест 3. product_group='1' → pg='lp'
# =====================================================================

def test_product_group_1_maps_to_pg_lp(client, monkeypatch):
    """
    Когда settings.product_group='1', check_document_status_by_id
    вызывается с pg='lp' (лёгкая промышленность).
    """
    units = [
        FakeUnit(id=1, cz_code=FAKE_CZ_CODES[0],
                 disposal_price=10.0, disposal_doc_number="DOC-X",
                 disposal_doc_date="2024-04-01", disposal_fias_id="FIAS-X"),
    ]

    fake_query = MagicMock()
    fake_query.options.return_value.filter.return_value = units
    monkeypatch.setattr("app.routes.import_export.Unit.query", fake_query)

    monkeypatch.setattr(
        "app.cz_api.create_receipt_document",
        lambda cz_codes, document_format="MANUAL", unit_data=None: {
            "success": True,
            "data": {"documentId": FAKE_DOC_ID}
        }
    )

    # Настройки с product_group='1' → get_product_group_code('1') → 'lp'
    # Эти функции импортируются внутри тела маршрута через from app.utils import ...
    monkeypatch.setattr(
        "app.utils.load_settings",
        lambda: {"product_group": "1"}
    )
    monkeypatch.setattr(
        "app.utils.get_product_group_code",
        lambda x: "lp"
    )

    status_calls = []

    def fake_check_status(doc_id, pg=None, thumbprint=None):
        status_calls.append({"doc_id": doc_id, "pg": pg})
        return {"success": True, "data": {"status": "DONE"}}

    monkeypatch.setattr(
        "app.cz_api.check_document_status_by_id",
        fake_check_status
    )

    resp = client.post(
        "/api/cz/receipt/create",
        json={"unit_ids": [1]}
    )

    assert resp.status_code == 200
    assert len(status_calls) >= 1, \
        "check_document_status_by_id должен был быть вызван"
    assert status_calls[0]["pg"] == "lp", \
        f"Ожидался pg='lp', получено pg='{status_calls[0]['pg']}'"
    assert status_calls[0]["doc_id"] == FAKE_DOC_ID


# =====================================================================
# Тест 4. Пустой unit_ids → 400
# =====================================================================

def test_empty_unit_ids_returns_400(client):
    """
    Пустой список unit_ids возвращает 400.
    create_receipt_document не вызывается (проверка на уровне сети не нужна —
    маршрут возвращается до любых внешних вызовов).
    """
    resp = client.post(
        "/api/cz/receipt/create",
        json={"unit_ids": [], "document_format": "MANUAL"}
    )
    assert resp.status_code == 400
    body = resp.get_json()
    assert "error" in body


# =====================================================================
# Тест 5. Единицы без cz_code → 400
# =====================================================================

def test_units_without_cz_code_returns_400(client, monkeypatch):
    """
    Если у выбранных единиц нет cz_code (None или ''),
    маршрут возвращает 400 и НЕ вызывает create_receipt_document.
    """
    units = [
        FakeUnit(id=1, cz_code=None,
                 disposal_price=10.0, disposal_doc_number="DOC-N",
                 disposal_doc_date="2024-05-01", disposal_fias_id="FIAS-N"),
        FakeUnit(id=2, cz_code="",
                 disposal_price=20.0, disposal_doc_number="DOC-O",
                 disposal_doc_date="2024-05-02", disposal_fias_id="FIAS-O"),
    ]

    fake_query = MagicMock()
    fake_query.options.return_value.filter.return_value = units
    monkeypatch.setattr("app.routes.import_export.Unit.query", fake_query)

    called = {"value": False}

    def fake_create(*args, **kwargs):
        called["value"] = True
        return {"success": True, "data": {"documentId": "should-not-happen"}}

    monkeypatch.setattr(
        "app.cz_api.create_receipt_document",
        fake_create
    )

    resp = client.post(
        "/api/cz/receipt/create",
        json={"unit_ids": [1, 2]}
    )

    assert resp.status_code == 400
    body = resp.get_json()
    assert "error" in body
    assert "нет кодов ЧЗ" in body["error"].lower() or \
           "нет кодов" in body["error"].lower()
    assert called["value"] is False, \
        "create_receipt_document не должен вызываться при отсутствии кодов ЧЗ"
