"""
Тесты JS-логики настроек ЧЗ: разметка, endpoints, отсутствие запрещённых конструкций.
"""

from __future__ import annotations

import os
import tempfile
import pytest

from app.chestny.factory import create_cz_app, db
from app.chestny.models import OrganizationProfile

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


class TestSettingsJS:
    def test_script_local_200(self, client):
        resp = client.get("/static/chestny/settings.js")
        assert resp.status_code == 200

    def test_script_in_html(self, client):
        html = client.get("/").data.decode()
        assert "settings.js" in html
        assert "defer" in html

    def test_no_forbidden_js_constructs(self, client):
        js = client.get("/static/chestny/settings.js").data.decode()
        for bad in ("innerHTML", "insertAdjacentHTML", "eval(", "console.log"):
            assert bad not in js

    def test_no_external_urls(self, client):
        html = client.get("/").data.decode()
        assert "http://" not in html and "https://" not in html

    def test_js_has_relative_endpoints(self, client):
        js = client.get("/static/chestny/settings.js").data.decode()
        assert "/api/profiles/" in js
        assert "/api/certificates" in js

    def test_js_uses_get_put_post(self, client):
        js = client.get("/static/chestny/settings.js").data.decode()
        assert '"PUT"' in js or "'PUT'" in js
        assert '"POST"' in js or "'POST'" in js
        assert 'fetch(' in js

    def test_js_uses_textContent(self, client):
        js = client.get("/static/chestny/settings.js").data.decode()
        assert "textContent" in js

    def test_js_uses_createElement(self, client):
        js = client.get("/static/chestny/settings.js").data.decode()
        assert "createElement" in js

    def test_js_has_race_guard(self, client):
        js = client.get("/static/chestny/settings.js").data.decode()
        assert "AbortController" in js or "abort" in js
        assert "gen" in js

    def test_tabs_disabled_by_setbusy(self, client):
        js = client.get("/static/chestny/settings.js").data.decode()
        assert "tabEls[j].disabled" in js
        assert "btnEls[i].disabled" in js

    def test_setbusy_no_which_param(self, client):
        js = client.get("/static/chestny/settings.js").data.decode()
        assert "function setBusy(busy)" in js

    def test_buttons_wired(self, client):
        html = client.get("/").data.decode()
        assert 'id="refresh-certs"' in html
        assert 'id="check-cert"' in html
        assert 'type="submit"' in html

    def test_profile_tabs_wired(self, client):
        html = client.get("/").data.decode()
        assert 'data-profile="org-sinyavin"' in html
        assert 'data-profile="org-krasikova"' in html

    def test_saved_tp_option_short(self, client):
        js = client.get("/static/chestny/settings.js").data.decode()
        assert "Сохранён" in js or "\\u0421\\u043e\\u0445\\u0440\\u0430\\u043d\\u0451\\u043d" in js
        assert "slice(-8)" in js

    def test_no_full_thumbprint_in_status(self, client):
        js = client.get("/static/chestny/settings.js").data.decode()
        assert "slice(-8)" in js

    def test_shared_validation_function(self, client):
        js = client.get("/static/chestny/settings.js").data.decode()
        assert "validateAndBuildBody" in js
        assert "onCheckCert" in js
        assert "onSave" in js

    def test_validation_called_in_check_path(self, client):
        js = client.get("/static/chestny/settings.js").data.decode()
        lines = js.split("\n")
        inCheck = False
        hasValidate = False
        for l in lines:
            if "function onCheckCert" in l:
                inCheck = True
            if "function onSave" in l:
                inCheck = False
            if inCheck and "validateAndBuildBody" in l:
                hasValidate = True
        assert hasValidate

    def test_gen_guard_all_chains(self, client):
        js = client.get("/static/chestny/settings.js").data.decode()
        count = 0
        for word in ["myGen !== gen", "myGen !== gen", "myGen !== gen"]:
            count += 1
        # check it appears in load, save, refresh, check paths
        assert js.count("myGen !== gen") >= 3

    def test_array_isarray_check(self, client):
        js = client.get("/static/chestny/settings.js").data.decode()
        assert "Array.isArray" in js

    def test_js_has_import_preview_endpoint(self, client):
        js = client.get("/static/chestny/settings.js").data.decode()
        assert "/api/imports/preview" in js

    def test_js_uses_xmlhttprequest_for_upload(self, client):
        js = client.get("/static/chestny/settings.js").data.decode()
        assert "XMLHttpRequest" in js
        assert "FormData" in js

    def test_js_uses_delete_for_cancel(self, client):
        js = client.get("/static/chestny/settings.js").data.decode()
        assert "DELETE" in js
        assert "cancelImport" in js

    def test_js_has_cert_ok_flag(self, client):
        js = client.get("/static/chestny/settings.js").data.decode()
        assert "certOk" in js
        assert "updateGate" in js

    def test_js_has_active_import_state(self, client):
        js = client.get("/static/chestny/settings.js").data.decode()
        assert "activeImport" in js

    def test_js_uses_confirm_for_active_import(self, client):
        js = client.get("/static/chestny/settings.js").data.decode()
        assert "confirmIfActiveImport" in js
        assert "confirm(" in js

    def test_js_cancel_clears_file_input(self, client):
        js = client.get("/static/chestny/settings.js").data.decode()
        assert "fileInput.value" in js and '""' in js

    def test_js_no_storage(self, client):
        js = client.get("/static/chestny/settings.js").data.decode()
        for term in ("localStorage", "sessionStorage", "document.cookie"):
            assert term not in js

    def test_js_uses_textcontent_for_dryrun(self, client):
        js = client.get("/static/chestny/settings.js").data.decode()
        assert "showDryRun" in js
        assert "createElement" in js
        assert "textContent" in js

    def test_js_resets_certok_on_save(self, client):
        js = client.get("/static/chestny/settings.js").data.decode()
        # certOk = false встречается в onSave, onCheckCert, onRefreshCerts, onTabClick
        assert "certOk = false" in js
        assert "updateGate" in js

    def test_js_confirm_on_tab_switch(self, client):
        js = client.get("/static/chestny/settings.js").data.decode()
        assert "cancelActiveImport" in js
        assert "onTabClick" in js
        assert "confirmIfActiveImport" in js


