"""Reviewed WB sale/return workflow with durable reservations and reconciliation."""
from __future__ import annotations

import base64
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
import hashlib
import hmac
import io
import json
import time
from uuid import uuid4

from flask import Blueprint, current_app, jsonify, request, render_template, send_file
from sqlalchemy.exc import IntegrityError

from app.chestny.factory import db
from app.chestny.models import OrganizationProfile, ProcessedKiz, TurnoverDocument, TurnoverEvent, TurnoverObservation
from app.chestny.services.cz_auth import CredentialsSnapshot, PRODUCTION_API_BASE_URL, SANDBOX_API_BASE_URL
from app.chestny.services.dedup import load_or_create_hmac_key, hmac_digest, mask_ki
from app.chestny.services.return_packaging import build_return_document
from app.chestny.services.turnover_client import TurnoverClient, eligibility
from app.services.wb_events import parse_wb_events

turnover = Blueprint('turnover', __name__)
ACTIVE = ('SENDING', 'SENT', 'UNKNOWN', 'VERIFYING')


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def body_digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


@turnover.before_request
def same_origin():
    if request.method in ('POST', 'DELETE'):
        origin = request.headers.get('Origin')
        if origin and origin.rstrip('/') != request.host_url.rstrip('/'):
            return jsonify(message='Запрос с другого сайта запрещён'), 403


def key():
    return load_or_create_hmac_key(current_app.instance_path)


def api_base():
    environment = current_app.config.get('TURNOVER_ENVIRONMENT', 'production')
    if environment not in ('production', 'sandbox'):
        raise ValueError('Неизвестный контур ЧЗ; выберите production или sandbox')
    return SANDBOX_API_BASE_URL if environment == 'sandbox' else PRODUCTION_API_BASE_URL


def profile_for(pid):
    profile = db.session.get(OrganizationProfile, pid)
    if profile is None or not profile.inn or not profile.certificate_thumbprint:
        raise ValueError('Выберите настроенный профиль с ИНН и сертификатом')
    return profile


def client_for(profile, environment=None):
    injected = current_app.extensions.get('turnover_client_factory')
    if injected:
        return injected(profile)
    auth = current_app.extensions['auth_registry'].get_or_create(
        CredentialsSnapshot(profile.id, profile.inn, profile.certificate_thumbprint,
                            environment or api_base(), allow_sandbox=True),
        transport=current_app.extensions['cz_auth_transport'],
        signer=current_app.extensions['cz_signer'])
    return TurnoverClient(auth, current_app.extensions['cz_auth_transport'])


def session_for(token):
    store = current_app.extensions['turnover_imports']
    session = store.get(token)
    if not session or session['expires'] < time.time():
        store.pop(token, None)
        raise ValueError('Импорт истёк. Загрузите исходный файл повторно')
    profile = profile_for(session['profile'])
    if profile.inn != session['inn'] or session['environment'] != api_base():
        raise ValueError('ИНН профиля изменился. Загрузите файл заново')
    return session, profile


@turnover.errorhandler(ValueError)
def invalid(error):
    return jsonify(message=str(error)), 400


def selection(session, data):
    indexes = data.get('selected_rows')
    if not isinstance(indexes, list) or not indexes or any(type(i) is not int for i in indexes):
        raise ValueError('Выберите строки для обработки')
    if len(set(indexes)) != len(indexes):
        raise ValueError('Повтор строки в выборе')
    mapping = {e.row_index: e for e in session['events']}
    if not set(indexes) <= mapping.keys():
        raise ValueError('Строка отсутствует в импорте')
    events = [mapping[i] for i in indexes]
    if len({e.operation for e in events}) != 1:
        raise ValueError('Выберите отдельно продажи или возвраты')
    if len({e.ki for e in events}) != len(events):
        raise ValueError('Выберите только одно событие каждого КИ; сначала проверьте порядок событий')
    return events


def local_status(event, profile):
    digest = hmac_digest(event.ki, key())
    past = TurnoverEvent.query.filter_by(profile_id=profile.id,
        environment=api_base(), code_digest=digest).order_by(TurnoverEvent.id.desc()).first()
    same = TurnoverEvent.query.filter_by(profile_id=profile.id,
        environment=api_base(), event_digest=event.fingerprint(key())).first()
    busy = TurnoverEvent.query.filter_by(profile_id=profile.id,
        environment=api_base(), code_digest=digest).filter(
            TurnoverEvent.active_key.isnot(None)).first()
    legacy = ProcessedKiz.query.filter_by(profile_id=profile.id, hmac_digest=digest).first()
    return past, same, busy, legacy


