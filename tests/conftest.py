"""
conftest.py — autouse guard, запрещающий любые реальные socket/requests вне явных mocks.
Все тесты в tests/ наследуют этот guard.
"""

import socket
import pytest

# Сохраняем оригиналы для восстановления
_original_connect = socket.socket.connect
_original_connect_ex = socket.socket.connect_ex


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    """
    Блокирует любые реальные socket-соединения.
    Тесты обязаны замокать все внешние вызовы (requests, socket).
    """
    def _blocked_connect(self, address, *args, **kwargs):
        host, port = address[:2] if isinstance(address, tuple) else (address, 0)
        raise RuntimeError(
            f"[TEST GUARD] Реальный сетевой вызов заблокирован: "
            f"connect({host}:{port}). "
            f"Замокайте все внешние зависимости."
        )

    monkeypatch.setattr(socket.socket, "connect", _blocked_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked_connect)
    yield
    monkeypatch.setattr(socket.socket, "connect", _original_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _original_connect_ex)
