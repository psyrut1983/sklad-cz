"""Read WB events with per-row payment classification or persisting raw marking codes."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import hashlib
import hmac
import json
from typing import BinaryIO

import openpyxl

from app.services.excel_import import FileImportError, HEADERS, _normalize_date, _normalize_cost
from app.services.kiz_codec import extract_ki_or_none


@dataclass(frozen=True, repr=False)
class WbEvent:
    row_index: int
    ki: str
    operation: str
    assignment: str
    sticker: str
    receipt: str
    fiscal_drive: str
    date: str | None
    raw_date: str
    legal_entity: str
    paid: bool | None = None
    cost_kopecks: int | None = None
    currency: str = ''

    def __repr__(self):
        return f"WbEvent(row_index={self.row_index}, operation={self.operation!r})"

    def fingerprint(self, key: bytes) -> str:
        # Receipt/date can be filled in a later export. Stable assignment and
        # sticker identify the event across overlapping exports when available.
        identity = [self.ki, self.operation, self.assignment, self.sticker]
        if not self.assignment and not self.sticker:
            identity.extend([self.raw_date, self.receipt, self.fiscal_drive])
        return hmac.new(key, json.dumps(identity, ensure_ascii=False).encode(),
                        hashlib.sha256).hexdigest()

    @property
    def has_order_identity(self) -> bool:
        return bool(self.assignment or self.sticker)


@dataclass
class WbEvents:
    events: list[WbEvent] = field(default_factory=list)
    excluded: list[dict] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)


def _normalize_currency(value: str) -> str:
    """Return the ISO code used by CRPT for WB's supported ruble labels."""
    normalized = value.upper().replace('.', '').strip()
    return 'RUB' if normalized in {'RUB', 'RUR', 'РУБ', '₽'} else normalized


def parse_wb_events(source: str | BinaryIO) -> WbEvents:
    workbook = None
    try:
        workbook = openpyxl.load_workbook(source, read_only=True, data_only=True)
        if "КИЗ" not in workbook.sheetnames:
            raise FileImportError('В файле отсутствует лист «КИЗ»')
        sheet = workbook['КИЗ']
        sheet.reset_dimensions()  # WB may declare A1 while storing many rows.
        rows = sheet.iter_rows(values_only=True)
        headers = [str(v or '').strip() for v in next(rows, ())]
        if headers != HEADERS:
            raise FileImportError('Не совпадают заголовки листа «КИЗ»')
        result = WbEvents()
        counts = Counter()
        for index, values in enumerate(rows, start=1):
            cells = [str(v).strip() if v is not None else '' for v in values]
            if not any(cells):
                continue
            cells += [''] * max(0, len(HEADERS) - len(cells))
            operation = cells[8]
            counts[operation] += 1
            if operation not in ('Продажа', 'Возврат'):
                result.excluded.append(dict(row_index=index, reason='unknown_operation'))
                continue
            ki = extract_ki_or_none(cells[2])
            if not ki:
                result.excluded.append(dict(row_index=index, reason='invalid_ki'))
                continue
            result.events.append(WbEvent(
                index, ki, operation, cells[0], cells[1], cells[3], cells[6],
                _normalize_date(values[7]) if len(values) > 7 and values[7] else None,
                cells[7], cells[9], cost_kopecks=(int(_normalize_cost(values[4]))
                    if len(values) > 4 and _normalize_cost(values[4]) is not None else None),
                currency=_normalize_currency(cells[5]),
                paid=(bool(cells[3]) if operation == 'Возврат' and bool(cells[3]) == bool(cells[6]) else None),
            ))
        result.counts = dict(counts)
        return result
    except FileImportError:
        raise
    except Exception:
        raise FileImportError('Не удалось прочитать XLSX Wildberries') from None
    finally:
        if workbook is not None:
            workbook.close()
