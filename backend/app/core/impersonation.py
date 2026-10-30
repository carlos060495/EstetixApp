"""Sesión de soporte: el JWT propio que viaja en la cookie httpOnly.

Este token NO sirve para hablar con Supabase: solo lo entiende FastAPI. El
token real del salón vive en el backend (app/core/tenant_token_broker.py) y
nunca sale de ahí. Emisor y audiencia son distintos de los del panel para que
ninguno de los dos tokens valga en el sitio del otro.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import jwt
from fastapi import HTTPException, status
from pydantic import BaseModel

from app.core.cookies_de_sesion import COOKIE_SOPORTE
from app.core.config import Settings

logger = logging.getLogger("estetix.impersonation")

_log = logging.getLogger(__name__)

# Ver `core/cookies_de_sesion.py`: el nombre y los atributos, en un solo sitio.
COOKIE_IMPERSONACION = COOKIE_SOPORTE
MINUTOS_SESION = 30
ISS_IMPERSONACION = "estetixapp-impersonation"
AUD_IMPERSONACION = "estetixapp-tenant-support"

LONGITUD_MINIMA_SECRETO = 32


class ImpersonationContext(BaseModel):
    """Lo que sabe el backend mientras atiende una petición en modo soporte."""

    sid: str
    admin_id: str
    admin_email: str
    salon_id: str
    tenant_auth_user_id: str
    tenant_email: str
    expires_at: str


def secreto_impersonacion(settings: Settings) -> str:
    """Devuelve el secreto o corta con 503.

    Un secreto vacío, corto o compartido con el del panel no se "arregla"
    emitiendo tokens igualmente: desactiva la función. Suplantar a un salón da
    acceso a los datos personales de sus clientes.

    El 503 solo lo ve quien llama, así que además se deja una línea CRITICAL
    con el motivo concreto (spec §7.1): sin ella, un despliegue mal configurado
    falla EN SILENCIO para operaciones —todo el modo soporte responde 503 y no
    hay nada en el servidor que explique por qué—.
    """
    secreto = settings.jwt_impersonation_secret

    # El VALOR del secreto no entra aquí jamás, ni truncado: estos mensajes
    # acaban en el log del servidor, y unos primeros caracteres ya recortan el
    # espacio de búsqueda de quien los lea. Solo el nombre de la variable y el
    # motivo, que es justo lo que operaciones necesita para arreglarlo.
    motivo: str | None = None
    if not secreto:
        motivo = "JWT_IMPERSONATION_SECRET está vacío o no está definido"
    elif len(secreto) < LONGITUD_MINIMA_SECRETO:
        motivo = f"JWT_IMPERSONATION_SECRET tiene menos de {LONGITUD_MINIMA_SECRETO} caracteres"
    elif secreto == settings.admin_jwt_secret:
        motivo = "JWT_IMPERSONATION_SECRET es idéntico a ADMIN_JWT_SECRET"

    if motivo:
        _log.critical(
            "Modo soporte DESACTIVADO (503 en todas sus rutas): %s. "
            "Corrige la variable de entorno y reinicia el servicio.",
            motivo,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="El modo soporte no está configurado.",
        )
    return secreto


def crear_token_impersonacion(
    settings: Settings,
    *,
    sid: str,
    admin_id: str,
    admin_email: str,
    salon_id: str,
    code_id: str,
    expira: datetime,
) -> str:
    """Acuña la cookie de la sesión de soporte.

    `expira` DEBE ser timezone-aware: `.timestamp()` sobre un datetime naive lo
    interpreta en la zona local del servidor y desplazaría el `exp` sin avisar.
    """
    if expira.tzinfo is None or expira.tzinfo.utcoffset(expira) is None:
        raise ValueError("`expira` debe ser timezone-aware (UTC).")
    return jwt.encode(
        {
            "sub": admin_id,
            "email": admin_email,
            "salon_id": salon_id,
            "sid": sid,
            # El consentimiento concreto que abrió la sesión, para que el token
            # diga por sí solo con qué código se entró.
            "code_id": code_id,
            "iat": int(datetime.now(timezone.utc).timestamp()),
            "exp": int(expira.timestamp()),
            "iss": ISS_IMPERSONACION,
            "aud": AUD_IMPERSONACION,
        },
        secreto_impersonacion(settings),
        algorithm="HS256",
    )


def decodificar_token_impersonacion(settings: Settings, token: str) -> dict:
    try:
        return jwt.decode(
            token,
            secreto_impersonacion(settings),
            algorithms=["HS256"],
            audience=AUD_IMPERSONACION,
            issuer=ISS_IMPERSONACION,
        )
    except jwt.PyJWTError as e:
        logger.warning("Cookie de soporte rechazada: %s", e)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="La sesión de soporte no es válida."
        )


_COLUMNAS_SESION = (
    "id,admin_id,admin_email,salon_id,tenant_auth_user_id,tenant_email,expires_at,ended_at"
)


async def cargar_contexto_sesion(settings: Settings, db, claims: dict) -> ImpersonationContext:
    """Relee la sesión en BD y corta si ya no está viva.

    Se hace en CADA petición y sin caché a propósito: es lo que convierte el
    botón "Revocar" del dueño en algo inmediato. Un JWT de 30 minutos no se
    puede invalidar por sí solo.

    Detectar que la sesión ha muerto no basta con devolver 401: hay que
    EXPULSAR el token del salón de la custodia (spec §4.2). Si solo se
    rechazase la petición, la credencial del dueño seguiría en memoria del
    proceso y viva en GoTrue.
    """
    from datetime import datetime, timezone

    # Import local a propósito: tenant_token_broker importa MINUTOS_SESION de
    # este módulo, así que a nivel de módulo esto sería un ciclo real.
    from app.core.tenant_token_broker import expulsar_token_tenant
    from app.db.postgrest_client import PostgrestError

    sid = claims.get("sid")
    try:
        filas = await db.select(
            "impersonation_sessions",
            {"select": _COLUMNAS_SESION, "id": f"eq.{sid}", "limit": "1"},
        )
    except PostgrestError as e:
        # "No sé" no es "está muerta": un fallo de PostgREST no debe expulsar
        # el token, o un hipo de red obligaría a reacuñar la sesión entera.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo verificar la sesión de soporte.",
        ) from e

    if not filas:
        await expulsar_token_tenant(settings, sid)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="La sesión de soporte no existe."
        )

    fila = filas[0]
    ahora = datetime.now(timezone.utc)

    if fila.get("ended_at"):
        await expulsar_token_tenant(settings, sid)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="La sesión de soporte ha terminado."
        )

    if datetime.fromisoformat(fila["expires_at"]) <= ahora:
        # Marcado perezoso: nadie vigila el reloj, así que la primera petición
        # que llega tarde es la que deja constancia de por qué murió.
        #
        # Guarda optimista (`ended_at=is.null` repetido en el WHERE): entre el
        # SELECT de arriba y este UPDATE puede colarse /admin/impersonation/end
        # o `revocar_acceso_soporte` (soporte.py). Si alguno de los dos ya
        # cerró la fila, este UPDATE no debe pisar su motivo más preciso
        # ("salida"/"revocada_por_salon") con "caducada".
        try:
            await db.update(
                "impersonation_sessions",
                {"id": f"eq.{sid}", "ended_at": "is.null"},
                {"ended_at": ahora.isoformat(), "ended_reason": "caducada"},
            )
        except PostgrestError:
            pass
        await expulsar_token_tenant(settings, sid)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="La sesión de soporte ha caducado."
        )

    if fila["salon_id"] != claims.get("salon_id"):
        # El token va firmado, así que esto no debería pasar nunca. Si pasa,
        # algo se ha cruzado y lo correcto es no atender la petición.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="La sesión de soporte no es válida."
        )

    return ImpersonationContext(
        sid=str(fila["id"]),
        admin_id=str(fila["admin_id"]),
        admin_email=fila["admin_email"],
        salon_id=str(fila["salon_id"]),
        tenant_auth_user_id=str(fila["tenant_auth_user_id"]),
        tenant_email=fila["tenant_email"],
        expires_at=fila["expires_at"],
    )
