"""Submission endpoint connecting a reviewed import to the CRPT API."""

from __future__ import annotations

from datetime import date
import hashlib
from types import MappingProxyType
from typing import Any

import requests
from flask import Blueprint, current_app, jsonify, request

from app.chestny.factory import db
from app.chestny.models import ImportJob, OrganizationProfile, SubmissionBatch
from app.chestny.services.active_imports import ActiveImport, ExpiredError, NotFoundError
from app.chestny.services.cz_auth import CredentialsSnapshot, PRODUCTION_API_BASE_URL
from app.chestny.services.cz_auth import (
    AccessDeniedError,
    ChallengeError,
    NetworkError,
    RateLimitError,
    ServerError,
    SignatureInvalidError,
    SigningError,
    TimeoutError as CzTimeoutError,
    TokenParseError,
    UnauthorizedError,
)
from app.chestny.services.dedup import load_or_create_hmac_key
from app.chestny.services.packaging import CONFIRMED, PackageBuilder, PackageBuilderError
from app.services.excel_import import ImportResult, ImportSummary

cz_submit_api = Blueprint("cz_submit_api", __name__, url_prefix="/api/imports")


def _required_date(data: dict[str, Any], name: str) -> str:
    value = str(data.get(name, "")).strip()
    try:
        date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"Поле {name} должно быть датой в формате ГГГГ-ММ-ДД") from None
    return value


def _request_transport(method: str, url: str, **kwargs: Any) -> requests.Response:
    return requests.request(method, url, **kwargs)


def _select_submission_rows(active: ActiveImport, data: dict[str, Any]) -> ActiveImport:
    """Build a server-authoritative subset from accepted Excel row numbers."""
    raw = data.get("selected_rows")
    if raw is None:  # Backward compatible: old UI submits every accepted row.
        selected = list(active.accepted)
    else:
        if not isinstance(raw, list):
            raise ValueError("selected_rows должен быть списком строк")
        if not raw:
            raise ValueError("Выберите хотя бы один КИЗ")
        if any(type(value) is not int or value <= 0 for value in raw):
            raise ValueError("Некорректный номер выбранной строки")
        if len(raw) != len(set(raw)):
            raise ValueError("Список выбранных строк содержит повторы")
        accepted_by_index = {row.row_index: row for row in active.accepted}
        unknown = set(raw) - set(accepted_by_index)
        if unknown:
            raise ValueError("Выбранная строка отсутствует в проверенном импорте")
        wanted = set(raw)
        selected = [row for row in active.accepted if row.row_index in wanted]

    summary = MappingProxyType({
        "total_rows": len(selected),
        "accepted": len(selected),
        "excluded": 0,
        "by_reason": MappingProxyType({}),
    })
    return ActiveImport(
        token=active.token,
        profile_id=active.profile_id,
        accepted=tuple(selected),
        excluded=(),
        summary=summary,
        created_at=active.created_at,
        expires_at=active.expires_at,
    )


def _remaining_result(active: ActiveImport, confirmed_kis: set[str]) -> ImportResult:
    remaining = [row for row in active.accepted if row.ki not in confirmed_kis]
    return ImportResult(
        accepted=remaining,
        excluded=[],
        summary=ImportSummary(
            total_rows=len(remaining), accepted=len(remaining), excluded=0
        ),
    )


