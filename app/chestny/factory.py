"""
chestny.factory — Flask app factory для Честного Знака.

Создаёт минимальное Flask-приложение с отдельной SQLite БД,
моделями из chestny.models и идемпотентным seed.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from flask import Flask
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


def create_cz_app(
    instance_path: Optional[str] = None,
    db_uri: Optional[str] = None,
    testing: bool = False,
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

    Возвращает
    ----------
    Flask-приложение с зарегистрированными моделями и health-рутом.
    """
    if instance_path is None:
        instance_path = str(Path(__file__).parent / "instance")

    Path(instance_path).mkdir(parents=True, exist_ok=True)

    app = Flask(__name__, instance_path=instance_path, instance_relative_config=False)
    app.config["TESTING"] = testing

    if db_uri is None:
        db_path = os.path.join(instance_path, "cz.db")
        db_uri = f"sqlite:///{db_path}"

    app.config["SQLALCHEMY_DATABASE_URI"] = db_uri
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.secret_key = "cz-secret-do-not-use-in-production"

    db.init_app(app)

    # Импортируем модели, чтобы SQLAlchemy их увидела
    from app.chestny import models  # noqa: F401

    with app.app_context():
        db.create_all()
        _seed_profiles()

    # ── Health endpoint ───────────────────────────────────────────────────
    @app.route("/health")
    def health():
        return {"status": "ok", "app": "chestny-znak"}

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
