"""
Тесты reconciliation: сверка неоднозначных пакетов (UNKNOWN, PARTIAL).

Все внешние вызовы замоканы. Никакой реальной сети/подписи/ЧЗ.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest

from app.chestny.services.packaging import (
    CONFIRMED,
    FAILED,
    PARTIAL,
    UNKNOWN,
    BatchItem,
    Package,
    PackageItem,
    PackageStore,
)
from app.chestny.services.reconciliation import ReconciliationService, ScheduledReconciler
from app.chestny.services.cz_status import (
    CzStatusClient,
    DocumentStatus,
    DocumentStatusResult,
)


# ═════════════════════════════════════════════════════════════════════════════
#  Mock CzStatusClient
# ═════════════════════════════════════════════════════════════════════════════


class MockStatusClient:
    """Mock CzStatusClient, возвращает предсказуемые статусы."""

    def __init__(self) -> None:
        self.checked: list[str] = []
        self._results: dict[str, DocumentStatusResult] = {}

    def set_result(self, document_id: str, status: DocumentStatus) -> None:
        self._results[document_id] = DocumentStatusResult(status=status)

    def check(self, document_id: str) -> DocumentStatusResult:
        self.checked.append(document_id)
        result = self._results.get(document_id)
        if result is not None:
            return result
        return DocumentStatusResult(status=DocumentStatus.NOT_FOUND)


# ═════════════════════════════════════════════════════════════════════════════
#  Хелперы
# ═════════════════════════════════════════════════════════════════════════════


def _make_package_item(index: int = 1) -> PackageItem:
    return PackageItem(
        ki31=f"010123456789099921GOODS{index:03d}ABCDE",
        hmac=f"hmac-{index:04x}",
        mask=f"01012345****{index:04d}",
        check=f"CHK{index:03d}",
        fn=f"FN{index:03d}",
        cost_kopecks=100 * index,
        date=f"2026-09-{index:02d}",
    )


def _make_package(
    batch_statuses: list[str],
    package_status: str = PARTIAL,
) -> Package:
    """Создаёт пакет с заданными статусами батчей."""
    batches = []
    for i, status in enumerate(batch_statuses):
        items = [_make_package_item(i * 10 + j + 1) for j in range(3)]
        doc_id = str(uuid.uuid4()) if status != CONFIRMED else None
        if status == CONFIRMED:
            doc_id = str(uuid.uuid4())
        batches.append(BatchItem(
            index=i,
            items=items,
            status=status,
            document_id=doc_id,
        ))

    total_items = sum(len(b.items) for b in batches)
    from app.services.excel_import import ImportSummary
    summary = ImportSummary(
        total_rows=total_items,
        accepted=total_items,
        excluded=0,
        by_reason={},
    )

    # Simulate summary from PackageBuilder
    submitted = sum(len(b.items) for b in batches if b.status == CONFIRMED)
    failed = sum(len(b.items) for b in batches if b.status != CONFIRMED)

    from dataclasses import replace
    summary = replace(
        summary,
        accepted_submitted=submitted,
        accepted_failed=failed,
    )

    return Package(
        id=str(uuid.uuid4()),
        profile_id="org-sinyavin",
        import_token="test-token",
        status=package_status,
        summary=summary,
        batches=batches,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


# ═════════════════════════════════════════════════════════════════════════════
#  Тесты ReconciliationService
# ═════════════════════════════════════════════════════════════════════════════


class TestReconcile:
    """Reconcile одного пакета."""

    def test_partial_to_confirmed(self) -> None:
        """PARTIAL пакет: один FAILED батч → CONFIRMED → весь пакет CONFIRMED."""
        mock = MockStatusClient()
        service = ReconciliationService(mock)

        # Пакет с 2 батчами: один CONFIRMED, один FAILED
        pkg = _make_package([CONFIRMED, FAILED], package_status=PARTIAL)
        failed_batch = pkg.batches[1]
        mock.set_result(failed_batch.document_id, DocumentStatus.CONFIRMED)

        updated = service.reconcile(pkg)

        assert updated.status == CONFIRMED
        assert updated.batches[1].status == CONFIRMED
        assert updated.summary is not None
        assert updated.summary.accepted_failed == 0
        assert updated.summary.accepted_submitted == len(updated.batches[0].items) + len(updated.batches[1].items)
        assert mock.checked == [failed_batch.document_id]

    def test_failed_to_confirmed(self) -> None:
        """FAILED батч → CONFIRMED, пакет был PARTIAL → CONFIRMED."""
        mock = MockStatusClient()
        service = ReconciliationService(mock)

        pkg = _make_package([CONFIRMED, FAILED], package_status=PARTIAL)
        failed_batch = pkg.batches[1]
        mock.set_result(failed_batch.document_id, DocumentStatus.CONFIRMED)

        updated = service.reconcile(pkg)

        assert updated.status == CONFIRMED
        assert updated.batches[1].status == CONFIRMED

    def test_unknown_to_confirmed(self) -> None:
        """UNKNOWN батч → CONFIRMED, пакет был UNKNOWN → CONFIRMED."""
        mock = MockStatusClient()
        service = ReconciliationService(mock)

        pkg = _make_package([UNKNOWN, UNKNOWN], package_status=UNKNOWN)
        for b in pkg.batches:
            mock.set_result(b.document_id, DocumentStatus.CONFIRMED)

        updated = service.reconcile(pkg)

        assert updated.status == CONFIRMED
        assert all(b.status == CONFIRMED for b in updated.batches)
        assert updated.summary is not None
        assert updated.summary.accepted_failed == 0

    def test_no_change_still_failed(self) -> None:
        """Статус не изменился: ЧЗ всё ещё FAILED."""
        mock = MockStatusClient()
        service = ReconciliationService(mock)

        pkg = _make_package([CONFIRMED, FAILED], package_status=PARTIAL)
        failed_batch = pkg.batches[1]
        mock.set_result(failed_batch.document_id, DocumentStatus.FAILED)

        updated = service.reconcile(pkg)

        assert updated.status == PARTIAL  # не изменился
        assert updated.batches[1].status == FAILED  # не изменился
        assert mock.checked == [failed_batch.document_id]

    def test_no_change_unknown_still_unknown(self) -> None:
        """UNKNOWN пакет: ЧЗ всё ещё UNKNOWN."""
        mock = MockStatusClient()
        service = ReconciliationService(mock)

        pkg = _make_package([UNKNOWN], package_status=UNKNOWN)
        mock.set_result(pkg.batches[0].document_id, DocumentStatus.UNKNOWN)

        updated = service.reconcile(pkg)

        # Ни один батч не изменил статус → пакет остаётся UNKNOWN
        assert updated.status == UNKNOWN
        assert updated.batches[0].status == UNKNOWN
        assert mock.checked == [pkg.batches[0].document_id]

    def test_no_batch_document_id(self) -> None:
        """Батч без document_id — пропускается."""
        mock = MockStatusClient()
        service = ReconciliationService(mock)

        batches = [BatchItem(index=0, items=[_make_package_item(1)], status=FAILED, document_id=None)]
        from app.services.excel_import import ImportSummary
        from dataclasses import replace
        summary = ImportSummary(total_rows=1, accepted=1, excluded=0, by_reason={})
        summary = replace(summary, accepted_submitted=0, accepted_failed=1)
        pkg = Package(
            id=str(uuid.uuid4()),
            profile_id="org-sinyavin",
            import_token="test-token",
            status=PARTIAL,
            summary=summary,
            batches=batches,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

        updated = service.reconcile(pkg)

        # Батч без document_id пропущен → пакет остаётся PARTIAL
        assert updated.status == PARTIAL
        assert updated.batches[0].status == FAILED
        assert mock.checked == []

    def test_skips_confirmed_batches(self) -> None:
        """CONFIRMED батчи не проверяются."""
        mock = MockStatusClient()
        service = ReconciliationService(mock)

        pkg = _make_package([CONFIRMED], package_status=CONFIRMED)

        updated = service.reconcile(pkg)

        assert updated.status == CONFIRMED
        assert mock.checked == []  # ни одного вызова

    def test_skips_non_reconcilable_package(self) -> None:
        """Пакет не PARTIAL и не UNKNOWN — пропускается."""
        mock = MockStatusClient()
        service = ReconciliationService(mock)

        pkg = _make_package([CONFIRMED], package_status=CONFIRMED)

        updated = service.reconcile(pkg)
        assert updated is pkg  # та же ссылка, без изменений
        assert mock.checked == []

    def test_exception_handling(self) -> None:
        """Ошибка при проверке одного батча не ломает остальные."""
        mock = MockStatusClient()

        class FlakyClient:
            def __init__(self):
                self.calls = 0

            def check(self, document_id):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("Network error")
                return DocumentStatusResult(status=DocumentStatus.CONFIRMED)

        service = ReconciliationService(FlakyClient())  # type: ignore[arg-type]

        pkg = _make_package([FAILED, FAILED], package_status=PARTIAL)
        for b in pkg.batches:
            b.document_id = str(uuid.uuid4())

        updated = service.reconcile(pkg)

        # Первый батч упал, второй обновился
        assert updated.batches[0].status == FAILED  # не изменился
        assert updated.batches[1].status == CONFIRMED  # обновился
        assert updated.status == PARTIAL  # один CONFIRMED, один нет


# ═════════════════════════════════════════════════════════════════════════════
#  Тесты ScheduledReconciler
# ═════════════════════════════════════════════════════════════════════════════


class TestScheduledReconciler:
    """Периодическая сверка."""

    def test_run_once_updates_packages(self) -> None:
        """run_once находит PARTIAL/UNKNOWN пакеты и обновляет их."""
        mock = MockStatusClient()
        service = ReconciliationService(mock)
        store = PackageStore()

        # PARTIAL пакет: один FAILED батч
        pkg1 = _make_package([CONFIRMED, FAILED], package_status=PARTIAL)
        mock.set_result(pkg1.batches[1].document_id, DocumentStatus.CONFIRMED)
        store.save(pkg1)

        # UNKNOWN пакет
        pkg2 = _make_package([UNKNOWN], package_status=UNKNOWN)
        mock.set_result(pkg2.batches[0].document_id, DocumentStatus.CONFIRMED)
        store.save(pkg2)

        # CONFIRMED пакет — не должен трогаться
        pkg3 = _make_package([CONFIRMED], package_status=CONFIRMED)
        store.save(pkg3)

        reconciler = ScheduledReconciler(store, service)
        changed = reconciler.run_once()

        assert changed == 2  # pkg1 и pkg2 изменились

        # Проверяем обновления в store
        updated1 = store.get(pkg1.id)
        assert updated1 is not None
        assert updated1.status == CONFIRMED

        updated2 = store.get(pkg2.id)
        assert updated2 is not None
        assert updated2.status == CONFIRMED

        updated3 = store.get(pkg3.id)
        assert updated3 is not None
        assert updated3.status == CONFIRMED  # не изменился

    def test_run_once_no_candidates(self) -> None:
        """Нет PARTIAL/UNKNOWN пакетов — run_once возвращает 0."""
        mock = MockStatusClient()
        service = ReconciliationService(mock)
        store = PackageStore()

        pkg = _make_package([CONFIRMED], package_status=CONFIRMED)
        store.save(pkg)

        reconciler = ScheduledReconciler(store, service)
        changed = reconciler.run_once()

        assert changed == 0
        assert mock.checked == []

    def test_run_once_empty_store(self) -> None:
        """Пустой store — run_once возвращает 0."""
        mock = MockStatusClient()
        service = ReconciliationService(mock)
        store = PackageStore()

        reconciler = ScheduledReconciler(store, service)
        changed = reconciler.run_once()

        assert changed == 0

    def test_run_once_preserves_other_statuses(self) -> None:
        """PENDING, SUBMITTING пакеты не трогаются."""
        mock = MockStatusClient()
        service = ReconciliationService(mock)
        store = PackageStore()

        from app.chestny.services.packaging import PENDING, SUBMITTING

        pkg_pending = _make_package([FAILED], package_status=PENDING)
        store.save(pkg_pending)

        pkg_submitting = _make_package([FAILED], package_status=SUBMITTING)
        store.save(pkg_submitting)

        reconciler = ScheduledReconciler(store, service)
        changed = reconciler.run_once()

        assert changed == 0
        assert mock.checked == []

    def test_exception_in_one_package_does_not_block_others(self) -> None:
        """Ошибка в одном пакете не мешает обработке остальных."""

        class FailingReconciliation:
            def __init__(self):
                self.call_count = 0

            def reconcile(self, package):
                self.call_count += 1
                if self.call_count == 1:
                    raise RuntimeError("Reconciliation failed")
                # второй пакет — успешно
                package.batches[0].status = CONFIRMED
                package.status = CONFIRMED
                return package

        store = PackageStore()

        pkg1 = _make_package([FAILED], package_status=PARTIAL)
        store.save(pkg1)

        pkg2 = _make_package([FAILED], package_status=PARTIAL)
        store.save(pkg2)

        # У pkg2 должен быть document_id для сверки
        mock_client = MockStatusClient()
        mock_client.set_result(pkg2.batches[0].document_id, DocumentStatus.CONFIRMED)
        real_service = ReconciliationService(mock_client)

        # Подменяем service на failing
        reconciler = ScheduledReconciler(store, FailingReconciliation())  # type: ignore[arg-type]
        changed = reconciler.run_once()

        # Первый упал, второй — нет (FailingReconciliation обработал оба)
        assert changed == 1
        # Первый пакет не изменился
        assert store.get(pkg1.id) is not None
        # Второй пакет должен был быть обработан
        pkg2_updated = store.get(pkg2.id)
        assert pkg2_updated is not None
