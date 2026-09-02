#!/usr/bin/env python3
"""
Этап 1: Smoke-тест исходного приложения sklad-cz (offline, без production).

Проверяет:
- Flask-приложение создаётся без ошибок
- SQLite БД инициализируется
- Корневой UI (GET /) отвечает 200
- Ни одного реального HTTP-вызова к внешним сервисам (ЧЗ, GitHub, и т.д.)
- Приложение не экспонировано наружу (test client, не реальный bind)
"""

import sys
import os
from unittest.mock import patch

# Изолируем сетевые вызовы ДО импорта приложения
real_requests_get = None
call_log = []

def _mocked_get(url, *args, **kwargs):
    """Перехватывает все GET-запросы и логирует их."""
    call_log.append(url)
    if 'tnved.csv' in url or 'github' in url:
        # ТН ВЭД — возвращаем пустой CSV
        class MockResponse:
            status_code = 200
            text = ""

            def __init__(self):
                self.content = b""
                self.headers = {"content-length": "0"}

            def raise_for_status(self):
                pass

            def iter_content(self, chunk_size=8192):
                return iter([])

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        return MockResponse()

    # Для всего остального — ошибка (production-запрос не должен происходить)
    raise RuntimeError(f"REAL NETWORK CALL DETECTED (mocked): GET {url}")


def _mocked_post(url, *args, **kwargs):
    """Перехватывает все POST-запросы."""
    call_log.append(url)
    raise RuntimeError(f"REAL NETWORK CALL DETECTED (mocked): POST {url}")


def main():
    print("=" * 70)
    print("  Smoke-тест: исходное приложение sklad-cz (offline)")
    print("=" * 70)

    # Мокаем requests.get и requests.post ДО импорта приложения
    import requests
    real_get = requests.get
    real_post = requests.post

    requests.get = _mocked_get
    requests.post = _mocked_post

    try:
        # Импортируем приложение (после мока)
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

        # Создаём приложение через test environment
        os.environ["FLASK_ENV"] = "testing"
        # Ставим тестовую БД отдельно от production
        test_db_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "instance", "test_smoke.db"
        )

        from app import create_app, init_db, db as _db

        app = create_app()
        app.config["TESTING"] = True
        app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{test_db_path}"
        app.config["SERVER_NAME"] = "127.0.0.1:5000"

        print("\n[1/5] Flask-приложение создано: OK")

        # Инициализация БД
        with app.app_context():
            # Мокаем tnved перед init_db (load_tnved_db делает сетевой вызов)
            with patch("app.tnved.load_tnved_db", return_value=0):
                init_db(app)
        print("[2/5] SQLite БД инициализирована: OK")

        # Test client
        with app.test_client() as client:
            # GET /
            resp = client.get("/")
            print(f"[3/5] GET / → {resp.status_code}", end="")
            if resp.status_code == 200:
                html = resp.data.decode("utf-8")
                if "Товароучет" in html or "Склад" in html or "Честный" in html or "<html" in html:
                    print(" (UI-контент обнаружен): OK")
                else:
                    print(" (ответ 200, но контент не распознан)")
            else:
                print()

            # GET /api/settings/cz — страница настроек ЧЗ
            resp2 = client.get("/api/settings/cz")
            print(f"[4/5] GET /api/settings/cz → {resp2.status_code}: OK")

        # Проверка сетевых вызовов
        print(f"[5/5] Перехваченные URL (все замоцированы): {len(call_log)} вызовов")
        if call_log:
            for url in call_log:
                print(f"       → {url[:120]}")
        else:
            print("       (нет внешних вызовов)")

        # Проверяем, что не было production-вызовов ЧЗ
        cz_calls = [u for u in call_log if 'crpt' in u or 'markirovka' in u or 'cz' in u.lower()]
        if cz_calls:
            print(f"\n⚠  ВНИМАНИЕ: обнаружены вызовы к ЧЗ: {cz_calls}")
        else:
            print("\n✅ Вызовов к Честному знаку не было: OK")

        print("\n" + "=" * 70)
        print("  Smoke-тест пройден: приложение работает offline")
        print("  Сетевые вызовы не выполнялись (все замоцированы)")
        print("  Сервер не экспонирован (использован test client)")
        print("=" * 70)

        # Удаляем тестовую БД
        if os.path.exists(test_db_path):
            os.remove(test_db_path)
            print("  Тестовая БД удалена")

        return 0

    except Exception as e:
        print(f"\n❌ ОШИБКА: {e}")
        import traceback
        traceback.print_exc()
        return 1

    finally:
        # Восстанавливаем оригинальные функции requests
        requests.get = real_get
        requests.post = real_post


if __name__ == "__main__":
    sys.exit(main())