def inspect_events(events, profile, infos):
    output = []
    for event in events:
        past, same, busy, legacy = local_status(event, profile)
        state, message = eligibility(infos.get(event.ki), profile.inn, event.operation)
        if event.legal_entity.lower() not in ('', 'нет', 'false', '0'):
            state, message = 'BLOCKED', 'Продажа юрлицу требует отдельного сценария проверки'
        if same:
            state, message = 'DUPLICATE', 'Событие уже зарегистрировано: ' + same.state
        elif busy:
            state, message = 'BLOCKED', 'Предыдущая операция требует завершения или сверки'
        output.append(dict(row_index=event.row_index, state=state, message=message,
                           local_history=bool(past or legacy)))
    return output


@turnover.get('/turnover')
def page():
    return render_template('chestny/turnover.html')


@turnover.get('/api/turnover/profiles')
def profiles():
    return jsonify(environment=current_app.config['TURNOVER_ENVIRONMENT'],
                   profiles=[dict(id=p.id, name=p.display_name) for p in OrganizationProfile.query.all()])


@turnover.post('/api/turnover/import')
def import_file():
    profile = profile_for(request.form.get('profile_id'))
    upload = request.files.get('file')
    if not upload or not (upload.filename or '').lower().endswith('.xlsx'):
        raise ValueError('Загрузите исходный XLSX Wildberries')
    raw = upload.read(10 * 1024 * 1024 + 1)
    if len(raw) > 10 * 1024 * 1024:
        raise ValueError('Размер файла превышает 10 МБ')
    result = parse_wb_events(io.BytesIO(raw))
    with current_app.extensions['submission_lock']:
        store = current_app.extensions['turnover_imports']
        for token in list(store):
            if store[token]['expires'] < time.time():
                del store[token]
        if len(store) >= 5:
            raise ValueError('Открыто пять импортов. Закройте ненужный импорт или подождите 30 минут')
        token = str(uuid4())
        store[token] = dict(profile=profile.id, inn=profile.inn, environment=api_base(), events=result.events,
                            expires=time.time() + 1800)
    return jsonify(token=token, counts=result.counts, excluded=result.excluded,
        events=[dict(row_index=e.row_index, mask=mask_ki(e.ki), operation=e.operation,
                     assignment=e.assignment, date=e.date, receipt=e.receipt,
                     paid=e.paid, has_identity=e.has_order_identity) for e in result.events])


@turnover.delete('/api/turnover/import/<token>')
def close_import(token):
    current_app.extensions['turnover_imports'].pop(token, None)
    return '', 204


@turnover.post('/api/turnover/import/<token>/check')
def check(token):
    session, profile = session_for(token)
    events = selection(session, request.get_json(silent=True) or {})
    try:
        infos = client_for(profile).codes([e.ki for e in events])
    except Exception:
        return jsonify(message='Не удалось проверить ЧЗ. Отправка недоступна; повторите проверку'), 503
    rows = inspect_events(events, profile, infos)
    for event, decision in zip(events, rows):
        digest = hmac_digest(event.ki, key())
        observation = TurnoverObservation.query.filter_by(profile_id=profile.id,
            environment=api_base(), code_digest=digest).first()
        if observation is None:
            observation = TurnoverObservation(profile_id=profile.id,
                environment=api_base(), code_digest=digest)
            db.session.add(observation)
        raw_status = infos.get(event.ki, {}).get('status')
        observation.state = raw_status if raw_status in ('RETIRED', 'INTRODUCED', 'APPLIED', 'EMITTED') else 'UNKNOWN'
        observation.decision = decision['state']
        observation.checked_at = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify(rows=rows)


