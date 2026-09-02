"""
Smoke-тест: новое приложение Честного Знака (offline).

Проверяет создание factory, health endpoint и route isolation
без старого приложения.
"""

import sys
import os
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.chestny.factory import create_cz_app, db


def main():
    print("=" * 60)
    print("  Smoke-тест: приложение Честного Знака (offline)")
    print("=" * 60)

    # 1) Factory с temp DB
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        app = create_cz_app(db_uri=f"sqlite:///{db_path}", testing=True)
        print("[1/4] Factory создана: OK")

        # 2) Health
        with app.test_client() as client:
            resp = client.get("/health")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["status"] == "ok"
            assert data["app"] == "chestny-znak"
            print("[2/4] GET /health → 200: OK")

        # 3) Route isolation
        with app.test_client() as client:
            assert client.get("/").status_code == 200
            for path in ["/api/warehouses", "/api/skus"]:
                resp = client.get(path)
                assert resp.status_code == 404, f"{path} должен быть 404"
            print("[3/4] Старые маршруты не зарегистрированы: OK")

        # 4) Два профиля
        with app.app_context():
            from app.chestny.models import OrganizationProfile
            profiles = OrganizationProfile.query.all()
            assert len(profiles) == 2
            ids = {p.id for p in profiles}
            assert ids == {"org-sinyavin", "org-krasikova"}
            print(f"[4/4] Профили: {len(profiles)} шт., ids={ids}: OK")

    finally:
        os.unlink(db_path)

    print()
    print("✅ Smoke-тест ЧЗ пройден: приложение работает offline")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
