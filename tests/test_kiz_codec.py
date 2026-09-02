"""
Тесты kiz_codec — строгий извлекатель КИ-31.

Покрытие:
- Позитивные: чистый КИ-31, полный код с GS/WB-FFFD/экранированными разделителями,
  только AI 91, только AI 92, ведущие FNC1/GS, текстовый "GS" на границе.
- Негативные: пустой/None, слишком короткий, неверный префикс/GTIN/серийный,
  хвост без разделителя, усечённые 91/92, лишние данные, неизвестный AI,
  "91" внутри serial, "GS" внутри serial, ошибки без фрагментов КИЗ.
"""

import pytest
from app.services.kiz_codec import (
    extract_ki,
    extract_ki_or_none,
    extract_ki_or_reason,
    KizResult,
    KizCodecError,
    EmptyInputError,
    InvalidPrefixError,
    InvalidGtinError,
    MissingSerialAiError,
    InvalidSerialError,
    TooShortError,
    InvalidTailError,
    GS,
    WB_FFFD,
    KI_LENGTH,
)

# ═════════════════════════════════════════════════════════════════════════════
#  Фикстуры
# ═════════════════════════════════════════════════════════════════════════════

# Валидный КИ-31: 01 + GTIN-14 + 21 + serial-13
VALID_KI = "010123456789012321SERIAL1234567"
assert len(VALID_KI) == KI_LENGTH

# Другой валидный КИ (с цифрами в serial)
VALID_KI2 = "014612345678901221ABC123XYZ7890"
assert len(VALID_KI2) == KI_LENGTH

# Валидные криптохвосты
VALID_91 = "91abcd"
VALID_92 = "92" + "B" * 44

# ═════════════════════════════════════════════════════════════════════════════
#  Позитивные тесты
# ═════════════════════════════════════════════════════════════════════════════


