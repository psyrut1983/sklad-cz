"""Version-specific CRPT reads and one-shot document creation."""
from __future__ import annotations

from urllib.parse import quote
from uuid import UUID

from app.chestny.services.cz_auth import UnauthorizedError


class TurnoverClient:
    def __init__(self, auth, transport):
        self.auth, self.transport = auth, transport
        self.base = auth.credentials.api_base_url

    def _read(self, method, path, payload=None, v4=False):
        base = self.base.replace('/api/v3/', '/api/v4/') if v4 else self.base
        for attempt in range(2):
            headers = {'Authorization': 'Bearer ' + self.auth.get_token(),
                       'Accept': 'application/json'}
            try:
                if method == 'POST':
                    return self.transport.post_json(base + path, payload, headers=headers, timeout=30)
                return self.transport.get_json(base + path, headers=headers, timeout=30)
            except UnauthorizedError:
                if attempt:
                    raise
                self.auth.reset_token()

    def codes(self, codes):
        result = {}
        wanted = list(dict.fromkeys(codes))
        for offset in range(0, len(wanted), 500):
            chunk = wanted[offset:offset + 500]
            response = self._read('POST', '/cises/info?pg=lp', chunk)
            if isinstance(response, dict) and 'cisInfo' in response:
                response = [response]
            if not isinstance(response, list):
                raise ValueError('Неожиданный ответ проверки кодов ЧЗ')
            for entry in response:
                if not isinstance(entry, dict):
                    continue
                info = entry.get('cisInfo')
                if not isinstance(info, dict):
                    continue
                code = info.get('requestedCis') or info.get('cis')
                if code not in chunk:
                    continue
                if code in result or entry.get('errorCode') not in (None, '', 0, '0'):
                    result[code] = {}  # duplicates/errors cannot authorize an action
                else:
                    result[code] = info
        return result

    def document(self, document_id, expected_type, inn):
        response = self._read('GET', '/doc/' + quote(document_id, safe='') +
                              '/info?pg=lp&body=true', v4=True)
        entries = response if isinstance(response, list) else [response]
        matches = [r for r in entries if isinstance(r, dict) and r.get('number') == document_id]
        if len(matches) != 1:
            raise ValueError('Документ с точным ID не найден')
        doc = matches[0]
        if doc.get('senderInn') != inn or doc.get('type') != expected_type:
            raise ValueError('Документ не соответствует профилю или операции')
        return doc

    def create(self, envelope):
        # Do not retry a mutating request, including after an ambiguous timeout.
        response = self.transport.post_json(self.base + '/lk/documents/create?pg=lp',
                    envelope, headers={'Authorization': 'Bearer ' + self.auth.get_token(),
                                       'Accept': 'application/json'}, timeout=60)
        if isinstance(response, dict):
            response = response.get('documentId') or response.get('id') or response.get('value')
        try:
            return str(UUID(response))
        except (ValueError, TypeError, AttributeError):
            raise ValueError('ЧЗ не вернул однозначный ID документа; требуется сверка') from None


def eligibility(info, inn, operation):
    if not info:
        return 'CHECK_REQUIRED', 'ЧЗ не предоставил сведения о коде'
    if info.get('ownerInn') != inn:
        return 'BLOCKED', 'Владелец кода не подтверждён для выбранного профиля'
    if info.get('productGroup') != 'lp' or info.get('packageType') != 'UNIT':
        return 'BLOCKED', 'Требуется индивидуальный код лёгкой промышленности'
    if info.get('statusEx') not in (None, '', 'EMPTY'):
        return 'BLOCKED', 'У кода есть особое состояние'
    status = info.get('status')
    if operation == 'Возврат':
        if status == 'INTRODUCED':
            return 'ALREADY', 'Уже в обороте — отправка не требуется'
        if status != 'RETIRED':
            return 'BLOCKED', 'Код не находится в статусе «Выбыл»'
        if info.get('withdrawReason') != 'DISTANCE':
            return 'CHECK_REQUIRED', 'Причина дистанционного выбытия не подтверждена'
    elif status != 'INTRODUCED':
        return 'BLOCKED', 'Для продажи код должен находиться в обороте'
    return 'READY', 'Состояние ЧЗ допускает операцию'
