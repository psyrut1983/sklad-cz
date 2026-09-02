"""
chestny.validation — нормализация и валидация полей профилей.

Ни одна функция не логирует полный INN, thumbprint или raw subject.
"""

from __future__ import annotations

import re
import uuid


# ═════════════════════════════════════════════════════════════════════════════
#  INN (физлицо/ИП, 12 цифр)
# ═════════════════════════════════════════════════════════════════════════════

INN_WEIGHTS_1 = (7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
INN_WEIGHTS_2 = (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)


def normalize_inn(raw: str) -> str | None:
    """
    Нормализует и проверяет ИНН физлица/ИП (12 цифр).

    После strip принимает РОВНО 12 цифр.
    Любые буквы, дефисы, пробелы внутри или прочий мусор → None.
    Проверяет обе контрольные суммы.
    Возвращает каноническую строку или None.
    """
    cleaned = raw.strip()
    if len(cleaned) != 12 or not cleaned.isdigit():
        return None

    digits = [int(d) for d in cleaned]

    # Контрольная сумма 1 (позиция 11)
    s1 = sum(d * w for d, w in zip(digits[:10], INN_WEIGHTS_1))
    check1 = (s1 % 11) % 10
    if check1 != digits[10]:
        return None

    # Контрольная сумма 2 (позиция 12)
    s2 = sum(d * w for d, w in zip(digits[:11], INN_WEIGHTS_2))
    check2 = (s2 % 11) % 10
    if check2 != digits[11]:
        return None

    return cleaned


# ═════════════════════════════════════════════════════════════════════════════
#  Certificate thumbprint
# ═════════════════════════════════════════════════════════════════════════════

HEX40_RE = re.compile(r"^[0-9A-F]{40}$")


def normalize_thumbprint(raw: str) -> str | None:
    """
    Нормализует отпечаток сертификата.

    Удаляет пробелы, двоеточия → uppercase → 40 hex символов.
    """
    cleaned = raw.strip().replace(" ", "").replace(":", "").upper()
    if not HEX40_RE.match(cleaned):
        return None
    return cleaned


# ═════════════════════════════════════════════════════════════════════════════
#  FIAS UUID
# ═════════════════════════════════════════════════════════════════════════════


def normalize_fias_id(raw: str) -> str | None:
    """
    Нормализует FIAS UUID.

    Принимает любой UUID-формат, возвращает lowercase canonical.
    """
    cleaned = raw.strip()
    if not cleaned:
        return None
    try:
        return str(uuid.UUID(cleaned)).lower()
    except (ValueError, AttributeError):
        return None