@cz_submit_api.route("/<token>/submit", methods=["POST"])
def submit_import(token: str):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"code": "invalid_json", "message": "Некорректный JSON"}), 400

    store = current_app.extensions["active_imports"]
    try:
        active = store.get(token)
    except NotFoundError:
        return jsonify({"code": "import_not_found", "message": "Активный импорт не найден"}), 404
    except ExpiredError:
        return jsonify({"code": "import_expired", "message": "Срок действия импорта истёк"}), 410

    profile = db.session.get(OrganizationProfile, active.profile_id)
    if profile is None:
        return jsonify({"code": "profile_not_found", "message": "Профиль не найден"}), 404
    if not profile.inn or not profile.certificate_thumbprint or not profile.fias_id:
        return jsonify({"code": "profile_not_configured", "message": "Профиль настроен не полностью"}), 422
    if not active.accepted:
        return jsonify({"code": "nothing_to_submit", "message": "Нет строк для отправки"}), 422

    try:
        submission = _select_submission_rows(active, data)
    except ValueError as exc:
        return jsonify({"code": "invalid_selection", "message": str(exc)}), 400

    if not current_app.config.get("TESTING"):
        from app.chestny.services.certificates import (
            CertificateBackendError,
            can_sign_with_certificate,
            list_local_certificates,
        )
        try:
            certs = list_local_certificates()
        except CertificateBackendError:
            return jsonify({
                "code": "certificate_backend_unavailable",
                "message": "Не удалось открыть хранилище сертификатов. Проверьте КриптоПро CSP.",
            }), 503
        selected = next(
            (c for c in certs if c.get("thumbprint", "").upper() == profile.certificate_thumbprint.upper()),
            None,
        )
        if selected is None or not selected.get("has_private_key"):
            return jsonify({
                "code": "certificate_unavailable",
                "message": "Выбранная ЭЦП сейчас недоступна. Подключите токен, нажмите «Обновить сертификаты» и выберите сертификат заново.",
            }), 422
        if not can_sign_with_certificate(profile.certificate_thumbprint):
            return jsonify({
                "code": "private_key_unavailable",
                "message": "USB-токен ЭЦП не подключён или закрытый ключ недоступен. Подключите токен и снова проверьте сертификат.",
            }), 422

    try:
        action_date = _required_date(data, "action_date")
        document_date = _required_date(data, "document_date")
    except ValueError as exc:
        return jsonify({"code": "invalid_fields", "message": str(exc)}), 400

    document_number = str(data.get("document_number", "")).strip()
    custom_name = str(data.get("primary_document_custom_name", "")).strip()
    if not document_number or len(document_number) > 255:
        return jsonify({"code": "invalid_fields", "message": "Укажите номер первичного документа"}), 400
    if len(custom_name) > 255:
        return jsonify({"code": "invalid_fields", "message": "Название документа слишком длинное"}), 400

    # Claim before any signing/network call. An uncertain response must never be
    # retried blindly from a double click or a second browser tab.
    with current_app.extensions["submission_lock"]:
        claimed = current_app.extensions["claimed_imports"]
        if token in claimed:
            return jsonify({"code": "already_submitting", "message": "Этот импорт уже отправляется или был отправлен"}), 409
        claimed.add(token)

    try:
        credentials = CredentialsSnapshot(
            profile_id=profile.id,
            inn=profile.inn,
            certificate_thumbprint=profile.certificate_thumbprint,
            api_base_url=PRODUCTION_API_BASE_URL,
        )
        auth = current_app.extensions["auth_registry"].get_or_create(
            credentials,
            transport=current_app.extensions["cz_auth_transport"],
            signer=current_app.extensions["cz_signer"],
        )
        signer = current_app.extensions["cz_signer"]
        builder = PackageBuilder(
            auth=auth,
            transport=current_app.extensions.get("cz_submit_transport", _request_transport),
            signer=signer.sign,
            hmac_key=load_or_create_hmac_key(current_app.instance_path),
        )
        package = builder.create(
            submission,
            {
                "id": profile.id,
                "inn": profile.inn,
                "fias_id": profile.fias_id,
                "certificate_thumbprint": profile.certificate_thumbprint,
                "api_base_url": PRODUCTION_API_BASE_URL,
            },
            action_date=action_date,
            document_number=document_number,
            document_date=document_date,
            primary_document_custom_name=custom_name,
        )
    except PackageBuilderError as exc:
        return jsonify({"code": "submit_rejected", "message": str(exc)}), 409
    except SigningError as exc:
        current_app.logger.warning("CRPT signing failed: %s", exc)
        return jsonify({"code": "signing_failed", "message": str(exc)}), 422
    except SignatureInvalidError:
        return jsonify({"code": "signature_invalid", "message": "Честный знак отклонил электронную подпись. Проверьте выбранный сертификат и ИНН профиля."}), 403
    except AccessDeniedError:
        return jsonify({"code": "access_denied", "message": "Честный знак отказал в доступе. У сертификата или участника нет прав на работу через API."}), 403
    except UnauthorizedError:
        return jsonify({"code": "unauthorized", "message": "Не удалось авторизоваться в Честном знаке. Повторите попытку после проверки сертификата."}), 401
    except RateLimitError:
        return jsonify({"code": "rate_limited", "message": "Честный знак временно ограничил количество запросов. Повторите попытку позже."}), 429
    except CzTimeoutError:
        return jsonify({"code": "timeout", "message": "Честный знак не ответил вовремя. Документ не был отправлен; проверьте интернет и повторите позже."}), 504
    except NetworkError:
        return jsonify({"code": "network_error", "message": "Нет связи с Честным знаком. Проверьте интернет, VPN, прокси и антивирус."}), 503
    except ServerError:
        return jsonify({"code": "crpt_server_error", "message": "Сервис Честного знака временно недоступен. Повторите попытку позже."}), 502
    except (ChallengeError, TokenParseError) as exc:
        current_app.logger.warning("CRPT authentication response error: %s", exc)
        return jsonify({"code": "auth_response_error", "message": "Честный знак вернул некорректный ответ при авторизации."}), 502
    except Exception:
        current_app.logger.exception("CRPT submission failed")
        return jsonify({"code": "submit_failed", "message": "Внутренняя ошибка отправки. Подробности записаны в журнал приложения."}), 500

    try:
        job = ImportJob(
            profile_id=profile.id,
            file_fingerprint=hashlib.sha256(token.encode("utf-8")).hexdigest(),
            total_rows=package.summary.total_rows,
            accepted_count=package.summary.accepted,
            excluded_count=package.summary.excluded,
            status=package.status,
        )
        db.session.add(job)
        db.session.flush()
        for batch in package.batches:
            db.session.add(SubmissionBatch(
                job_id=job.id,
                profile_id=profile.id,
                batch_fingerprint=f"{package.id}:{batch.index}",
                state=batch.status,
                document_id=batch.document_id,
                attempts=1,
                error_message=batch.error,
            ))
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Could not persist submission result")
        return jsonify({"code": "persistence_failed", "message": "Документ отправлен, но результат не удалось сохранить. Не отправляйте его повторно."}), 500

    current_app.extensions["package_store"].save(package)
    confirmed_kis = {
        item.ki31
        for batch in package.batches if batch.status == CONFIRMED
        for item in batch.items
    }
    try:
        remaining_token = store.replace(token, _remaining_result(active, confirmed_kis))
        remaining_preview = None
        if remaining_token:
            from app.chestny.import_routes import _serialize_preview
            remaining_preview = _serialize_preview(store.get(remaining_token), profile)
    except Exception:
        current_app.logger.exception("Could not preserve unsubmitted import remainder")
        return jsonify({
            "code": "remainder_failed",
            "message": "Результат отправки сохранён, но остаток импорта восстановить не удалось. Не повторяйте уже подтверждённые КИЗ.",
        }), 500
    if package.status == "FAILED":
        errors = list(dict.fromkeys(b.error for b in package.batches if b.error))
        detail = "; ".join(errors) if errors else "причина не указана"
        return jsonify({
            "code": "crpt_document_rejected",
            "message": f"Честный знак не принял документ: {detail}",
            "status": package.status,
            "submitted": 0,
            "failed": package.summary.accepted_failed,
            "remaining_import": remaining_preview,
        }), 422
    return jsonify({
        "package_id": package.id,
        "status": package.status,
        "document_id": package.document_id,
        "submitted": package.summary.accepted_submitted,
        "failed": package.summary.accepted_failed,
        "remaining_import": remaining_preview,
    }), 201
