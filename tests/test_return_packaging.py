from datetime import date

import pytest

from app.chestny.services.return_packaging import build_return_document

KI = '010123456789012321SERIAL1234567'
INN = '123456789012'


def test_unpaid_has_no_primary_fields():
    body = build_return_document(INN, [dict(ki=KI, paid=False,
        primary_document_type='OTHER', primary_document_number='stale')])
    assert body == dict(trade_participant_inn=INN, return_type='REMOTE_SALE_RETURN',
                        products_list=[dict(ki=KI, paid=False)])


@pytest.mark.parametrize('paid', [None, '', 0, 1, 'false', 'true'])
def test_payment_must_be_explicit_boolean(paid):
    with pytest.raises(ValueError):
        build_return_document(INN, [dict(ki=KI, paid=paid)])


def test_paid_document_and_custom_name():
    item = dict(ki=KI, paid=True, primary_document_type='OTHER',
                primary_document_number='return-1', primary_document_date=date.today().isoformat())
    with pytest.raises(ValueError):
        build_return_document(INN, [item])
    item['primary_document_custom_name'] = 'Документ возврата'
    assert build_return_document(INN, [item])['products_list'][0] == item


def test_no_duplicate_codes():
    with pytest.raises(ValueError):
        build_return_document(INN, [dict(ki=KI, paid=False)] * 2)
