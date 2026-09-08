"""End-to-end tests for preview-to-submit without external calls."""

import base64
import json
from dataclasses import dataclass

from app.chestny.factory import create_cz_app, db
from app.chestny.models import ImportJob, OrganizationProfile, ProcessedKiz, SubmissionBatch
from app.chestny.services.cz_auth import PRODUCTION_API_BASE_URL
from app.services.excel_import import AcceptedRow, ImportResult, ImportSummary


class FakeSigner:
    def sign(self, data, thumbprint):
        return "signed"


class FakeAuthTransport:
    def get_json(self, url, headers=None, timeout=15):
        return {"uuid": "challenge-id", "data": "challenge"}

    def post_json(self, url, payload, headers=None, timeout=15):
        return {"token": "test-token"}


@dataclass
class FakeResponse:
    status_code: int = 201
    text: str = ""

    def json(self):
        return {"documentId": "doc-123"}


def test_submit_persists_and_consumes_import(tmp_path):
    app = create_cz_app(instance_path=str(tmp_path), db_uri=f"sqlite:///{tmp_path / 'test.db'}", testing=True)
    app.extensions["cz_signer"] = FakeSigner()
    app.extensions["cz_auth_transport"] = FakeAuthTransport()
    sent = []

    def submit_transport(method, url, **kwargs):
        sent.append((method, url, kwargs))
        return FakeResponse()

    app.extensions["cz_submit_transport"] = submit_transport
    with app.app_context():
        profile = db.session.get(OrganizationProfile, "org-sinyavin")
        profile.inn = "123456789012"
        profile.certificate_thumbprint = "A" * 40
        profile.fias_id = "12345678-1234-1234-1234-123456789012"
        db.session.commit()
        row = AcceptedRow(row_index=2, ki="010123456789012321SERIAL1234567", check_number="CHK-1", fn_number="FN-1", cost_kopecks=150000, date="2026-09-04")
        result = ImportResult(accepted=[row], excluded=[], summary=ImportSummary(total_rows=1, accepted=1, excluded=0))
        token = app.extensions["active_imports"].create(profile.id, result)

    response = app.test_client().post(f"/api/imports/{token}/submit", json={
        "action_date": "2026-09-04", "document_date": "2026-09-04",
        "document_number": "WB-1", "primary_document_custom_name": "Отчёт Wildberries",
    })
    assert response.status_code == 201
    assert response.get_json()["status"] == "CONFIRMED"
    assert len(sent) == 1
    assert sent[0][1] == f"{PRODUCTION_API_BASE_URL}/lk/documents/create?pg=lp"
    with app.app_context():
        assert ImportJob.query.count() == 1
        assert SubmissionBatch.query.count() == 1
        assert ProcessedKiz.query.count() == 1
    assert app.test_client().get(f"/api/imports/{token}").status_code == 404


def _make_app_with_rows(tmp_path, count=3):
    app = create_cz_app(
        instance_path=str(tmp_path),
        db_uri=f"sqlite:///{tmp_path / 'selection.db'}",
        testing=True,
    )
    app.extensions["cz_signer"] = FakeSigner()
    app.extensions["cz_auth_transport"] = FakeAuthTransport()
    sent = []

    def submit_transport(method, url, **kwargs):
        sent.append((method, url, kwargs))
        return FakeResponse()

    app.extensions["cz_submit_transport"] = submit_transport
    with app.app_context():
        profile = db.session.get(OrganizationProfile, "org-sinyavin")
        profile.inn = "123456789012"
        profile.certificate_thumbprint = "A" * 40
        profile.fias_id = "12345678-1234-1234-1234-123456789012"
        db.session.commit()
        rows = [
            AcceptedRow(
                row_index=i + 2,
                ki=f"010123456789012321SERIAL{i:07d}",
                check_number=f"CHK-{i}", fn_number=f"FN-{i}",
                cost_kopecks=10000 * (i + 1), date="2026-09-04",
            )
            for i in range(count)
        ]
        result = ImportResult(
            accepted=rows, excluded=[],
            summary=ImportSummary(total_rows=count, accepted=count, excluded=0),
        )
        token = app.extensions["active_imports"].create(profile.id, result)
    return app, token, sent


def _submit(client, token, selected_rows):
    return client.post(f"/api/imports/{token}/submit", json={
        "action_date": "2026-09-04",
        "document_date": "2026-09-04",
        "document_number": "WB-selection",
        "primary_document_custom_name": "Отчёт Wildberries",
        "selected_rows": selected_rows,
    })


def test_submit_selected_subset_and_preserve_remainder(tmp_path):
    app, token, sent = _make_app_with_rows(tmp_path)
    response = _submit(app.test_client(), token, [2, 4])
    body = response.get_json()
    assert response.status_code == 201
    assert body["submitted"] == 2
    assert body["remaining_import"]["summary"]["accepted"] == 1
    assert [r["row_index"] for r in body["remaining_import"]["accepted"]] == [3]

    outer = sent[0][2]["json"]
    inner = json.loads(base64.b64decode(outer["product_document"]))
    assert len(inner["products"]) == 2
    assert {p["primary_document_number"] for p in inner["products"]} == {"WB-selection"}
    with app.app_context():
        assert ProcessedKiz.query.count() == 2
    assert app.test_client().get(f"/api/imports/{token}").status_code == 404
    remainder_token = body["remaining_import"]["import_token"]
    assert app.test_client().get(f"/api/imports/{remainder_token}").status_code == 200


def test_submit_rejects_empty_selection(tmp_path):
    app, token, sent = _make_app_with_rows(tmp_path)
    response = _submit(app.test_client(), token, [])
    assert response.status_code == 400
    assert response.get_json()["code"] == "invalid_selection"
    assert sent == []


def test_submit_rejects_forged_or_duplicate_selection(tmp_path):
    app, token, sent = _make_app_with_rows(tmp_path)
    assert _submit(app.test_client(), token, [999]).status_code == 400
    assert _submit(app.test_client(), token, [2, 2]).status_code == 400
    assert sent == []
