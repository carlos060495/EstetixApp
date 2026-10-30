"""Rastro de auditoría del panel Super Admin.

`admin_logs` es append-only a nivel de BD (migración 0004: revoke UPDATE/DELETE
más triggers que abortan la operación), así que lo que se escriba aquí no se
puede reescribir después — ni siquiera desde el propio panel.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import Request

from app.db.postgrest_client import PostgrestClient


def client_ip(request: Request) -> str:
    """IP del cliente. Railway va detrás de proxy, así que X-Forwarded-For es la
    única fuente fiable; el primer valor es el cliente original."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


async def append_admin_log(
    db: PostgrestClient,
    admin_id: str,
    accion: str,
    ip: str,
    target_salon_id: str | None = None,
    detalle: dict | None = None,
) -> str:
    """Devuelve el id de la fila: con él se completa después su resultado
    (`admin_logs_resultado`, 0091). Se pone aquí para no depender de que
    PostgREST devuelva la fila."""
    fila: dict = {
        "id": str(uuid4()),
        "admin_id": admin_id,
        "accion": accion,
        "ip": ip,
        "detalle": detalle or {},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if target_salon_id:
        fila["target_salon_id"] = target_salon_id
    await db.insert("admin_logs", fila)
    return fila["id"]


async def completar_admin_log(db: PostgrestClient, admin_log_id: str, detalle: dict) -> None:
    """Cómo acabó lo apuntado (0091). Una vez por apunte, y no se puede cambiar."""
    await db.insert("admin_logs_resultado", {"admin_log_id": admin_log_id, "detalle": detalle})