def event_fields(event, data):
    overrides = data.get('items', {})
    if not isinstance(overrides, dict):
        raise ValueError('Некорректные реквизиты строк')
    defaults = data.get('defaults') or {}
    override = overrides.get(str(event.row_index), {})
    if not isinstance(defaults, dict) or not isinstance(override, dict):
        raise ValueError('Некорректные реквизиты выбранных строк')
    values = dict(defaults)
    values.update(override)
    if values.get('event_confirmed') is not True:
        raise ValueError('Подтвердите факт события, сохранность маркировки и соответствие текущей продаже')
    if not event.has_order_identity:
        raise ValueError('Нет номера задания или стикера: требуется исходный отчёт с идентификатором события')
    event_date = (event.date or values.get('event_date')) if event.operation == 'Продажа' else (values.get('event_date') or event.date)
    try:
        parsed = date.fromisoformat(event_date)
        if parsed > date.today():
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError('Укажите подтверждённую дату события, не позднее сегодняшней') from None
    values['event_date'] = parsed.isoformat()
    if event.operation == 'Продажа':
        # Application document reference, not a receipt number assigned by WB.
        values['primary_document_type'] = 'OTHER'
        if not values.get('primary_document_number'):
            values['primary_document_number'] = 'WB-' + parsed.strftime('%Y%m%d')
        if not values.get('primary_document_date'):
            values['primary_document_date'] = parsed.isoformat()
        if not values.get('primary_document_custom_name'):
            values['primary_document_custom_name'] = 'Отчёт о дистанционных продажах Wildberries'
    if event.operation == 'Возврат':
        if bool(event.receipt) != bool(event.fiscal_drive):
            raise ValueError(f'Строка {event.row_index}: заполнено только одно поле — номер чека или фискального накопителя. Уточните исходный отчёт')
        values['paid'] = bool(event.receipt)
        if values['paid']:
            values.update(primary_document_type='RECEIPT', primary_document_number=event.receipt,
                          primary_document_date=event.date or parsed.isoformat())
    return values


def body_for(profile, events, fields):
    if events[0].operation == 'Возврат':
        return build_return_document(profile.inn,
            [dict(fields[e.row_index], ki=e.ki) for e in events])
    products = []
    dates = set()
    for e in events:
        if e.cost_kopecks is None or e.cost_kopecks <= 0 or e.currency != 'RUB':
            raise ValueError('Для продажи нужна положительная стоимость в RUB')
        dates.add(fields[e.row_index]['event_date'])
        products.append(dict(cis=e.ki, product_cost=e.cost_kopecks))
    if len(dates) != 1 or not profile.fias_id:
        raise ValueError('Продажи группируются по дате; FIAS профиля обязателен')
    # Retain the project's explicit primary-document contract for withdrawal.
    first = fields[events[0].row_index]
    for name in ('primary_document_number', 'primary_document_date', 'primary_document_custom_name'):
        if not isinstance(first.get(name), str) or not 1 <= len(first[name].strip()) <= 255:
            raise ValueError('Для вывода укажите реквизиты первичного документа')
    date.fromisoformat(first['primary_document_date'])
    body = dict(inn=profile.inn, action='DISTANCE', action_date=next(iter(dates)),
                fias_id=profile.fias_id, withdrawal_type_other='', document_type='OTHER',
                document_number=first['primary_document_number'],
                document_date=first['primary_document_date'],
                primary_document_custom_name=first['primary_document_custom_name'], products=products)
    for p in products:
        p.update(primary_document_type='OTHER', primary_document_number=body['document_number'],
                 primary_document_date=body['document_date'],
                 primary_document_custom_name=body['primary_document_custom_name'])
    return body


def prepare(session, profile, data):
    events = selection(session, data)
    fields = {e.row_index: event_fields(e, data) for e in events}
    for e in events:
        past, same, busy, _ = local_status(e, profile)
        if same or busy:
            raise ValueError('Есть повторное событие или незавершённая операция. Обновите проверку')
        if past and fields[e.row_index]['event_date'] < past.event_date:
            raise ValueError('Событие старше уже зарегистрированной операции. Требуется сверка')
    groups = defaultdict(list)
    for event in events:
        f = fields[event.row_index]
        group = () if event.operation == 'Возврат' else tuple(f.get(n) for n in (
            'event_date', 'primary_document_number', 'primary_document_date', 'primary_document_custom_name'))
        groups[group].append(event)
    size = current_app.config.get('TURNOVER_BATCH_SIZE', 500)
    if type(size) is not int or not 1 <= size <= 30000:
        raise ValueError('Размер пачки должен быть от 1 до 30000')
    batches = []
    def append_chunk(chunk):
        body = body_for(profile, chunk, fields)
        # Conservative 20 MB inner JSON leaves room for Base64 and signature.
        if len(dumps(body).encode()) > 20_000_000:
            if len(chunk) == 1:
                raise ValueError('Одна позиция превышает допустимый размер документа')
            middle = len(chunk) // 2
            append_chunk(chunk[:middle]); append_chunk(chunk[middle:])
        else:
            batches.append((chunk, body))
    for group in groups.values():
        for start in range(0, len(group), size):
            append_chunk(group[start:start + size])
    return batches, fields


