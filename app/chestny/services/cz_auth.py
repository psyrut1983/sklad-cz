"""
cz_auth — изолированная auth/token-граница для Честного Знака.

Per-profile immutable credentials, token strictly in-memory,
dependency-injected HTTP transport and signer, thread-safe cache.
"""

from __future__ import annotations

import abc
import threading
from dataclasses import dataclass
from typing import Any


# ═════════════════════════════════════════════════════════════════════════════
#  Константы
# ═════════════════════════════════════════════════════════════════════════════

PRODUCTION_API_BASE_URL = "https://markirovka.crpt.ru/api/v3/true-api"


def _ensure_nonempty_str(val: object, field_name: str) -> str:
    """Проверяет, что значение — непустая строка."""
    if not isinstance(val, str) or not val.strip():
        raise ValueError(
            f"Поле {field_name} должно быть непустой строкой, получено {type(val).__name__}"
        )
    return val


# ═════════════════════════════════════════════════════════════════════════════
#  DTO
# ═════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class CredentialsSnapshot:
    """Неизменяемый слепок credentials профиля."""

    profile_id: str
    inn: str
    certificate_thumbprint: str
    api_base_url: str

    def __post_init__(self) -> None:
        # Валидация profile_id
        if not isinstance(self.profile_id, str) or not self.profile_id.strip():
            raise ValueError("profile_id должен быть непустой строкой.")

        # Валидация INN: ровно 12 цифр
        if not isinstance(self.inn, str) or not self.inn.isdigit() or len(self.inn) != 12:
            raise ValueError("INN должен содержать ровно 12 цифр.")

        # Валидация certificate_thumbprint: ровно 40 hex, нормализация uppercase
        if not isinstance(self.certificate_thumbprint, str) or len(self.certificate_thumbprint) != 40:
            raise ValueError("certificate_thumbprint должен содержать ровно 40 hex-символов.")
        try:
            int(self.certificate_thumbprint, 16)
        except ValueError:
            raise ValueError("certificate_thumbprint должен содержать ровно 40 hex-символов.")
        normalized_tp = self.certificate_thumbprint.upper()
        if normalized_tp != self.certificate_thumbprint:
            object.__setattr__(self, "certificate_thumbprint", normalized_tp)

        # Валидация api_base_url: только production URL, нормализация trailing slash
        url = self.api_base_url.rstrip("/")
        if url != PRODUCTION_API_BASE_URL:
            raise ValueError(
                "Недопустимый api_base_url. Разрешён только production URL "
                "Честного Знака."
            )
        if url != self.api_base_url:
            object.__setattr__(self, "api_base_url", url)

    def __repr__(self) -> str:
        return f"CredentialsSnapshot(profile_id={self.profile_id!r})"


@dataclass(frozen=True)
class Challenge:
    uuid: str
    data: str


# ═════════════════════════════════════════════════════════════════════════════
#  Типизированные исключения (без токена/подписи/challenge в сообщении)
# ═════════════════════════════════════════════════════════════════════════════


class CzAuthError(Exception):
    """Базовая ошибка аутентификации ЧЗ."""
    pass


class ChallengeError(CzAuthError):
    """Ошибка получения challenge (GET /auth/key)."""
    pass


class SignatureInvalidError(CzAuthError):
    """Подпись не прошла проверку."""
    pass


class AccessDeniedError(CzAuthError):
    """Доступ запрещён."""
    pass


class TokenParseError(CzAuthError):
    """Ответ не содержит token."""
    pass


class UnauthorizedError(CzAuthError):
    """401 — токен недействителен."""
    pass


class RateLimitError(CzAuthError):
    """429 Too Many Requests."""
    pass


class ServerError(CzAuthError):
    """5xx ошибка сервера ЧЗ."""
    pass


class TimeoutError(CzAuthError):
    """Таймаут соединения."""
    pass


class NetworkError(CzAuthError):
    """Сетевая ошибка."""
    pass


class SigningError(CzAuthError):
    """Ошибка подписи (обёртка над Signer)."""
    pass


# ═════════════════════════════════════════════════════════════════════════════
#  Абстракции для DI
# ═════════════════════════════════════════════════════════════════════════════


