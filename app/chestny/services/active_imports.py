"""
chestny.services.active_imports — in-memory store активных импортов.

Thread-safe (RLock), per-app instance. Без файлов/БД/логов/сериализации.
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable

from app.services.excel_import import AcceptedRow, ExcludedRow, ImportResult


@dataclass(frozen=True)
class ActiveImport:
    """Активный импорт — неизменяемый слепок."""
    token: str
    profile_id: str
    accepted: tuple[AcceptedRow, ...] = field(repr=False)
    excluded: tuple[ExcludedRow, ...] = field(repr=False)
    summary: MappingProxyType[str, Any] = field()
    created_at: float = field()
    expires_at: float = field()

    def __repr__(self) -> str:
        return (
            f"ActiveImport(token=…{self.token[-8:]}, "
            f"profile_id={self.profile_id!r}, "
            f"accepted={len(self.accepted)}, excluded={len(self.excluded)}, "
            f"expires_at={self.expires_at})"
        )


class NotFoundError(KeyError):
    pass


class ExpiredError(KeyError):
    pass


class CapacityError(RuntimeError):
    pass


def _build_summary(result: ImportResult) -> MappingProxyType[str, Any]:
    by_reason = MappingProxyType(dict(result.summary.by_reason))
    return MappingProxyType({
        "total_rows": result.summary.total_rows,
        "accepted": result.summary.accepted,
        "excluded": result.summary.excluded,
        "by_reason": by_reason,
    })


class ActiveImportStore:
    """Thread-safe in-memory store активных импортов."""

    def __init__(
        self,
        max_active_sessions: int = 5,
        default_ttl: float = 1800.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if max_active_sessions <= 0:
            raise ValueError("max_active_sessions должен быть > 0")
        if default_ttl <= 0:
            raise ValueError("default_ttl должен быть > 0")
        self._lock = threading.RLock()
        self._imports: dict[str, ActiveImport] = {}
        self._max = max_active_sessions
        self._ttl = default_ttl
        self._clock = clock if clock is not None else time.time

    def cleanup_expired(self, now: float | None = None) -> int:
        if now is None:
            now = self._clock()
        with self._lock:
            expired = [t for t, ai in self._imports.items()
                       if ai.expires_at <= now]
            for t in expired:
                del self._imports[t]
            return len(expired)

    def create(
        self,
        profile_id: str,
        result: ImportResult,
        ttl_seconds: float | None = None,
    ) -> str:
        if not profile_id:
            raise ValueError("profile_id не может быть пустым")
        if ttl_seconds is not None and ttl_seconds <= 0:
            raise ValueError("ttl_seconds должен быть > 0")

        with self._lock:
            self.cleanup_expired()
            if len(self._imports) >= self._max:
                raise CapacityError(
                    "Достигнут лимит активных сессий импорта"
                )

            if len(result.accepted) > 1000:
                raise ValueError("Превышен лимит принятых строк: 1000")

            now = self._clock()
            ttl = ttl_seconds if ttl_seconds is not None else self._ttl
            token = secrets.token_urlsafe(32)
            while token in self._imports:
                token = secrets.token_urlsafe(32)

            ai = ActiveImport(
                token=token,
                profile_id=profile_id,
                accepted=tuple(result.accepted),
                excluded=tuple(result.excluded),
                summary=_build_summary(result),
                created_at=now,
                expires_at=now + ttl,
            )
            self._imports[token] = ai
            return token

    def get(self, token: str, now: float | None = None) -> ActiveImport:
        with self._lock:
            if now is None:
                now = self._clock()
            ai = self._imports.get(token)
            if ai is None:
                raise NotFoundError("Активный импорт не найден")
            if ai.expires_at <= now:
                del self._imports[token]
                raise ExpiredError("Срок действия импорта истёк")
            return ai

    def pop(self, token: str) -> bool:
        with self._lock:
            if token in self._imports:
                del self._imports[token]
                return True
            return False

    cancel = pop

    def clear(self) -> None:
        with self._lock:
            self._imports.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._imports)