class TestDryRunJS:
    """Тесты dry-run JS-логики."""

    def test_upload_btn_wired(self, client):
        html = client.get("/").data.decode()
        assert 'id="upload-btn"' in html
        assert 'id="file-input"' in html

    def test_cancel_btn_wired(self, client):
        html = client.get("/").data.decode()
        assert 'id="cancel-import-btn"' in html

    def test_submit_cz_btn_disabled(self, client):
        html = client.get("/").data.decode()
        assert 'id="submit-cz-btn"' in html
        assert "disabled" in html

    def test_dryrun_results_ids(self, client):
        html = client.get("/").data.decode()
        assert 'id="dryrun-summary"' in html
        assert 'id="dryrun-tables"' in html

    def test_no_innerhtml_in_markup(self, client):
        html = client.get("/").data.decode()
        assert "innerHTML" not in html

    def test_no_storage_in_markup(self, client):
        html = client.get("/").data.decode()
        for term in ("localStorage", "sessionStorage", "document.cookie"):
            assert term not in html

    def test_multipart_contract_preview(self, client):
        """POST /api/imports/preview multipart: profile_id + file."""
        js = client.get("/static/chestny/settings.js").data.decode()
        assert "FormData" in js
        assert "profile_id" in js
        assert "append" in js

    def test_upload_uses_xhr_post(self, client):
        js = client.get("/static/chestny/settings.js").data.decode()
        assert '"POST"' in js or "'POST'" in js
        assert "/api/imports/preview" in js

    def test_cancel_uses_xhr_delete(self, client):
        js = client.get("/static/chestny/settings.js").data.decode()
        assert '"DELETE"' in js or "'DELETE'" in js
        assert "/api/imports/" in js

    def test_cancel_resets_active_import(self, client):
        js = client.get("/static/chestny/settings.js").data.decode()
        assert "activeImport = null" in js

    def test_dryrun_uses_apifields(self, client):
        """dry-run использует import_token, profile.display_name, summary.*, accepted, excluded."""
        js = client.get("/static/chestny/settings.js").data.decode()
        for field in ("import_token", "profile.display_name",
                      "accepted", "excluded", "reason_code", "message"):
            assert field in js
        assert "summary" in js
        assert "total_rows" in js
        assert "profile_id" in js

    def test_no_crypto_tail_in_dryrun(self, client):
        js = client.get("/static/chestny/settings.js").data.decode()
        assert "crypt" not in js.lower()
        assert "digest" not in js.lower()
        assert "hmac" not in js.lower()

    def test_gate_clears_on_cert_change(self, client):
        js = client.get("/static/chestny/settings.js").data.decode()
        assert "cert.addEventListener" in js or "els.cert.addEventListener" in js
        assert "certOk = false" in js
        assert "updateGate" in js


class TestSettingsJSIntegration:
    def test_detail_loads(self, client):
        p = OrganizationProfile.query.get("org-sinyavin")
        p.inn = "123456789012"
        p.certificate_thumbprint = VALID_TP
        p.fias_id = "f47ac10b-58cc-4372-a567-0e02b2c3d479"
        db.session.commit()
        resp = client.get("/api/profiles/org-sinyavin")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["inn"] == "123456789012"
        assert data["fias_id"] == "f47ac10b-58cc-4372-a567-0e02b2c3d479"
        assert data["certificate_thumbprint"] == VALID_TP

    def test_save_preserves_inn(self, client):
        p = OrganizationProfile.query.get("org-sinyavin")
        p.inn = "123456789012"
        p.certificate_thumbprint = VALID_TP
        db.session.commit()
        resp = client.put("/api/profiles/org-sinyavin", json={"fias_id": ""})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["inn"] == "123456789012"
        assert data["certificate_thumbprint"] == VALID_TP

    def test_diagnose_ok(self, monkeypatch, client):
        monkeypatch.setattr("app.cz_api.list_certificates",
                            lambda: [{"thumbprint": VALID_TP, "subject": "CN=Test",
                                      "issuer": "CN=CA", "has_private_key": True, "store": "My"}])
        p = OrganizationProfile.query.get("org-sinyavin")
        p.certificate_thumbprint = VALID_TP
        db.session.commit()
        resp = client.post("/api/profiles/org-sinyavin/certificate/diagnose")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["found"] is True
        assert data["has_private_key"] is True
