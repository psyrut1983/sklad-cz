"""
Тесты import_routes: POST /api/imports/preview, GET/DELETE /api/imports/<token>.
Изолированная БД + temp instance_path + синтетический XLSX.
"""

from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path

import pytest

from app.chestny.factory import create_cz_app, db
from app.chestny.models import OrganizationProfile
from app.services.synthetic_xlsx import create_synthetic_xlsx

# ═════════════════════════════════════════════════════════════════════════════
#  Фикстуры
# ═════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def app():
    """Flask app с temp instance_path (HMAC-ключ создаётся там)."""
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


def _configure_profile(profile_id: str):
    """Устанавливает INN, thumbprint и fias_id для профиля."""
    p = db.session.get(OrganizationProfile, profile_id)
    assert p is not None, f"Profile {profile_id} not found"
    p.inn = "123456789012"
    p.certificate_thumbprint = "AABBCCDDEEAABBCCDDEEAABBCCDDEEAABBCCDDEE"
    p.fias_id = "f47ac10b-58cc-4372-a567-0e02b2c3d479"
    db.session.commit()


def _xlsx_bytes() -> bytes:
    """Создаёт синтетический XLSX и возвращает bytes."""
    path = create_synthetic_xlsx()
    data = Path(path).read_bytes()
    os.unlink(path)
    return data


def _post_preview(client, profile_id="org-sinyavin", **kw) -> tuple:
    """Удобный POST /api/imports/preview; возвращает (response, body)."""
    data = {"profile_id": profile_id, "file": (io.BytesIO(_xlsx_bytes()), "test.xlsx")}
    data.update(kw)
    resp = client.post("/api/imports/preview", data=data,
                       content_type="multipart/form-data")
    return resp, resp.get_json() if resp.is_json else None


# ═════════════════════════════════════════════════════════════════════════════
#  POST — success
# ═════════════════════════════════════════════════════════════════════════════


class TestPreviewSuccess:
    def test_synthetic_counts_and_clean_ki(self, client, app):
        """Синтетический XLSX → total=10, accepted=4, excluded=6, KI чистые."""
        _configure_profile("org-sinyavin")
        resp, body = _post_preview(client)
        assert resp.status_code == 201

        assert body["profile"]["id"] == "org-sinyavin"
        assert body["profile"]["display_name"] == "ИП Синявин"
        assert body["summary"]["total_rows"] == 10
        assert body["summary"]["accepted"] == 4
        assert body["summary"]["excluded"] == 6
        assert len(body["accepted"]) == 4
        assert len(body["excluded"]) == 6
        for row in body["accepted"]:
            assert len(row["ki"]) == 31
            assert row["ki"].startswith("01")
        assert "import_token" in body
        assert isinstance(body["expires_in"], int) and body["expires_in"] > 0

    def test_accepted_fields_types(self, client, app):
        """accepted: ki str, check str, fn str, cost_kopecks int, date str."""
        _configure_profile("org-sinyavin")
        resp, body = _post_preview(client)
        assert resp.status_code == 201
        row = body["accepted"][0]
        assert isinstance(row["ki"], str)
        assert isinstance(row["check_number"], str)
        assert isinstance(row["fn_number"], str)
        assert isinstance(row["cost_kopecks"], int)
        assert isinstance(row["date"], str)

    def test_both_profiles_and_profile_id_pinned(self, client, app):
        """Оба seeded профиля работают; ActiveImport.profile_id сохранён."""
        _configure_profile("org-sinyavin")
        _configure_profile("org-krasikova")
        for pid, pname in (("org-sinyavin", "ИП Синявин"),
                           ("org-krasikova", "ИП Красикова")):
            resp, body = _post_preview(client, profile_id=pid)
            assert resp.status_code == 201, f"Failed for {pid}"
            assert body["profile"]["id"] == pid
            assert body["profile"]["display_name"] == pname
            assert body["summary"]["accepted"] == 4
            assert body["summary"]["excluded"] == 6

            store = app.extensions["active_imports"]
            active = store.get(body["import_token"])
            assert active.profile_id == pid


# ═════════════════════════════════════════════════════════════════════════════
#  POST — ошибки (code/message)
# ═════════════════════════════════════════════════════════════════════════════


