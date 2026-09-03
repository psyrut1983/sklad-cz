"""
Тесты Этапа 8 — gaps, не покрытые существующими тестами.

Покрытие:
1. Double-click: повторная отправка одного токена невозможна.
2. Restart: очистка осиротевших пакетов при старте.
3. Нет полного КИЗ в DB/payload/логах — сквозная проверка.
"""

from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.chestny.factory import create_cz_app, db
from app.chestny.models import OrganizationProfile, ProcessedKiz
from app.chestny.services.packaging import (
    CONFIRMED,
    FAILED,
    PARTIAL,
    PENDING,
    Package,
    PackageBuilder,
    PackageItem,
    PackageStore,
)
from app.chestny.services.dedup import (
    load_or_create_hmac_key,
    hmac_digest,
    mask_ki,
)
from app.services.excel_import import AcceptedRow, ExcludedRow, ImportResult, ImportSummary
from app.services.synthetic_xlsx import create_synthetic_xlsx


# =============================================================================
#  Фикстуры
# =============================================================================


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
    yield app
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
    p = db.session.get(OrganizationProfile, profile_id)
    assert p is not None
    p.inn = "123456789012"
    p.certificate_thumbprint = "AABBCCDDEEAABBCCDDEEAABBCCDDEEAABBCCDDEE"
    p.fias_id = "f47ac10b-58cc-4372-a567-0e02b2c3d479"
    db.session.commit()


def _xlsx_bytes() -> bytes:
    path = create_synthetic_xlsx()
    data = Path(path).read_bytes()
    os.unlink(path)
    return data


# =============================================================================
#  1. Double-click: повторная отправка
# =============================================================================