class HttpTransport(abc.ABC):
    """HTTP-транспорт для запросов к API ЧЗ."""

    @abc.abstractmethod
    def get_json(self, url: str, headers: dict[str, str] | None = None,
                 timeout: float = 15) -> Any:
        """GET → JSON."""
        ...

    @abc.abstractmethod
    def post_json(self, url: str, payload: dict[str, Any],
                  headers: dict[str, str] | None = None,
                  timeout: float = 15) -> Any:
        """POST JSON → JSON."""
        ...


class Signer(abc.ABC):
    """Подпись данных (CAdES-BES)."""

    @abc.abstractmethod
    def sign(self, data: str, thumbprint: str) -> str:
        """Подписывает строку данных, возвращает подпись."""
        ...


# ═════════════════════════════════════════════════════════════════════════════
#  Реализация транспорта через requests
# ═════════════════════════════════════════════════════════════════════════════


class RequestsTransport(HttpTransport):
    """Реальный HTTP-транспорт через библиотеку requests."""

    def get_json(self, url: str, headers: dict[str, str] | None = None,
                 timeout: float = 15) -> Any:
        import requests
        try:
            r = requests.get(url, headers=headers, timeout=timeout)
            return self._handle_response(r)
        except requests.exceptions.Timeout:
            raise TimeoutError("Таймаут соединения с ЧЗ") from None
        except requests.exceptions.ConnectionError:
            raise NetworkError("Сетевая ошибка") from None

    def post_json(self, url: str, payload: dict[str, Any],
                  headers: dict[str, str] | None = None,
                  timeout: float = 15) -> Any:
        import requests
        try:
            r = requests.post(url, json=payload, headers=headers, timeout=timeout)
            return self._handle_response(r)
        except requests.exceptions.Timeout:
            raise TimeoutError("Таймаут соединения с ЧЗ") from None
        except requests.exceptions.ConnectionError:
            raise NetworkError("Сетевая ошибка") from None

    def _handle_response(self, r: Any) -> Any:
        if r.status_code == 429:
            raise RateLimitError("Превышен лимит запросов к ЧЗ")
        if 500 <= r.status_code < 600:
            raise ServerError(f"Ошибка сервера ЧЗ")
        if r.status_code == 403:
            try:
                err = r.json().get("error_message", "") or r.text
            except Exception:
                err = r.text
            if "Подпись невалидна" in err or "nevalidna" in err.lower():
                raise SignatureInvalidError("Подпись не прошла проверку")
            if "Отсутствует доступ" in err or "access" in err.lower():
                raise AccessDeniedError("Доступ запрещён")
            raise AccessDeniedError("Доступ запрещён")
        if r.status_code == 401:
            raise UnauthorizedError("Токен недействителен")
        r.raise_for_status()
        try:
            return r.json()
        except Exception:
            raise TokenParseError("Ответ ЧЗ не является JSON") from None


# ═════════════════════════════════════════════════════════════════════════════
#  Production signer (lazy адаптер над legacy _sign_data)
# ═════════════════════════════════════════════════════════════════════════════


class LegacySigner(Signer):
    """Адаптер над существующей _sign_data из cz_api.

    Gap (Stage 7): заменить на выделенный CAdES-BES модуль без
    Windows COM/linux openssl в этом файле.
    """

    def sign(self, data: str, thumbprint: str) -> str:
        from app.cz_api import _sign_data
        return _sign_data(data, thumbprint)


# ═════════════════════════════════════════════════════════════════════════════
#  Per-profile auth client
# ═════════════════════════════════════════════════════════════════════════════