class TestPositive:
    """Граничные случаи, которые должны проходить."""

    def test_clean_ki31(self):
        """Чистый 31-символьный КИ возвращается без изменений."""
        result = extract_ki(VALID_KI)
        assert result.ki == VALID_KI
        assert not result.had_tail
        assert result.tail_description == ""

    def test_ki31_with_different_serial(self):
        """Serial может содержать буквы и цифры."""
        result = extract_ki(VALID_KI2)
        assert result.ki == VALID_KI2
        assert not result.had_tail

    def test_full_gs_91_92(self):
        """Полный код: КИ + GS + 91(4) + GS + 92(44)."""
        full = VALID_KI + GS + VALID_91 + GS + VALID_92
        result = extract_ki(full)
        assert result.ki == VALID_KI
        assert result.had_tail
        assert "91" in result.tail_description
        assert "92" in result.tail_description

    def test_full_wb_fffd(self):
        """WB-вариант: U+FFFD вместо GS."""
        full = VALID_KI + WB_FFFD + VALID_91 + WB_FFFD + VALID_92
        result = extract_ki(full)
        assert result.ki == VALID_KI
        assert result.had_tail

    def test_only_ai_91(self):
        """Только AI 91 после разделителя."""
        result = extract_ki(VALID_KI + GS + VALID_91)
        assert result.ki == VALID_KI
        assert result.had_tail
        assert "91" in result.tail_description
        assert "92" not in result.tail_description

    def test_only_ai_92(self):
        """Только AI 92 после разделителя."""
        result = extract_ki(VALID_KI + GS + VALID_92)
        assert result.ki == VALID_KI
        assert result.had_tail
        assert "92" in result.tail_description

    def test_leading_fnc1(self):
        """Ведущий FNC1 (\xe8) сбрасывается."""
        result = extract_ki("\xe8" + VALID_KI)
        assert result.ki == VALID_KI
        assert not result.had_tail

    def test_leading_gs(self):
        """Ведущий GS сбрасывается."""
        result = extract_ki(GS + VALID_KI)
        assert result.ki == VALID_KI
        assert not result.had_tail

    def test_trailing_whitespace(self):
        """Обрезка пробелов и кавычек."""
        result = extract_ki(' "' + VALID_KI + '" ')
        assert result.ki == VALID_KI

    def test_escaped_unicode_gs(self):
        """Экранированный \\u001d нормализуется."""
        result = extract_ki(VALID_KI + "\\u001d" + VALID_91)
        assert result.ki == VALID_KI
        assert result.had_tail

    def test_escaped_hex_gs(self):
        """Экранированный \\x1d нормализуется."""
        result = extract_ki(VALID_KI + "\\x1d" + VALID_91)
        assert result.ki == VALID_KI
        assert result.had_tail

    def test_escaped_fffd(self):
        """Экранированный \\ufffd нормализуется."""
        result = extract_ki(VALID_KI + "\\ufffd" + VALID_91)
        assert result.ki == VALID_KI
        assert result.had_tail

    def test_textual_gs_at_boundary(self):
        """Текстовый 'GS' на границе между AI нормализуется."""
        result = extract_ki(VALID_KI + "GS" + VALID_91 + "GS" + VALID_92)
        assert result.ki == VALID_KI
        assert result.had_tail

    def test_textual_gs_before_ai_01(self):
        """'GS' перед AI 01 на границе КИ нормализуется."""
        result = extract_ki(VALID_KI + "GS" + "91abcd")
        assert result.ki == VALID_KI
        assert result.had_tail

    def test_mixed_separators(self):
        """Смешанные разделители: GS и FFFD."""
        full = VALID_KI + GS + VALID_91 + WB_FFFD + VALID_92
        result = extract_ki(full)
        assert result.ki == VALID_KI
        assert result.had_tail

    def test_ki31_with_91_in_serial(self):
        """'91' внутри серийного номера не считается AI."""
        serial = "ABC91XYZ12345"
        ki = "010123456789012321" + serial
        assert len(ki) == 31
        result = extract_ki(ki)
        assert result.ki == ki
        assert not result.had_tail

    def test_ki31_with_gs_in_serial(self):
        """Текстовый 'GS' внутри serial не нормализуется."""
        serial = "XXGS99YY12345"
        ki = "010123456789012321" + serial
        result = extract_ki(ki)
        assert result.ki == ki
        assert not result.had_tail
        # Проверяем, что 'GS' остался как литерал
        assert "GS" in result.ki[18:]

    def test_safe_wrapper_or_none(self):
        """extract_ki_or_none возвращает None при ошибке."""
        assert extract_ki_or_none(VALID_KI) == VALID_KI
        assert extract_ki_or_none("") is None
        assert extract_ki_or_none(None) is None

    def test_safe_wrapper_or_reason(self):
        """extract_ki_or_reason возвращает (None, reason) при ошибке."""
        ki, reason = extract_ki_or_reason(VALID_KI)
        assert ki == VALID_KI
        assert reason is None
        ki2, reason2 = extract_ki_or_reason("")
        assert ki2 is None
        assert reason2 is not None


# ═════════════════════════════════════════════════════════════════════════════
#  Негативные тесты
# ═════════════════════════════════════════════════════════════════════════════