@turnover.post('/api/turnover/import/<token>/prepare')
def preview_documents(token):
    session, profile = session_for(token)
    batches, _ = prepare(session, profile, request.get_json(silent=True) or {})
    return jsonify(documents=[dict(index=i + 1, count=len(events),
        operation=events[0].operation, date=body.get('action_date'),
        bytes=len(dumps(body).encode())) for i, (events, body) in enumerate(batches)])


@turnover.post('/api/turnover/import/<token>/submit')
def submit(token):
    data = request.get_json(silent=True) or {}
    if data.get('confirmed') is not True:
        raise ValueError('Подтвердите отправку документов')
    with current_app.extensions['submission_lock']:
        session, profile = session_for(token)
        batches, fields = prepare(session, profile, data)
        client = client_for(profile)
        events = [e for chunk, _ in batches for e in chunk]
        try:
            infos = client.codes([e.ki for e in events])
        except Exception:
            return jsonify(message='Проверка ЧЗ перед отправкой не выполнена'), 503
        checks = inspect_events(events, profile, infos)
        if any(row['state'] != 'READY' for row in checks):
            return jsonify(message='Не все коды допускаются к отправке', rows=checks), 409
        # Sign all documents before reserving or transmitting any of them.
        prepared = []
        for chunk, body in batches:
            serialized = dumps(body)
            try:
                signature = current_app.extensions['cz_signer'].sign(serialized, profile.certificate_thumbprint)
            except Exception:
                return jsonify(message='Не удалось подписать документ. Отправка не начиналась'), 422
            envelope = dict(document_format='MANUAL', type='LP_RETURN' if chunk[0].operation == 'Возврат' else 'LK_RECEIPT',
                product_document=base64.b64encode(serialized.encode()).decode(), signature=signature)
            if len(dumps(envelope).encode()) > 30_000_000:
                raise ValueError('Подписанный документ превышает 30 МБ; уменьшите пачку')
            doc = TurnoverDocument(id=str(uuid4()), profile_id=profile.id,
                environment=api_base(), inn=profile.inn, operation=chunk[0].operation,
                state='PREPARED', payload_digest=body_digest(body))
            db.session.add(doc)
            for event in chunk:
                past, _, _, legacy = local_status(event, profile)
                digest = hmac_digest(event.ki, key())
                lock_id = hmac.new(key(), f'{profile.id}:{api_base()}:{digest}'.encode(), hashlib.sha256).hexdigest()
                db.session.add(TurnoverEvent(document_key=doc.id, profile_id=profile.id,
                    environment=api_base(), code_digest=digest,
                    event_digest=event.fingerprint(key()), mask=mask_ki(event.ki),
                    operation=event.operation, state='PREPARED', active_key=lock_id,
                    previous_id=past.id if past else None,
                    cycle=(past.cycle + (event.operation == 'Продажа' and past.operation == 'Возврат')) if past else 1,
                    source='LOCAL' if past or legacy else 'CRPT', event_date=fields[event.row_index]['event_date'],
                    row_index=event.row_index, paid=fields[event.row_index].get('paid') if event.operation == 'Возврат' else None))
            prepared.append((doc, envelope))
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return jsonify(message='Код или событие уже зарезервированы другой отправкой'), 409
        code_by_digest = {hmac_digest(e.ki, key()): e.ki for e in events}
        for doc, envelope in prepared:
            # Signing a large upload can take time. Refresh each batch's code
            # state immediately before its mutating request.
            batch_codes = [code_by_digest[item.code_digest] for item in doc.items]
            try:
                fresh = client.codes(batch_codes)
                allowed = all(eligibility(fresh.get(code), profile.inn, doc.operation)[0] == 'READY'
                              for code in batch_codes)
            except Exception:
                allowed = False
            if not allowed:
                doc.message = 'Повторная проверка ЧЗ не пройдена. Документ не отправлялся'
                db.session.commit()
                break
            doc.state = 'SENDING'
            for item in doc.items:
                item.state = 'SENDING'
            db.session.commit()
            try:
                doc.document_id = client.create(envelope)
                doc.state, doc.message = 'SENT', 'Принят ЧЗ; ожидается обработка'
            except Exception:
                doc.state, doc.message = 'UNKNOWN', 'Результат отправки неизвестен. Не повторяйте; выполните сверку'
            for item in doc.items:
                item.state = doc.state
            db.session.commit()
            if doc.state == 'UNKNOWN':
                break  # later PREPARED documents are definitely not transmitted
        return jsonify(documents=[serialize_doc(d) for d, _ in prepared]), 201


