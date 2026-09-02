"""
chestny.import_routes — POST /api/imports/preview, GET/DELETE /api/imports/<token>.

Без сохранения raw-файла. Без sign/auth/network. Без КМ/КИЗ в логах/БД.
"""

from __future__ import annotations

import io
from typing import Any

from flask import Blueprint, current_app, jsonify, request

from app.chestny.factory import db
from app.chestny.models import OrganizationProfile
from app.chestny.services.active_imports import (
    CapacityError,
    ExpiredError,
    NotFoundError,
)
from app.chestny.services.dedup import (
    HmacKeyError,
    find_confirmed_duplicates,
    load_or_create_hmac_key,
)
from app.services.excel_import import (
    FileImportError,
    AcceptedRow,
    ExcludedRow,
    ImportResult,
    ImportSummary,
    parse_xlsx,
)

cz_import_api = Blueprint("cz_import_api", __name__, url_prefix="/api/imports")

MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 MiB


# ═════════════════════════════════════════════════════════════════════════════
#  Сериализация (общая для POST и GET)
# ═════════════════════════════════════════════════════════════════════════════


def _serialize_preview(active: Any, profile: OrganizationProfile) -> dict[str, Any]:
    """Безопасный словарь preview — без криптохвоста, полного КИЗ, HMAC."""
    accepted_out: list[dict[str, Any]] = [
        {
            "row_index": row.row_index,
            "ki": row.ki,
            "check_number": row.check_number,
            "fn_number": row.fn_number,
            "cost_kopecks": int(row.cost_kopecks),
            "date": row.date,
        }
        for row in active.accepted
    ]
    excluded_out: list[dict[str, Any]] = [
        {
            "row_index": row.row_index,
            "reason_code": row.reason_code,
            "message": row.message,
        }
        for row in active.excluded
    ]
    return {
        "import_token": active.token,
        "profile": {"id": profile.id, "display_name": profile.display_name},
        "expires_in": int(active.expires_at - active.created_at),
        "summary": {
            "total_rows": active.summary["total_rows"],
            "accepted": active.summary["accepted"],
            "excluded": active.summary["excluded"],
            "by_reason": dict(active.summary["by_reason"]),
        },
        "accepted": accepted_out,
        "excluded": excluded_out,
    }


# ═════════════════════════════════════════════════════════════════════════════
#  POST /api/imports/preview
# ═════════════════════════════════════════════════════════════════════════════


