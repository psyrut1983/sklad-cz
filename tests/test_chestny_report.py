"""
Тесты отчёта и очистки (Этап 6).

Без реальной сети/ЧЗ/подписи.
"""

from __future__ import annotations

import io
import json
import os
import tempfile

import pytest

from app.chestny.factory import create_cz_app, db
from app.chestny.models import OrganizationProfile
from app.chestny.services.packaging import (
    CONFIRMED,
    FAILED,
    PARTIAL,
    PENDING,
    Package,
    PackageItem,
    PackageStore,
)
from app.services.excel_import import ImportSummary


@pytest.fixture
def app():
    tmp_dir = tempfile.mkdtemp()
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)

    app = create_cz_app(
        instance_path=tmp_dir,
        db_uri=f"sqlite:///{db_path}",
        testing=True,
    )
    ctx = app.app_context()
    ctx.push()
    try:
        yield app
    finally:
        db.session.close()
        db.engine.dispose()
        ctx.pop()
        os.unlink(db_path)
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def store(app):
    return app.extensions["package_store"]


def _make_package(
    profile_id: str = "org-sinyavin",
    status: str = CONFIRMED,
    n_batches: int = 1,
    items_per_batch: int = 2,
) -> Package:
    summary = ImportSummary(total_rows=10, accepted=10, excluded=0, accepted_submitted=items_per_batch * n_batches)
    pkg = Package(
        id=f"pkg-{profile_id}-{status}",
        profile_id=profile_id,
        import_token="tok-" + profile_id,
        status=status,
        summary=summary,
    )
    for b in range(n_batches):
        batch_items = []
        for i in range(items_per_batch):
            batch_items.append(PackageItem(
                ki31=f"010123456789099921MASK{b}{i:02d}ABCDE",
                hmac=f"hmac-{b}-{i}",
                mask=f"0101****{b}{i:02d}CDE",
                check=f"CHK{b}{i}",
                fn=f"FN{b}{i}",
                cost_kopecks=100,
                date="2026-09-03",
            ))
        from app.chestny.services.packaging import BatchItem
        pkg.batches.append(BatchItem(
            index=b,
            items=batch_items,
            status=CONFIRMED if status != FAILED else FAILED,
            document_id=f"doc-{b}" if status != FAILED else None,
        ))
    return pkg


# ═════════════════════════════════════════════════════════════════════════════
#  Список пакетов
# ═════════════════════════════════════════════════════════════════════════════


class TestListPackages:
    def test_empty(self, client):
        resp = client.get("/api/packages/org-sinyavin")
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_one_package(self, client, store):
        pkg = _make_package()
        store.save(pkg)
        resp = client.get("/api/packages/org-sinyavin")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 1
        assert data[0]["status"] == CONFIRMED

    def test_no_ki_in_response(self, client, store):
        pkg = _make_package()
        store.save(pkg)
        resp = client.get("/api/packages/org-sinyavin")
        text = json.dumps(resp.get_json())
        # Проверяем, что полный KI не утек
        assert "010123456789099921" not in text
        assert "ki31" not in text
        assert "ki" not in text

    def test_profile_filter(self, client, store):
        store.save(_make_package(profile_id="org-sinyavin"))
        store.save(_make_package(profile_id="org-krasikova"))
        resp = client.get("/api/packages/org-sinyavin")
        assert len(resp.get_json()) == 1


# ═════════════════════════════════════════════════════════════════════════════
#  Детали пакета
# ═════════════════════════════════════════════════════════════════════════════


class TestGetPackage:
    def test_found(self, client, store):
        pkg = _make_package()
        store.save(pkg)
        resp = client.get(f"/api/packages/{pkg.profile_id}/{pkg.id}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["id"] == pkg.id
        assert data["status"] == CONFIRMED
        assert "batches" in data

    def test_not_found(self, client):
        resp = client.get("/api/packages/org-sinyavin/nonexistent")
        assert resp.status_code == 404

    def test_wrong_profile(self, client, store):
        pkg = _make_package(profile_id="org-sinyavin")
        store.save(pkg)
        resp = client.get(f"/api/packages/org-krasikova/{pkg.id}")
        assert resp.status_code == 404

    def test_no_ki_in_detail(self, client, store):
        pkg = _make_package()
        store.save(pkg)
        resp = client.get(f"/api/packages/{pkg.profile_id}/{pkg.id}")
        text = json.dumps(resp.get_json())
        assert "010123456789099921" not in text


# ═════════════════════════════════════════════════════════════════════════════
#  XLSX выгрузка
# ═════════════════════════════════════════════════════════════════════════════


class TestXlsx:
    def test_generated(self, client, store):
        pkg = _make_package()
        store.save(pkg)
        resp = client.get(f"/api/packages/{pkg.profile_id}/{pkg.id}/xlsx")
        assert resp.status_code == 200
        assert resp.mimetype == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        assert resp.headers.get("Content-Disposition", "").startswith("attachment")

    def test_no_confirmed(self, client, store):
        pkg = _make_package(status=FAILED)
        store.save(pkg)
        resp = client.get(f"/api/packages/{pkg.profile_id}/{pkg.id}/xlsx")
        assert resp.status_code == 404

    def test_not_found(self, client):
        resp = client.get("/api/packages/org-sinyavin/nonexistent/xlsx")
        assert resp.status_code == 404


# ═════════════════════════════════════════════════════════════════════════════
#  DELETE пакета
# ═════════════════════════════════════════════════════════════════════════════


class TestDeletePackage:
    def test_delete_existing(self, client, store):
        pkg = _make_package()
        store.save(pkg)
        resp = client.delete(f"/api/packages/{pkg.id}")
        assert resp.status_code == 204
        assert store.get(pkg.id) is None

    def test_delete_nonexistent(self, client):
        resp = client.delete("/api/packages/nonexistent")
        assert resp.status_code == 204  # idempotent


# ═════════════════════════════════════════════════════════════════════════════
#  Cleanup на старте
# ═════════════════════════════════════════════════════════════════════════════


class TestCleanup:
    def test_orphan_packages_removed(self, app, store):
        """Пакет без связанного активного импорта удаляется при старте."""
        pkg = _make_package()
        pkg.import_token = "orphan-token"
        store.save(pkg)
        assert len(store) == 1

        # Симулируем cleanup при старте
        from app.chestny.factory import _cleanup_on_startup
        _cleanup_on_startup(app)

        assert len(store) == 0


# ═════════════════════════════════════════════════════════════════════════════
#  UI report button
# ═════════════════════════════════════════════════════════════════════════════


class TestUiReportSection:
    def test_report_section_in_html(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert 'id="report-section"' in html
        assert 'id="show-report-btn"' in html
        assert 'id="clear-report-btn"' in html
