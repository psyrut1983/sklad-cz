"""
reconciliation — сверка неоднозначного результата отправки (UNKNOWN, PARTIAL).

Периодически (или при старте) перепроверяет батчи, чей статус
FAILED или UNKNOWN, через CzStatusClient.check(document_id).

Thread-safe: не держит блокировку store дольше одного вызова get/list/update.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any

from app.chestny.services.cz_status import (
    CzStatusClient,
    DocumentStatus,
    DocumentStatusResult,
)
from app.chestny.services.packaging import (
    CONFIRMED,
    FAILED,
    PARTIAL,
    UNKNOWN,
    BatchItem,
    Package,
    PackageStore,
)

log = logging.getLogger(__name__)

# ── Список статусов батча, подлежащих сверке ───────────────────────────────

_RECONCILABLE_BATCH_STATUSES: frozenset[str] = frozenset({FAILED, UNKNOWN})

# ── Список статусов пакета, подлежащих сверке ──────────────────────────────

_RECONCILABLE_PACKAGE_STATUSES: frozenset[str] = frozenset({PARTIAL, UNKNOWN})


# ═════════════════════════════════════════════════════════════════════════════
#  ReconciliationService
# ═════════════════════════════════════════════════════════════════════════════


class ReconciliationService:
    """Сверяет один пакет через CzStatusClient."""

    def __init__(self, status_client: CzStatusClient) -> None:
        self._status_client = status_client

    def reconcile(self, package: Package) -> Package:
        """Перепроверяет все FAILED/UNKNOWN батчи пакета.

        Для каждого батча с document_id вызывает CzStatusClient.check().
        Если ЧЗ вернул CONFIRMED — обновляет статус батча и summary пакета.
        Если ЧЗ всё ещё вернул FAILED/UNKNOWN/NOT_FOUND — оставляет без изменений.

        Возвращает обновлённый Package (ту же ссылку, мутирует in-place).
        """
        if package.status not in _RECONCILABLE_PACKAGE_STATUSES:
            return package

        changed = False
        reconciled_failed = 0  # количество КИЗ, перешедших FAILED/UNKNOWN → CONFIRMED

        for batch in package.batches:
            if batch.status not in _RECONCILABLE_BATCH_STATUSES:
                continue
            if not batch.document_id:
                continue

            try:
                result: DocumentStatusResult = self._status_client.check(batch.document_id)
            except Exception as exc:
                log.warning(
                    "Reconciliation check failed for batch %s (doc=%s): %s",
                    batch.index, batch.document_id, exc,
                )
                continue

            if result.status == DocumentStatus.CONFIRMED:
                batch.status = CONFIRMED
                reconciled_failed += len(batch.items)
                changed = True
                log.info(
                    "Batch %s reconciled: FAILED/UNKNOWN → CONFIRMED (%d items)",
                    batch.index, len(batch.items),
                )
            else:
                log.debug(
                    "Batch %s still %s (doc=%s)",
                    batch.index, result.status.value, batch.document_id,
                )

        if not changed:
            return package

        # ── Пересчёт статуса пакета ─────────────────────────────────────
        all_confirmed = all(b.status == CONFIRMED for b in package.batches)
        any_confirmed = any(b.status == CONFIRMED for b in package.batches)

        if all_confirmed:
            package.status = CONFIRMED
        elif any_confirmed:
            package.status = PARTIAL
        else:
            package.status = FAILED

        # ── Обновление summary ──────────────────────────────────────────
        if package.summary is not None:
            old_failed = package.summary.accepted_failed
            new_failed = max(0, old_failed - reconciled_failed)
            package.summary = dataclasses.replace(
                package.summary,
                accepted_submitted=package.summary.accepted_submitted + reconciled_failed,
                accepted_failed=new_failed,
            )

        package.updated_at = __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        )

        return package


# ═════════════════════════════════════════════════════════════════════════════
#  ScheduledReconciler
# ═════════════════════════════════════════════════════════════════════════════


class ScheduledReconciler:
    """Периодическая сверка всех PARTIAL/UNKNOWN пакетов в store."""

    def __init__(
        self,
        store: PackageStore,
        reconciliation: ReconciliationService,
    ) -> None:
        self._store = store
        self._reconciliation = reconciliation

    def run_once(self) -> int:
        """Один проход сверки.

        Перебирает все пакеты со статусом PARTIAL или UNKNOWN.
        Для каждого вызывает reconcile().

        Returns:
            Количество пакетов, статус которых изменился.
        """
        candidates: list[Package] = []

        # Собираем кандидатов под блокировкой, обрабатываем без неё
        for status in (PARTIAL, UNKNOWN):
            candidates.extend(self._store.list_by_status(status))

        if not candidates:
            return 0

        changed_count = 0

        for pkg in candidates:
            old_status = pkg.status
            try:
                updated = self._reconciliation.reconcile(pkg)
                if updated.status != old_status:
                    changed_count += 1
                # Сохраняем изменения (даже если статус не изменился,
                # updated_at мог обновиться при частичном прогрессе)
                self._store.save(updated)
            except Exception as exc:
                log.error("Reconciliation failed for package %s: %s", pkg.id, exc)

        return changed_count
