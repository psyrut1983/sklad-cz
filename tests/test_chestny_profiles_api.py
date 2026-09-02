"""
Тесты API профилей Честного Знака.
Изолированная БД + тестовый клиент.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from app.chestny.factory import create_cz_app, db
from app.chestny.models import OrganizationProfile

# ═════════════════════════════════════════════════════════════════════════════
#  Фикстуры
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


def _set_inn_tp(profile_id: str, inn: str, tp: str):
    """Устанавливает INN и thumbprint профиля."""
    p = OrganizationProfile.query.get(profile_id)
    p.inn = inn
    p.certificate_thumbprint = tp
    db.session.commit()


# ═════════════════════════════════════════════════════════════════════════════
#  1. GET /api/profiles — список
# ═════════════════════════════════════════════════════════════════════════════


class TestListProfiles:
    def test_list_returns_two(self, client):
        resp = client.get("/api/profiles")
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, list) and len(data) == 2

    def test_list_inn_masked(self, client):
        _set_inn_tp("org-sinyavin", "123456789012",
                     "AABBCCDDEEAABBCCDDEEAABBCCDDEEAABBCCDDEE")
        resp = client.get("/api/profiles")
        data = resp.get_json()
        for item in data:
            if item["id"] == "org-sinyavin":
                assert item["inn"] == "1234****9012"
                return
        pytest.fail("org-sinyavin not found")

    def test_list_certificate_thumbprint_absent(self, client):
        resp = client.get("/api/profiles")
        data = resp.get_json()
        for item in data:
            assert "certificate_thumbprint" not in item

    def test_list_api_url_absent(self, client):
        resp = client.get("/api/profiles")
        data = resp.get_json()
        for item in data:
            assert "api_url" not in item


# ═════════════════════════════════════════════════════════════════════════════
#  2. GET /api/profiles/<id> — детали
# ═════════════════════════════════════════════════════════════════════════════


class TestGetProfile:
    def test_detail_full_inn_thumbprint(self, client):
        _set_inn_tp("org-sinyavin", "123456789012",
                     "AABBCCDDEEAABBCCDDEEAABBCCDDEEAABBCCDDEE")
        resp = client.get("/api/profiles/org-sinyavin")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["inn"] == "123456789012"
        assert data["certificate_thumbprint"] == "AABBCCDDEEAABBCCDDEEAABBCCDDEEAABBCCDDEE"

    def test_detail_404(self, client):
        resp = client.get("/api/profiles/nonexistent")
        assert resp.status_code == 404


# ═════════════════════════════════════════════════════════════════════════════
#  3. PUT /api/profiles/<id> — обновление
# ═════════════════════════════════════════════════════════════════════════════


class TestUpdateProfile:
    def test_put_partial_fias_id_only(self, client):
        _set_inn_tp("org-sinyavin", "123456789012",
                     "AABBCCDDEEAABBCCDDEEAABBCCDDEEAABBCCDDEE")
        resp = client.put("/api/profiles/org-sinyavin",
                          json={"fias_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["inn"] == "123456789012"
        assert data["certificate_thumbprint"] == "AABBCCDDEEAABBCCDDEEAABBCCDDEEAABBCCDDEE"
        assert data["fias_id"] == "f47ac10b-58cc-4372-a567-0e02b2c3d479"

    def test_put_explicit_empty_inn(self, client):
        _set_inn_tp("org-sinyavin", "123456789012",
                     "AABBCCDDEEAABBCCDDEEAABBCCDDEEAABBCCDDEE")
        resp = client.put("/api/profiles/org-sinyavin",
                          json={"inn": ""})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["inn"] == ""
        assert data["certificate_thumbprint"] == "AABBCCDDEEAABBCCDDEEAABBCCDDEEAABBCCDDEE"
