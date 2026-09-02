# tests/test_excel_import.py
"""Тесты строгого Excel-парсера (этап 3B)."""

from __future__ import annotations

import datetime
import io
import os
import tempfile

import openpyxl
import pytest

from app.services.excel_import import (
    AcceptedRow,
    ExcludedRow,
    FileImportError,
    ImportResult,
    InvalidHeadersError,
    MissingSheetError,
    _normalize_cost,
    _normalize_date,
    parse_xlsx,
    HEADERS,
)

from app.services.synthetic_xlsx import (
    create_synthetic_xlsx,
)

# ═════════════════════════════════════════════════════════════════════════════
#  Константы
# ═════════════════════════════════════════════════════════════════════════════

KI_CLEAN = "010123456789012321SERIAL1234567"
KI_OTHER = "014612345678901221ABC123XYZ7890"
FFFD_CHAR = "\ufffd"

KI_WITH_FFFD = KI_CLEAN + FFFD_CHAR + "91abcd" + FFFD_CHAR + "92" + "B" * 44


# ═════════════════════════════════════════════════════════════════════════════
#  Хелперы
# ═════════════════════════════════════════════════════════════════════════════


def make_wb(headers: list[str] | None = None) -> openpyxl.Workbook:
    """Создаёт workbook с листом КИЗ и указанными заголовками."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "КИЗ"
    if headers is not None:
        ws.append(headers)
    return wb


def add_row(ws: openpyxl.worksheet.worksheet.Worksheet, row: list) -> None:
    """Добавляет строку данных."""
    ws.append(row)


def _save_and_parse(wb: openpyxl.Workbook) -> ImportResult:
    """Сохраняет workbook во временный файл и парсит."""
    fd, path = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    try:
        wb.save(path)
        return parse_xlsx(path)
    finally:
        os.unlink(path)


# ═════════════════════════════════════════════════════════════════════════════
#  1. Заголовки
# ═════════════════════════════════════════════════════════════════════════════

class TestHeaders:
    def test_exact_headers_pass(self):
        """Точный набор заголовков проходит."""
        wb = make_wb(HEADERS)
        add_row(wb["КИЗ"], [1, "STK", KI_CLEAN, "CHK", 100, "RUB", "FN",
                            datetime.date(2026, 9, 1), "Продажа", "-"])
        result = _save_and_parse(wb)
        assert result.summary.accepted == 1

    def test_missing_headers(self):
        """Неполный набор заголовков → InvalidHeadersError."""
        wb = make_wb(HEADERS[:5])
        with pytest.raises(InvalidHeadersError, match="10 колонок"):
            _save_and_parse(wb)

    def test_extra_headers(self):
        """Лишняя колонка → InvalidHeadersError."""
        wb = make_wb(HEADERS + ["extra"])
        with pytest.raises(InvalidHeadersError, match="10 колонок"):
            _save_and_parse(wb)

    def test_duplicate_headers(self):
        """Дубликат заголовка → InvalidHeadersError (несовпадение порядка)."""
        dup = HEADERS[:]
        dup[3] = dup[2]  # заменить "Номер чека" на "КИЗ"
        wb = make_wb(dup)
        with pytest.raises(InvalidHeadersError, match="колонка 4"):
            _save_and_parse(wb)

    def test_wrong_order_headers(self):
        """Переставленные заголовки → InvalidHeadersError."""
        wrong = HEADERS[:]
        wrong[2], wrong[3] = wrong[3], wrong[2]
        wb = make_wb(wrong)
        with pytest.raises(InvalidHeadersError, match="колонка 3"):
            _save_and_parse(wb)

    def test_header_row_only(self):
        """Только заголовок, нет данных → 0 accepted."""
        wb = make_wb(HEADERS)
        result = _save_and_parse(wb)
        assert result.summary.total_rows == 0
        assert result.summary.accepted == 0

    def test_header_is_data_row_ignored(self):
        """Строка с данными вместо заголовка → InvalidHeadersError."""
        wb = make_wb([1, "STK", KI_CLEAN, "CHK", 100, "RUB", "FN",
                      "2026-09-01", "Продажа", "-"])
        with pytest.raises(InvalidHeadersError):
            _save_and_parse(wb)


# ═════════════════════════════════════════════════════════════════════════════
#  2. Missing sheet / corrupt
# ═════════════════════════════════════════════════════════════════════════════

class TestFileLevelErrors:
    def test_missing_sheet(self):
        """Нет листа КИЗ → MissingSheetError."""
        wb = openpyxl.Workbook()
        wb.active.title = "Другой"
        with pytest.raises(MissingSheetError):
            _save_and_parse(wb)

    def test_corrupt_xlsx(self):
        """Повреждённый файл → FileImportError с безопасным сообщением."""
        fd, path = tempfile.mkstemp(suffix=".xlsx")
        os.close(fd)
        with open(path, "wb") as f:
            f.write(b"not a zip file")
        try:
            with pytest.raises(FileImportError, match="Не удалось открыть файл"):
                parse_xlsx(path)
        finally:
            os.unlink(path)


# ═════════════════════════════════════════════════════════════════════════════
#  3. Тип операции
# ═════════════════════════════════════════════════════════════════════════════

class TestOperationFilter:
    def test_sale_accepted(self):
        """Продажа → accepted."""
        wb = make_wb(HEADERS)
        add_row(wb["КИЗ"], [1, "STK", KI_CLEAN, "CHK", 100, "RUB", "FN",
                            datetime.date(2026, 9, 1), "Продажа", "-"])
        result = _save_and_parse(wb)
        assert result.summary.accepted == 1

    def test_sale_with_whitespace(self):
        """' Продажа ' (с пробелами) → accepted."""
        wb = make_wb(HEADERS)
        add_row(wb["КИЗ"], [1, "STK", KI_CLEAN, "CHK", 100, "RUB", "FN",
                            datetime.date(2026, 9, 1), "  Продажа  ", "-"])
        result = _save_and_parse(wb)
        assert result.summary.accepted == 1

    def test_return_excluded(self):
        """Возврат → excluded."""
        wb = make_wb(HEADERS)
        add_row(wb["КИЗ"], [1, "STK", KI_CLEAN, "CHK", 100, "RUB", "FN",
                            datetime.date(2026, 9, 1), "Возврат", "-"])
        result = _save_and_parse(wb)
        assert result.excluded[0].reason_code == "return_operation"

    def test_dash_excluded(self):
        """'-' → excluded."""
        wb = make_wb(HEADERS)
        add_row(wb["КИЗ"], [1, "STK", KI_CLEAN, "CHK", 100, "RUB", "FN",
                            datetime.date(2026, 9, 1), "-", "-"])
        result = _save_and_parse(wb)
        assert result.excluded[0].reason_code == "dash_operation"

    def test_empty_operation_excluded(self):
        """Пустой тип операции → excluded."""
        wb = make_wb(HEADERS)
        add_row(wb["КИЗ"], [1, "STK", KI_CLEAN, "CHK", 100, "RUB", "FN",
                            datetime.date(2026, 9, 1), "", "-"])
        result = _save_and_parse(wb)
        assert result.excluded[0].reason_code == "empty_operation"

    def test_other_operation_excluded(self):
        """Неизвестный тип → excluded."""
        wb = make_wb(HEADERS)
        add_row(wb["КИЗ"], [1, "STK", KI_CLEAN, "CHK", 100, "RUB", "FN",
                            datetime.date(2026, 9, 1), "Списание", "-"])
        result = _save_and_parse(wb)
        assert result.excluded[0].reason_code == "unsupported_operation"

    def test_unsupported_message_no_raw_value(self):
        """Сообщение unsupported_operation не содержит произвольного значения ячейки."""
        wb = make_wb(HEADERS)
        add_row(wb["КИЗ"], [1, "STK", KI_CLEAN, "CHK", 100, "RUB", "FN",
                            datetime.date(2026, 9, 1), "!@#$%^&*()", "-"])
        result = _save_and_parse(wb)
        msg = result.excluded[0].message
        assert "!@#$%" not in msg
        assert "не поддерживается" in msg


# ═════════════════════════════════════════════════════════════════════════════
#  4. КИЗ
# ═════════════════════════════════════════════════════════════════════════════

class TestKizValidation:
    def test_empty_kiz_excluded(self):
        """Пустой КИЗ → excluded."""
        wb = make_wb(HEADERS)
        add_row(wb["КИЗ"], [1, "STK", "", "CHK", 100, "RUB", "FN",
                            datetime.date(2026, 9, 1), "Продажа", "-"])
        result = _save_and_parse(wb)
        assert result.excluded[0].reason_code == "empty_kiz"

    def test_whitespace_kiz_excluded(self):
        """Пробельный КИЗ → excluded (пустой)."""
        wb = make_wb(HEADERS)
        add_row(wb["КИЗ"], [1, "STK", "   ", "CHK", 100, "RUB", "FN",
                            datetime.date(2026, 9, 1), "Продажа", "-"])
        result = _save_and_parse(wb)
        assert result.excluded[0].reason_code == "empty_kiz"

    def test_invalid_kiz_excluded(self):
        """Невалидный КИЗ → excluded."""
        wb = make_wb(HEADERS)
        add_row(wb["КИЗ"], [1, "STK", "INVALID", "CHK", 100, "RUB", "FN",
                            datetime.date(2026, 9, 1), "Продажа", "-"])
        result = _save_and_parse(wb)
        assert result.excluded[0].reason_code == "invalid_kiz"

    def test_valid_kiz_accepted(self):
        """Валидный КИЗ → accepted."""
        wb = make_wb(HEADERS)
        add_row(wb["КИЗ"], [1, "STK", KI_CLEAN, "CHK", 100, "RUB", "FN",
                            datetime.date(2026, 9, 1), "Продажа", "-"])
        result = _save_and_parse(wb)
        assert result.accepted[0].ki == KI_CLEAN

    def test_kiz_with_tail_accepted_ki31(self):
        """КИЗ с криптохвостом → accepted, ki=31 символов."""
        wb = make_wb(HEADERS)
        add_row(wb["КИЗ"], [1, "STK", KI_WITH_FFFD, "CHK", 100, "RUB", "FN",
                            datetime.date(2026, 9, 1), "Продажа", "-"])
        result = _save_and_parse(wb)
        assert result.accepted[0].ki == KI_CLEAN
        assert len(result.accepted[0].ki) == 31


# ═════════════════════════════════════════════════════════════════════════════
#  5. Номер чека / ФН (пустые / пробельные)
# ═════════════════════════════════════════════════════════════════════════════

class TestCheckAndFn:
    def test_empty_check_excluded(self):
        """Пустой чек → excluded."""
        wb = make_wb(HEADERS)
        add_row(wb["КИЗ"], [1, "STK", KI_CLEAN, "", 100, "RUB", "FN",
                            datetime.date(2026, 9, 1), "Продажа", "-"])
        result = _save_and_parse(wb)
        assert result.excluded[0].reason_code == "empty_check"

    def test_whitespace_check_excluded(self):
        """Пробельный чек → excluded."""
        wb = make_wb(HEADERS)
        add_row(wb["КИЗ"], [1, "STK", KI_CLEAN, "   ", 100, "RUB", "FN",
                            datetime.date(2026, 9, 1), "Продажа", "-"])
        result = _save_and_parse(wb)
        assert result.excluded[0].reason_code == "empty_check"

    def test_empty_fn_excluded(self):
        """Пустой ФН → excluded."""
        wb = make_wb(HEADERS)
        add_row(wb["КИЗ"], [1, "STK", KI_CLEAN, "CHK", 100, "RUB", "",
                            datetime.date(2026, 9, 1), "Продажа", "-"])
        result = _save_and_parse(wb)
        assert result.excluded[0].reason_code == "empty_fn"

    def test_whitespace_fn_excluded(self):
        """Пробельный ФН → excluded."""
        wb = make_wb(HEADERS)
        add_row(wb["КИЗ"], [1, "STK", KI_CLEAN, "CHK", 100, "RUB", "  ",
                            datetime.date(2026, 9, 1), "Продажа", "-"])
        result = _save_and_parse(wb)
        assert result.excluded[0].reason_code == "empty_fn"


# ═════════════════════════════════════════════════════════════════════════════
#  6. Стоимость
# ═════════════════════════════════════════════════════════════════════════════

class TestCostNormalization:
    def test_int_rubles(self):
        """int 100 → 10000 коп."""
        assert _normalize_cost(100) == 10000

    def test_string_rubles(self):
        """str '100' → 10000 коп."""
        assert _normalize_cost("100") == 10000

    def test_string_with_kopecks_dot(self):
        """str '100.50' → 10050 коп."""
        assert _normalize_cost("100.50") == 10050

    def test_string_with_kopecks_comma(self):
        """str '100,50' → 10050 коп."""
        assert _normalize_cost("100,50") == 10050

    def test_one_kopeck(self):
        """str '0.01' → 1 коп."""
        assert _normalize_cost("0.01") == 1

    def test_large_value(self):
        """str '999999.99' → 99999999 коп."""
        assert _normalize_cost("999999.99") == 99999999

    def test_decimal_input(self):
        """Decimal('100') → 10000 коп."""
        from decimal import Decimal
        assert _normalize_cost(Decimal("100")) == 10000

    def test_zero_rejected(self):
        """0 → None."""
        assert _normalize_cost(0) is None

    def test_negative_rejected(self):
        """-1 → None."""
        assert _normalize_cost(-1) is None

    def test_empty_string_rejected(self):
        """'' → None."""
        assert _normalize_cost("") is None

    def test_whitespace_string_rejected(self):
        """'   ' → None."""
        assert _normalize_cost("   ") is None

    def test_none_rejected(self):
        """None → None."""
        assert _normalize_cost(None) is None

    def test_abc_rejected(self):
        """'abc' → None."""
        assert _normalize_cost("abc") is None

    def test_bool_true_rejected(self):
        """True → None."""
        assert _normalize_cost(True) is None

    def test_bool_false_rejected(self):
        """False → None."""
        assert _normalize_cost(False) is None

    def test_float_finite_accepted(self):
        """float 99.99 → 9999 коп (через Decimal(str()))."""
        assert _normalize_cost(99.99) == 9999

    def test_float_fractional_accepted(self):
        """float 1299.99 → 129999 коп без binary artifact."""
        result = _normalize_cost(1299.99)
        assert result == 129999
        # Проверка: нет binary-float ошибки
        assert result * 2 == 259998

    def test_float_nan_rejected(self):
        """float NaN → None."""
        assert _normalize_cost(float("nan")) is None

    def test_float_inf_rejected(self):
        """float inf → None."""
        assert _normalize_cost(float("inf")) is None

    def test_float_neg_inf_rejected(self):
        """float -inf → None."""
        assert _normalize_cost(float("-inf")) is None

    def test_gt_2_decimals_string_rejected(self):
        """str '100.123' (>2 decimals) → None."""
        assert _normalize_cost("100.123") is None

    def test_gt_2_decimals_float_rejected(self):
        """float 100.123 (>2 decimals) → None."""
        assert _normalize_cost(100.123) is None

    def test_float_1299_99_accepted(self):
        """float 1299.99 → Decimal 129999."""
        assert _normalize_cost(1299.99) == 129999



class TestCostEndToEnd:
    """Сквозные тесты стоимости через XLSX."""

    def test_xlsx_numeric_1299_99_accepted(self):
        """XLSX numeric 1299.99 в колонке Стоимость → accepted 129999 коп."""
        wb = make_wb(HEADERS)
        add_row(wb["КИЗ"], [1, "STK", KI_CLEAN, "CHK", 1299.99, "RUB", "FN",
                            datetime.date(2026, 9, 1), "Продажа", "-"])
        result = _save_and_parse(wb)
        assert result.summary.accepted == 1
        assert result.accepted[0].cost_kopecks == 129999


# ═════════════════════════════════════════════════════════════════════════════
#  7. Дата
# ═════════════════════════════════════════════════════════════════════════════

class TestDateNormalization:
    """Юнит-тесты _normalize_date."""

    def test_date_object(self):
        """datetime.date(2026, 9, 1) → '2026-09-01'."""
        assert _normalize_date(datetime.date(2026, 9, 1)) == "2026-09-01"

    def test_naive_datetime(self):
        """naive datetime → '2026-09-01'."""
        assert _normalize_date(datetime.datetime(2026, 9, 1, 14, 30)) == "2026-09-01"

    def test_iso_string(self):
        """str '2026-09-01' → '2026-09-01'."""
        assert _normalize_date("2026-09-01") == "2026-09-01"

    def test_dd_mm_yyyy(self):
        """str '01.09.2026' → '2026-09-01'."""
        assert _normalize_date("01.09.2026") == "2026-09-01"

    def test_excel_serial_int(self):
        """int 46223 (2026-07-20) → '2026-07-20'."""
        assert _normalize_date(46223) == "2026-07-20"

    def test_excel_serial_float(self):
        """float 46223.5 → '2026-07-20' (дробный serial, дата без int-truncate)."""
        assert _normalize_date(46223.5) == "2026-07-20"

    def test_invalid_string_rejected(self):
        """str 'not-a-date' → None."""
        assert _normalize_date("not-a-date") is None

    def test_empty_string_rejected(self):
        """str '' → None."""
        assert _normalize_date("") is None

    def test_none_rejected(self):
        """None → None."""
        assert _normalize_date(None) is None

    def test_tz_aware_datetime_rejected(self):
        """timezone-aware datetime → None."""
        import datetime as dt
        from zoneinfo import ZoneInfo
        aware = dt.datetime(2026, 9, 1, tzinfo=ZoneInfo("Europe/Moscow"))
        assert _normalize_date(aware) is None

    def test_bool_rejected(self):
        """True → None (не путать с Excel serial 1)."""
        assert _normalize_date(True) is None


class TestDateEndToEnd:
    """Сквозные тесты даты через XLSX."""

    def test_date_object_accepted(self):
        """datetime.date в ячейке → accepted."""
        wb = make_wb(HEADERS)
        add_row(wb["КИЗ"], [1, "STK", KI_CLEAN, "CHK", 100, "RUB", "FN",
                            datetime.date(2026, 9, 1), "Продажа", "-"])
        result = _save_and_parse(wb)
        assert result.summary.accepted == 1
        assert result.accepted[0].date == "2026-09-01"

    def test_datetime_accepted(self):
        """naive datetime в ячейке → accepted."""
        wb = make_wb(HEADERS)
        add_row(wb["КИЗ"], [1, "STK", KI_CLEAN, "CHK", 100, "RUB", "FN",
                            datetime.datetime(2026, 9, 1, 14, 30), "Продажа", "-"])
        result = _save_and_parse(wb)
        assert result.summary.accepted == 1
        assert result.accepted[0].date == "2026-09-01"

    def test_excel_serial_int_accepted(self):
        """Excel serial int 46239 в ячейке → accepted 2026-07-20."""
        wb = make_wb(HEADERS)
        add_row(wb["КИЗ"], [1, "STK", KI_CLEAN, "CHK", 100, "RUB", "FN",
                            46223, "Продажа", "-"])
        result = _save_and_parse(wb)
        assert result.summary.accepted == 1
        assert result.accepted[0].date == "2026-07-20"

    def test_excel_serial_float_accepted(self):
        """Excel serial float 46223.5 в ячейке → accepted 2026-07-20."""
        wb = make_wb(HEADERS)
        add_row(wb["КИЗ"], [1, "STK", KI_CLEAN, "CHK", 100, "RUB", "FN",
                            46223.5, "Продажа", "-"])
        result = _save_and_parse(wb)
        assert result.summary.accepted == 1
        assert result.accepted[0].date == "2026-07-20"


# ═════════════════════════════════════════════════════════════════════════════
#  8. Валюта
# ═════════════════════════════════════════════════════════════════════════════

class TestCurrencyEndToEnd:
    """Сквозные тесты валюты через XLSX."""

    def test_rub_accepted(self):
        """RUB → accepted."""
        wb = make_wb(HEADERS)
        add_row(wb["КИЗ"], [1, "STK", KI_CLEAN, "CHK", 100, "RUB", "FN",
                            datetime.date(2026, 9, 1), "Продажа", "-"])
        result = _save_and_parse(wb)
        assert result.summary.accepted == 1

    def test_rub_lowercase_accepted(self):
        """'rub' (нижний регистр) → accepted."""
        wb = make_wb(HEADERS)
        add_row(wb["КИЗ"], [1, "STK", KI_CLEAN, "CHK", 100, "rub", "FN",
                            datetime.date(2026, 9, 1), "Продажа", "-"])
        result = _save_and_parse(wb)
        assert result.summary.accepted == 1

    def test_rub_with_spaces_accepted(self):
        """' RUB ' (с пробелами) → accepted."""
        wb = make_wb(HEADERS)
        add_row(wb["КИЗ"], [1, "STK", KI_CLEAN, "CHK", 100, "  RUB  ", "FN",
                            datetime.date(2026, 9, 1), "Продажа", "-"])
        result = _save_and_parse(wb)
        assert result.summary.accepted == 1

    def test_empty_currency_excluded(self):
        """Пустая валюта → missing_currency."""
        wb = make_wb(HEADERS)
        add_row(wb["КИЗ"], [1, "STK", KI_CLEAN, "CHK", 100, "", "FN",
                            datetime.date(2026, 9, 1), "Продажа", "-"])
        result = _save_and_parse(wb)
        assert result.excluded[0].reason_code == "missing_currency"

    def test_none_currency_excluded(self):
        """None (пустая ячейка) → missing_currency."""
        wb = make_wb(HEADERS)
        add_row(wb["КИЗ"], [1, "STK", KI_CLEAN, "CHK", 100, None, "FN",
                            datetime.date(2026, 9, 1), "Продажа", "-"])
        result = _save_and_parse(wb)
        assert result.excluded[0].reason_code == "missing_currency"

    def test_whitespace_currency_excluded(self):
        """Пробельная валюта → missing_currency."""
        wb = make_wb(HEADERS)
        add_row(wb["КИЗ"], [1, "STK", KI_CLEAN, "CHK", 100, "   ", "FN",
                            datetime.date(2026, 9, 1), "Продажа", "-"])
        result = _save_and_parse(wb)
        assert result.excluded[0].reason_code == "missing_currency"

    def test_usd_currency_excluded(self):
        """USD → unsupported_currency."""
        wb = make_wb(HEADERS)
        add_row(wb["КИЗ"], [1, "STK", KI_CLEAN, "CHK", 100, "USD", "FN",
                            datetime.date(2026, 9, 1), "Продажа", "-"])
        result = _save_and_parse(wb)
        assert result.excluded[0].reason_code == "unsupported_currency"

    def test_eur_currency_excluded(self):
        """EUR → unsupported_currency."""
        wb = make_wb(HEADERS)
        add_row(wb["КИЗ"], [1, "STK", KI_CLEAN, "CHK", 100, "EUR", "FN",
                            datetime.date(2026, 9, 1), "Продажа", "-"])
        result = _save_and_parse(wb)
        assert result.excluded[0].reason_code == "unsupported_currency"

    def test_numeric_currency_excluded(self):
        """Число в валюте → unsupported_currency."""
        wb = make_wb(HEADERS)
        add_row(wb["КИЗ"], [1, "STK", KI_CLEAN, "CHK", 100, 123, "FN",
                            datetime.date(2026, 9, 1), "Продажа", "-"])
        result = _save_and_parse(wb)
        assert result.excluded[0].reason_code == "unsupported_currency"

    def test_bool_currency_excluded(self):
        """bool в валюте → missing_currency (bool → '')."""
        wb = make_wb(HEADERS)
        add_row(wb["КИЗ"], [1, "STK", KI_CLEAN, "CHK", 100, True, "FN",
                            datetime.date(2026, 9, 1), "Продажа", "-"])
        result = _save_and_parse(wb)
        assert result.excluded[0].reason_code == "missing_currency"


# ═════════════════════════════════════════════════════════════════════════════
#  9. Дубликаты КИ-31
# ═════════════════════════════════════════════════════════════════════════════

class TestDuplicateKi:
    """Проверка дубликатов КИ-31 внутри файла."""

    def test_two_identical_clean_ki(self):
        """Два одинаковых чистых KI → первый accepted, второй duplicate_in_file."""
        wb = make_wb(HEADERS)
        add_row(wb["КИЗ"], [1, "STK", KI_CLEAN, "CHK", 100, "RUB", "FN",
                            datetime.date(2026, 9, 1), "Продажа", "-"])
        add_row(wb["КИЗ"], [2, "STK", KI_CLEAN, "CHK", 100, "RUB", "FN",
                            datetime.date(2026, 9, 1), "Продажа", "-"])
        result = _save_and_parse(wb)
        assert result.summary.accepted == 1
        assert result.summary.excluded == 1
        assert result.excluded[0].reason_code == "duplicate_in_file"

    def test_clean_and_fffd_tail_duplicate(self):
        """Чистый KI и тот же KI с валидным U+FFFD crypto tail → первый accepted, второй duplicate_in_file."""
        wb = make_wb(HEADERS)
        add_row(wb["КИЗ"], [1, "STK", KI_CLEAN, "CHK", 100, "RUB", "FN",
                            datetime.date(2026, 9, 1), "Продажа", "-"])
        add_row(wb["КИЗ"], [2, "STK", KI_WITH_FFFD, "CHK", 100, "RUB", "FN",
                            datetime.date(2026, 9, 1), "Продажа", "-"])
        result = _save_and_parse(wb)
        assert result.summary.accepted == 1
        assert result.summary.excluded == 1
        assert result.excluded[0].reason_code == "duplicate_in_file"

    def test_excluded_check_does_not_occupy_seen(self):
        """КИ из строки, исключённой из-за empty check, НЕ занимает seen-set."""
        wb = make_wb(HEADERS)
        add_row(wb["КИЗ"], [1, "STK", KI_CLEAN, "", 100, "RUB", "FN",
                            datetime.date(2026, 9, 1), "Продажа", "-"])
        add_row(wb["КИЗ"], [2, "STK", KI_CLEAN, "CHK", 100, "RUB", "FN",
                            datetime.date(2026, 9, 1), "Продажа", "-"])
        result = _save_and_parse(wb)
        assert result.summary.accepted == 1
        assert result.summary.excluded == 1
        assert result.excluded[0].reason_code == "empty_check"
        assert result.accepted[0].ki == KI_CLEAN

    def test_excluded_fn_does_not_occupy_seen(self):
        """КИ из строки, исключённой из-за empty FN, НЕ занимает seen-set."""
        wb = make_wb(HEADERS)
        add_row(wb["КИЗ"], [1, "STK", KI_CLEAN, "CHK", 100, "RUB", "",
                            datetime.date(2026, 9, 1), "Продажа", "-"])
        add_row(wb["КИЗ"], [2, "STK", KI_CLEAN, "CHK", 100, "RUB", "FN",
                            datetime.date(2026, 9, 1), "Продажа", "-"])
        result = _save_and_parse(wb)
        assert result.summary.accepted == 1
        assert result.summary.excluded == 1
        assert result.excluded[0].reason_code == "empty_fn"
        assert result.accepted[0].ki == KI_CLEAN

    def test_excluded_cost_does_not_occupy_seen(self):
        """КИ из строки, исключённой из-за invalid cost, НЕ занимает seen-set."""
        wb = make_wb(HEADERS)
        add_row(wb["КИЗ"], [1, "STK", KI_CLEAN, "CHK", 0, "RUB", "FN",
                            datetime.date(2026, 9, 1), "Продажа", "-"])
        add_row(wb["КИЗ"], [2, "STK", KI_CLEAN, "CHK", 100, "RUB", "FN",
                            datetime.date(2026, 9, 1), "Продажа", "-"])
        result = _save_and_parse(wb)
        assert result.summary.accepted == 1
        assert result.summary.excluded == 1
        assert result.excluded[0].reason_code == "invalid_cost"
        assert result.accepted[0].ki == KI_CLEAN

    def test_excluded_missing_currency_does_not_occupy_seen(self):
        """КИ из строки, исключённой из-за missing currency, НЕ занимает seen-set."""
        wb = make_wb(HEADERS)
        add_row(wb["КИЗ"], [1, "STK", KI_CLEAN, "CHK", 100, "", "FN",
                            datetime.date(2026, 9, 1), "Продажа", "-"])
        add_row(wb["КИЗ"], [2, "STK", KI_CLEAN, "CHK", 100, "RUB", "FN",
                            datetime.date(2026, 9, 1), "Продажа", "-"])
        result = _save_and_parse(wb)
        assert result.summary.accepted == 1
        assert result.summary.excluded == 1
        assert result.excluded[0].reason_code == "missing_currency"
        assert result.accepted[0].ki == KI_CLEAN

    def test_excluded_unsupported_currency_does_not_occupy_seen(self):
        """КИ из строки, исключённой из-за unsupported currency, НЕ занимает seen-set."""
        wb = make_wb(HEADERS)
        add_row(wb["КИЗ"], [1, "STK", KI_CLEAN, "CHK", 100, "USD", "FN",
                            datetime.date(2026, 9, 1), "Продажа", "-"])
        add_row(wb["КИЗ"], [2, "STK", KI_CLEAN, "CHK", 100, "RUB", "FN",
                            datetime.date(2026, 9, 1), "Продажа", "-"])
        result = _save_and_parse(wb)
        assert result.summary.accepted == 1
        assert result.summary.excluded == 1
        assert result.excluded[0].reason_code == "unsupported_currency"
        assert result.accepted[0].ki == KI_CLEAN

    def test_excluded_date_does_not_occupy_seen(self):
        """КИ из строки, исключённой из-за invalid date, НЕ занимает seen-set."""
        wb = make_wb(HEADERS)
        add_row(wb["КИЗ"], [1, "STK", KI_CLEAN, "CHK", 100, "RUB", "FN",
                            "not-a-date", "Продажа", "-"])
        add_row(wb["КИЗ"], [2, "STK", KI_CLEAN, "CHK", 100, "RUB", "FN",
                            datetime.date(2026, 9, 1), "Продажа", "-"])
        result = _save_and_parse(wb)
        assert result.summary.accepted == 1
        assert result.summary.excluded == 1
        assert result.excluded[0].reason_code == "invalid_date"
        assert result.accepted[0].ki == KI_CLEAN


# ═════════════════════════════════════════════════════════════════════════════
#  10. Trim в AcceptedRow (check/fn)
# ═════════════════════════════════════════════════════════════════════════════

class TestAcceptedRowTrim:
    """Проверка trim полей check/fn в AcceptedRow."""

    def test_check_number_trimmed(self):
        """Номер чека с пробелами → trim в AcceptedRow."""
        wb = make_wb(HEADERS)
        add_row(wb["КИЗ"], [1, "STK", KI_CLEAN, "  CHK-001  ", 100, "RUB", "FN",
                            datetime.date(2026, 9, 1), "Продажа", "-"])
        result = _save_and_parse(wb)
        assert result.accepted[0].check_number == "CHK-001"

    def test_fn_number_trimmed(self):
        """Номер ФН с пробелами → trim в AcceptedRow."""
        wb = make_wb(HEADERS)
        add_row(wb["КИЗ"], [1, "STK", KI_CLEAN, "CHK", 100, "RUB", "  FN-123  ",
                            datetime.date(2026, 9, 1), "Продажа", "-"])
        result = _save_and_parse(wb)
        assert result.accepted[0].fn_number == "FN-123"


# ═════════════════════════════════════════════════════════════════════════════
#  11. Интеграционные — synthetic fixture
# ═════════════════════════════════════════════════════════════════════════════

class TestSyntheticFixture:
    """Парсинг полной синтетической фикстуры."""

    def test_parse_synthetic_fixture(self, tmp_path):
        """Полный разбор synthetic_xlsx: total_rows=10, accepted=4, excluded=6, точный by_reason."""
        path = create_synthetic_xlsx(os.path.join(tmp_path, "synth.xlsx"))
        result = parse_xlsx(path)
        assert result.summary.total_rows == 10
        assert result.summary.accepted == 4
        assert result.summary.excluded == 6
        assert result.summary.by_reason == {
            "return_operation": 1,
            "dash_operation": 1,
            "empty_check": 1,
            "empty_fn": 1,
            "invalid_kiz": 1,
            "duplicate_in_file": 1,
        }

    def test_bytesio_input(self):
        """Передача BytesIO вместо пути → корректный разбор."""
        path = create_synthetic_xlsx()
        with open(path, "rb") as f:
            data = f.read()
        os.unlink(path)
        buf = io.BytesIO(data)
        result = parse_xlsx(buf)
        assert result.summary.total_rows == 10
        assert result.summary.accepted == 4

    def test_deterministic_result(self, tmp_path):
        """Два одинаковых файла → идентичный результат."""
        p1 = create_synthetic_xlsx(os.path.join(tmp_path, "a.xlsx"))
        p2 = create_synthetic_xlsx(os.path.join(tmp_path, "b.xlsx"))
        r1 = parse_xlsx(p1)
        r2 = parse_xlsx(p2)
        assert r1.summary == r2.summary
        assert r1.accepted == r2.accepted
        assert r1.excluded == r2.excluded


# ═════════════════════════════════════════════════════════════════════════════
#  12. Пустые строки
# ═════════════════════════════════════════════════════════════════════════════

class TestEmptyRows:
    """Пустые строки не считаются data rows."""

    def test_empty_rows_not_counted(self):
        """Полностью пустые строки (None, '', bool) → не влияют на total_rows."""
        wb = make_wb(HEADERS)
        ws = wb["КИЗ"]
        # Одна валидная строка
        add_row(ws, [1, "STK", KI_CLEAN, "CHK", 100, "RUB", "FN",
                     datetime.date(2026, 9, 1), "Продажа", "-"])
        # Три пустых строки (разные варианты "пустоты")
        add_row(ws, [None, None, None, None, None, None, None, None, None, None])
        add_row(ws, ["", "", "", "", "", "", "", "", "", ""])
        add_row(ws, [True, None, "", None, True, None, None, None, None, None])
        result = _save_and_parse(wb)
        assert result.summary.total_rows == 1
        assert result.summary.accepted == 1

    def test_partial_none_is_data(self):
        """Строка с частичными None (но не всеми) → считается data row."""
        wb = make_wb(HEADERS)
        ws = wb["КИЗ"]
        # Только КИЗ и операция заполнены, остальное None — это data row, будет excluded
        add_row(ws, [None, None, KI_CLEAN, None, None, None, None, None, "Продажа", None])
        result = _save_and_parse(wb)
        assert result.summary.total_rows == 1
        assert result.summary.excluded == 1


# ═════════════════════════════════════════════════════════════════════════════
#  13. Безопасность repr/str
# ═════════════════════════════════════════════════════════════════════════════

class TestReprSafety:
    """repr/str DTO и ошибок не содержат криптохвост или полный КМ."""

    KIZ_WITH_TAIL = KI_CLEAN + FFFD_CHAR + "91abcd" + FFFD_CHAR + "92" + "B" * 10

    def _make_accepted(self) -> AcceptedRow:
        return AcceptedRow(
            row_index=1,
            ki=KI_CLEAN,
            check_number="CHK",
            fn_number="FN",
            cost_kopecks=10000,
            date="2026-09-01",
        )

    def _make_excluded(self) -> ExcludedRow:
        return ExcludedRow(
            row_index=2,
            reason_code="invalid_kiz",
            message="КИЗ не удалось преобразовать",
        )

    def test_accepted_repr_has_only_ki31(self):
        """repr(AcceptedRow) содержит только KI-31, не криптохвост."""
        r = repr(self._make_accepted())
        assert KI_CLEAN in r
        assert FFFD_CHAR not in r
        assert "91abcd" not in r

    def test_excluded_repr_no_km(self):
        """repr(ExcludedRow) не содержит КМ или криптохвост."""
        r = repr(self._make_excluded())
        assert FFFD_CHAR not in r
        assert "91abcd" not in r
        assert "010123456789" not in r

    def test_file_import_error_str_no_raw(self):
        """str(FileImportError) не содержит произвольных значений ячеек."""
        err = FileImportError("Не удалось открыть файл: повреждённый XLSX")
        s = str(err)
        assert FFFD_CHAR not in s
        assert "91abcd" not in s
        assert s == "Не удалось открыть файл: повреждённый XLSX"

    def test_invalid_headers_error_str_no_raw(self):
        """str(InvalidHeadersError) не содержит криптохвост."""
        err = InvalidHeadersError("колонка 3: ожидалось «КИЗ», получено «другое»")
        s = str(err)
        assert FFFD_CHAR not in s
        assert "91abcd" not in s


# ═════════════════════════════════════════════════════════════════════════════
#  14. DTO не содержит raw/source KIZ
# ═════════════════════════════════════════════════════════════════════════════

class TestDtoNoRawKiz:
    """DTO не содержат полей с исходным КМ/сырым КИЗ."""

    def test_accepted_row_no_raw_kiz(self):
        """AcceptedRow не имеет поля raw_kiz/source_kiz."""
        assert not hasattr(AcceptedRow, "raw_kiz")
        assert not hasattr(AcceptedRow, "source_kiz")
        assert not hasattr(AcceptedRow, "raw_ki")
        assert not hasattr(AcceptedRow, "full_km")

    def test_excluded_row_no_raw_kiz(self):
        """ExcludedRow не имеет поля raw_kiz/source_kiz."""
        assert not hasattr(ExcludedRow, "raw_kiz")
        assert not hasattr(ExcludedRow, "source_kiz")
        assert not hasattr(ExcludedRow, "raw_ki")
        assert not hasattr(ExcludedRow, "full_km")

    def test_accepted_row_fields(self):
        """AcceptedRow имеет только ожидаемые поля: row_index, ki, check_number, fn_number, cost_kopecks, date."""
        fields = {f.name for f in AcceptedRow.__dataclass_fields__.values()}
        assert fields == {"row_index", "ki", "check_number", "fn_number", "cost_kopecks", "date"}

    def test_excluded_row_fields(self):
        """ExcludedRow имеет только ожидаемые поля: row_index, reason_code, message."""
        fields = {f.name for f in ExcludedRow.__dataclass_fields__.values()}
        assert fields == {"row_index", "reason_code", "message"}