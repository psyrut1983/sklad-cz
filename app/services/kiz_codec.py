"""
kiz_codec — строгий извлекатель КИ-31 из кода маркировки Честного Знака.

Безопасные разделители
─────────────────────
- GS (U+001D) — настоящий Group Separator.
- U+FFFD — символ замены, которым Wildberries представляет GS
  в некоторых экспортированных XLSX (доказано в PROJECT_PLAN.md).
  Принимается только на позициях, где структурно ожидается разделитель
  (сразу после 31-символьного КИ, между AI 91 и AI 92).
- Текстовый литерал "GS" — только между известными AI.
- Экранированные варианты: \\u001d, \\u001D, \\x1d, \\x1D, \\ufffd.

Что делает
──────────
1. Нормализует безопасные представления разделителей.
2. Валидирует КИ: строго `01` + GTIN-14 + `21` + serial-13 = 31 символ.
3. Для 31-символьного входа возвращает его без изменений.
4. Для полного кода (с криптохвостом) удаляет хвост ТОЛЬКО если
   после КИ-31 распознана допустимая структура:
   разделитель + AI 91 (+ 4 символа) + [разделитель + AI 92 (+ 44 символа)].
5. Отклоняет повреждённые, неоднозначные и пустые значения
   типизированной ошибкой.

Чего НЕ делает
──────────────
- Не логирует полный вход.
- Не делает split по первой подстроке "91".
- Не отрезает фиксированное число символов.
- Не изменяет 31-символьный КИ.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# ── Константы ────────────────────────────────────────────────────────────────

GS = "\u001d"           # Group Separator — настоящий разделитель GS1
WB_FFFD = "\ufffd"      # U+FFFD — чем Wildberries заменил GS в XLSX
FNC1 = "\xe8"           # FNC1 (Function Code 1) — префикс DataMatrix

KI_LENGTH = 31          # Длина КИ: 01(2) + GTIN(14) + 21(2) + serial(13)
GTIN_LENGTH = 14        # Длина GTIN в AI 01
SERIAL_LENGTH = 13      # Длина серийного номера в AI 21

KNOWN_SEPARATORS = {GS, WB_FFFD}

# ── Ошибки ───────────────────────────────────────────────────────────────────


class KizCodecError(ValueError):
    """Базовая ошибка codec."""
    reason: str

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


class EmptyInputError(KizCodecError):
    def __init__(self) -> None:
        super().__init__("Пустой вход", "empty_input")


class InvalidPrefixError(KizCodecError):
    def __init__(self, detail: str = "") -> None:
        msg = "КИ не начинается с AI 01" + (f": {detail}" if detail else "")
        super().__init__(msg, "invalid_prefix")


class InvalidGtinError(KizCodecError):
    def __init__(self, detail: str = "") -> None:
        msg = "GTIN не 14 цифр"
        if detail:
            msg += f": {detail}"
        super().__init__(msg, "invalid_gtin")


class MissingSerialAiError(KizCodecError):
    def __init__(self) -> None:
        super().__init__("Отсутствует AI 21 после GTIN", "missing_serial_ai")


class InvalidSerialError(KizCodecError):
    def __init__(self, detail: str = "") -> None:
        msg = "Серийный номер не 13 символов" + (f": {detail}" if detail else "")
        super().__init__(msg, "invalid_serial")


class TooShortError(KizCodecError):
    def __init__(self, length: int) -> None:
        super().__init__(
            f"Код короче {KI_LENGTH} символов ({length})",
            "too_short",
        )


class InvalidTailError(KizCodecError):
    def __init__(self, detail: str = "") -> None:
        msg = "Не удалось распознать криптохвост" + (f": {detail}" if detail else "")
        super().__init__(msg, "invalid_tail")


# ── Результат ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class KizResult:
    """Результат успешного извлечения КИ."""
    ki: str                    # 31-символьный КИ
    had_tail: bool = False     # Был ли удалён криптохвост
    tail_description: str = "" # Описание удалённого хвоста (без данных)


# ── Нормализация ─────────────────────────────────────────────────────────────


def _normalize(raw: str) -> str:
    """
    Нормализует только безопасные представления разделителей.
    Не логирует полный вход.
    """
    s = raw.strip(" \t\n\r\x0b\x0c").strip('"')
    if not s:
        return s

    # Экранированные последовательности → реальные символы
    s = s.replace("\\u001d", GS).replace("\\u001D", GS)
    s = s.replace("\\x1d", GS).replace("\\x1D", GS)
    s = s.replace("\\ufffd", WB_FFFD).replace("\\uFFFD", WB_FFFD)

    # Символ U+241d (символьный GS) → настоящий GS
    s = s.replace("\u241d", GS)

    # Текстовый "GS" → GS только на структурных границах между AI
    s = re.sub(r'(?<=\d)GS(?=01|21|91|92)', GS, s)
    s = re.sub(r'(?<=[A-Za-z0-9+/=])GS(?=01|21|91|92)', GS, s)

    # Удаляем только ASCII-пробелы (не GS/U+FFFD, т.к. Python считает их whitespace)
    s = s.strip(" \t\n\r\x0b\x0c")
    return s


# ── Валидация КИ-31 ──────────────────────────────────────────────────────────


def _validate_ki_structure(ki: str) -> None:
    """Проверяет, что строка — валидный КИ-31."""
    if len(ki) != KI_LENGTH:
        raise TooShortError(len(ki))

    if not ki.startswith("01"):
        raise InvalidPrefixError()

    gtin = ki[2:16]
    if not (len(gtin) == GTIN_LENGTH and gtin.isdigit()):
        raise InvalidGtinError(gtin if gtin else "пустой")

    if ki[16:18] != "21":
        raise MissingSerialAiError()

    serial = ki[18:31]
    if len(serial) != SERIAL_LENGTH:
        raise InvalidSerialError(f"длина {len(serial)}")

    # Serial может содержать буквы и цифры — всё ок
    if not serial:
        raise InvalidSerialError("пустой")


# ── Извлечение КИ ────────────────────────────────────────────────────────────


def extract_ki(raw: str | None) -> KizResult:
    """
    Извлекает 31-символьный КИ из кода маркировки.

    Параметры
    ---------
    raw: str | None
        Входной код маркировки (полный или уже КИ-31).

    Возвращает
    ----------
    KizResult с полем ki = 31-символьный КИ.

    Исключения
    ----------
    KizCodecError — если код не удалось разобрать.
    """
    if not raw:
        raise EmptyInputError()

    normalized = _normalize(raw)
    if not normalized:
        raise EmptyInputError()

    # Проверяем наличие FNC1 в начале и сбрасываем его
    body = normalized
    if body.startswith(FNC1):
        body = body[1:]
    if body.startswith(GS):
        body = body[1:]

    if len(body) < KI_LENGTH:
        raise TooShortError(len(body))

    # Первые 31 символ — потенциальный КИ
    candidate_ki = body[:KI_LENGTH]
    _validate_ki_structure(candidate_ki)

    tail = body[KI_LENGTH:]

    if not tail:
        # Чистый КИ-31, без хвоста
        return KizResult(ki=candidate_ki, had_tail=False)

    # ── Разбор хвоста ────────────────────────────────────────────────────
    # Ожидаемая структура:
    #   [разделитель] 91<4 символа> [разделитель] 92<44 символа>
    #   [разделитель] 91<4 символа>
    #   [разделитель] 92<44 символа>
    # Разделитель перед первым AI ОБЯЗАТЕЛЕН. Голые "91..." или "92..."
    # без разделителя не принимаются.

    pos = 0
    tail_parts: list[tuple[str, int]] = []

    # Первый символ хвоста ДОЛЖЕН быть разделителем
    if pos >= len(tail) or tail[pos] not in KNOWN_SEPARATORS:
        raise InvalidTailError("ожидался разделитель после КИ")

    pos += 1

    while pos < len(tail):
        # Пропускаем разделители между AI
        while pos < len(tail) and tail[pos] in KNOWN_SEPARATORS:
            pos += 1

        if pos >= len(tail):
            break

        ai = tail[pos:pos+2]
        if ai == "91":
            if pos + 2 + 4 > len(tail):
                raise InvalidTailError("AI 91: недостаточно данных")
            tail_parts.append(("91", 4))
            pos += 2 + 4
        elif ai == "92":
            if pos + 2 + 44 > len(tail):
                raise InvalidTailError("AI 92: недостаточно данных")
            tail_parts.append(("92", 44))
            pos += 2 + 44
        else:
            raise InvalidTailError(
                f"неизвестный AI в хвосте на позиции {pos + KI_LENGTH}"
            )

    if not tail_parts:
        raise InvalidTailError("разделитель без AI после КИ")

    # Успех: хвост распознан
    desc = "+".join(f"{ai}({n}ch)" for ai, n in tail_parts)
    return KizResult(ki=candidate_ki, had_tail=True, tail_description=desc)


# ── Упрощённый API ───────────────────────────────────────────────────────────


def extract_ki_or_none(raw: str | None) -> str | None:
    """
    Безопасная обёртка: возвращает КИ-31 или None.
    """
    try:
        return extract_ki(raw).ki
    except KizCodecError:
        return None


def extract_ki_or_reason(raw: str | None) -> tuple[str | None, str | None]:
    """
    Безопасная обёртка: возвращает (ki, None) или (None, reason).
    """
    try:
        result = extract_ki(raw)
        return result.ki, None
    except KizCodecError as e:
        return None, e.reason