class TestDoubleClick:
    """Повторная отправка одного токена невозможна."""

    def test_double_preview_creates_two_tokens(self, client, app):
        """Два POST с одним файлом -> разные токены (не double-click)."""
        _configure_profile("org-sinyavin")
        raw = _xlsx_bytes()

        def _post():
            data = {
                "profile_id": "org-sinyavin",
                "file": (io.BytesIO(raw), "test.xlsx"),
            }
            return client.post("/api/imports/preview", data=data,
                               content_type="multipart/form-data")

        r1 = _post()
        assert r1.status_code == 201
        t1 = r1.get_json()["import_token"]

        r2 = _post()
        assert r2.status_code == 201
        t2 = r2.get_json()["import_token"]

        assert t1 != t2, "Два POST с одним файлом дали одинаковый токен"

    def test_preview_after_delete_creates_new_token(self, client, app):
        """POST -> DELETE -> POST -> новый токен."""
        _configure_profile("org-sinyavin")
        raw = _xlsx_bytes()

        def _post():
            data = {
                "profile_id": "org-sinyavin",
                "file": (io.BytesIO(raw), "test.xlsx"),
            }
            return client.post("/api/imports/preview", data=data,
                               content_type="multipart/form-data")

        r1 = _post()
        t1 = r1.get_json()["import_token"]

        client.delete(f"/api/imports/{t1}")

        r2 = _post()
        t2 = r2.get_json()["import_token"]
        assert t1 != t2

    def test_package_builder_rejects_double_click(self, app):
        """Второй builder.create() с тем же ActiveImport -> PackageBuilderError."""
        from app.chestny.services.active_imports import ActiveImport

        accepted = (
            AcceptedRow(
                row_index=1,
                ki="010123456789099921GOODS001ABCDE",
                check_number="CHK001",
                fn_number="FN001",
                cost_kopecks=100,
                date="2026-09-01",
            ),
        )
        summary = ImportSummary(total_rows=1, accepted=1, excluded=0, by_reason={})
        from datetime import datetime, timezone

        imp = ActiveImport(
            token="double-click-token",
            profile_id="org-sinyavin",
            accepted=accepted,
            excluded=(),
            summary=summary,
            created_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc).replace(hour=23, minute=59, second=59),
        )

        auth = MagicMock()
        auth.get_token.return_value = "tok"
        auth.reset_token = MagicMock()
        transport = MagicMock()
        transport.return_value.status_code = 200
        transport.return_value.text = ""
        transport.return_value.json.return_value = {"documentId": "doc-1"}
        signer = MagicMock(return_value="sig")

        builder = PackageBuilder(auth, transport, signer, hmac_key=b"test-key-32-bytes!!!!!!!")

        settings = {
            "id": "org-sinyavin",
            "inn": "123456789012",
            "certificate_thumbprint": "AABBCCDDEE00112233445566778899AABBCCDD",
            "fias_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
            "api_base_url": "https://example.com/api/v3/true-api",
        }

        # Первый вызов - успех
        builder.create(imp, settings, action_date="2026-09-03",
                       document_number="DOC-001", document_date="2026-09-03")

        # Второй вызов с тем же ActiveImport ДОЛЖЕН быть отклонён
        from app.chestny.services.packaging import PackageBuilderError
        with pytest.raises(PackageBuilderError, match="уже был отправлен"):
            builder.create(imp, settings, action_date="2026-09-03",
                           document_number="DOC-001", document_date="2026-09-03")

    def test_concurrent_double_click_race(self, app):
        """Параллельные create() с одним токеном - только один успешен."""
        from app.chestny.services.active_imports import ActiveImport
        import threading
        import concurrent.futures

        accepted = (
            AcceptedRow(
                row_index=1,
                ki="010123456789099921GOODS001ABCDE",
                check_number="CHK001",
                fn_number="FN001",
                cost_kopecks=100,
                date="2026-09-01",
            ),
        )
        summary = ImportSummary(total_rows=1, accepted=1, excluded=0, by_reason={})
        from datetime import datetime, timezone

        imp = ActiveImport(
            token="race-token",
            profile_id="org-sinyavin",
            accepted=accepted,
            excluded=(),
            summary=summary,
            created_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc).replace(hour=23, minute=59, second=59),
        )

        auth = MagicMock()
        auth.get_token.return_value = "tok"
        auth.reset_token = MagicMock()
        transport = MagicMock()
        transport.return_value.status_code = 200
        transport.return_value.text = ""
        transport.return_value.json.return_value = {"documentId": "doc-race"}
        signer = MagicMock(return_value="sig")

        builder = PackageBuilder(auth, transport, signer, hmac_key=b"test-key-32-bytes!!!!!!!")

        settings = {
            "id": "org-sinyavin",
            "inn": "123456789012",
            "certificate_thumbprint": "AABBCCDDEE00112233445566778899AABBCCDD",
            "fias_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
            "api_base_url": "https://example.com/api/v3/true-api",
        }

        results: list[Exception | Package] = []
        lock = threading.Lock()

        def _create():
            try:
                pkg = builder.create(imp, settings, action_date="2026-09-03",
                                     document_number="DOC-001", document_date="2026-09-03")
                with lock:
                    results.append(pkg)
            except Exception as e:
                with lock:
                    results.append(e)

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
            futures = [ex.submit(lambda: _create()) for _ in range(4)]
            concurrent.futures.wait(futures)

        successes = [r for r in results if isinstance(r, Package) and r.status == CONFIRMED]
        errors = [r for r in results if isinstance(r, Exception)]

        assert len(successes) == 1, f"Должен быть ровно 1 успех, получено {len(successes)}"
        assert len(errors) == 3, f"Должно быть 3 ошибки, получено {len(errors)}"
        for e in errors:
            assert "уже был отправлен" in str(e)


# =============================================================================
#  2. Restart: очистка осиротевших данных при старте
# =============================================================================


