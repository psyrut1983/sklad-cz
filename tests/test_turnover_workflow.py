import base64
from datetime import date
import io
import json
from uuid import uuid4

import openpyxl
import pytest

from app.chestny.factory import create_cz_app, db
from app.chestny.models import OrganizationProfile, TurnoverDocument, TurnoverEvent
from app.chestny.services.turnover_client import eligibility, TurnoverClient
from app.services.excel_import import HEADERS

KI = '010123456789012321SERIAL1234567'


class Signer:
    def sign(self, data, thumbprint):
        return 'signature'


class FakeClient:
    def __init__(self):
        self.status = 'RETIRED'
        self.sent = {}
        self.fail = False
        self.doc_status = 'CHECKED_OK'

    def codes(self, codes):
        return {code: dict(ownerInn='123456789012', productGroup='lp',
            packageType='UNIT', statusEx='EMPTY', status=self.status,
            withdrawReason='DISTANCE') for code in codes}

    def create(self, envelope):
        if self.fail:
            raise TimeoutError()
        docid = str(uuid4())
        self.sent[docid] = (json.loads(base64.b64decode(envelope['product_document'])), envelope['type'])
        return docid

    def document(self, docid, expected_type, inn):
        body, kind = self.sent[docid]
        return dict(number=docid, type=kind, senderInn=inn, body=body, status=self.doc_status)


@pytest.fixture
def setup(tmp_path):
    app = create_cz_app(instance_path=str(tmp_path), testing=True)
    fake = FakeClient()
    app.extensions['cz_signer'] = Signer()
    app.extensions['turnover_client_factory'] = lambda p: fake
    with app.app_context():
        profile = db.session.get(OrganizationProfile, 'org-sinyavin')
        profile.inn = '123456789012'; profile.certificate_thumbprint = 'A' * 40
        profile.fias_id = '12345678-1234-1234-1234-123456789012'
        db.session.commit()
    return app, app.test_client(), fake


def upload(client, operation='Возврат', order='order1'):
    book = openpyxl.Workbook(); book.active.title = 'КИЗ'; book.active.append(HEADERS)
    book.active.append([order, order, KI, '', 100, 'RUB', '', date.today().isoformat(), operation, 'Нет'])
    stream = io.BytesIO(); book.save(stream); book.close(); stream.seek(0)
    result = client.post('/api/turnover/import', data=dict(profile_id='org-sinyavin', file=(stream, 'wb.xlsx')))
    assert result.status_code == 200, result.get_json()
    return result.get_json()['token']


def payload():
    return dict(selected_rows=[1], confirmed=True, defaults=dict(paid=False,
        event_confirmed=True, primary_document_type='OTHER', primary_document_number='WB-1',
        primary_document_date=date.today().isoformat(), primary_document_custom_name='Отчёт WB'))


def send(client, token):
    result = client.post(f'/api/turnover/import/{token}/submit', json=payload())
    assert result.status_code == 201, result.get_json()
    return result.get_json()['documents'][0]


def reconcile(client, doc):
    result = client.post('/api/turnover/documents/' + doc['id'] + '/reconcile', json={})
    assert result.status_code == 200, result.get_json()
    return result.get_json()['document']


def test_return_without_history_then_sale_return(setup):
    app, client, fake = setup
    first = send(client, upload(client))
    assert first['state'] == 'SENT'
    assert first['items'][0]['source'] == 'CRPT'
    # Document success alone is not enough while code remains retired.
    assert reconcile(client, first)['state'] == 'VERIFYING'
    fake.status = 'INTRODUCED'
    assert reconcile(client, first)['state'] == 'CONFIRMED'
    sale = send(client, upload(client, 'Продажа', 'order2'))
    fake.status = 'RETIRED'
    assert reconcile(client, sale)['state'] == 'CONFIRMED'
    returned = send(client, upload(client, 'Возврат', 'order2'))
    fake.status = 'INTRODUCED'
    assert reconcile(client, returned)['state'] == 'CONFIRMED'
    assert returned['items'][0]['cycle'] == 2
    with app.app_context():
        assert TurnoverEvent.query.count() == 3


