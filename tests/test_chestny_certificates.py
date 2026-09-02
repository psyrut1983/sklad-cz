"""
Тесты сервиса и API сертификатов ЧЗ.
Изолированная БД + monkeypatch app.cz_api.list_certificates.
"""

from __future__ import annotations

import os
import tempfile
import pytest

from app.chestny.factory import create_cz_app, db
from app.chestny.models import OrganizationProfile
from app.chestny.services.certificates import (
    CertificateBackendError,
    diagnose_profile_certificate,
    list_local_certificates,
)

VALID_TP = "AABBCCDDEEAABBCCDDEEAABBCCDDEEAABBCCDDEE"


@pytest.fixture
def app():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    app = create_cz_app(db_uri=f"sqlite:///{db_path}", testing=True)
    ctx = app.app_context()
    ctx.push()
    yield app
    db.session.close()
    db.engine.dispose()
    ctx.pop()
    os.unlink(db_path)


@pytest.fixture
def client(app):
    return app.test_client()


def _good(tp=None, **kw):
    d = {"thumbprint": tp or VALID_TP, "subject": "CN=Test", "issuer": "CN=CA",
         "has_private_key": True, "store": "My"}
    d.update(kw)
    return d


class TestListCerts:
    def test_valid_normalization(self, monkeypatch):
        monkeypatch.setattr("app.cz_api.list_certificates", lambda: [_good()])
        r = list_local_certificates()
        assert len(r) == 1 and r[0]["thumbprint"] == VALID_TP and r[0]["has_private_key"] is True

    def test_sanitize_length(self, monkeypatch):
        monkeypatch.setattr("app.cz_api.list_certificates",
                            lambda: [_good(subject="x" * 600, issuer="y" * 600, store="z" * 150)])
        r = list_local_certificates()
        assert len(r[0]["subject"]) == 500 and len(r[0]["issuer"]) == 500 and len(r[0]["store"]) == 100

    def test_control_chars_stripped(self, monkeypatch):
        monkeypatch.setattr("app.cz_api.list_certificates",
                            lambda: [_good(subject="CN\x00\x1b\x9fOK")])
        r = list_local_certificates()
        assert r[0]["subject"] == "CNOK"

    def test_non_string_values(self, monkeypatch):
        monkeypatch.setattr("app.cz_api.list_certificates",
                            lambda: [_good(subject=None, issuer=42, store=[], has_private_key=True)])
        r = list_local_certificates()
        assert r[0]["subject"] == "" and r[0]["issuer"] == "" and r[0]["store"] == ""

    def test_has_private_key_only_bool(self, monkeypatch):
        monkeypatch.setattr("app.cz_api.list_certificates", lambda: [_good(has_private_key="yes")])
        assert list_local_certificates()[0]["has_private_key"] is False

    def test_skip_non_dict(self, monkeypatch):
        monkeypatch.setattr("app.cz_api.list_certificates", lambda: [_good(), "s", None, 42])
        assert len(list_local_certificates()) == 1

    def test_skip_invalid_tp(self, monkeypatch):
        monkeypatch.setattr("app.cz_api.list_certificates", lambda: [_good(tp="bad"), _good()])
        assert len(list_local_certificates()) == 1

    def test_skip_on_exception(self, monkeypatch):
        class B:  # noqa
            def get(self, *a): raise RuntimeError
        monkeypatch.setattr("app.cz_api.list_certificates", lambda: [B(), _good()])
        assert len(list_local_certificates()) == 1

    def test_backend_exception(self, monkeypatch):
        monkeypatch.setattr("app.cz_api.list_certificates",
                            lambda: (_ for _ in ()).throw(RuntimeError("orig")))
        with pytest.raises(CertificateBackendError) as exc:
            list_local_certificates()
        assert "orig" not in str(exc.value)

    def test_non_list_result(self, monkeypatch):
        monkeypatch.setattr("app.cz_api.list_certificates", lambda: None)
        with pytest.raises(CertificateBackendError):
            list_local_certificates()


class TestDiagnose:
    def test_not_configured(self):
        assert diagnose_profile_certificate(None) == {
            "configured": False, "found": False, "has_private_key": False}

    def test_found_private_true(self, monkeypatch):
        monkeypatch.setattr("app.cz_api.list_certificates", lambda: [_good()])
        r = diagnose_profile_certificate(VALID_TP)
        assert r["found"] and r["has_private_key"]

    def test_found_no_private(self, monkeypatch):
        monkeypatch.setattr("app.cz_api.list_certificates", lambda: [_good(has_private_key=False)])
        r = diagnose_profile_certificate(VALID_TP)
        assert r["found"] and not r["has_private_key"]

    def test_not_found(self, monkeypatch):
        monkeypatch.setattr("app.cz_api.list_certificates", lambda: [_good()])
        assert not diagnose_profile_certificate("1" * 40)["found"]

    def test_backend_503(self, monkeypatch):
        monkeypatch.setattr("app.cz_api.list_certificates",
                            lambda: (_ for _ in ()).throw(RuntimeError))
        with pytest.raises(CertificateBackendError):
            diagnose_profile_certificate(VALID_TP)


class TestApiCerts:
    def test_success(self, monkeypatch, client):
        monkeypatch.setattr("app.cz_api.list_certificates", lambda: [_good()])
        monkeypatch.setattr("app.cz_api._sign_data", lambda *a: (_ for _ in ()).throw(RuntimeError))
        monkeypatch.setattr("app.cz_api.get_uuid_token", lambda *a: (_ for _ in ()).throw(RuntimeError))
        monkeypatch.setattr("requests.get", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError))
        monkeypatch.setattr("requests.post", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError))
        resp = client.get("/api/certificates")
        assert resp.status_code == 200
        assert resp.get_json()[0]["thumbprint"] == VALID_TP

    def test_503(self, monkeypatch, client):
        monkeypatch.setattr("app.cz_api.list_certificates",
                            lambda: (_ for _ in ()).throw(RuntimeError))
        assert client.get("/api/certificates").status_code == 503


class TestApiDiagnose:
    def test_no_thumbprint_422(self, client):
        assert client.post("/api/profiles/org-sinyavin/certificate/diagnose").status_code == 422

    def test_unknown_profile_404(self, client):
        assert client.post("/api/profiles/nonexistent/certificate/diagnose").status_code == 404

    def test_found(self, monkeypatch, client):
        monkeypatch.setattr("app.cz_api.list_certificates", lambda: [_good()])
        monkeypatch.setattr("app.cz_api._sign_data", lambda *a: (_ for _ in ()).throw(RuntimeError))
        monkeypatch.setattr("app.cz_api.get_uuid_token", lambda *a: (_ for _ in ()).throw(RuntimeError))
        monkeypatch.setattr("requests.get", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError))
        monkeypatch.setattr("requests.post", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError))
        OrganizationProfile.query.get("org-sinyavin").certificate_thumbprint = VALID_TP
        db.session.commit()
        resp = client.post("/api/profiles/org-sinyavin/certificate/diagnose")
        assert resp.status_code == 200 and resp.get_json()["found"] is True

    def test_backend_503(self, monkeypatch, client):
        monkeypatch.setattr("app.cz_api.list_certificates",
                            lambda: (_ for _ in ()).throw(RuntimeError))
        OrganizationProfile.query.get("org-sinyavin").certificate_thumbprint = VALID_TP
        db.session.commit()
        assert client.post("/api/profiles/org-sinyavin/certificate/diagnose").status_code == 503
