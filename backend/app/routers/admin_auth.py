from __future__ import annotations

from datetime import datetime, timezone

import pyotp
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.core.cookies_de_sesion import COOKIE_PANEL, borrar_cookie_de_sesion, poner_cookie_de_sesion
from app.core import publico
from app.core.admin_audit import append_admin_log, client_ip
from app.core.admin_security import (
    admin_login_rate_limiter,
    create_admin_token,
    verify_admin_password,
)
from app.core.config import Settings, get_settings
from app.db.postgrest_client import PostgrestClient, PostgrestError
from app.schemas.admin_auth import AdminLoginIn, AdminMeOut

router = APIRouter(prefix="/admin", tags=["admin-auth"])


def _service_db(settings: Settings) -> PostgrestClient:
    return PostgrestClient(settings.supabase_url, settings.supabase_secret_key)


@router.post("/login", dependencies=[publico.CUALQUIERA], status_code=status.HTTP_204_NO_CONTENT)
async def admin_login(
    payload: AdminLoginIn,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> Response:
    ip = client_ip(request)
    blocked, retry_after = admin_login_rate_limiter.is_blocked(ip)
    if blocked:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Demasiados intentos fallidos. Inténtalo más tarde.",
            headers={"Retry-After": str(retry_after)},
        )

    db = _service_db(settings)
    try:
        rows = await db.select(
            "admin_users",
            {
                "select": "id,email,nombre,rol,password_hash,totp_secret,totp_enabled,ultimo_acceso",
                "email": f"eq.{payload.email.lower()}",
                "limit": "1",
            },
        )
    except PostgrestError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo validar el acceso admin",
        )

    if not rows:
        admin_login_rate_limiter.add_failure(ip)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciales inválidas")

    admin = rows[0]
    valid_password = verify_admin_password(payload.password, admin["password_hash"])
    totp_secret = admin.get("totp_secret")
    valid_totp = bool(
        admin.get("totp_enabled")
        and totp_secret
        and pyotp.TOTP(totp_secret).verify(payload.totp_code, valid_window=1)
    )

    if not (valid_password and valid_totp):
        admin_login_rate_limiter.add_failure(ip)
        try:
            await append_admin_log(
                db,
                admin_id=admin["id"],
                accion="admin_login_failed",
                ip=ip,
                detalle={"reason": "invalid_credentials"},
            )
        except PostgrestError:
            # Nunca exponer detalles internos al login.
            pass
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciales inválidas")

    admin_login_rate_limiter.clear(ip)

    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        await db.update("admin_users", {"id": f"eq.{admin['id']}"}, {"ultimo_acceso": now_iso})
        await append_admin_log(db, admin_id=admin["id"], accion="admin_login_success", ip=ip)
    except PostgrestError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo registrar el acceso admin",
        )

    token = create_admin_token(settings, admin_id=admin["id"], email=admin["email"], rol=admin["rol"])
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    # Atributos en `core/cookies_de_sesion.py`: mismo sitio que el frontend.
    poner_cookie_de_sesion(response, COOKIE_PANEL, token, settings.admin_jwt_ttl_minutes * 60)
    return response


@router.post("/logout", dependencies=[publico.PLATAFORMA], status_code=status.HTTP_204_NO_CONTENT)
async def admin_logout(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> Response:
    claims = request.state.admin_claims
    db = _service_db(settings)
    ip = client_ip(request)
    try:
        await append_admin_log(db, admin_id=claims["sub"], accion="admin_logout", ip=ip)
    except PostgrestError:
        pass
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    # Con los MISMOS atributos con los que se emitió: si no, el navegador no la
    # reconoce como la misma y sobreviviría hasta su `Max-Age`.
    borrar_cookie_de_sesion(response, COOKIE_PANEL)
    return response


@router.get("/me", dependencies=[publico.PLATAFORMA], response_model=AdminMeOut)
async def admin_me(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> AdminMeOut:
    claims = request.state.admin_claims
    db = _service_db(settings)
    try:
        rows = await db.select(
            "admin_users",
            {
                "select": "id,email,nombre,rol,ultimo_acceso",
                "id": f"eq.{claims['sub']}",
                "limit": "1",
            },
        )
    except PostgrestError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo cargar el usuario admin",
        )

    if not rows:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No autorizado")

    return AdminMeOut(**rows[0])
