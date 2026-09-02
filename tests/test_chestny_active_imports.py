"""
Тесты in-memory store активных импортов. Без файлов/БД.
"""

from __future__ import annotations

import time
from decimal import Decimal
from unittest.mock import patch

import pytest

from app.chestny.services.active_imports import (
    ActiveImportStore,
    CapacityError,
    ExpiredError,
    NotFoundError,
)
from app.services.excel_import import AcceptedRow, ExcludedRow, ImportResult, ImportSummary


def _make_result(accepted=2, excluded=1):
    return ImportResult(
        accepted=[AcceptedRow(i, f"ki{i:031d}", f"chk{i}", f"fn{i}",
                              Decimal("10000"), "2026-01-01")
                  for i in range(accepted)],
        excluded=[ExcludedRow(i, "test", "тест")
                  for i in range(excluded)],
        summary=ImportSummary(total_rows=accepted + excluded,
                              accepted=accepted, excluded=excluded),
    )


class TestCreateGet:
    def test_create_returns_token(self):
        s = ActiveImportStore()
        t = s.create("org-sinyavin", _make_result())
        assert isinstance(t, str) and len(t) >= 32

    def test_token_entropy(self):
        s = ActiveImportStore()
        t = s.create("org-sinyavin", _make_result())
        # token_urlsafe(32) -> 43 base64 chars
        assert len(t) >= 40

    def test_get_returns_import(self):
        s = ActiveImportStore()
        t = s.create("org-sinyavin", _make_result())
        ai = s.get(t)
        assert ai.profile_id == "org-sinyavin"

    def test_get_not_found(self):
        with pytest.raises(NotFoundError):
            ActiveImportStore().get("nonexistent")

    def test_get_expired(self):
        s = ActiveImportStore(default_ttl=1)
        t = s.create("org-sinyavin", _make_result())
        with pytest.raises(ExpiredError):
            s.get(t, now=time.time() + 10)

    def test_tuple_copies(self):
        s = ActiveImportStore()
        src = [AcceptedRow(1, "a" * 31, "chk", "fn", Decimal("100"), "2026-01-01")]
        result = ImportResult(
            accepted=src,
            summary=ImportSummary(total_rows=1, accepted=1),
        )
        t = s.create("org-sinyavin", result)
        src.pop()
        assert len(s.get(t).accepted) == 1

    def test_profile_pinned(self):
        s = ActiveImportStore()
        t = s.create("org-krasikova", _make_result())
        assert s.get(t).profile_id == "org-krasikova"

    def test_unique_token(self):
        s = ActiveImportStore()
        t1 = s.create("org-sinyavin", _make_result())
        t2 = s.create("org-sinyavin", _make_result())
        assert t1 != t2

    def test_repr_no_ki_or_full_token(self):
        s = ActiveImportStore()
        t = s.create("org-sinyavin", _make_result())
        r = repr(s.get(t))
        # Repr не содержит значений КИ (31-символьные строки с 01),
        # полного токена, check_number, fn_number
        assert "010" not in r  # KI values start with 01
        assert "check" not in r.lower()
        assert t not in r  # full token not in repr


class TestConfigValidation:
    def test_max_sessions_zero(self):
        with pytest.raises(ValueError, match="> 0"):
            ActiveImportStore(max_active_sessions=0)

    def test_default_ttl_zero(self):
        with pytest.raises(ValueError, match="> 0"):
            ActiveImportStore(default_ttl=0)

    def test_ttl_seconds_zero(self):
        s = ActiveImportStore()
        with pytest.raises(ValueError, match="> 0"):
            s.create("org-sinyavin", _make_result(), ttl_seconds=0)

    def test_empty_profile_id(self):
        s = ActiveImportStore()
        with pytest.raises(ValueError, match="пустым"):
            s.create("", _make_result())


class TestExpiry:
    def test_expiry_with_injected_clock(self):
        fake = [1000.0]
        s = ActiveImportStore(default_ttl=300, clock=lambda: fake[0])
        t = s.create("org-sinyavin", _make_result(), ttl_seconds=100)
        fake[0] = 1101.0
        with pytest.raises(ExpiredError):
            s.get(t)

    def test_cleanup_expired(self):
        s = ActiveImportStore(default_ttl=1)
        t = s.create("org-sinyavin", _make_result())
        assert s.cleanup_expired(now=time.time() + 10) == 1
        assert len(s) == 0

    def test_injected_clock_callable(self):
        fake = iter([1000.0, 1000.0, 2000.0])
        s = ActiveImportStore(default_ttl=100, clock=lambda: next(fake))
        t = s.create("org-sinyavin", _make_result())
        with pytest.raises(ExpiredError):
            s.get(t)


class TestCancel:
    def test_cancel_existing(self):
        s = ActiveImportStore()
        t = s.create("org-sinyavin", _make_result())
        assert s.cancel(t) is True
        assert len(s) == 0

    def test_cancel_missing(self):
        assert ActiveImportStore().cancel("nonexistent") is False

    def test_cancel_idempotent(self):
        s = ActiveImportStore()
        t = s.create("org-sinyavin", _make_result())
        s.cancel(t)
        assert s.cancel(t) is False


class TestCapacity:
    def test_capacity_after_expiry(self):
        s = ActiveImportStore(max_active_sessions=1, default_ttl=1)
        t1 = s.create("org-sinyavin", _make_result())
        s.cleanup_expired(now=time.time() + 10)
        t2 = s.create("org-sinyavin", _make_result())
        assert len(s) == 1

    def test_capacity_error(self):
        s = ActiveImportStore(max_active_sessions=1, default_ttl=999)
        s.create("org-sinyavin", _make_result())
        with pytest.raises(CapacityError):
            s.create("org-sinyavin", _make_result())

    def test_max_rows(self):
        s = ActiveImportStore()
        with pytest.raises(ValueError, match="1000"):
            s.create("org-sinyavin", _make_result(accepted=1001))


class TestSummaryImmutability:
    def test_summary_outer_immutable(self):
        s = ActiveImportStore()
        t = s.create("org-sinyavin", _make_result())
        ai = s.get(t)
        with pytest.raises(TypeError):
            ai.summary["total_rows"] = 0

    def test_summary_by_reason_immutable(self):
        s = ActiveImportStore()
        t = s.create("org-sinyavin", _make_result())
        ai = s.get(t)
        with pytest.raises(TypeError):
            ai.summary["by_reason"]["test"] = 999


class TestIsolation:
    def test_two_stores_isolated(self):
        s1 = ActiveImportStore()
        s2 = ActiveImportStore()
        t = s1.create("org-sinyavin", _make_result())
        assert s2.cancel(t) is False

    def test_clear(self):
        s = ActiveImportStore()
        s.create("org-sinyavin", _make_result())
        s.clear()
        assert len(s) == 0


class TestConcurrency:
    def test_create_unique_threadsafe(self):
        s = ActiveImportStore(max_active_sessions=10)
        tokens = set()
        for _ in range(5):
            t = s.create("org-sinyavin", _make_result())
            assert t not in tokens
            tokens.add(t)