class TestPreviewErrors:
    def test_missing_config_422(self, client, app):
        """Профиль без INN → 422."""
        resp, body = _post_preview(client)
        assert resp.status_code == 422
        assert body["code"] == "profile_not_configured"
        assert "не настроен" in body["message"]

    def test_unknown_profile(self, client, app):
        """Неизвестный profile_id → 422."""
        resp, body = _post_preview(client, profile_id="org-nonexistent")
        assert resp.status_code == 422
        assert body["code"] == "profile_not_found"
        assert "не найден" in body["message"]

    def test_missing_profile_id(self, client, app):
        """Без profile_id → 400."""
        data = {"file": (io.BytesIO(_xlsx_bytes()), "test.xlsx")}
        resp = client.post("/api/imports/preview", data=data,
                           content_type="multipart/form-data")
        assert resp.status_code == 400
        assert resp.get_json()["code"] == "missing_profile_id"

    def test_missing_file(self, client, app):
        """Без файла → 400."""
        _configure_profile("org-sinyavin")
        data = {"profile_id": "org-sinyavin"}
        resp = client.post("/api/imports/preview", data=data,
                           content_type="multipart/form-data")
        assert resp.status_code == 400
        assert resp.get_json()["code"] == "missing_file"

    def test_invalid_extension(self, client, app):
        """Не .xlsx → 400."""
        _configure_profile("org-sinyavin")
        data = {
            "profile_id": "org-sinyavin",
            "file": (io.BytesIO(b"fake"), "test.csv"),
        }
        resp = client.post("/api/imports/preview", data=data,
                           content_type="multipart/form-data")
        assert resp.status_code == 400
        assert resp.get_json()["code"] == "invalid_extension"

    def test_empty_file(self, client, app):
        """Пустой файл → 400."""
        _configure_profile("org-sinyavin")
        data = {
            "profile_id": "org-sinyavin",
            "file": (io.BytesIO(b""), "empty.xlsx"),
        }
        resp = client.post("/api/imports/preview", data=data,
                           content_type="multipart/form-data")
        assert resp.status_code == 400
        assert resp.get_json()["code"] == "empty_file"

    def test_file_too_large(self, client, app):
        """Файл >10 MiB → 413 JSON (не HTML)."""
        data = {
            "profile_id": "org-sinyavin",
            "file": (io.BytesIO(b"x" * (10 * 1024 * 1024 + 1)), "big.xlsx"),
        }
        resp = client.post("/api/imports/preview", data=data,
                           content_type="multipart/form-data")
        assert resp.status_code == 413
        assert resp.is_json
        assert resp.get_json()["code"] == "file_too_large"

    def test_invalid_xlsx(self, client, app):
        """Повреждённый XLSX → 400."""
        _configure_profile("org-sinyavin")
        data = {
            "profile_id": "org-sinyavin",
            "file": (io.BytesIO(b"not a real xlsx content"), "bad.xlsx"),
        }
        resp = client.post("/api/imports/preview", data=data,
                           content_type="multipart/form-data")
        assert resp.status_code == 400
        assert resp.get_json()["code"] == "invalid_xlsx"

    def test_confirmed_duplicate_from_other_profile(self, client, app):
        """Дубликат из другого профиля → excluded с display_name в сообщении."""
        _configure_profile("org-sinyavin")

        raw = _xlsx_bytes()

        from app.chestny.services.dedup import hmac_digest, load_or_create_hmac_key
        from app.chestny.models import ProcessedKiz
        from datetime import datetime, timezone

        from app.services.excel_import import parse_xlsx
        tmp_result = parse_xlsx(io.BytesIO(raw))
        target_ki = tmp_result.accepted[0].ki

        key = load_or_create_hmac_key(app.instance_path)
        digest = hmac_digest(target_ki, key)
        pk = ProcessedKiz(
            hmac_digest=digest,
            mask=target_ki[:4] + "****" + target_ki[-4:],
            profile_id="org-krasikova",
            status="CONFIRMED",
            document_id="doc-123",
            processed_at=datetime.now(timezone.utc),
        )
        db.session.add(pk)
        db.session.commit()

        resp, body = _post_preview(client)
        assert resp.status_code == 201

        assert body["summary"]["accepted"] == 3
        assert body["summary"]["excluded"] == 7
        assert body["summary"]["total_rows"] == 10

        dup_excluded = [e for e in body["excluded"]
                        if e["reason_code"] == "previously_confirmed"]
        assert len(dup_excluded) == 1
        msg = dup_excluded[0]["message"]
        assert "Ранее подтверждён: ИП Красикова" in msg
        assert "документ: doc-123" in msg
        assert "ki" not in dup_excluded[0]
        assert "digest" not in msg.lower()

    def test_no_sign_auth_network_calls_post(self, client, app, monkeypatch):
        """POST: _sign_data/get_uuid_token/requests не вызваны."""
        _configure_profile("org-sinyavin")

        import app.chestny.import_routes as ir_module
        import requests

        calls = []

        def _fail(*args, **kwargs):
            calls.append(1)
            raise RuntimeError("Неожиданный вызов")

        monkeypatch.setattr(ir_module, "_sign_data", _fail, raising=False)
        monkeypatch.setattr(ir_module, "get_uuid_token", _fail, raising=False)
        monkeypatch.setattr(requests, "get", _fail)
        monkeypatch.setattr(requests, "post", _fail)

        resp, _ = _post_preview(client)
        assert resp.status_code == 201
        assert len(calls) == 0, f"Обнаружены неожиданные вызовы: {calls}"

    def test_store_full_503(self, client, app, monkeypatch):
        """6-й импорт при max=5 → 503 code=store_full, без сети."""
        _configure_profile("org-sinyavin")

        import app.chestny.import_routes as ir_module
        import requests

        calls = []

        def _fail(*args, **kwargs):
            calls.append(1)
            raise RuntimeError("Неожиданный вызов")

        monkeypatch.setattr(ir_module, "_sign_data", _fail, raising=False)
        monkeypatch.setattr(ir_module, "get_uuid_token", _fail, raising=False)
        monkeypatch.setattr(requests, "get", _fail)
        monkeypatch.setattr(requests, "post", _fail)

        # Заполняем 5 слотов
        for i in range(5):
            data = {
                "profile_id": "org-sinyavin",
                "file": (io.BytesIO(_xlsx_bytes()), f"f{i}.xlsx"),
            }
            resp = client.post("/api/imports/preview", data=data,
                               content_type="multipart/form-data")
            assert resp.status_code == 201, f"Slot {i} failed"

        # 6-й — переполнение
        data = {
            "profile_id": "org-sinyavin",
            "file": (io.BytesIO(_xlsx_bytes()), "overflow.xlsx"),
        }
        resp = client.post("/api/imports/preview", data=data,
                           content_type="multipart/form-data")
        assert resp.status_code == 503
        assert resp.is_json
        assert resp.get_json()["code"] == "store_full"
        assert len(calls) == 0, f"Обнаружены неожиданные вызовы: {calls}"