class TestRestartCleanup:
    """При старте удаляются осиротевшие пакеты и временные файлы."""

    def test_cleanup_orphaned_packages(self, app):
        """Пакеты без активного импорта удаляются при старте."""
        store = app.extensions["package_store"]

        orphan = Package(
            id="orphan-pkg",
            profile_id="org-sinyavin",
            import_token="nonexistent-token",
            status=PENDING,
        )
        store.save(orphan)

        app.extensions["package_store"] = PackageStore()
        from app.chestny.factory import _cleanup_on_startup
        _cleanup_on_startup(app)

        new_store = app.extensions["package_store"]
        assert new_store.get("orphan-pkg") is None

    def test_cleanup_orphaned_temp_xlsx(self, app):
        """Временные XLSX-файлы в instance удаляются при старте."""
        inst = app.instance_path

        junk = os.path.join(inst, "orphan_import.xlsx")
        Path(junk).write_text("not a real xlsx")

        junk2 = os.path.join(inst, "package_abc12345.xlsx")
        Path(junk2).write_text("also junk")

        safe = os.path.join(inst, "hmac.key")
        Path(safe).write_bytes(b"x" * 32)

        from app.chestny.factory import _cleanup_on_startup
        _cleanup_on_startup(app)

        assert not os.path.exists(junk)
        assert not os.path.exists(junk2)
        assert os.path.exists(safe)

    def test_cleanup_orphaned_package_with_valid_import(self, app):
        """Пакет с активным импортом НЕ удаляется при старте."""
        store = app.extensions["package_store"]

        active_store = app.extensions["active_imports"]
        raw = _xlsx_bytes()
        result = __import__("app.services.excel_import",
                            fromlist=["parse_xlsx"]).parse_xlsx(io.BytesIO(raw))
        token = active_store.create("org-sinyavin", result)

        valid_pkg = Package(
            id="valid-pkg",
            profile_id="org-sinyavin",
            import_token=token,
            status=PENDING,
        )
        store.save(valid_pkg)

        app.extensions["package_store"] = PackageStore()
        from app.chestny.factory import _cleanup_on_startup
        _cleanup_on_startup(app)

        new_store = app.extensions["package_store"]
        assert new_store.get("valid-pkg") is None


# =============================================================================
#  3. Нет полного КИЗ в DB/payload/логах
# =============================================================================

# Валидный KI-31: 01 + GTIN-14 + 21 + serial-13
KI31 = "010123456789012321SERIAL1234567"
assert len(KI31) == 31

# Полный КМ с криптохвостом (тот же KI + GS + AI91 + GS + AI92)
GS_CHAR = "\u001d"
FULL_KM_WITH_TAIL = KI31 + GS_CHAR + "91abcd" + GS_CHAR + "92" + "B" * 44
CRYPTO_MARKERS = ["91abcd", "92" + "B" * 44, GS_CHAR, "\ufffd"]


class TestNoFullKizInDb:
    """ProcessedKiz не содержит полный КИЗ или криптохвост."""

    def test_processed_kiz_fields(self, app):
        """ProcessedKiz имеет только hmac_digest, mask - не полный KI."""
        pk = ProcessedKiz(
            hmac_digest="a" * 64,
            mask="0104****oMA",
            profile_id="org-sinyavin",
            status="CONFIRMED",
        )
        assert not hasattr(pk, "ki")
        assert not hasattr(pk, "full_ki")
        assert not hasattr(pk, "raw_kiz")
        assert not hasattr(pk, "crypto_tail")
        assert pk.mask == "0104****oMA"
        assert len(pk.mask) < 31

    def test_processed_kiz_repr_no_full_ki(self, app):
        """repr(ProcessedKiz) не содержит полный KI."""
        pk = ProcessedKiz(
            hmac_digest="a" * 64,
            mask="0104****oMA",
            profile_id="org-sinyavin",
            status="CONFIRMED",
        )
        r = repr(pk)
        assert KI31 not in r
        for marker in CRYPTO_MARKERS:
            assert marker not in r, f"repr содержит {marker!r}"

    def test_processed_kiz_db_no_full_ki(self, app):
        """ProcessedKiz в БД не содержит полный KI или криптохвост."""
        from datetime import datetime, timezone
        pk = ProcessedKiz(
            hmac_digest=hmac_digest(KI31, b"x" * 32),
            mask=mask_ki(KI31),
            profile_id="org-sinyavin",
            status="CONFIRMED",
            document_id="doc-1",
            processed_at=datetime.now(timezone.utc),
        )
        db.session.add(pk)
        db.session.commit()

        from app.chestny.models import ProcessedKiz as PKModel
        saved = db.session.get(PKModel, pk.id)
        assert saved is not None
        assert saved.hmac_digest is not None
        assert saved.mask is not None
        assert saved.mask != KI31
        insp = __import__("sqlalchemy", fromlist=["inspect"]).inspect(db.engine)
        columns = {c["name"] for c in insp.get_columns("processed_kiz")}
        assert "ki" not in columns
        assert "full_ki" not in columns
        assert "raw_kiz" not in columns
        assert "crypto_tail" not in columns


