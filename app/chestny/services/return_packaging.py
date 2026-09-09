"""Pure LP_RETURN builder. No network, signing, or inference of payment."""
from __future__ import annotations

from datetime import date
from typing import Any

from app.services.kiz_codec import extract_ki


def build_return_document(inn: str, products: list[dict[str, Any]]) -> dict:
    if not isinstance(inn, str) or not inn.isdigit() or len(inn) not in (10, 12):
        raise ValueError('Некорректный ИНН')
    if not products:
        raise ValueError('Нет кодов для возврата')
    result = []
    seen = set()
    for product in products:
        ki = extract_ki(product.get('ki')).ki
        if ki in seen:
            raise ValueError('Повтор кода внутри документа')
        seen.add(ki)
        paid = product.get('paid')
        if type(paid) is not bool:
            raise ValueError('Подтвердите, был ли товар оплачен')
        item = {'ki': ki, 'paid': paid}
        if paid:
            kind = product.get('primary_document_type')
            if kind not in ('RECEIPT', 'SALES_RECEIPT', 'OTHER'):
                raise ValueError('Укажите вид первичного документа')
            number = product.get('primary_document_number')
            if not isinstance(number, str) or not 1 <= len(number.strip()) <= 255:
                raise ValueError('Укажите номер первичного документа')
            try:
                doc_date = date.fromisoformat(product.get('primary_document_date', ''))
            except (ValueError, TypeError):
                raise ValueError('Укажите дату первичного документа') from None
            today = date.today()
            try:
                minimum = today.replace(year=today.year - 5)
            except ValueError:
                minimum = today.replace(year=today.year - 5, day=28)
            if not minimum <= doc_date <= today:
                raise ValueError('Дата документа должна быть за последние пять лет и не в будущем')
            item.update(primary_document_type=kind,
                        primary_document_number=number.strip(),
                        primary_document_date=doc_date.isoformat())
            if kind == 'OTHER':
                name = product.get('primary_document_custom_name')
                if not isinstance(name, str) or not 1 <= len(name.strip()) <= 255:
                    raise ValueError('Укажите наименование первичного документа')
                item['primary_document_custom_name'] = name.strip()
        # Unpaid returns must not carry primary document fields. Explicit
        # whitelisting also prevents stale UI values entering the signed body.
        result.append(item)
    return {'trade_participant_inn': inn, 'return_type': 'REMOTE_SALE_RETURN',
            'products_list': result}
