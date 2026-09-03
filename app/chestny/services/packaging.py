"""
packaging — новый builder для LK_RECEIPT.

Зависит от cz_auth (токен, подпись) и active_imports (временные импорты).
Не вызывает реальную сеть/ЧЗ/подпись — транспорт и signer инжектируются.
"""

from __future__ import annotations

import base64
import dataclasses
import json
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from app.chestny.services.active_imports import ActiveImport, ExpiredError, NotFoundError
from app.chestny.services.cz_auth import CzAuthClient
from app.services.excel_import import AcceptedRow, ImportResult, ImportSummary
from app.chestny.services.dedup import mask_ki

# ── Типы ────────────────────────────────────────────────────────────────────

HttpTransport = Callable[..., Any]
Signer = Callable[[str, str], str]

# ── Статусы пакета ──────────────────────────────────────────────────────────

PENDING = "PENDING"
SUBMITTING = "SUBMITTING"
CONFIRMED = "CONFIRMED"
PARTIAL = "PARTIAL"
FAILED = "FAILED"
UNKNOWN = "UNKNOWN"

BATCH_SIZE = 100

# ── Data-классы ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PackageItem:
    """Одна КИЗ внутри пакета."""
    ki31: str
    hmac: str
    mask: str
    check: str
    fn: str
    cost_kopecks: int
    date: str


@dataclass
class BatchItem:
    """Один батч (подмножество items) внутри пакета."""
    index: int
    items: list[PackageItem]
    status: str = PENDING
    document_id: str | None = None
    error: str | None = None


@dataclass
class BatchResult:
    """Результат отправки одного батча."""
    index: int
    success: bool
    document_id: str | None = None
    error: str | None = None


@dataclass
class Package:
    """Пакет документов для отправки в ЧЗ."""
    id: str
    profile_id: str
    import_token: str
    document_id: str | None = None
    status: str = PENDING
    summary: ImportSummary | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    processing: bool = False
    batches: list[BatchItem] = field(default_factory=list)

    def __repr__(self) -> str:
        return (
            f"Package(id={self.id!r}, profile_id={self.profile_id!r}, "
            f"status={self.status!r}, document_id={self.document_id!r})"
        )


# ── PackageBuilder ──────────────────────────────────────────────────────────


class PackageBuilderError(Exception):
    """Базовая ошибка пакетирования."""
    pass


class PackageCreateError(PackageBuilderError):
    """Ошибка создания документа в ЧЗ."""
    pass


class PackageStoreError(PackageBuilderError):
    """Ошибка хранилища пакетов."""
    pass


