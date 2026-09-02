"""
chestny.services.dedup — HMAC-дедупликация КИЗ.

Постоянный 32-байтовый ключ в instance/hmac.key (игнорируется Git).
HMAC-SHA256 по очищенному КИ-31. Глобальная проверка дублей между профилями.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import stat
from pathlib import Path
from typing import Any

from app.chestny.factory import db
from app.chestny.models import OrganizationProfile, ProcessedKiz
from app.services.kiz_codec import extract_ki

KEY_FILENAME = "hmac.key"


class HmacKeyError(RuntimeError):
    """Ошибка работы с ключом HMAC."""
    pass


class KiRejectedError(ValueError):
    """КИ отклонён: не чистый KI-31 или невалидный."""
    pass


def _safe_read_key(key_path: Path) -> bytes:
    """Читает 32 байта ключа, санитизирует ошибки."""
    try:
        data = key_path.read_bytes()
    except Exception:
        raise HmacKeyError("Не удалось прочитать файл ключа HMAC") from None
    if len(data) != 32:
        raise HmacKeyError(
            "Файл ключа HMAC имеет неверный размер"
        ) from None
    return data


def load_or_create_hmac_key(instance_path: str) -> bytes:
    """
    Загружает или создаёт 32-байтовый HMAC-ключ.

    Атомарное создание (write+rename), permissions 0600 на POSIX.
    Не следует symlink. Ошибки не содержат key/secret/raw.
    """
    key_path = Path(instance_path) / KEY_FILENAME
    parent = key_path.parent

    if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
        raise HmacKeyError("Некорректный путь instance для HMAC-ключа")

    parent.mkdir(parents=True, exist_ok=True)

    if key_path.is_symlink():
        raise HmacKeyError("Путь к ключу HMAC является симлинком")

    if key_path.exists():
        if not key_path.is_file():
            raise HmacKeyError("Путь к ключу HMAC не является обычным файлом")
        return _safe_read_key(key_path)

    # Атомарное создание
    key = secrets.token_bytes(32)
    tmp = key_path.with_suffix(".tmp." + secrets.token_hex(8))
    try:
        tmp.write_bytes(key)
        os.chmod(str(tmp), stat.S_IRUSR | stat.S_IWUSR)
        tmp.replace(key_path)
    except Exception:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
        raise HmacKeyError("Не удалось создать HMAC-ключ") from None
    return key


def _validate_ki31(ki31: str) -> str:
    """Проверяет, что строка — чистый KI-31 без хвоста. Без raw в ошибках."""
    try:
        result = extract_ki(ki31)
    except Exception:
        raise KiRejectedError("Некорректный КИ") from None
    if result.had_tail:
        raise KiRejectedError("КИ содержит криптохвост")
    return result.ki


def hmac_digest(ki31: str, key: bytes) -> str:
    """HMAC-SHA256 от чистого KI-31, lowercase hex."""
    clean = _validate_ki31(ki31)
    return hmac.new(key, clean.encode("ascii"), hashlib.sha256).hexdigest()


def mask_ki(ki31: str) -> str:
    """Безопасная маска: первые 4 + **** + последние 4."""
    _validate_ki31(ki31)
    return ki31[:4] + "****" + ki31[-4:]


def find_confirmed_duplicates(
    kis: list[str], key: bytes
) -> dict[str, dict[str, Any]]:
    """
    Проверяет список КИ на глобальные дубли (только CONFIRMED).

    Возвращает mapping KI → {display_name, document_id, processed_at, mask}.
    """
    if not kis:
        return {}

    digests = [hmac_digest(ki, key) for ki in kis]

    rows = (
        db.session.query(ProcessedKiz, OrganizationProfile.display_name)
        .join(OrganizationProfile, ProcessedKiz.profile_id == OrganizationProfile.id)
        .filter(
            ProcessedKiz.hmac_digest.in_(digests),
            ProcessedKiz.status == "CONFIRMED",
        )
        .all()
    )

    digest_to_ki = dict(zip(digests, kis))

    result: dict[str, dict[str, Any]] = {}
    for pk, display_name in rows:
        ki = digest_to_ki.get(pk.hmac_digest)
        if ki is None:
            continue
        result[ki] = {
            "display_name": display_name,
            "document_id": pk.document_id,
            "processed_at": pk.processed_at.isoformat() if pk.processed_at else "",
            "mask": pk.mask,
        }
    return result
