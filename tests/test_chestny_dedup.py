"""
Тесты HMAC-дедупликации КИЗ.
Изолированная БД + temp instance_path.
"""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

import pytest

from app.chestny.factory import create_cz_app, db
from app.chestny.models import OrganizationProfile, ProcessedKiz
from app.chestny.services.dedup import (
    HmacKeyError,
    KiRejectedError,
    find_confirmed_duplicates,
    hmac_digest,
    load_or_create_hmac_key,
    mask_ki,
)

KI31 = "0104630039391397215VH2LpGkLvG7v"
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


@pytest.fixture
def tmp_instance():
    d = tempfile.mkdtemp()
    yield d
    for f in Path(d).iterdir():
        f.unlink()
    os.rmdir(d)


class TestHmacKey:
    def test_create(self, tmp_instance):
        key = load_or_create_hmac_key(tmp_instance)
        assert len(key) == 32

    def test_reuse(self, tmp_instance):
        k1 = load_or_create_hmac_key(tmp_instance)
        k2 = load_or_create_hmac_key(tmp_instance)
        assert k1 == k2

    def test_permissions_0600(self, tmp_instance):
        load_or_create_hmac_key(tmp_instance)
        st = os.stat(Path(tmp_instance) / "hmac.key")
        assert stat.S_IMODE(st.st_mode) == 0o600

    def test_invalid_length(self, tmp_instance):
        p = Path(tmp_instance) / "hmac.key"
        p.write_bytes(b"too short")
        with pytest.raises(HmacKeyError, match="неверный размер"):
            load_or_create_hmac_key(tmp_instance)

    def test_read_failure_sanitized(self, tmp_instance, monkeypatch):
        p = Path(tmp_instance) / "hmac.key"
        p.write_bytes(b"x" * 32)
        monkeypatch.setattr(Path, "read_bytes", lambda self: (_ for _ in ()).throw(PermissionError()))
        with pytest.raises(HmacKeyError, match="прочитать"):
            load_or_create_hmac_key(tmp_instance)

    def test_symlink_reject(self, tmp_instance):
        target = Path(tmp_instance) / "target"
        target.write_bytes(b"x" * 32)
        link = Path(tmp_instance) / "hmac.key"
        link.symlink_to(target)
        with pytest.raises(HmacKeyError, match="симлинком"):
            load_or_create_hmac_key(tmp_instance)


class TestHmacDigest:
    def test_deterministic(self, tmp_instance):
        key = load_or_create_hmac_key(tmp_instance)
        assert hmac_digest(KI31, key) == hmac_digest(KI31, key)

    def test_different_keys_differ(self, tmp_instance):
        k1 = load_or_create_hmac_key(tmp_instance)
        k2 = b"a" * 32
        assert hmac_digest(KI31, k1) != hmac_digest(KI31, k2)

    def test_different_ki_differ(self, tmp_instance):
        key = load_or_create_hmac_key(tmp_instance)
        ki2 = "0104630039391397215VH2LpGkLvG7w"
        assert hmac_digest(KI31, key) != hmac_digest(ki2, key)

    def test_exactly_64_hex(self, tmp_instance):
        key = load_or_create_hmac_key(tmp_instance)
        d = hmac_digest(KI31, key)
        assert len(d) == 64
        int(d, 16)


class TestHmacDigestValidation:
    def test_tail_rejected(self, tmp_instance):
        key = load_or_create_hmac_key(tmp_instance)
        with_tail = KI31 + "\u001d" + "91ABCD"
        with pytest.raises(KiRejectedError):
            hmac_digest(with_tail, key)

    def test_invalid_ki_no_raw(self, tmp_instance):
        key = load_or_create_hmac_key(tmp_instance)
        with pytest.raises(KiRejectedError, match="Некорректный КИ"):
            hmac_digest("", key)
        with pytest.raises(KiRejectedError, match="Некорректный КИ"):
            hmac_digest("abc", key)


class TestMaskKi:
    def test_mask(self):
        assert mask_ki(KI31) == "0104****vG7v"

    def test_invalid_rejected(self):
        with pytest.raises(KiRejectedError, match="Некорректный КИ"):
            mask_ki("short")

    def test_malicious_no_raw_in_error(self):
        with pytest.raises(KiRejectedError) as exc:
            mask_ki("<script>alert(1)</script>")
        assert "<script>" not in str(exc.value)


class TestFindDuplicates:
    def test_confirmed_blocks(self, app, tmp_instance):
        key = load_or_create_hmac_key(tmp_instance)
        dig = hmac_digest(KI31, key)
        pk = ProcessedKiz(
            hmac_digest=dig, mask="0104****vG7v",
            profile_id="org-sinyavin", status="CONFIRMED",
            document_id="doc-1",
        )
        db.session.add(pk)
        db.session.commit()
        result = find_confirmed_duplicates([KI31], key)
        assert KI31 in result
        assert result[KI31]["document_id"] == "doc-1"

    def test_pending_not_block(self, app, tmp_instance):
        key = load_or_create_hmac_key(tmp_instance)
        dig = hmac_digest(KI31, key)
        pk = ProcessedKiz(
            hmac_digest=dig, mask="0104****vG7v",
            profile_id="org-sinyavin", status="PENDING",
        )
        db.session.add(pk)
        db.session.commit()
        assert find_confirmed_duplicates([KI31], key) == {}

    def test_failed_not_block(self, app, tmp_instance):
        key = load_or_create_hmac_key(tmp_instance)
        dig = hmac_digest(KI31, key)
        pk = ProcessedKiz(
            hmac_digest=dig, mask="0104****vG7v",
            profile_id="org-sinyavin", status="FAILED",
        )
        db.session.add(pk)
        db.session.commit()
        assert find_confirmed_duplicates([KI31], key) == {}

    def test_unknown_not_block(self, app, tmp_instance):
        key = load_or_create_hmac_key(tmp_instance)
        dig = hmac_digest(KI31, key)
        pk = ProcessedKiz(
            hmac_digest=dig, mask="0104****vG7v",
            profile_id="org-sinyavin", status="UNKNOWN",
        )
        db.session.add(pk)
        db.session.commit()
        assert find_confirmed_duplicates([KI31], key) == {}

    def test_across_profiles(self, app, tmp_instance):
        key = load_or_create_hmac_key(tmp_instance)
        dig = hmac_digest(KI31, key)
        pk = ProcessedKiz(
            hmac_digest=dig, mask="0104****vG7v",
            profile_id="org-krasikova", status="CONFIRMED",
            document_id="doc-kras",
        )
        db.session.add(pk)
        db.session.commit()
        result = find_confirmed_duplicates([KI31], key)
        assert KI31 in result
        assert result[KI31]["display_name"] == "ИП Красикова"

    def test_result_no_digest(self, app, tmp_instance):
        key = load_or_create_hmac_key(tmp_instance)
        dig = hmac_digest(KI31, key)
        pk = ProcessedKiz(
            hmac_digest=dig, mask="0104****vG7v",
            profile_id="org-sinyavin", status="CONFIRMED",
        )
        db.session.add(pk)
        db.session.commit()
        result = find_confirmed_duplicates([KI31], key)
        assert "hmac_digest" not in result[KI31]
        assert "digest" not in str(result[KI31].keys())
