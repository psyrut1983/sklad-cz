"""
chestny.factory — Flask app factory для Честного Знака.

Создаёт минимальное Flask-приложение с отдельной SQLite БД,
моделями из chestny.models и идемпотентным seed.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Optional

from flask import Flask, jsonify, render_template
from flask_sqlalchemy import SQLAlchemy

from app.chestny.services.active_imports import ActiveImportStore

db = SQLAlchemy()


def create_cz_app(
    instance_path: Optional[str] = None,
    db_uri: Optional[str] = None,
    testing: bool = False,
    secret_key: Optional[str] = None,
) -> Flask:
    """
    Фабрика Flask-приложения для ЧЗ.

    Параметры
    ---------
    instance_path : str, optional
        Путь к instance-папке (для SQLite). По умолчанию app/chestny/instance/.
    db_uri : str, optional
        Полный SQLAlchemy URI. По умолчанию sqlite:///{instance_path}/cz.db.
    testing : bool
        Режим тестирования (отключает CSRF и пр.).
    secret_key : str, optional
        Явный SECRET_KEY для Flask. Если не передан — генерируется
        через secrets.token_hex(32). Не хранится в репозитории.

    Возвращает
    ----------
    Flask-приложение с зарегистрированными моделями и health-рутом.
    """
    if instance_path is None:
        instance_path = str(Path(__file__).parent / "instance")

    Path(instance_path).mkdir(parents=True, exist_ok=True)

    _chestny_dir = Path(__file__).parent
    app = Flask(
        __name__,
        instance_path=instance_path,
        instance_relative_config=False,
        template_folder=str(_chestny_dir / "templates"),
        static_folder=str(_chestny_dir / "static"),
        static_url_path="/static",
    )
    app.config["TESTING"] = testing

    if db_uri is None:
        db_path = os.path.join(instance_path, "cz.db")
        db_uri = f"sqlite:///{db_path}"

    app.config["SQLALCHEMY_DATABASE_URI"] = db_uri
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.secret_key = secret_key if secret_key is not None else secrets.token_hex(32)

    db.init_app(app)

    # Импортируем модели, чтобы SQLAlchemy их увидела
    from app.chestny import models  # noqa: F401

    with app.app_context():
        db.create_all()
        _seed_profiles()

    # ── Blueprint ──────────────────────────────────────────────────────────
    from app.chestny.routes import cz_api
    app.register_blueprint(cz_api)

    # ── Active import store ────────────────────────────────────────────────
    app.extensions["active_imports"] = ActiveImportStore(5, 1800)
    app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MiB

    # ── Package store ───────────────────────────────────────────────────────
    from app.chestny.services.packaging import PackageStore
    app.extensions["package_store"] = PackageStore()

    # ── Cleanup orphaned data on startup ─────────────────────────────────────
    _cleanup_on_startup(app)

    @app.errorhandler(413)
    def _json_413(e):
        return jsonify({"code": "file_too_large", "message": "Файл превышает 10 MiB"}), 413

    # ── Import blueprint ───────────────────────────────────────────────────
    from app.chestny.import_routes import cz_import_api
    app.register_blueprint(cz_import_api)

    # ── Report blueprint ────────────────────────────────────────────────────
    from app.chestny.report_routes import cz_report
    app.register_blueprint(cz_report)

    # ── Health endpoint ───────────────────────────────────────────────────
    @app.route("/health")
    def health():
        return {"status": "ok", "app": "chestny-znak"}

    # ── UI ────────────────────────────────────────────────────────────────
    @app.route("/")
    def index():
        return render_template("chestny/settings.html")

    return app


# ═════════════════════════════════════════════════════════════════════════════
#  Seed
# ═════════════════════════════════════════════════════════════════════════════

STABLE_PROFILES = [
    {
        "id": "org-sinyavin",
        "display_name": "ИП Синявин",
        "product_group": "lp",
    },
    {
        "id": "org-krasikova",
        "display_name": "ИП Красикова",
        "product_group": "lp",
    },
]


def _seed_profiles() -> None:
    """Идемпотентно создаёт ровно два профиля."""
    from app.chestny.models import OrganizationProfile

    existing_ids = {p.id for p in OrganizationProfile.query.all()}

    for data in STABLE_PROFILES:
        if data["id"] not in existing_ids:
            profile = OrganizationProfile(
                id=data["id"],
                display_name=data["display_name"],
                product_group=data["product_group"],
            )
            db.session.add(profile)

    db.session.commit()


def _reconcile_orphaned_packages(app: Flask) -> None:
    """Один проход reconciliation для осиротевших PARTIAL/UNKNOWN пакетов.

    При старте пакеты in-memory, так что это no-op.
    Вызов сохранён для правильной инициализации цепочки вызовов.
    """
    pkg_store = app.extensions.get("package_store")
    if pkg_store is None:
        return

    # Если есть PARTIAL/UNKNOWN пакеты — пробуем создать reconciler.
    # При старте пакетов нет, поэтому выходим сразу.
    has_candidates = bool(
        pkg_store.list_by_status("PARTIAL") or pkg_store.list_by_status("UNKNOWN")
    )
    if not has_candidates:
        return

    from app.chestny.services.reconciliation import ScheduledReconciler, ReconciliationService
    from app.chestny.services.cz_status import CzStatusClient
    from app.chestny.services.cz_auth import CzAuthClientPool

    auth_pool = app.extensions.get("auth_pool")
    if auth_pool is None:
        auth_pool = CzAuthClientPool()
        app.extensions["auth_pool"] = auth_pool

    transport = app.extensions.get("cz_transport")
    if transport is None:
        return  # транспорт не настроен — не можем сверять

    from app.chestny.models import OrganizationProfile
    profile = OrganizationProfile.query.first()
    if profile is None:
        return

    auth_client = auth_pool.get_or_create(
        profile_id=profile.id,
        api_base_url="https://example.com",
        inn="",
        certificate_thumbprint="",
    )
    status_client = CzStatusClient(auth_client, transport)
    reconciler = ScheduledReconciler(pkg_store, ReconciliationService(status_client))
    reconciler.run_once()


def _cleanup_on_startup(app: Flask) -> None:
    """Очистка осиротевших временных данных при старте."""
    # Cleanup expired active imports
    imports_store = app.extensions.get("active_imports")
    if imports_store is not None:
        imports_store.cleanup_expired()

    # Cleanup orphaned packages
    pkg_store = app.extensions.get("package_store")
    if pkg_store is not None:
        valid_tokens = set()
        if imports_store is not None:
            for tok in list(imports_store._imports.keys()):
                valid_tokens.add(tok)
        for pkg in list(pkg_store._packages.values()):
            if pkg.import_token not in valid_tokens:
                pkg_store._packages.pop(pkg.id, None)

    # Cleanup temp XLSX files in instance
    inst = app.instance_path
    if os.path.isdir(inst):
        for fname in os.listdir(inst):
            if fname.endswith(".xlsx"):
                try:
                    os.unlink(os.path.join(inst, fname))
                except OSError:
                    pass

    # ── Reconciliation orphaned packages ─────────────────────────────────
    _reconcile_orphaned_packages(app)
