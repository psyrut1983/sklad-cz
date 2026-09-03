"""
chestny.report_routes — отчёт и очистка пакетов (Этап 6).

GET    /api/packages/<profile_id>          — список пакетов профиля
GET    /api/packages/<profile_id>/<id>     — детали пакета
GET    /api/packages/<profile_id>/<id>/xlsx — XLSX с успешными КИЗ
DELETE /api/packages/<id>                  — удалить пакет и XLSX
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, send_file

from app.chestny.services.packaging import CONFIRMED, FAILED, PARTIAL, Package, PackageStore

cz_report = Blueprint("cz_report", __name__, url_prefix="/api/packages")


def _safe_package_json(pkg: Package) -> dict:
    """Безопасная сериализация пакета — без KI, токена, raw body."""
    return {
        "id": pkg.id,
        "profile_id": pkg.profile_id,
        "status": pkg.status,
        "document_id": pkg.document_id,
        "summary": {
            "total_rows": pkg.summary.total_rows if pkg.summary else 0,
            "accepted": pkg.summary.accepted if pkg.summary else 0,
            "accepted_submitted": pkg.summary.accepted_submitted if pkg.summary else 0,
            "accepted_failed": pkg.summary.accepted_failed if pkg.summary else 0,
            "excluded": pkg.summary.excluded if pkg.summary else 0,
        },
        "batches": [
            {
                "index": b.index,
                "status": b.status,
                "document_id": b.document_id,
                "count": len(b.items),
            }
            for b in pkg.batches
        ],
        "created_at": pkg.created_at.isoformat() if pkg.created_at else "",
        "updated_at": pkg.updated_at.isoformat() if pkg.updated_at else "",
    }


def _get_store() -> PackageStore:
    store: PackageStore = current_app.extensions.get("package_store", PackageStore())
    return store


# ═════════════════════════════════════════════════════════════════════════════
#  GET /api/packages/<profile_id> — список пакетов профиля
# ═════════════════════════════════════════════════════════════════════════════


@cz_report.route("/<profile_id>")
def list_packages(profile_id: str):
    """Список пакетов для профиля. Без KI/токена."""
    store = _get_store()
    packages = store.list_by_profile(profile_id)
    return jsonify([_safe_package_json(p) for p in packages])


# ═════════════════════════════════════════════════════════════════════════════
#  GET /api/packages/<profile_id>/<package_id> — детали пакета
# ═════════════════════════════════════════════════════════════════════════════


@cz_report.route("/<profile_id>/<package_id>")
def get_package(profile_id: str, package_id: str):
    """Детали пакета. Без KI/токена."""
    store = _get_store()
    pkg = store.get(package_id)
    if pkg is None or pkg.profile_id != profile_id:
        return jsonify({"error": "Пакет не найден"}), 404
    return jsonify(_safe_package_json(pkg))


# ═════════════════════════════════════════════════════════════════════════════
#  GET /api/packages/<profile_id>/<package_id>/xlsx — XLSX с успешными КИЗ
# ═════════════════════════════════════════════════════════════════════════════


@cz_report.route("/<profile_id>/<package_id>/xlsx")
def download_xlsx(profile_id: str, package_id: str):
    """XLSX только с CONFIRMED строками. Без полного KI — только маска."""
    store = _get_store()
    pkg = store.get(package_id)
    if pkg is None or pkg.profile_id != profile_id:
        return jsonify({"error": "Пакет не найден"}), 404

    try:
        import openpyxl
        from openpyxl.styles import Font
    except ImportError:
        return jsonify({"error": "openpyxl не установлен"}), 500

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "КИЗ"

    # Заголовки
    headers = ["Маска КИЗ", "Чек", "ФН", "Стоимость (коп)", "Дата"]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)

    # Только CONFIRMED батчи
    written = 0
    for batch in pkg.batches:
        if batch.status != CONFIRMED:
            continue
        for item in batch.items:
            ws.append([
                item.mask,     # только маска, не полный KI
                item.check,
                item.fn,
                item.cost_kopecks,
                item.date,
            ])
            written += 1

    if written == 0:
        return jsonify({"error": "Нет подтверждённых КИЗ для выгрузки"}), 404

    # Временный файл
    tmp = tempfile.NamedTemporaryFile(
        dir=current_app.instance_path,
        suffix=".xlsx",
        delete=False,
    )
    wb.save(tmp.name)
    tmp.close()

    return send_file(
        tmp.name,
        as_attachment=True,
        download_name=f"package_{package_id[:8]}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ═════════════════════════════════════════════════════════════════════════════
#  DELETE /api/packages/<package_id> — удалить пакет и XLSX
# ═════════════════════════════════════════════════════════════════════════════


@cz_report.route("/<package_id>", methods=["DELETE"])
def delete_package(package_id: str):
    """Удаляет пакет и связанные временные файлы."""
    store = _get_store()
    pkg = store.get(package_id)
    if pkg is None:
        return "", 204  # idempotent

    # Удалить временные файлы в instance_path
    inst = current_app.instance_path
    if os.path.isdir(inst):
        for fname in os.listdir(inst):
            if fname.startswith(f"package_{package_id[:8]}") and fname.endswith(".xlsx"):
                try:
                    os.unlink(os.path.join(inst, fname))
                except OSError:
                    pass

    store._packages.pop(package_id, None)
    return "", 204
