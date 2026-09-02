"""
chestny.routes — API blueprint для профилей и диагностики сертификатов.

Регистрируется только в новой chestny factory.
Не использует старые складские модели или blueprints.
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, jsonify, request

from app.chestny.factory import db
from app.chestny.models import OrganizationProfile
from app.chestny.validation import (
    normalize_fias_id,
    normalize_inn,
    normalize_thumbprint,
)

cz_api = Blueprint("cz_api", __name__, url_prefix="/api")


# ═════════════════════════════════════════════════════════════════════════════
#  Профили
# ═════════════════════════════════════════════════════════════════════════════


def _profile_to_dict(p: OrganizationProfile, mask_inn: bool = True) -> dict[str, Any]:
    """Сериализует профиль в dict.

    Если mask_inn=True — маскирует INN и не возвращает полный certificate_thumbprint.
    api_url не возвращается (неизменяемое поле, не нужно клиенту).
    """
    inn = p.inn or ""
    if mask_inn and inn and len(inn) == 12:
        inn = inn[:4] + "****" + inn[-4:]

    result: dict[str, Any] = {
        "id": p.id,
        "display_name": p.display_name,
        "product_group": p.product_group,
        "inn": inn,
        "fias_id": p.fias_id or "",
        "updated_at": p.updated_at.isoformat() if p.updated_at else "",
    }

    if mask_inn:
        # список — не возвращаем certificate_thumbprint вообще
        pass
    else:
        result["certificate_thumbprint"] = p.certificate_thumbprint or ""

    return result


@cz_api.route("/profiles", methods=["GET"])
def list_profiles():
    """GET /api/profiles — список ровно двух профилей (INN маскирован, certificate_thumbprint отсутствует)."""
    profiles = OrganizationProfile.query.order_by(OrganizationProfile.id).all()
    return jsonify([_profile_to_dict(p, mask_inn=True) for p in profiles])


@cz_api.route("/profiles/<profile_id>", methods=["GET"])
def get_profile(profile_id: str):
    """GET /api/profiles/<id> — детали одного профиля (полные INN/thumbprint для редактирования)."""
    p = OrganizationProfile.query.get(profile_id)
    if p is None:
        return jsonify({"error": "Профиль не найден"}), 404
    return jsonify(_profile_to_dict(p, mask_inn=False))


@cz_api.route("/profiles/<profile_id>", methods=["PUT"])
def update_profile(profile_id: str):
    """PUT /api/profiles/<id> — обновление редактируемых полей.

    Разрешены: inn, certificate_thumbprint, fias_id.
    Запрещены: id, display_name, product_group, api_url.
    Поля, отсутствующие в теле запроса, сохраняют прежнее значение.
    Явная пустая строка — очистить поле.
    """
    p = OrganizationProfile.query.get(profile_id)
    if p is None:
        return jsonify({"error": "Профиль не найден"}), 404

    data = request.get_json(silent=True)
    if data is None or not isinstance(data, dict):
        return jsonify({"error": "Некорректный JSON"}), 400

    # ── Неизвестные поля → 400 ────────────────────────────────────────────
    ALLOWED = {"inn", "certificate_thumbprint", "fias_id"}
    unknown = set(data.keys()) - ALLOWED
    if unknown:
        return jsonify({"error": f"Неизвестные поля: {', '.join(sorted(unknown))}"}), 400

    # ── Валидация всех полей до присваивания ──────────────────────────────
    validated: dict[str, str | None] = {}

    if "inn" in data:
        inn_raw = data["inn"]
        if inn_raw is not None and inn_raw != "":
            inn = normalize_inn(str(inn_raw))
            if inn is None:
                return jsonify({"error": "Некорректный ИНН"}), 400
            validated["inn"] = inn
        else:
            validated["inn"] = None

    if "certificate_thumbprint" in data:
        tp_raw = data["certificate_thumbprint"]
        if tp_raw is not None and tp_raw != "":
            tp = normalize_thumbprint(str(tp_raw))
            if tp is None:
                return jsonify({"error": "Некорректный отпечаток сертификата"}), 400
            validated["certificate_thumbprint"] = tp
        else:
            validated["certificate_thumbprint"] = None

    if "fias_id" in data:
        fias_raw = data["fias_id"]
        if fias_raw is not None and fias_raw != "":
            fias = normalize_fias_id(str(fias_raw))
            if fias is None:
                return jsonify({"error": "Некорректный FIAS UUID"}), 400
            validated["fias_id"] = fias
        else:
            validated["fias_id"] = None

    # ── Все поля прошли валидацию — атомарно присваиваем и коммитим ────────
    for key, val in validated.items():
        setattr(p, key, val)

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"error": "Ошибка обновления профиля"}), 500

    return jsonify(_profile_to_dict(p, mask_inn=False))


# ═════════════════════════════════════════════════════════════════════════════
#  Сертификаты
# ═════════════════════════════════════════════════════════════════════════════


@cz_api.route("/certificates", methods=["GET"])
def list_certs():
    """GET /api/certificates — список локальных сертификатов."""
    from app.chestny.services.certificates import (
        CertificateBackendError,
        list_local_certificates,
    )
    try:
        certs = list_local_certificates()
    except CertificateBackendError:
        return jsonify({"error": "Сервис сертификатов недоступен"}), 503
    return jsonify(certs)


@cz_api.route("/profiles/<profile_id>/certificate/diagnose", methods=["POST"])
def diagnose_certificate(profile_id: str):
    """POST /api/profiles/<id>/certificate/diagnose — диагностика сертификата профиля."""
    p = OrganizationProfile.query.get(profile_id)
    if p is None:
        return jsonify({"error": "Профиль не найден"}), 404

    if not p.certificate_thumbprint:
        return jsonify({
            "error": "Отпечаток сертификата не настроен для данного профиля",
        }), 422

    from app.chestny.services.certificates import (
        CertificateBackendError,
        diagnose_profile_certificate,
    )

    try:
        diag = diagnose_profile_certificate(p.certificate_thumbprint)
    except CertificateBackendError:
        return jsonify({"error": "Сервис сертификатов недоступен"}), 503

    return jsonify(diag)