def serialize_doc(doc):
    return dict(id=doc.id, document_id=doc.document_id, operation=doc.operation,
        state=doc.state, message=doc.message, count=len(doc.items),
        environment='sandbox' if doc.environment == SANDBOX_API_BASE_URL else 'production',
        items=[dict(mask=e.mask, state=e.state, row_index=e.row_index, cycle=e.cycle,
                    source=e.source, event_date=e.event_date) for e in doc.items])


@turnover.get('/api/turnover/documents')
def documents():
    profile = profile_for(request.args.get('profile_id'))
    docs = TurnoverDocument.query.filter_by(profile_id=profile.id).order_by(TurnoverDocument.created_at.desc()).all()
    return jsonify(documents=[serialize_doc(d) for d in docs])


@turnover.post('/api/turnover/documents/<doc_key>/reconcile')
def reconcile(doc_key):
    return reconcile_document(doc_key, request.get_json(silent=True) or {})


def reconcile_document(doc_key, data=None):
    with current_app.extensions['submission_lock']:
        doc = db.session.get(TurnoverDocument, doc_key)
        if doc is None:
            raise ValueError('Документ не найден')
        profile = profile_for(doc.profile_id)
        if doc.inn != profile.inn:
            raise ValueError('ИНН профиля изменён; восстановите профиль документа')
        if doc.state == 'CONFIRMED':
            return jsonify(document=serialize_doc(doc))
        if doc.state in ('PREPARED', 'CANCELLED'):
            raise ValueError('Этот документ не отправлялся')
        data = data or {}
        candidate = doc.document_id or data.get('document_id')
        if not candidate:
            raise ValueError('Проверьте документ в ЛК ЧЗ и укажите его ID для сверки')
        try:
            client = client_for(profile, doc.environment)
            response = client.document(candidate, 'LP_RETURN' if doc.operation == 'Возврат' else 'LK_RECEIPT', doc.inn)
        except Exception:
            return jsonify(message='Не удалось получить соответствующий документ из ЧЗ'), 503
        if doc.document_id and response.get('status') == 'PARSE_ERROR':
            doc.state, doc.message = 'FAILED', 'ЧЗ отклонил формат документа. Можно освободить строки и исправить реквизиты'
            for item in doc.items:
                item.state = 'FAILED'
            db.session.commit()
            return jsonify(document=serialize_doc(doc))
        body = response.get('body')
        if not isinstance(body, dict):
            raise ValueError('ЧЗ не вернул тело документа; сверка не завершена')
        entries = body.get('products_list' if doc.operation == 'Возврат' else 'products')
        if not isinstance(entries, list):
            raise ValueError('Не удалось сопоставить состав документа')
        try:
            codes = [p['ki' if doc.operation == 'Возврат' else 'cis'] for p in entries]
            code_map = {hmac_digest(code, key()): code for code in codes}
        except Exception:
            raise ValueError('Коды документа не соответствуют текущему формату') from None
        if len(code_map) != len(codes) or set(code_map) != {e.code_digest for e in doc.items}:
            raise ValueError('Состав документа не совпадает с сохранённой отправкой')
        if not doc.document_id and body_digest(body) != doc.payload_digest:
            raise ValueError('Тело найденного документа не совпадает с отправленным. Нужна ручная сверка')
        doc.document_id = candidate
        if response.get('status') != 'CHECKED_OK' or response.get('errors') or response.get('commonErrors'):
            doc.state = 'FAILED' if response.get('status') == 'PARSE_ERROR' else (
                'UNKNOWN' if response.get('status') in ('CHECKED_NOT_OK', 'PROCESSING_ERROR') else 'SENT')
            doc.message = 'ЧЗ: ' + str(response.get('status', 'неизвестно')) + '. Проверьте ошибки в ЛК; повторная отправка заблокирована'
            for item in doc.items:
                if item.state != 'CONFIRMED':
                    item.state = doc.state
            db.session.commit()
            return jsonify(document=serialize_doc(doc))
        try:
            infos = client.codes(codes)
        except Exception:
            return jsonify(message='Документ обработан; проверка состояния кодов пока недоступна'), 503
        for item in doc.items:
            if item.state == 'CONFIRMED':
                continue
            info = infos.get(code_map[item.code_digest], {})
            target = 'INTRODUCED' if doc.operation == 'Возврат' else 'RETIRED'
            if (info.get('status') == target and info.get('ownerInn') == doc.inn
                    and info.get('productGroup') == 'lp'
                    and info.get('statusEx') in (None, '', 'EMPTY')
                    and (doc.operation == 'Возврат' or info.get('withdrawReason') == 'DISTANCE')):
                item.state, item.active_key = 'CONFIRMED', None
                item.confirmed_at = datetime.now(timezone.utc)
        doc.state = 'CONFIRMED' if all(e.state == 'CONFIRMED' for e in doc.items) else 'VERIFYING'
        doc.message = 'Результат подтверждён' if doc.state == 'CONFIRMED' else 'Документ обработан, состояния части кодов требуют сверки'
        db.session.commit()
        return jsonify(document=serialize_doc(doc))