def test_sale_automatically_fills_document_fields(setup):
    _, client, fake = setup
    fake.status = 'INTRODUCED'
    token = upload(client, 'Продажа')
    result = client.post(f'/api/turnover/import/{token}/submit', json=dict(
        selected_rows=[1], confirmed=True, defaults=dict(event_confirmed=True)))
    assert result.status_code == 201, result.get_json()
    body, kind = next(iter(fake.sent.values()))
    assert kind == 'LK_RECEIPT'
    assert body['action'] == 'DISTANCE'
    assert body['document_number'] == 'WB-' + date.today().strftime('%Y%m%d')
    assert body['document_date'] == date.today().isoformat()
    assert body['products'][0]['product_cost'] == 10000


def test_old_report_does_not_return_new_sale(setup):
    _, client, fake = setup
    doc = send(client, upload(client)); fake.status = 'INTRODUCED'; reconcile(client, doc)
    sale = send(client, upload(client, 'Продажа', 'order2')); fake.status = 'RETIRED'; reconcile(client, sale)
    old = upload(client)
    response = client.post(f'/api/turnover/import/{old}/submit', json=payload())
    assert response.status_code == 400
    assert len(fake.sent) == 2


def test_unknown_keeps_durable_reservation_after_restart(setup, tmp_path):
    app, client, fake = setup; fake.fail = True
    doc = send(client, upload(client))
    assert doc['state'] == 'UNKNOWN'
    with app.app_context():
        assert TurnoverEvent.query.first().active_key
    another = create_cz_app(instance_path=str(tmp_path), testing=True)
    another.extensions['turnover_client_factory'] = lambda p: fake
    second = another.test_client()
    token = upload(second)
    response = second.post(f'/api/turnover/import/{token}/submit', json=payload())
    assert response.status_code == 400


def test_payment_unknown_cannot_sign(setup):
    app, client, fake = setup
    from dataclasses import replace
    token = upload(client); data = payload(); data['defaults']['paid'] = False
    events = app.extensions['turnover_imports'][token]['events']
    events[0] = replace(events[0], receipt='123')
    assert client.post(f'/api/turnover/import/{token}/submit', json=data).status_code == 400
    assert not fake.sent


def test_document_error_is_not_confirmation(setup):
    app, client, fake = setup
    doc = send(client, upload(client)); fake.status = 'INTRODUCED'; fake.doc_status = 'CHECKED_NOT_OK'
    assert reconcile(client, doc)['state'] == 'UNKNOWN'
    with app.app_context():
        assert TurnoverEvent.query.first().active_key


def test_already_introduced_is_not_sent(setup):
    _, client, fake = setup; fake.status = 'INTRODUCED'
    token = upload(client)
    response = client.post(f'/api/turnover/import/{token}/submit', json=payload())
    assert response.status_code == 409
    assert not fake.sent


def test_code_rules_fail_closed():
    info = FakeClient().codes([KI])[KI]
    assert eligibility(info, '123456789012', 'Возврат')[0] == 'READY'
    for change in [dict(ownerInn='other'), dict(statusEx='BLOCKED'), dict(productGroup='shoes'),
                   dict(withdrawReason=None), dict(status='EMITTED')]:
        assert eligibility(dict(info, **change), '123456789012', 'Возврат')[0] != 'READY'


def test_exact_document_matching():
    class Auth:
        class credentials:
            api_base_url = 'https://markirovka.crpt.ru/api/v3/true-api'
        def get_token(self): return 'token'
    class Transport:
        def get_json(self, url, **kwargs):
            assert '/api/v4/' in url
            return [dict(number='wrong', type='LP_RETURN', senderInn='123456789012')]
    with pytest.raises(ValueError):
        TurnoverClient(Auth(), Transport()).document('wanted', 'LP_RETURN', '123456789012')


