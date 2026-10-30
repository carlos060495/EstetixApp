"""Freno de abuso por IP y por clave, compartido.

Vivía dentro de `routers/auth.py`. Se mueve aquí en cuanto lo necesita un
segundo router: importar un router desde otro es la forma barata de
acabar con un import circular, y `billing` no tiene por qué saber que el
alta existe.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import Lock


class AttemptRateLimiter:
    """Ventana deslizante en memoria por IP y por email.

    La usan alta y recuperación con cupos distintos. En memoria = por proceso:
    con varias réplicas el límite es por réplica, suficiente como freno de abuso
    básico pero no como control antifraude.
    """

    def __init__(
        self,
        max_attempts_per_ip: int = 12,
        max_attempts_per_email: int = 5,
        ip_window_minutes: int = 15,
        email_window_minutes: int = 30,
    ):
        self._max_ip = max_attempts_per_ip
        self._max_email = max_attempts_per_email
        self._ip_window = timedelta(minutes=ip_window_minutes)
        self._email_window = timedelta(minutes=email_window_minutes)
        self._by_ip: dict[str, list[datetime]] = {}
        self._by_email: dict[str, list[datetime]] = {}
        self._lock = Lock()

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _trim(values: list[datetime], window: timedelta, now: datetime) -> list[datetime]:
        return [ts for ts in values if now - ts <= window]

    def _retry_after(self, values: list[datetime], window: timedelta, now: datetime) -> int:
        oldest = min(values)
        seconds = int((oldest + window - now).total_seconds())
        return max(seconds, 0)

    def is_blocked(self, ip: str, email: str) -> tuple[bool, int]:
        with self._lock:
            now = self._now()

            fresh_ip = self._trim(self._by_ip.get(ip, []), self._ip_window, now)
            if fresh_ip:
                self._by_ip[ip] = fresh_ip
            elif ip in self._by_ip:
                self._by_ip.pop(ip, None)

            fresh_email = self._trim(self._by_email.get(email, []), self._email_window, now)
            if fresh_email:
                self._by_email[email] = fresh_email
            elif email in self._by_email:
                self._by_email.pop(email, None)

            retry_after = 0
            blocked = False
            if len(fresh_ip) >= self._max_ip:
                blocked = True
                retry_after = max(retry_after, self._retry_after(fresh_ip, self._ip_window, now))
            if len(fresh_email) >= self._max_email:
                blocked = True
                retry_after = max(
                    retry_after, self._retry_after(fresh_email, self._email_window, now)
                )
            return blocked, retry_after

    def add_attempt(self, ip: str, email: str) -> None:
        with self._lock:
            now = self._now()
            self._by_ip[ip] = self._trim(self._by_ip.get(ip, []), self._ip_window, now) + [now]
            self._by_email[email] = self._trim(
                self._by_email.get(email, []), self._email_window, now
            ) + [now]
