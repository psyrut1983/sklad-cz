import io
import zipfile

import openpyxl
import pytest

from app.services.excel_import import HEADERS, FileImportError
from app.services.wb_events import parse_wb_events

KI = '010123456789012321SERIAL1234567'


def workbook(rows, wrong_dimensions=False):
    book = openpyxl.Workbook()
    book.active.title = 'КИЗ'
    book.active.append(HEADERS)
    for row in rows:
        book.active.append(row)
    stream = io.BytesIO()
    book.save(stream)
    book.close()
    if wrong_dimensions:
        import re
        out = io.BytesIO()
        with zipfile.ZipFile(stream) as source, zipfile.ZipFile(out, 'w') as target:
            for name in source.namelist():
                data = source.read(name)
                if name == 'xl/worksheets/sheet1.xml':
                    data = re.sub(rb'<dimension ref="[^"]+"', b'<dimension ref="A1"', data)
                target.writestr(name, data)
        stream = out
    stream.seek(0)
    return stream


def row(op='Возврат', order='order1', receipt='', date=''):
    return [order, 'sticker', KI, receipt, 100, 'RUB', '', date, op, 'Нет']


@pytest.mark.parametrize('count', [0, 1, 73, 1100])
def test_arbitrary_size_and_bad_dimensions(count):
    result = parse_wb_events(workbook([row(order=str(i)) for i in range(count)], True))
    assert len(result.events) == count


def test_sales_returns_unknown_and_payment():
    result = parse_wb_events(workbook([row('Продажа'), row(), row('-'), row(receipt='receipt')]))
    assert result.counts == {'Продажа': 1, 'Возврат': 2, '-': 1}
    assert len(result.events) == 3
    assert [event.paid for event in result.events] == [None, False, None]
    assert len(result.excluded) == 1
    assert KI not in repr(result.events)


def test_overlap_and_next_order():
    events = parse_wb_events(workbook([
        row(), row(receipt='filled-later'), row(order='order2'), row('Продажа')
    ])).events
    fingerprints = [event.fingerprint(b'key') for event in events]
    assert fingerprints[0] == fingerprints[1]
    assert len(set(fingerprints)) == 3


def test_invalid_file_is_safe():
    with pytest.raises(FileImportError):
        parse_wb_events(io.BytesIO(b'not xlsx'))


def test_serial_ending_in_quote_survives_import_and_masking():
    from app.chestny.services.dedup import mask_ki, hmac_digest
    from app.services.kiz_codec import extract_ki
    code = KI[:-1] + '"'
    values = row()
    values[2] = code + '\ufffd91abcd\ufffd92' + 'B' * 44
    event = parse_wb_events(workbook([values])).events[0]
    assert event.ki == code
    assert extract_ki(code).ki == code
    assert extract_ki('"' + code + '"').ki == code
    assert mask_ki(code).endswith('"')
    assert len(hmac_digest(code, b'test-key')) == 64


@pytest.mark.parametrize('receipt,fn,expected', [('', '', False), ('123', '999', True), ('123', '', None), ('', '999', None)])
def test_payment_from_receipt_and_fiscal_drive(receipt, fn, expected):
    values = row(receipt=receipt)
    values[6] = fn
    assert parse_wb_events(workbook([values])).events[0].paid is expected


@pytest.mark.parametrize('source', ['RUB', 'RUR', 'руб.', '₽'])
def test_ruble_currency_labels_are_normalized(source):
    values = row(op='Продажа')
    values[5] = source
    event = parse_wb_events(workbook([values])).events[0]
    assert event.cost_kopecks == 10000
    assert event.currency == 'RUB'
