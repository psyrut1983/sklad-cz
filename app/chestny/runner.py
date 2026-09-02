"""
chestny.runner — точка входа для приложения Честного Знака.

Запускает только новую factory, без старых складских маршрутов.
Жёстко host=127.0.0.1, port configurable, debug=False, use_reloader=False.
"""

from __future__ import annotations

import argparse
import sys

from app.chestny.factory import create_cz_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Честный Знак — приложение")
    parser.add_argument("--port", type=int, default=5100, help="Порт (по умолчанию 5100)")
    args = parser.parse_args()

    app = create_cz_app()

    print("=" * 60)
    print("  Честный Знак — минимальное приложение")
    print(f"  http://127.0.0.1:{args.port}")
    print("=" * 60)

    app.run(host="127.0.0.1", port=args.port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