class CzAuthClient:
    """Auth-клиент для одного профиля.

    Токен строго in-memory, никогда не сохраняется в SQLAlchemy/log/repr.
    Thread-safe: параллельные вызовы get_token не делают двойную auth.
    """

    def __init__(
        self,
        credentials: CredentialsSnapshot,
        transport: HttpTransport,
        signer: Signer,
    ) -> None:
        self._credentials = credentials
        self._transport = transport
        self._signer = signer
        self._lock = threading.Lock()
        self._token: str | None = None

    @property
    def credentials(self) -> CredentialsSnapshot:
        return self._credentials

    def get_token(self) -> str:
        """Возвращает текущий токен или запрашивает новый (thread-safe)."""
        if self._token is not None:
            return self._token
        return self._authenticate()

    def reset_token(self) -> None:
        """Сбрасывает кэш токена. Следующий get_token() сделает новый запрос."""
        with self._lock:
            self._token = None

    def force_authenticate(self) -> str:
        """Принудительная аутентификация (сбрасывает кэш)."""
        self.reset_token()
        return self._authenticate()

    def _authenticate(self) -> str:
        """Challenge → sign → POST → token."""
        with self._lock:
            if self._token is not None:
                return self._token

            challenge = self._get_challenge()
            try:
                signature = self._signer.sign(
                    challenge.data, self._credentials.certificate_thumbprint
                )
            except Exception:
                raise SigningError(
                    "Ошибка подписи запроса аутентификации"
                ) from None

            # Валидация результата signer: только непустая строка
            if not isinstance(signature, str) or not signature.strip():
                raise SigningError(
                    "Ошибка подписи запроса аутентификации"
                )

            payload: dict[str, Any] = {
                "uuid": challenge.uuid,
                "data": signature,
                "unitedToken": True,
            }
            if self._credentials.inn:
                payload["inn"] = self._credentials.inn

            base = self._credentials.api_base_url.rstrip("/")
            headers = {"Content-Type": "application/json", "accept": "application/json"}
            resp = self._transport.post_json(
                f"{base}/auth/simpleSignIn", payload, headers=headers
            )

            if not isinstance(resp, dict):
                raise TokenParseError("Ответ /auth/simpleSignIn не является JSON-объектом")

            try:
                token = _ensure_nonempty_str(
                    resp.get("token") or resp.get("uuidToken"), "token/uuidToken"
                )
            except ValueError as e:
                raise TokenParseError(str(e)) from None

            self._token = token
            return self._token

    def _get_challenge(self) -> Challenge:
        base = self._credentials.api_base_url.rstrip("/")
        headers = {"accept": "application/json"}
        resp = self._transport.get_json(f"{base}/auth/key", headers=headers)

        if not isinstance(resp, dict):
            raise ChallengeError("Ответ /auth/key не является JSON-объектом")

        try:
            chal_uuid = _ensure_nonempty_str(resp.get("uuid"), "uuid")
            chal_data = _ensure_nonempty_str(resp.get("data"), "data")
        except ValueError as e:
            raise ChallengeError(str(e)) from None

        return Challenge(uuid=chal_uuid, data=chal_data)

    def __repr__(self) -> str:
        return (
            f"CzAuthClient(profile_id={self._credentials.profile_id!r})"
        )


# ═════════════════════════════════════════════════════════════════════════════
#  Per-app registry
# ═════════════════════════════════════════════════════════════════════════════


class CzAuthRegistry:
    """Per-app/per-Flask registry auth-клиентов по profile_id.

    Два профиля никогда не разделяют token.
    Изменение credential snapshot инвалидирует старый клиент.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._clients: dict[str, CzAuthClient] = {}

    def get_or_create(
        self,
        credentials: CredentialsSnapshot,
        transport: HttpTransport | None = None,
        signer: Signer | None = None,
    ) -> CzAuthClient:
        """Возвращает существующий или создаёт новый клиент для профиля.

        Если credentials изменились, старый клиент заменяется.
        """
        with self._lock:
            existing = self._clients.get(credentials.profile_id)
            if existing is not None:
                old = existing.credentials
                if (old.inn == credentials.inn
                        and old.certificate_thumbprint == credentials.certificate_thumbprint
                        and old.api_base_url == credentials.api_base_url):
                    return existing
                del self._clients[credentials.profile_id]

            client = CzAuthClient(
                credentials=credentials,
                transport=transport or RequestsTransport(),
                signer=signer or LegacySigner(),
            )
            self._clients[credentials.profile_id] = client
            return client

    def get(self, profile_id: str) -> CzAuthClient | None:
        with self._lock:
            return self._clients.get(profile_id)

    def remove(self, profile_id: str) -> None:
        with self._lock:
            self._clients.pop(profile_id, None)

    def clear(self) -> None:
        with self._lock:
            self._clients.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._clients)