def test_batch_limit_and_untransmitted_cancellation(setup):
    app, client, fake = setup
    from app.services.wb_events import WbEvent
    token = upload(client)
    original = app.extensions['turnover_imports'][token]['events'][0]
    from dataclasses import replace
    app.extensions['turnover_imports'][token]['events'] = [original, replace(original,
        row_index=2, ki='014612345678901221ABC123XYZ7890', assignment='second')]
    app.config['TURNOVER_BATCH_SIZE'] = 1
    data = payload(); data['selected_rows'] = [1, 2]
    preview = client.post(f'/api/turnover/import/{token}/prepare', json=data)
    assert [d['count'] for d in preview.get_json()['documents']] == [1, 1]
    fake.fail = True
    response = client.post(f'/api/turnover/import/{token}/submit', json=data)
    docs = response.get_json()['documents']
    assert [d['state'] for d in docs] == ['UNKNOWN', 'PREPARED']
    assert client.post('/api/turnover/documents/' + docs[0]['id'] + '/cancel-unsent', json={}).status_code == 400
    assert client.post('/api/turnover/documents/' + docs[1]['id'] + '/cancel-unsent', json={}).status_code == 200
    with app.app_context():
        items = TurnoverEvent.query.order_by(TurnoverEvent.id).all()
        assert items[0].active_key and items[1].active_key is None


def test_paid_and_unpaid_can_share_document(setup):
    app, client, fake = setup
    from dataclasses import replace
    token = upload(client)
    event = app.extensions['turnover_imports'][token]['events'][0]
    app.extensions['turnover_imports'][token]['events'].append(replace(event,
        row_index=2, ki='014612345678901221ABC123XYZ7890', assignment='second', receipt='123', fiscal_drive='999', paid=True))
    data = payload(); data['selected_rows'] = [1, 2]
    data['items'] = {'2': {'paid': False}}  # Cannot override the source row classification.
    response = client.post(f'/api/turnover/import/{token}/submit', json=data)
    assert response.status_code == 201
    body, _ = next(iter(fake.sent.values()))
    assert body['products_list'][0] == dict(ki=KI, paid=False)
    assert body['products_list'][1]['paid'] is True
    assert body['products_list'][1]['primary_document_number'] == '123'
    assert body['products_list'][1]['primary_document_type'] == 'RECEIPT'


def test_recovery_and_report(setup):
    app, client, fake = setup
    doc = send(client, upload(client)); fake.status = 'INTRODUCED'
    from app.chestny.services.turnover_recovery import reconcile_pending
    reconcile_pending(app)
    with app.app_context():
        assert db.session.get(TurnoverDocument, doc['id']).state == 'CONFIRMED'
    response = client.get('/api/turnover/documents/' + doc['id'] + '/report')
    assert response.status_code == 200
    book = openpyxl.load_workbook(io.BytesIO(response.data))
    assert KI not in str(list(book.active.values))
    assert book.active['F2'].value == 'CONFIRMED'
    book.close()


def test_check_records_external_observation(setup):
    app, client, fake = setup; fake.status = 'INTRODUCED'
    token = upload(client)
    response = client.post(f'/api/turnover/import/{token}/check', json={'selected_rows':[1]})
    assert response.get_json()['rows'][0]['state'] == 'ALREADY'
    from app.chestny.models import TurnoverObservation
    with app.app_context():
        assert TurnoverObservation.query.first().state == 'INTRODUCED'
        assert TurnoverEvent.query.count() == 0


def test_cross_site_submission_rejected(setup):
    _, client, _ = setup
    assert client.post('/api/turnover/import', headers={'Origin':'https://example.org'}).status_code == 403


def test_ui_renders_without_real_data(setup):
    _, client, _ = setup
    response = client.get('/turnover')
    assert response.status_code == 200
    assert 'turnover.js' in response.text
    assert 'Подписать и отправить' in response.text
