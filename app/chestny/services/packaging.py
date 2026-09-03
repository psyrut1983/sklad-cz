"""
packaging — новый builder для LK_RECEIPT.

Зависит от cz_auth (токен, подпись) и active_imports (временные импорты).
Не вызывает реальную сеть/ЧЗ/подпись — транспорт и signer инжектируются.
"""

from __future__ import annotations

import base64
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
CONFIRMED = "CONFIRMED"
FAILED = "FAILED"
UNKNOWN = "UNKNOWN"

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
        """Создаёт пакет из активного импорта и отправляет в ЧЗ."""
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

        # ── Формирование JSON ───────────────────────────────────────────
        inner = self._build_inner_json(items, profile_settings, action_date, document_number, document_date, primary_document_custom_name)
        inner_json = json.dumps(inner, ensure_ascii=False, separators=(",", ":"))
        product_document = base64.b64encode(inner_json.encode("utf-8")).decode("ascii")

        outer = {
            "document_format": "MANUAL",
            "product_document": product_document,
            "type": "LK_RECEIPT",
            "signature": self._signer(inner_json, profile_settings.get("certificate_thumbprint", "")),
        }

        # ── Отправка ────────────────────────────────────────────────────
        token = self._auth.get_token()
        url = f"{profile_settings['api_base_url']}/lk/documents/create?pg=lp"

        resp = self._transport("POST", url, headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }, json=outer, timeout=60)

        # ── Обработка 401 ───────────────────────────────────────────────
        if resp.status_code == 401:
            self._auth.reset_token()
            token = self._auth.get_token()
            resp = self._transport("POST", url, headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }, json=outer, timeout=60)

        if resp.status_code not in (200, 201):
            raise PackageCreateError("Не удалось создать документ")

        # ── Извлечение document_id ──────────────────────────────────────
        document_id = self._extract_document_id(resp)
        if not document_id:
            raise PackageCreateError("Не удалось извлечь document_id из ответа")

        pkg = Package(
            id=str(uuid.uuid4()),
            profile_id=profile_id,
            import_token=active_import.token,
            document_id=document_id,
            status=CONFIRMED,
            summary=active_import.summary,
        )
        return pkg

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
