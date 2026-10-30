"""Lado salón del acceso de soporte: el dueño autoriza, consulta y revoca.

Leer y crear pasan por el JWT del propio salón y por tanto por su RLS. Revocar
NO: va con service role, a propósito. `support_access_codes` e
`impersonation_sessions` no son datos del salón, son la prueba de que el acceso
fue consentido y de cuándo terminó; si el admin del salón pudiera hacer UPDATE
sobre ellas podría reescribir esa prueba, así que desde la migración 0021 su
RLS es SELECT+INSERT y SELECT respectivamente. El código en claro existe solo
en la respuesta de POST /codigo.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.core import publico
from app.core.admin_security import AdminLoginRateLimiter
from app.core.config import Settings, get_settings
from app.core.security import (
    get_current_user,
    CurrentUser,
    get_postgrest_client,
    get_service_postgrest_client,
)
from app.core.support_codes import formatear_codigo, generar_codigo, hash_codigo
from app.core.tenant_token_broker import expulsar_token_tenant
from app.db.postgrest_client import PostgrestClient, PostgrestError
from app.schemas.soporte import (
    AccesoSoporteOut,
    CodigoActivoOut,
    CodigoSoporteOut,
    SesionActivaOut,
)

router = APIRouter(prefix="/soporte", tags=["soporte"], dependencies=[publico.ADMINISTRADORA])

MINUTOS_VIDA_CODIGO = 15

# Mismo mecanismo que el rate limit del panel, con el salón como clave:
# generar códigos es barato, pero un bucle dejaría la tabla llena de basura.
_limitador = AdminLoginRateLimiter(max_fails=5, window_minutes=60)


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


async def _codigo_vivo(db: PostgrestClient, salon_id: str) -> dict | None:
    """Como mucho hay uno: generar otro revoca el anterior."""
    try:
        filas = await db.select(
            "support_access_codes",
            {
                "select": "id,expires_at,used_at,revoked_at",
                "salon_id": f"eq.{salon_id}",
                "used_at": "is.null",
                "revoked_at": "is.null",
            },
        )
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo consultar el acceso de soporte.",
        ) from e

    ahora = _ahora()
    for fila in filas:
        if datetime.fromisoformat(fila["expires_at"]) > ahora:
            return fila
    return None


async def _sesion_viva(db: PostgrestClient, salon_id: str) -> dict | None:
    try:
        filas = await db.select(
            "impersonation_sessions",
            {
                "select": "id,admin_email,expires_at,ended_at",
                "salon_id": f"eq.{salon_id}",
                "ended_at": "is.null",
            },
        )
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo consultar el acceso de soporte.",
        ) from e

    ahora = _ahora()
    for fila in filas:
        if datetime.fromisoformat(fila["expires_at"]) > ahora:
            return fila
    return None


@router.post("/codigo", response_model=CodigoSoporteOut, status_code=status.HTTP_201_CREATED)
async def crear_codigo_soporte(
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
    service_db: PostgrestClient = Depends(get_service_postgrest_client),
) -> CodigoSoporteOut:
    """Autoriza una (1) sesión de soporte. El código caduca en 15 minutos."""
    bloqueado, retry_after = _limitador.is_blocked(user.salon_id)
    if bloqueado:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Has generado demasiados códigos. Inténtalo dentro de un rato.",
            headers={"Retry-After": str(retry_after)},
        )
    _limitador.add_failure(user.salon_id)

    ahora = _ahora()
    anterior = await _codigo_vivo(db, user.salon_id)
    if anterior:
        # Un solo código vivo por salón: si el dueño pide otro, el que dictó
        # antes deja de valer aunque alguien lo tuviera apuntado.
        #
        # Con service role (ver docstring del módulo): la RLS del salón ya no
        # permite UPDATE aquí. Y como el service role tampoco aplica RLS, el
        # WHERE es la única frontera entre inquilinos: se acota por salon_id
        # además de por id. El id sale de `_codigo_vivo`, que ya filtra por
        # salón, pero esto es lo que impide que un refactor de ese SELECT abra
        # un cruce silencioso.
        await service_db.update(
            "support_access_codes",
            {"id": f"eq.{anterior['id']}", "salon_id": f"eq.{user.salon_id}"},
            {"revoked_at": ahora.isoformat()},
        )

    codigo = generar_codigo()
    expira = ahora + timedelta(minutes=MINUTOS_VIDA_CODIGO)
    try:
        await db.insert(
            "support_access_codes",
            {
                "salon_id": user.salon_id,
                "code_hash": hash_codigo(codigo),
                "expires_at": expira.isoformat(),
            },
        )
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo generar el código de soporte.",
        ) from e

    return CodigoSoporteOut(codigo=formatear_codigo(codigo), expires_at=expira.isoformat())


@router.get("/acceso", response_model=AccesoSoporteOut)
async def estado_acceso_soporte(
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> AccesoSoporteOut:
    codigo = await _codigo_vivo(db, user.salon_id)
    sesion = await _sesion_viva(db, user.salon_id)
    return AccesoSoporteOut(
        codigo_activo=CodigoActivoOut(expires_at=codigo["expires_at"]) if codigo else None,
        sesion_activa=(
            SesionActivaOut(admin_email=sesion["admin_email"], expires_at=sesion["expires_at"])
            if sesion
            else None
        ),
    )


@router.delete("/acceso", status_code=status.HTTP_204_NO_CONTENT)
async def revocar_acceso_soporte(
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
    service_db: PostgrestClient = Depends(get_service_postgrest_client),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Retira el consentimiento: mata el código pendiente y la sesión en curso.

    La sesión se corta de verdad porque cada petición en modo soporte relee esta
    fila (ver app/core/impersonation.py), no porque caduque el JWT. Pero cerrar
    la fila no basta: hay que EXPULSAR además la credencial custodiada, o el
    access_token real del dueño seguiría vivo aunque ya nadie pudiera usarlo
    (ver el comentario al pie).

    Los SELECT van con el token del salón (RLS); los dos UPDATE van con service
    role, porque el registro de consentimiento no debe poder reescribirlo el
    propio admin del salón. En ambos se acota por salon_id además de por id: sin
    RLS, el WHERE es toda la frontera que queda entre inquilinos.
    """
    ahora = _ahora().isoformat()

    codigo = await _codigo_vivo(db, user.salon_id)
    if codigo:
        await service_db.update(
            "support_access_codes",
            {"id": f"eq.{codigo['id']}", "salon_id": f"eq.{user.salon_id}"},
            {"revoked_at": ahora},
        )

    sesion = await _sesion_viva(db, user.salon_id)
    if sesion:
        # Guarda optimista: entre nuestro SELECT (_sesion_viva) y este UPDATE
        # puede colarse /admin/impersonation/end o el marcado perezoso de
        # caducidad. Repetir `ended_at=is.null` en el WHERE evita pisar su
        # motivo con "revocada_por_salon" si alguno de los dos ya cerró la
        # fila primero.
        await service_db.update(
            "impersonation_sessions",
            {
                "id": f"eq.{sesion['id']}",
                "salon_id": f"eq.{user.salon_id}",
                "ended_at": "is.null",
            },
            {"ended_at": ahora, "ended_reason": "revocada_por_salon"},
        )

        # La custodia no puede sobrevivir a la sesión que la justifica (spec
        # §4.2), exactamente igual que en /admin/impersonation/end. Cerrar la
        # fila ya corta el acceso por API —cada petición en modo soporte la
        # relee—, pero el access_token REAL del dueño seguiría residente en
        # memoria del proceso y vivo en GoTrue hasta la siguiente petición del
        # impersonador (que puede no llegar nunca) o hasta que otro acuñado
        # dispare la purga perezosa. Es justo el punto donde "revocación en
        # caliente" tiene que ser literal.
        #
        # Va después del UPDATE y sin mirar si afectó filas: si otra ruta ganó
        # la carrera, expulsar de todos modos es correcto (y barato). Es
        # best-effort de punta a punta —`expulsar_token_tenant` se traga
        # cualquier fallo de GoTrue— porque retirar el consentimiento no puede
        # depender de que GoTrue conteste. El token no se pide de vuelta aquí ni
        # se registra en ningún sitio.
        await expulsar_token_tenant(settings, str(sesion["id"]))

    return Response(status_code=status.HTTP_204_NO_CONTENT)