# ═════════════════════════════════════════════════════════════════════════════
#  GET /api/imports/<token>
# ═════════════════════════════════════════════════════════════════════════════


class TestGetImport:
    def test_get_returns_same_preview(self, client, app):
        """GET после POST возвращает те же данные."""
        _configure_profile("org-sinyavin")
        post_resp, post_body = _post_preview(client)
        assert post_resp.status_code == 201
        token = post_body["import_token"]

        get_resp = client.get(f"/api/imports/{token}")
        assert get_resp.status_code == 200
        assert get_resp.get_json() == post_body

    def test_get_no_crypto_xlsx_hmac(self, client, app):
        """GET не возвращает криптохвост, исходный XLSX, HMAC-ключ/дайджест."""
        _configure_profile("org-sinyavin")
        _, body = _post_preview(client)
        token = body["import_token"]

        get_resp = client.get(f"/api/imports/{token}")
        assert get_resp.status_code == 200
        text = get_resp.get_data(as_text=True).lower()
        assert "crypt" not in text
        assert "raw" not in text
        assert "hmac" not in text
        assert "digest" not in text
        assert "xlsx" not in text

    def test_get_unknown_404(self, client, app):
        """Неизвестный token → 404."""
        resp = client.get("/api/imports/nonexistent-token")
        assert resp.status_code == 404
        assert resp.get_json()["code"] == "import_not_found"

    def test_get_expired_410(self, client, app):
        """Истёкший импорт → 410."""
        _configure_profile("org-sinyavin")
        _, body = _post_preview(client)
        token = body["import_token"]

        # Форсируем expiry через подмену ActiveImport в store
        from app.chestny.services.active_imports import ActiveImport
        store = app.extensions["active_imports"]
        old = store._imports[token]
        expired = ActiveImport(
            token=old.token,
            profile_id=old.profile_id,
            accepted=old.accepted,
            excluded=old.excluded,
            summary=old.summary,
            created_at=0.0,
            expires_at=1.0,
        )
        store._imports[token] = expired

        resp = client.get(f"/api/imports/{token}")
        assert resp.status_code == 410
        assert resp.get_json()["code"] == "import_expired"

    def test_get_no_sign_auth_network_calls(self, client, app, monkeypatch):
        """GET: _sign_data/get_uuid_token/requests не вызваны."""
        _configure_profile("org-sinyavin")
        _, body = _post_preview(client)
        token = body["import_token"]

        import app.chestny.import_routes as ir_module
        import requests

        calls = []

        def _fail(*args, **kwargs):
            calls.append(1)
            raise RuntimeError("Неожиданный вызов")

        monkeypatch.setattr(ir_module, "_sign_data", _fail, raising=False)
        monkeypatch.setattr(ir_module, "get_uuid_token", _fail, raising=False)
        monkeypatch.setattr(requests, "get", _fail)
        monkeypatch.setattr(requests, "post", _fail)

        resp = client.get(f"/api/imports/{token}")
        assert resp.status_code == 200
        assert len(calls) == 0, f"Обнаружены неожиданные вызовы: {calls}"