@turnover.post('/api/turnover/documents/<doc_key>/cancel-unsent')
def cancel_unsent(doc_key):
    """Only definitely untransmitted intents can be released without CRPT."""
    with current_app.extensions['submission_lock']:
        doc = db.session.get(TurnoverDocument, doc_key)
        if doc is None or doc.state != 'PREPARED' or doc.document_id:
            raise ValueError('Освободить можно только документ, отправка которого не начиналась')
        doc.state, doc.message = 'CANCELLED', 'Отправка не начиналась; можно подготовить заново'
        for item in doc.items:
            item.state, item.active_key = 'CANCELLED', None
            item.event_digest = hashlib.sha256((item.event_digest + ':' + doc.id).encode()).hexdigest()
        db.session.commit()
        return jsonify(document=serialize_doc(doc))


@turnover.post('/api/turnover/documents/<doc_key>/release-rejected')
def release_rejected(doc_key):
    with current_app.extensions['submission_lock']:
        doc = db.session.get(TurnoverDocument, doc_key)
        if doc is None or doc.state != 'FAILED' or not doc.document_id:
            raise ValueError('Нет подтверждённого отказа по формату документа')
        profile = profile_for(doc.profile_id)
        if profile.inn != doc.inn:
            raise ValueError('ИНН профиля изменён')
        try:
            response = client_for(profile, doc.environment).document(doc.document_id,
                'LP_RETURN' if doc.operation == 'Возврат' else 'LK_RECEIPT', doc.inn)
        except Exception:
            return jsonify(message='Не удалось подтвердить отказ ЧЗ'), 503
        if response.get('status') != 'PARSE_ERROR':
            raise ValueError('ЧЗ не подтвердил отказ по формату. Повторная отправка запрещена')
        doc.state, doc.message = 'CANCELLED', 'Отказ ЧЗ по формату подтверждён; строки освобождены для исправления'
        for item in doc.items:
            item.state, item.active_key = 'FAILED', None
            item.event_digest = hashlib.sha256((item.event_digest + ':' + doc.id).encode()).hexdigest()
        db.session.commit()
        return jsonify(document=serialize_doc(doc))


@turnover.get('/api/turnover/documents/<doc_key>/report')
def report(doc_key):
    import openpyxl
    doc = db.session.get(TurnoverDocument, doc_key)
    if not doc:
        raise ValueError('Документ не найден')
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = 'Результат'
    sheet.append(['Строка WB', 'Маска КИ', 'Операция', 'Дата события', 'Цикл', 'Результат', 'ID ЧЗ'])
    for e in doc.items:
        sheet.append([e.row_index, e.mask, e.operation, e.event_date, e.cycle, e.state, doc.document_id or ''])
    for column in sheet.columns:
        sheet.column_dimensions[column[0].column_letter].width = 24
    sheet.freeze_panes = 'A2'
    stream = io.BytesIO()
    book.save(stream); book.close(); stream.seek(0)
    return send_file(stream, as_attachment=True, download_name=f'turnover-{doc.id}.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