class PackageBuilder:
    """Собирает пакет из активного импорта и отправляет в ЧЗ."""

    def __init__(
        self,
        auth: CzAuthClient,
        transport: HttpTransport,
        signer: Signer,
        hmac_key: bytes,
    ) -> None:
        self._auth = auth
        self._transport = transport
        self._signer = signer
        self._hmac_key = hmac_key

    def create(
        self,
        active_import: ActiveImport,
        profile_settings: dict[str, Any],
        action_date: str,
        document_number: str,
        document_date: str,
        primary_document_custom_name: str = "",
    ) -> Package:
        """Создаёт пакет из активного импорта и отправляет в ЧЗ.

        Разбивает items на батчи по {BATCH_SIZE} КИЗ.
        Каждый батч отправляется отдельным POST /doc/create.
        """
        # ── Валидация ────────────────────────────────────────────────────
        if active_import.expires_at is not None and active_import.expires_at < datetime.now(timezone.utc):
            raise PackageBuilderError("Срок действия импорта истёк")

        profile_id = profile_settings.get("id")
        if active_import.profile_id != profile_id:
            raise PackageBuilderError("Импорт принадлежит другому профилю")

        # ── Маппинг items ───────────────────────────────────────────────
        items: list[PackageItem] = []
        for row in active_import.accepted:
            items.append(PackageItem(
                ki31=row.ki,
                hmac=self._hmac(row.ki),
                mask=mask_ki(row.ki),
                check=row.check_number,
                fn=row.fn_number,
                cost_kopecks=row.cost_kopecks,
                date=row.date,
            ))

        # ── Разбивка на батчи ───────────────────────────────────────────
        batch_list: list[BatchItem] = []
        for i in range(0, len(items), BATCH_SIZE):
            chunk = items[i:i + BATCH_SIZE]
            batch_list.append(BatchItem(index=len(batch_list), items=chunk))

        # ── Создание пакета ─────────────────────────────────────────────
        summary = dataclasses.replace(active_import.summary, accepted_submitted=0, accepted_failed=0)

        pkg = Package(
            id=str(uuid.uuid4()),
            profile_id=profile_id,
            import_token=active_import.token,
            status=SUBMITTING,
            summary=summary,
            batches=batch_list,
        )

        # ── Отправка батчей ─────────────────────────────────────────────
        first_document_id: str | None = None
        submitted_count = 0
        failed_count = 0

        for batch in pkg.batches:
            result = self._send_batch(
                batch.index,
                batch.items,
                profile_settings,
                action_date,
                document_number,
                document_date,
                primary_document_custom_name,
            )
            if result.success:
                batch.status = CONFIRMED
                batch.document_id = result.document_id
                submitted_count += len(batch.items)
                if first_document_id is None:
                    first_document_id = result.document_id
            else:
                batch.status = FAILED
                batch.error = result.error
                failed_count += len(batch.items)

        # ── Итоговый статус ─────────────────────────────────────────────
        if submitted_count > 0 and failed_count == 0:
            pkg.status = CONFIRMED
        elif submitted_count > 0 and failed_count > 0:
            pkg.status = PARTIAL
        else:
            pkg.status = FAILED

        pkg.document_id = first_document_id
        pkg.summary = dataclasses.replace(
            pkg.summary,
            accepted_submitted=submitted_count,
            accepted_failed=failed_count,
        )
        pkg.updated_at = datetime.now(timezone.utc)
        return pkg

    def _send_batch(
        self,
        batch_index: int,
        batch_items: list[PackageItem],
        profile_settings: dict[str, Any],
        action_date: str,
        document_number: str,
        document_date: str,
        primary_document_custom_name: str,
    ) -> BatchResult:
        """Отправляет один батч в ЧЗ.

        401 → reset token + повтор ровно один раз.
        403/429/5xx → ошибка без повтора.
        """
        inner = self._build_inner_json(
            batch_items, profile_settings,
            action_date, document_number, document_date,
            primary_document_custom_name,
        )
        inner_json = json.dumps(inner, ensure_ascii=False, separators=(",", ":"))
        product_document = base64.b64encode(inner_json.encode("utf-8")).decode("ascii")

        outer = {
            "document_format": "MANUAL",
            "product_document": product_document,
            "type": "LK_RECEIPT",
            "signature": self._signer(inner_json, profile_settings.get("certificate_thumbprint", "")),
        }

        url = f"{profile_settings['api_base_url']}/lk/documents/create?pg=lp"

        def _do_request() -> Any:
            token = self._auth.get_token()
            return self._transport("POST", url, headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }, json=outer, timeout=60)

        resp = _do_request()

        # 401 → reset token + повтор
        if resp.status_code == 401:
            self._auth.reset_token()
            resp = _do_request()

        # 429 — rate limit, не повторяем
        if resp.status_code == 429:
            return BatchResult(index=batch_index, success=False, error="Rate limited (429)")

        # 403/5xx — не повторяем
        if resp.status_code in (403,) or (500 <= resp.status_code < 600):
            return BatchResult(index=batch_index, success=False, error=f"HTTP {resp.status_code}")

        # Остальные ошибки
        if resp.status_code not in (200, 201):
            return BatchResult(index=batch_index, success=False, error=f"HTTP {resp.status_code}")

        document_id = self._extract_document_id(resp)
        if not document_id:
            return BatchResult(index=batch_index, success=False, error="No document_id in response")

        return BatchResult(index=batch_index, success=True, document_id=document_id)

    # ── Приватные методы ──────────────────────────────────────────────────

    def _hmac(self, ki31: str) -> str:
        """HMAC-SHA256 от КИ-31."""
        import hashlib
        import hmac as hmac_lib
        return hmac_lib.new(self._hmac_key, ki31.encode("utf-8"), hashlib.sha256).hexdigest()

    def _build_inner_json(
        self,
        items: list[PackageItem],
        settings: dict[str, Any],
        action_date: str,
        document_number: str,
        document_date: str,
        primary_document_custom_name: str,
    ) -> dict[str, Any]:
        """Собирает inner JSON для /doc/create."""
        products = []
        for item in items:
            products.append({
                "cis": item.ki31,
                "product_cost": item.cost_kopecks,
                "primary_document_type": "OTHER",
                "primary_document_number": document_number,
                "primary_document_date": document_date,
                "primary_document_custom_name": primary_document_custom_name,
            })

        inner = {
            "inn": settings.get("inn", ""),
            "action": "DISTANCE",
            "action_date": action_date,
            "withdrawal_type_other": "",
            "document_type": "OTHER",
            "document_number": document_number,
            "document_date": document_date,
            "primary_document_custom_name": primary_document_custom_name,
            "fias_id": settings.get("fias_id", ""),
            "products": products,
        }
        return inner

    def _extract_document_id(self, resp: Any) -> str | None:
        """Извлекает document_id из ответа API."""
        try:
            data = resp.json()
        except Exception:
            # plain text UUID
            text = getattr(resp, "text", "") or ""
            text = text.strip()
            if text and len(text) == 36 and text.count("-") == 4:
                return text
            return None

        if data is None:
            return None

        if isinstance(data, str):
            return data if len(data) == 36 and data.count("-") == 4 else None

        # Поиск по ключам
        for key in ("documentId", "document_id", "id", "number", "value"):
            val = data.get(key)
            if val and isinstance(val, str):
                return val

        # Вложенный data
        nested = data.get("data")
        if isinstance(nested, dict):
            for key in ("documentId", "document_id", "id", "number", "value"):
                val = nested.get(key)
                if val and isinstance(val, str):
                    return val
        return None


# ── PackageStore ────────────────────────────────────────────────────────────


class PackageStore:
    """In-memory хранилище пакетов. Thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._packages: dict[str, Package] = {}

    def save(self, package: Package) -> None:
        with self._lock:
            self._packages[package.id] = package

    def get(self, package_id: str) -> Package | None:
        with self._lock:
            return self._packages.get(package_id)

    def list_by_profile(self, profile_id: str) -> list[Package]:
        with self._lock:
            return [p for p in self._packages.values() if p.profile_id == profile_id]

    def list_by_status(self, status: str) -> list[Package]:
        with self._lock:
            return [p for p in self._packages.values() if p.status == status]

    def update_status(self, package_id: str, new_status: str, document_id: str | None = None) -> None:
        with self._lock:
            pkg = self._packages.get(package_id)
            if pkg is None:
                raise PackageStoreError(f"Пакет {package_id} не найден")
            pkg.status = new_status
            pkg.updated_at = datetime.now(timezone.utc)
            if document_id is not None:
                pkg.document_id = document_id

    def clear(self) -> None:
        with self._lock:
            self._packages.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._packages)