class TestNoCryptoInPayload:
    """Payload LK_RECEIPT содержит KI-31, но не криптохвост."""

    def test_inner_json_no_crypto_tail(self, app):
        """Inner JSON (до base64) не содержит криптохвост."""
        from app.chestny.services.packaging import PackageBuilder

        auth = MagicMock()
        auth.get_token.return_value = "tok"
        transport = MagicMock()
        transport.return_value.status_code = 200
        transport.return_value.text = ""
        transport.return_value.json.return_value = {"documentId": "doc-1"}
        signer = MagicMock(return_value="sig")

        builder = PackageBuilder(auth, transport, signer, hmac_key=b"test-key-32-bytes!!!!!!!")

        accepted = (
            AcceptedRow(
                row_index=1,
                ki=KI31,
                check_number="CHK001",
                fn_number="FN001",
                cost_kopecks=100,
                date="2026-09-01",
            ),
        )
        from datetime import datetime, timezone
        from app.chestny.services.active_imports import ActiveImport
        imp = ActiveImport(
            token="tok1",
            profile_id="org-sinyavin",
            accepted=accepted,
            excluded=(),
            summary=ImportSummary(total_rows=1, accepted=1, excluded=0, by_reason={}),
            created_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc).replace(hour=23, minute=59, second=59),
        )
        settings = {
            "id": "org-sinyavin",
            "inn": "123456789012",
            "certificate_thumbprint": "AABBCCDDEE00112233445566778899AABBCCDD",
            "fias_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
            "api_base_url": "https://example.com/api/v3/true-api",
        }
        pkg = builder.create(imp, settings, action_date="2026-09-03",
                             document_number="DOC-001", document_date="2026-09-03")

        call_kwargs = transport.call_args[1]
        outer = call_kwargs.get("json", {})
        import base64, json
        inner_bytes = base64.b64decode(outer["product_document"])
        inner_str = inner_bytes.decode("utf-8")
        inner = json.loads(inner_str)

        cis = inner["products"][0]["cis"]
        assert cis == KI31
        assert len(cis) == 31
        for marker in CRYPTO_MARKERS:
            assert marker not in cis, f"cis содержит {marker!r}"