@cz_import_api.route("/preview", methods=["POST"])
def preview_import():
    """POST /api/imports/preview — предпросмотр импорта XLSX.

    multipart: profile_id + file (.xlsx).
    Seeded профили: org-sinyavin, org-krasikova.
    """
    # ── Размер ────────────────────────────────────────────────────────────
    content_length = request.content_length or 0
    if content_length > MAX_FILE_BYTES:
        return jsonify(
            {"code": "file_too_large", "message": "Файл превышает 10 MiB"}
        ), 413

    # ── profile_id ────────────────────────────────────────────────────────
    profile_id = request.form.get("profile_id", "").strip()
    if not profile_id:
        return jsonify(
            {"code": "missing_profile_id", "message": "profile_id обязателен"}
        ), 400

    profile = db.session.get(OrganizationProfile, profile_id)
    if profile is None:
        return jsonify(
            {"code": "profile_not_found", "message": "Профиль не найден"}
        ), 422

    # ── Проверка настроек профиля ─────────────────────────────────────────
    if not profile.inn or not profile.certificate_thumbprint or not profile.fias_id:
        return jsonify({
            "code": "profile_not_configured",
            "message": "Профиль не настроен: INN, сертификат и FIAS обязательны",
        }), 422

    # ── Файл ──────────────────────────────────────────────────────────────
    file_storage = request.files.get("file")
    if file_storage is None or file_storage.filename == "":
        return jsonify(
            {"code": "missing_file", "message": "Файл не передан"}
        ), 400

    filename: str = file_storage.filename or ""
    if not filename.lower().endswith(".xlsx"):
        return jsonify(
            {"code": "invalid_extension", "message": "Поддерживаются только файлы .xlsx"}
        ), 400

    raw_bytes = file_storage.read(MAX_FILE_BYTES + 1)
    if len(raw_bytes) > MAX_FILE_BYTES:
        return jsonify(
            {"code": "file_too_large", "message": "Файл превышает 10 MiB"}
        ), 413

    if len(raw_bytes) == 0:
        return jsonify(
            {"code": "empty_file", "message": "Пустой файл"}
        ), 400

    # ── Разбор XLSX ───────────────────────────────────────────────────────
    try:
        result: ImportResult = parse_xlsx(io.BytesIO(raw_bytes))
    except FileImportError:
        return jsonify(
            {"code": "invalid_xlsx", "message": "Файл XLSX не прошёл проверку"}
        ), 400

    # ── HMAC ключ ─────────────────────────────────────────────────────────
    try:
        instance_path: str = current_app.instance_path
        key: bytes = load_or_create_hmac_key(instance_path)
    except HmacKeyError:
        return jsonify(
            {"code": "hmac_key_error", "message": "Ошибка инициализации HMAC-ключа"}
        ), 503

    # ── Дубликаты ─────────────────────────────────────────────────────────
    accepted_kis = [row.ki for row in result.accepted]
    confirmed_duplicates = find_confirmed_duplicates(accepted_kis, key)

    # ── Исключённые из-за дублей ──────────────────────────────────────────
    final_excluded: list[ExcludedRow] = list(result.excluded)
    final_accepted: list[AcceptedRow] = []
    for row in result.accepted:
        if row.ki in confirmed_duplicates:
            dup = confirmed_duplicates[row.ki]
            msg = f"Ранее подтверждён: {dup['display_name']}"
            if dup.get("document_id"):
                msg += f" (документ: {dup['document_id']})"
            final_excluded.append(ExcludedRow(
                row_index=row.row_index,
                reason_code="previously_confirmed",
                message=msg,
            ))
        else:
            final_accepted.append(row)

    # ── Сводка ────────────────────────────────────────────────────────────
    by_reason: dict[str, int] = {}
    for ex in final_excluded:
        by_reason[ex.reason_code] = by_reason.get(ex.reason_code, 0) + 1

    cleaned_summary = ImportSummary(
        total_rows=len(final_accepted) + len(final_excluded),
        accepted=len(final_accepted),
        excluded=len(final_excluded),
        by_reason=by_reason,
    )
    cleaned_result = ImportResult(
        accepted=final_accepted,
        excluded=final_excluded,
        summary=cleaned_summary,
    )

    # ── Сохраняем в store (profile_id pinned) ─────────────────────────────
    store = current_app.extensions["active_imports"]
    try:
        token = store.create(profile_id, cleaned_result)
    except CapacityError:
        return jsonify(
            {"code": "store_full", "message": "Достигнут лимит активных сессий импорта"}
        ), 503
    except Exception:
        return jsonify(
            {"code": "store_error", "message": "Ошибка сохранения сессии импорта"}
        ), 503

    active = store.get(token)
    return jsonify(_serialize_preview(active, profile)), 201


# ═════════════════════════════════════════════════════════════════════════════
#  GET /api/imports/<token>
# ═════════════════════════════════════════════════════════════════════════════


@cz_import_api.route("/<token>", methods=["GET"])
def get_import(token: str):
    """GET /api/imports/<token> — безопасный preview активного импорта.

    404 для неизвестного token, 410 для истёкшего.
    Не возвращает криптохвост, исходный XLSX, HMAC-ключ/дайджест.
    """
    store = current_app.extensions["active_imports"]
    try:
        active = store.get(token)
    except NotFoundError:
        return jsonify(
            {"code": "import_not_found", "message": "Активный импорт не найден"}
        ), 404
    except ExpiredError:
        return jsonify(
            {"code": "import_expired", "message": "Срок действия импорта истёк"}
        ), 410

    profile = db.session.get(OrganizationProfile, active.profile_id)
    if profile is None:
        return jsonify(
            {"code": "profile_not_found", "message": "Профиль импорта не найден"}
        ), 404

    return jsonify(_serialize_preview(active, profile)), 200


# ═════════════════════════════════════════════════════════════════════════════
#  DELETE /api/imports/<token>
# ═════════════════════════════════════════════════════════════════════════════


@cz_import_api.route("/<token>", methods=["DELETE"])
def delete_import(token: str):
    """DELETE /api/imports/<token> — отмена и удаление временного импорта.

    Идемпотентен: повторный DELETE → 204.
    После удаления GET возвращает 404.
    """
    store = current_app.extensions["active_imports"]
    store.cancel(token)
    return "", 204