class TestNegative:
    """Граничные случаи, которые должны отклоняться."""

    def test_empty_input(self):
        """Пустая строка."""
        with pytest.raises(EmptyInputError):
            extract_ki("")

    def test_none_input(self):
        """None."""
        with pytest.raises(EmptyInputError):
            extract_ki(None)

    def test_whitespace_only(self):
        """Только пробелы."""
        with pytest.raises(EmptyInputError):
            extract_ki("   ")

    def test_too_short(self):
        """Меньше 31 символа."""
        with pytest.raises(TooShortError):
            extract_ki("010123456789012321SERIAL12")

    def test_invalid_prefix(self):
        """Не начинается с 01."""
        with pytest.raises(InvalidPrefixError):
            extract_ki("020123456789012321SERIAL1234567")

    def test_invalid_gtin_non_digit(self):
        """GTIN содержит не-цифры."""
        with pytest.raises(InvalidGtinError):
            extract_ki("01ABCD5678901234" + "21" + "SERIAL1234567")

    def test_invalid_gtin_wrong_length(self):
        """GTIN не 14 символов (короткий)."""
        # 31 символов, но GTIN на позиции 2:16 содержит не-цифру вместо 14-й цифры
        with pytest.raises(InvalidGtinError):
            extract_ki("011234567890123X21SERIAL1234567")

    def test_missing_serial_ai(self):
        """Нет AI 21 после GTIN."""
        with pytest.raises(MissingSerialAiError):
            extract_ki("0101234567890123XXSERIAL1234567")

    def test_invalid_serial_too_short(self):
        """Серийный номер короче 13 символов (невозможно при 31-символьном КИ)."""
        with pytest.raises(TooShortError):
            extract_ki("010123456789012321" + "AAAA")

    def test_tail_without_separator_bare_91(self):
        """Хвост '91...' без разделителя — отклонять."""
        with pytest.raises(InvalidTailError):
            extract_ki(VALID_KI + "91abcd")

    def test_tail_without_separator_bare_92(self):
        """Хвост '92...' без разделителя — отклонять."""
        with pytest.raises(InvalidTailError):
            extract_ki(VALID_KI + "92" + "B" * 44)

    def test_truncated_ai_91(self):
        """AI 91 короче 4 символов."""
        with pytest.raises(InvalidTailError):
            extract_ki(VALID_KI + GS + "91ab")

    def test_truncated_ai_92(self):
        """AI 92 короче 44 символов."""
        with pytest.raises(InvalidTailError):
            extract_ki(VALID_KI + GS + "92" + "B" * 10)

    def test_extra_data_after_recognized_tail(self):
        """Лишние данные после распознанного хвоста."""
        with pytest.raises(InvalidTailError):
            extract_ki(VALID_KI + GS + "91abcd" + "EXTRA")

    def test_extra_data_after_92(self):
        """Лишние данные после полного 91+92."""
        with pytest.raises(InvalidTailError):
            extract_ki(VALID_KI + GS + "91abcd" + GS + VALID_92 + "TAIL")

    def test_unknown_ai_in_tail(self):
        """Неизвестный AI в хвосте."""
        with pytest.raises(InvalidTailError):
            extract_ki(VALID_KI + GS + "96abcd")

    def test_separator_without_ai(self):
        """Разделитель без AI после него."""
        with pytest.raises(InvalidTailError):
            extract_ki(VALID_KI + GS)

    def test_ai_91_exceeds_4_chars(self):
        """AI 91 с лишними символами (больше 4)."""
        # 91 + 5 символов — код распознаёт 4, остаётся лишний
        with pytest.raises(InvalidTailError):
            extract_ki(VALID_KI + GS + "91abcde")

    def test_ai_92_exceeds_44_chars(self):
        """AI 92 с лишними символами (больше 44)."""
        with pytest.raises(InvalidTailError):
            extract_ki(VALID_KI + GS + "92" + "B" * 45)

    def test_gs_inside_serial_unchanged(self):
        """'GS' внутри serial не должен превращаться в разделитель
        и менять длину КИ."""
        # Serial 'GS' + 2 digits = 5 chars, оставшиеся 8 chars
        serial = "GS99ZZZ123456"  # 13 chars, 'GS99' at start
        ki = "010123456789012321" + serial
        # Старый regex GS(?=\\d{2}) заменил бы GS99 → \\u001d99, ломая длину
        result = extract_ki(ki)
        assert result.ki == ki
        assert not result.had_tail


# ═════════════════════════════════════════════════════════════════════════════
#  Тесты на чистоту ошибок
# ═════════════════════════════════════════════════════════════════════════════


class TestErrorMessages:
    """Ошибки не должны содержать фрагменты полного КИЗ или хвоста."""

    def test_error_no_full_ki(self):
        """Сообщение об ошибке не содержит КИЗ."""
        for code in [
            "",
            None,
            "   ",
            "020123456789012321SERIAL1234567",
            VALID_KI + GS + "EXTRA",
        ]:
            try:
                extract_ki(code)
            except KizCodecError as e:
                msg = str(e)
                assert "010123456789012321" not in msg
                assert len(VALID_KI) not in [len(msg)]
                # reason не содержит данных
                assert "abcd" not in e.reason

    def test_error_reason_short(self):
        """reason — короткий идентификатор, не данные."""
        for code in ["", None, VALID_KI + GS + "91ab"]:
            try:
                extract_ki(code)
            except KizCodecError as e:
                assert len(e.reason) < 40, f"reason слишком длинный: {e.reason}"
                assert " " not in e.reason, f"reason содержит пробел: {e.reason}"