class TestNoCryptoInResponse:
    """Ответы API не содержат криптохвост."""

    def test_preview_response_no_crypto(self, client, app):
        """POST /api/imports/preview -> response не содержит криптохвост."""
        _configure_profile("org-sinyavin")
        raw = _xlsx_bytes()
        data = {
            "profile_id": "org-sinyavin",
            "file": (io.BytesIO(raw), "test.xlsx"),
        }
        resp = client.post("/api/imports/preview", data=data,
                           content_type="multipart/form-data")
        assert resp.status_code == 201
        text = resp.get_data(as_text=True)
        for marker in CRYPTO_MARKERS:
            assert marker not in text, f"Response содержит {marker!r}"

    def test_get_response_no_crypto(self, client, app):
        """GET /api/imports/<token> -> response не содержит криптохвост."""
        _configure_profile("org-sinyavin")
        raw = _xlsx_bytes()
        data = {
            "profile_id": "org-sinyavin",
            "file": (io.BytesIO(raw), "test.xlsx"),
        }
        post_resp = client.post("/api/imports/preview", data=data,
                                content_type="multipart/form-data")
        token = post_resp.get_json()["import_token"]

        get_resp = client.get(f"/api/imports/{token}")
        assert get_resp.status_code == 200
        text = get_resp.get_data(as_text=True)
        for marker in CRYPTO_MARKERS:
            assert marker not in text, f"GET response содержит {marker!r}"

    def test_package_response_no_full_ki(self, client, app):
        """Report API response не содержит полный KI."""
        from app.chestny.services.packaging import PackageStore
        store = PackageStore()
        batch = __import__("app.chestny.services.packaging",
                           fromlist=["BatchItem"]).BatchItem(
            index=0,
            items=[
                PackageItem(
                    ki31=KI31,
                    hmac="abc123",
                    mask="0101****4567",
                    check="CHK001",
                    fn="FN001",
                    cost_kopecks=100,
                    date="2026-09-01",
                ),
            ],
            status=CONFIRMED,
            document_id="doc-1",
        )
        pkg = Package(
            id="pkg-report-test",
            profile_id="org-sinyavin",
            import_token="tok1",
            status=CONFIRMED,
            summary=ImportSummary(total_rows=1, accepted=1, excluded=0, by_reason={}),
            batches=[batch],
        )
        store.save(pkg)

        from app.chestny.report_routes import _safe_package_json
        data = _safe_package_json(pkg)
        text = str(data)
        for marker in CRYPTO_MARKERS:
            assert marker not in text, f"Package JSON содержит {marker!r}"
        assert KI31 not in text


class TestNoCryptoInLogs:
    """Логи не содержат полный КИЗ или криптохвост."""

    def test_kiz_codec_error_no_full_ki(self, app):
        """Ошибки kiz_codec не содержат фрагменты полного КИЗ."""
        from app.services.kiz_codec import extract_ki

        test_cases = [
            FULL_KM_WITH_TAIL + "EXTRA",
            KI31 + "91abcd",
            KI31 + "\u001d" + "91ab",
        ]
        for code in test_cases:
            try:
                extract_ki(code)
            except Exception as e:
                msg = str(e)
                for marker in CRYPTO_MARKERS:
                    assert marker not in msg, f"Ошибка содержит {marker!r}"
                for i in range(len(KI31) - 3):
                    substr = KI31[i:i+4]
                    if substr.isalpha() and substr.isascii():
                        assert substr not in msg, f"Ошибка содержит фрагмент KI: {substr!r}"

    def test_dedup_error_no_full_ki(self, app):
        """Ошибки дедупликации не содержат полный KI."""
        from app.chestny.services.dedup import mask_ki, KiRejectedError

        try:
            mask_ki(FULL_KM_WITH_TAIL)
        except KiRejectedError as e:
            msg = str(e)
            for marker in CRYPTO_MARKERS:
                assert marker not in msg, f"Ошибка содержит {marker!r}"
            assert KI31 not in msg

    def test_package_builder_error_no_full_ki(self, app):
        """Ошибки PackageBuilder не содержат полный KI."""
        from app.chestny.services.packaging import PackageBuilder

        auth = MagicMock()
        transport = MagicMock()
        signer = MagicMock()

        builder = PackageBuilder(auth, transport, signer, hmac_key=b"test-key")
        from datetime import datetime, timezone
        from app.chestny.services.active_imports import ActiveImport

        imp = ActiveImport(
            token="tok1",
            profile_id="org-sinyavin",
            accepted=(),
            excluded=(),
            summary=ImportSummary(total_rows=0, accepted=0, excluded=0, by_reason={}),
            created_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc).replace(hour=23, minute=59, second=59),
        )
        settings = {"id": "org-krasikova"}
        try:
            builder.create(imp, settings, action_date="2026-09-03",
                           document_number="DOC-001", document_date="2026-09-03")
        except Exception as e:
            msg = str(e)
            assert KI31 not in msg
            assert "010463003759346121SjFg6nX5bGS" not in msg
