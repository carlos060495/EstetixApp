from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from threading import Lock

import jwt
from fastapi import HTTPException, status
from passlib.context import CryptContext

from app.core.config import Settings

logger = logging.getLogger("estetix.admin_security")

PASSWORD_CTX = CryptContext(schemes=["argon2"])


class AdminLoginRateLimiter:
    def __init__(self, max_fails: int = 5, window_minutes: int = 10):
        self._max_fails = max_fails
        self._window = timedelta(minutes=window_minutes)
        self._failures: dict[str, list[datetime]] = {}
        self._lock = Lock()

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    def _trim(self, ip: str, now: datetime) -> list[datetime]:
        values = self._failures.get(ip, [])
        fresh = [ts for ts in values if now - ts <= self._window]
        if fresh:
            self._failures[ip] = fresh
        elif ip in self._failures:
            self._failures.pop(ip, None)
        return fresh

    def is_blocked(self, ip: str, max_fails: int | None = None) -> tuple[bool, int]:
        """`max_fails` por llamada: para el freno cuya cifra vive en la base y la
        cambia el owner (las demos, 0080). Sin él, la del constructor."""
        limite = self._max_fails if max_fails is None else max_fails
        with self._lock:
            now = self._now()
            fresh = self._trim(ip, now)
            if len(fresh) < limite:
                return False, 0
            oldest_kept = min(fresh)
            retry_after = int((oldest_kept + self._window - now).total_seconds())
            return retry_after > 0, max(retry_after, 0)

    def add_failure(self, ip: str) -> None:
        with self._lock:
            now = self._now()
            fresh = self._trim(ip, now)
            fresh.append(now)
            self._failures[ip] = fresh

    def clear(self, ip: str) -> None:
        with self._lock:
            self._failures.pop(ip, None)


admin_login_rate_limiter = AdminLoginRateLimiter()

# El mismo mecanismo —ventana deslizante por IP— sirve para frenar cualquier
# ruta pública, no solo un login. Se le da un nombre honesto para que quien lo
# use en otro sitio no tenga que leer "fallos de login" donde en realidad
# cuenta INTENTOS. Lo usa el registro público de referidos.
VentanaPorIP = AdminLoginRateLimiter


def verify_admin_password(plain_password: str, password_hash: str) -> bool:
    try:
        return PASSWORD_CTX.verify(plain_password, password_hash)
    except (ValueError, TypeError):
        return False


def create_admin_token(settings: Settings, admin_id: str, email: str, rol: str) -> str:
    now = datetime.now(timezone.utc)
    exp = now + timedelta(minutes=settings.admin_jwt_ttl_minutes)
    payload = {
        "sub": admin_id,
        "email": email,
        "rol": rol,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
        "iss": settings.admin_jwt_issuer,
        "aud": settings.admin_jwt_audience,
    }
    return jwt.encode(payload, settings.admin_jwt_secret, algorithm="HS256")


def decode_admin_token(settings: Settings, token: str) -> dict:
    try:
        return jwt.decode(
            token,
            settings.admin_jwt_secret,
            algorithms=["HS256"],
            audience=settings.admin_jwt_audience,
            issuer=settings.admin_jwt_issuer,
        )
    except jwt.PyJWTError as e:
        logger.warning("JWT del panel rechazado: %s", e)
        # 401, no 403: el token no identifica a nadie (falta, está mal firmado o
        # caducó). 403 queda reservado para "sé quién eres, pero no puedes":
        # rechazo CSRF por Origin y rol insuficiente.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No autorizado")
