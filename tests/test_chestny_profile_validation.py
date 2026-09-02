"""
Тесты валидации полей профилей ЧЗ.
Изолированная БД + клиент + прямые вызовы normalize_*.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from app.chestny.factory import create_cz_app, db
from app.chestny.models import OrganizationProfile
from app.chestny.validation import (
    normalize_fias_id,
    normalize_inn,
    normalize_thumbprint,
)

# ═════════════════════════════════════════════════════════════════════════════
#  Валидные ИНН ИП (12 цифр, обе контрольные суммы верны)
# ═════════════════════════════════════════════════════════════════════════════

VALID_INN_1 = "123456789047"
VALID_INN_2 = "987654321018"

WRONG_CHECK1 = "123456789037"  # первая КС неверна (4→3)
WRONG_CHECK2 = "123456789046"  # вторая КС неверна (7→6)

VALID_TP = "AABBCCDDEEAABBCCDDEEAABBCCDDEEAABBCCDDEE"


class TestNormalizeInn:
    @pytest.mark.parametrize("inn", [VALID_INN_1, VALID_INN_2])
    def test_valid(self, inn):
        assert normalize_inn(inn) == inn

    def test_wrong_check1(self):
        assert normalize_inn(WRONG_CHECK1) is None

    def test_wrong_check2(self):
        assert normalize_inn(WRONG_CHECK2) is None

    @pytest.mark.parametrize("bad", [
        "12345678904",       # 11 цифр
        "1234567890470",     # 13 цифр
        "12345678904A",      # буква
        "1234-56789047",     # дефис
        "1234 56789047",     # пробел внутри
    ])
    def test_rejected(self, bad):
        assert normalize_inn(bad) is None


class TestNormalizeThumbprint:
    def test_uppercase(self):
        assert normalize_thumbprint(VALID_TP) == VALID_TP

    def test_lowercase(self):
        assert normalize_thumbprint(VALID_TP.lower()) == VALID_TP

    def test_spaces(self):
        assert normalize_thumbprint(
            "AA BB CC DD EE AA BB CC DD EE AA BB CC DD EE AA BB CC DD EE"
        ) == VALID_TP

    def test_colons(self):
        assert normalize_thumbprint(
            "AA:BB:CC:DD:EE:AA:BB:CC:DD:EE:AA:BB:CC:DD:EE:AA:BB:CC:DD:EE"
        ) == VALID_TP

    @pytest.mark.parametrize("bad", [
        "AA-BB-CC-DD-EE-AA-BB-CC-DD-EE-AA-BB-CC-DD-EE-AA-BB-CC-DD-EE",  # дефисы
        "ZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ!",  # non-hex
        "AABBCCDDEEAABBCCDDEEAABBCCDDEEAABBCCDDEEF",   # 41 символ
        "AABBCCDDEEAABBCCDDEEAABBCCDDEEAABBCCDD",       # 39 символов
    ])
    def test_rejected(self, bad):
        assert normalize_thumbprint(bad) is None


class TestNormalizeFias:
    def test_canonical(self):
        assert normalize_fias_id("f47ac10b-58cc-4372-a567-0e02b2c3d479") \
            == "f47ac10b-58cc-4372-a567-0e02b2c3d479"

    def test_uppercase_to_lower(self):
        assert normalize_fias_id("F47AC10B-58CC-4372-A567-0E02B2C3D479") \
            == "f47ac10b-58cc-4372-a567-0e02b2c3d479"

    def test_braces(self):
        assert normalize_fias_id("{f47ac10b-58cc-4372-a567-0e02b2c3d479}") \
            == "f47ac10b-58cc-4372-a567-0e02b2c3d479"

    def test_invalid(self):
        assert normalize_fias_id("not-a-uuid") is None


# ═════════════════════════════════════════════════════════════════════════════
#  PUT — ошибки запроса
# ═════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def app():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    app = create_cz_app(db_uri=f"sqlite:///{db_path}", testing=True)
    ctx = app.app_context()
    ctx.push()
    try:
        yield app
    finally:
        db.session.close()
        db.engine.dispose()
        ctx.pop()
        os.unlink(db_path)


@pytest.fixture
def client(app):
    return app.test_client()


class TestPutErrors:
    def test_unknown_field_400(self, client):
        resp = client.put("/api/profiles/org-sinyavin",
                          json={"foo": "bar"})
        assert resp.status_code == 400

    def test_immutable_field_400(self, client):
        resp = client.put("/api/profiles/org-sinyavin",
                          json={"display_name": "new"})
        assert resp.status_code == 400

    def test_non_object_400(self, client):
        resp = client.put("/api/profiles/org-sinyavin",
                          json="not-a-dict")
        assert resp.status_code == 400

    def test_null_body_400(self, client):
        resp = client.put("/api/profiles/org-sinyavin",
                          json=None)
        assert resp.status_code == 400

    def test_broken_json_400(self, client):
        resp = client.put("/api/profiles/org-sinyavin",
                          data="not json", content_type="application/json")
        assert resp.status_code == 400


class TestPutAtomicity:
    def test_valid_inn_invalid_tp_leaves_unchanged(self, client):
        p = OrganizationProfile.query.get("org-sinyavin")
        p.inn = VALID_INN_1
        p.certificate_thumbprint = VALID_TP
        p.fias_id = "f47ac10b-58cc-4372-a567-0e02b2c3d479"
        db.session.commit()

        resp = client.put("/api/profiles/org-sinyavin", json={
            "inn": VALID_INN_2,
            "certificate_thumbprint": "too-short",
        })
        assert resp.status_code == 400

        db.session.expire_all()
        p2 = OrganizationProfile.query.get("org-sinyavin")
        assert p2.inn == VALID_INN_1
        assert p2.certificate_thumbprint == VALID_TP
        assert p2.fias_id == "f47ac10b-58cc-4372-a567-0e02b2c3d479"
