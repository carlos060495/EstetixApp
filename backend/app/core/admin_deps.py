"""Dependencias del panel Super Admin.

La AUTENTICACIÓN la resuelve el middleware `admin_auth_guard` (app/main.py), que
verifica la cookie `__Host-sa-access-token` y deja los claims en
`request.state.admin_claims`. Este módulo construye encima la AUTORIZACIÓN por
rol, y expone el acceso a BD como dependencia para que los tests puedan
sustituirlo con `app.dependency_overrides`.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from pydantic import BaseModel

from app.core.config import Settings, get_settings
from app.db.postgrest_client import PostgrestClient


class AdminClaims(BaseModel):
    sub: str
    email: str
    rol: str


def get_admin_claims(request: Request) -> AdminClaims:
    # El middleware ya ha corrido para toda ruta bajo /admin salvo /admin/login.
    # Si aun así no hay claims, la ruta está mal montada: mejor 401 explícito que
    # un AttributeError con traza 500.
    claims = getattr(request.state, "admin_claims", None)
    if not claims:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No autorizado")
    return AdminClaims(**claims)


def require_admin_roles(*roles: str):
    """Autorización por rol del panel: owner, support o viewer.

    Devuelve 403, nunca 401: quien llega aquí ya está autenticado; lo que falla
    es el permiso. `viewer` es de solo lectura, así que no debe aparecer en los
    roles de ningún endpoint mutable.
    """

    def _dependency(claims: AdminClaims = Depends(get_admin_claims)) -> AdminClaims:
        if claims.rol not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tu rol no permite esta acción.",
            )
        return claims

    return _dependency


def get_admin_db(settings: Settings = Depends(get_settings)) -> PostgrestClient:
    """Cliente PostgREST con la secret key (service role).

    El super admin no pertenece a ningún salón, así que la RLS por `salon_id` no
    le dejaría ver nada: por diseño el panel consulta con service role, y el
    control de acceso lo hacen el guard y `require_admin_roles`.
    """
    return PostgrestClient(settings.supabase_url, settings.supabase_secret_key)