# ═════════════════════════════════════════════════════════════════════════════
#  DELETE /api/imports/<token>
# ═════════════════════════════════════════════════════════════════════════════


class TestDeleteImport:
    def test_delete_removes_import(self, client, app):
        """DELETE → импорт удалён, GET → 404."""
        _configure_profile("org-sinyavin")
        _, body = _post_preview(client)
        token = body["import_token"]

        del_resp = client.delete(f"/api/imports/{token}")
        assert del_resp.status_code == 204
        assert del_resp.get_data() == b""

        get_resp = client.get(f"/api/imports/{token}")
        assert get_resp.status_code == 404
        assert get_resp.get_json()["code"] == "import_not_found"

    def test_delete_idempotent(self, client, app):
        """Повторный DELETE → 204 (безопасно)."""
        _configure_profile("org-sinyavin")
        _, body = _post_preview(client)
        token = body["import_token"]

        assert client.delete(f"/api/imports/{token}").status_code == 204
        assert client.delete(f"/api/imports/{token}").status_code == 204
        assert client.delete(f"/api/imports/{token}").status_code == 204

    def test_delete_nonexistent_204(self, client, app):
        """DELETE неизвестного token → 204 (идемпотентность)."""
        assert client.delete("/api/imports/nonexistent").status_code == 204

    def test_delete_no_sign_auth_network_calls(self, client, app, monkeypatch):
        """DELETE: _sign_data/get_uuid_token/requests не вызваны."""
        _configure_profile("org-sinyavin")
        _, body = _post_preview(client)
        token = body["import_token"]

        import app.chestny.import_routes as ir_module
        import requests

        calls = []

        def _fail(*args, **kwargs):
            calls.append(1)
            raise RuntimeError("Неожиданный вызов")

        monkeypatch.setattr(ir_module, "_sign_data", _fail, raising=False)
        monkeypatch.setattr(ir_module, "get_uuid_token", _fail, raising=False)
        monkeypatch.setattr(requests, "get", _fail)
        monkeypatch.setattr(requests, "post", _fail)

        resp = client.delete(f"/api/imports/{token}")
        assert resp.status_code == 204
        assert len(calls) == 0, f"Обнаружены неожиданные вызовы: {calls}"


# ═════════════════════════════════════════════════════════════════════════════
#  Интеграционные
# ═════════════════════════════════════════════════════════════════════════════


class TestIntegration:
    def test_targeted_import_dedup_active_twice(self, client, app):
        """Два последовательных импорта + GET + DELETE."""
        _configure_profile("org-sinyavin")
        raw = _xlsx_bytes()

        # Первый импорт
        data1 = {
            "profile_id": "org-sinyavin",
            "file": (io.BytesIO(raw), "a.xlsx"),
        }
        resp1 = client.post("/api/imports/preview", data=data1,
                            content_type="multipart/form-data")
        assert resp1.status_code == 201
        b1 = resp1.get_json()
        assert b1["summary"]["accepted"] == 4
        assert b1["summary"]["excluded"] == 6

        # Второй импорт
        data2 = {
            "profile_id": "org-sinyavin",
            "file": (io.BytesIO(raw), "b.xlsx"),
        }
        resp2 = client.post("/api/imports/preview", data=data2,
                            content_type="multipart/form-data")
        assert resp2.status_code == 201
        b2 = resp2.get_json()
        assert b2["summary"]["accepted"] == 4
        assert b2["summary"]["excluded"] == 6
        assert b2["import_token"] != b1["import_token"]

        # GET для обоих
        g1 = client.get(f"/api/imports/{b1['import_token']}")
        assert g1.status_code == 200
        assert g1.get_json() == b1

        g2 = client.get(f"/api/imports/{b2['import_token']}")
        assert g2.status_code == 200
        assert g2.get_json() == b2

        # DELETE первого
        assert client.delete(f"/api/imports/{b1['import_token']}").status_code == 204
        assert client.get(f"/api/imports/{b1['import_token']}").status_code == 404
        # Второй всё ещё жив
        assert client.get(f"/api/imports/{b2['import_token']}").status_code == 200


def test_py_compile():
    """Проверка компиляции import_routes.py и этого файла."""
    import py_compile
    py_compile.compile(
        "/root/.openclaw/workspace/sklad-cz/app/chestny/import_routes.py",
        doraise=True,
    )
    py_compile.compile(__file__, doraise=True)
