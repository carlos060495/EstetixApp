"""Inicio de una sesión de soporte, desde el panel Super Admin.

El token real del salón NO se acuña aquí: lo pide la primera petición atendida
en modo soporte y se queda en la custodia del backend
(app/core/tenant_token_broker.py). Lo único que sale hacia el navegador es la
cookie httpOnly firmada por nosotros, que no vale para hablar con Supabase.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.core.cookies_de_sesion import borrar_cookie_de_sesion, poner_cookie_de_sesion
from app.core import publico
from app.core.admin_audit import append_admin_log, client_ip, completar_admin_log
from app.core.admin_deps import AdminClaims, get_admin_db, require_admin_roles
from app.core.admin_security import AdminLoginRateLimiter
from app.core.config import Settings, get_settings
from app.core.emails import construir_email_branded, enviar_email_branded
from app.core.impersonation import (
    COOKIE_IMPERSONACION,
    MINUTOS_SESION,
    crear_token_impersonacion,
    decodificar_token_impersonacion,
    secreto_impersonacion,
)
import anyio.to_thread

from app.core.support_codes import normalizar_codigo, verificar_codigo
from app.core.tenant_token_broker import expulsar_token_tenant
from app.db.postgrest_client import PostgrestClient, PostgrestError
from app.schemas.impersonation import ImpersonacionOut, ImpersonarIn

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin-impersonation"], dependencies=[publico.PLATAFORMA])

ADMIN_MUTABLE = ("owner", "support")

# Fuerza bruta contra el código: 5 fallos por IP en 10 min. Además, 3 fallos
# sobre un mismo código lo queman (el dueño tendrá que dictar otro).
_limitador = AdminLoginRateLimiter(max_fails=5, window_minutes=10)
MAX_FALLOS_POR_CODIGO = 3


def _ahora() -> datetime:
    # Siempre timezone-aware: `crear_token_impersonacion` calcula el `exp` con
    # `.timestamp()`, que sobre un datetime naive lo interpretaría en la zona
    # local del servidor y desplazaría la caducidad sin avisar.
    return datetime.now(timezone.utc)


async def _salon(db: PostgrestClient, salon_id: str) -> dict:
    try:
        filas = await db.select(
            "salons",
            {"select": "id,nombre_comercial,email", "id": f"eq.{salon_id}", "limit": "1"},
        )
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudo cargar el salón."
        ) from e
    if not filas:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Salón no encontrado.")
    return filas[0]


async def _usuario_admin(db: PostgrestClient, salon: dict) -> dict:
    """Usuario del salón a suplantar: el admin enlazado con Supabase Auth.

    Si hay varios, gana el que coincide con el email del salón; si ninguno
    coincide, el más antiguo. Cualquier regla vale mientras sea DETERMINISTA:
    lo que no puede pasar es que dos sesiones de soporte del mismo salón entren
    como usuarios distintos y el rastro no cuadre.
    """
    try:
        filas = await db.select(
            "users",
            {
                "select": "id,email,auth_user_id,created_at",
                "salon_id": f"eq.{salon['id']}",
                "rol": "eq.admin",
                "order": "created_at.asc",
            },
        )
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudieron cargar los usuarios del salón.",
        ) from e

    enlazados = [f for f in filas if f.get("auth_user_id")]
    if not enlazados:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Este salón no tiene un usuario administrador enlazado.",
        )
    for fila in enlazados:
        if salon.get("email") and fila.get("email") == salon["email"]:
            return fila
    return enlazados[0]


async def _sesion_viva(db: PostgrestClient, salon_id: str) -> dict | None:
    try:
        filas = await db.select(
            "impersonation_sessions",
            {
                "select": "id,expires_at,ended_at",
                "salon_id": f"eq.{salon_id}",
                "ended_at": "is.null",
            },
        )
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo comprobar si ya hay una sesión de soporte.",
        ) from e
    ahora = _ahora()
    for fila in filas:
        if datetime.fromisoformat(fila["expires_at"]) > ahora:
            return fila
    return None


def _invalido() -> HTTPException:
    """Un solo mensaje para "no existe", "caducado", "ya usado" y "de otro
    salón": distinguirlos convertiría el endpoint en un oráculo."""
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="Código no válido o caducado."
    )


async def _codigo_valido(db: PostgrestClient, salon_id: str, tecleado: str) -> dict:
    """Busca el código vivo de ESTE salón que case con lo tecleado. 401 si no hay.

    Solo comprueba: marcarlo usado es un paso aparte (`_marcar_codigo_usado`)
    que se deja para el final, cuando ya se sabe que la sesión va a abrirse. El
    código en claro no se registra en ningún sitio, tampoco al fallar.
    """
    codigo = normalizar_codigo(tecleado)
    invalido = _invalido()

    try:
        filas = await db.select(
            "support_access_codes",
            {
                "select": "id,code_hash,expires_at,intentos_fallidos",
                "salon_id": f"eq.{salon_id}",
                "used_at": "is.null",
                "revoked_at": "is.null",
            },
        )
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudo validar el código."
        ) from e

    ahora = _ahora()
    vivos = [f for f in filas if datetime.fromisoformat(f["expires_at"]) > ahora]

    # UNO A UNO Y FUERA DEL BUCLE. Son varios argon2 seguidos —uno por código
    # vivo— y dentro de un `next()` corrían todos pegados, parando el backend.
    encontrado = None
    for f in vivos:
        if await anyio.to_thread.run_sync(verificar_codigo, codigo, f["code_hash"]):
            encontrado = f
            break
    if encontrado is None:
        # Cada fallo acerca el código vivo a la hoguera: quien lo esté
        # adivinando se queda sin objetivo antes de acertar.
        for fila in vivos:
            fallos = (fila.get("intentos_fallidos") or 0) + 1
            cambios: dict = {"intentos_fallidos": fallos}
            if fallos >= MAX_FALLOS_POR_CODIGO:
                cambios["revoked_at"] = ahora.isoformat()
            try:
                # `salon_id` además del `id`, como en soporte.py: el panel
                # escribe con service role, así que no hay RLS que acote al
                # inquilino y el WHERE es toda la frontera. El id viene del
                # SELECT de arriba, que ya filtra por salón, pero repetirlo es
                # lo que impide que un refactor de ese SELECT abra un cruce.
                await db.update(
                    "support_access_codes",
                    {"id": f"eq.{fila['id']}", "salon_id": f"eq.{salon_id}"},
                    cambios,
                )
            except PostgrestError:
                # Contar el intento es best-effort; cerrar la puerta no. Si este
                # error escapase: el 401 se volvería 500, `add_failure` no
                # llegaría a correr —la defensa antifuerza bruta fallaría
                # ABIERTA— y el 500 solo saldría cuando el salón tiene código
                # vivo, que es justo el oráculo que evita el mensaje único.
                pass
        raise invalido

    return encontrado


async def _marcar_codigo_usado(
    db: PostgrestClient, codigo_id: str, salon_id: str, admin_id: str
) -> None:
    """Quema el código. Es el ÚLTIMO portazo, justo antes de abrir la sesión.

    Un solo uso, resuelto de forma optimista: el filtro `used_at=is.null` hace
    que gane una sola petición aunque dos lleguen a la vez con el mismo código.
    Si no devuelve fila, otra se adelantó y aquí se sale con 401 —nunca dos
    sesiones con el mismo consentimiento—.

    `salon_id` va en el WHERE por lo mismo que en soporte.py: esto escribe con
    service role, sin RLS que acote al inquilino.
    """
    try:
        afectadas = await db.update(
            "support_access_codes",
            {"id": f"eq.{codigo_id}", "salon_id": f"eq.{salon_id}", "used_at": "is.null"},
            {"used_at": _ahora().isoformat(), "used_by_admin_id": admin_id},
        )
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudo validar el código."
        ) from e
    if not afectadas:
        raise _invalido()


async def _anotar_intento_fallido(
    db: PostgrestClient,
    error: HTTPException,
    admin: AdminClaims,
    ip: str,
    salon_id: str,
) -> None:
    """Un 401 cuenta para el límite por IP y deja rastro; un 502 no.

    En el rastro no entra ni el código ni por qué falló en concreto: `admin_logs`
    es append-only y el motivo fino solo serviría para reconstruir intentos.
    """
    if error.status_code != status.HTTP_401_UNAUTHORIZED:
        return
    _limitador.add_failure(ip)
    try:
        await append_admin_log(
            db,
            admin_id=admin.sub,
            accion="impersonation_denied",
            ip=ip,
            target_salon_id=salon_id,
            detalle={"motivo": "codigo_invalido"},
        )
    except PostgrestError:
        pass


async def _avisar_al_dueno(settings: Settings, salon: dict, admin_email: str) -> None:
    """Aviso al dueño: que se entre en su cuenta nunca puede ser silencioso.

    Se dice la DURACIÓN, no la hora de fin: el backend trabaja en UTC y aquí no
    se sabe el huso del dueño, así que un "caduca a las 14:35" le llegaría dos
    horas corrido en verano. La hora exacta la pinta /soporte, que sí está en el
    navegador del dueño.

    Que haya destinatario lo comprueba el llamador: aquí no se puede callar un
    "no había a quién avisar", porque eso es justo lo que el rastro tiene que
    contar.
    """
    html = construir_email_branded(
        title="Un agente de soporte ha entrado en tu cuenta",
        intro=(
            f"Has autorizado con un código el acceso de {admin_email} a "
            f"{salon['nombre_comercial']}. El acceso caduca solo dentro de "
            f"{MINUTOS_SESION} minutos, y puedes retirarlo antes desde "
            "Soporte > Acceso técnico remoto."
        ),
        cta_label="Ver el acceso en mi cuenta",
        action_link=f"{settings.frontend_base_url.rstrip('/')}/soporte",
        footer_note=(
            "Si no has autorizado este acceso, entra en tu cuenta y pulsa Revocar, "
            "y avísanos respondiendo a este correo."
        ),
    )
    await enviar_email_branded(
        settings, salon["email"], "Acceso de soporte a tu cuenta de EstetixApp", html
    )


@router.post("/tenants/{tenant_id}/impersonate", response_model=ImpersonacionOut)
async def iniciar_impersonacion(
    tenant_id: UUID,
    payload: ImpersonarIn,
    request: Request,
    response: Response,
    admin: AdminClaims = Depends(require_admin_roles(*ADMIN_MUTABLE)),
    db: PostgrestClient = Depends(get_admin_db),
    settings: Settings = Depends(get_settings),
) -> ImpersonacionOut:
    secreto_impersonacion(settings)  # 503 si el despliegue no está configurado

    ip = client_ip(request)
    bloqueado, retry_after = _limitador.is_blocked(ip)
    if bloqueado:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Demasiados intentos. Inténtalo más tarde.",
            headers={"Retry-After": str(retry_after)},
        )

    salon_id = str(tenant_id)
    salon = await _salon(db, salon_id)

    # El código manda y va primero: cualquier motivo por el que no valga —no
    # existe, caducó, ya se usó o es de otro salón— sale por el mismo 401. Si
    # los 409 de más abajo se comprobasen antes, el endpoint contaría lo que
    # pasa dentro de una cuenta ajena a quien solo tiene un código robado.
    try:
        codigo = await _codigo_valido(db, salon_id, payload.codigo)
    except HTTPException as e:
        await _anotar_intento_fallido(db, e, admin, ip, salon_id)
        raise

    if await _sesion_viva(db, salon_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ya hay una sesión de soporte abierta para este salón.",
        )

    usuario = await _usuario_admin(db, salon)

    # Lo último antes de abrir: así un 409 de los de arriba no quema el código
    # que el dueño acaba de dictar por teléfono.
    try:
        await _marcar_codigo_usado(db, codigo["id"], salon_id, admin.sub)
    except HTTPException as e:
        await _anotar_intento_fallido(db, e, admin, ip, salon_id)
        raise

    ahora = _ahora()
    expira = ahora + timedelta(minutes=MINUTOS_SESION)
    sid = str(uuid4())

    try:
        await db.insert(
            "impersonation_sessions",
            {
                "id": sid,
                "admin_id": admin.sub,
                "admin_email": admin.email,
                "salon_id": salon_id,
                "code_id": codigo["id"],
                "tenant_auth_user_id": usuario["auth_user_id"],
                "tenant_email": usuario["email"],
                "started_at": ahora.isoformat(),
                "expires_at": expira.isoformat(),
                # Explícito: la sesión nace viva, y es esta columna la que cada
                # petición en modo soporte relee para saber si sigue estándolo.
                "ended_at": None,
            },
        )
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo abrir la sesión de soporte.",
        ) from e

    token = crear_token_impersonacion(
        settings,
        sid=sid,
        admin_id=admin.sub,
        admin_email=admin.email,
        salon_id=salon_id,
        code_id=str(codigo["id"]),
        expira=expira,  # aware (UTC): ver _ahora()
    )

    detalle: dict = {"sid": sid, "code_id": str(codigo["id"]), "expires_at": expira.isoformat()}
    try:
        apunte = await append_admin_log(
            db,
            admin_id=admin.sub,
            accion="impersonation_start",
            ip=ip,
            target_salon_id=salon_id,
            detalle=detalle,
        )
    except Exception as e:  # noqa: BLE001 - también un corte de red, que no es PostgrestError
        # SIN SU LÍNEA NO SE ENTRA (20-10-2026): entrar en la cuenta de
        # alguien sin rastro es peor que no poder entrar. La sesión nace y
        # muere aquí, sin cookie, así que nadie llega a usarla.
        try:
            await db.update(
                "impersonation_sessions",
                {"id": f"eq.{sid}", "ended_at": "is.null"},
                {"ended_at": _ahora().isoformat(), "ended_reason": "sin_rastro"},
            )
        except Exception:  # noqa: BLE001 - sin cookie la sesión ya es inservible
            pass
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No se ha podido dejar constancia de la entrada, así que no se ha entrado. "
            "El código ya está usado: pide uno nuevo al salón.",
        ) from e

    # La cookie, lo último: solo existe si su entrada quedó apuntada.
    # Atributos en `core/cookies_de_sesion.py`: mismo sitio que el frontend.
    poner_cookie_de_sesion(response, COOKIE_IMPERSONACION, token, MINUTOS_SESION * 60)

    # EL AVISO AL DUEÑO, DESPUÉS (20-10-2026): sale cuando la entrada
    # está apuntada y hecha, así que nunca avisa de una que no ocurrió. Un
    # fallo suyo no deja al salón sin soporte.
    if not salon.get("email"):
        # `salons.email` es nullable. Sin destinatario no se avisa a nadie, y
        # tiene que constar.
        aviso = "sin_destinatario"
    else:
        try:
            await _avisar_al_dueno(settings, salon, admin.email)
            aviso = "entregado"
        except Exception as e:  # noqa: BLE001 - el aviso no puede tumbar la sesión
            # Solo el tipo de excepción: lo apuntado no se puede corregir y el
            # mensaje del proveedor puede arrastrar la dirección del dueño.
            aviso = type(e).__name__
    # Cómo acabó el aviso, junto a la entrada (0091). Si no se puede escribir,
    # la entrada se queda sin él y se lee «no sabemos si se avisó».
    try:
        await completar_admin_log(db, apunte, {"aviso": aviso})
    except Exception:  # noqa: BLE001 - la entrada ya está apuntada y hecha
        logger.error("No se pudo apuntar el aviso de la entrada de soporte %s: %s", sid, aviso, exc_info=True)

    return ImpersonacionOut(
        sid=sid,
        salon_id=salon_id,
        salon_nombre=salon["nombre_comercial"],
        expires_at=expira.isoformat(),
    )


@router.post("/impersonation/end", status_code=status.HTTP_204_NO_CONTENT)
async def terminar_impersonacion(
    request: Request,
    admin: AdminClaims = Depends(require_admin_roles(*ADMIN_MUTABLE)),
    db: PostgrestClient = Depends(get_admin_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Salida limpia del modo soporte.

    La cookie del panel NO se toca: el super admin sigue dentro, así que
    "restaurar la sesión de super admin" no consiste en restaurar nada.

    Idempotente a propósito: si la sesión ya estaba muerta (caducada o
    revocada por el dueño desde /soporte) o si ni siquiera hay cookie de
    soporte, la salida igual responde 204 — salir de un modo en el que ya no
    se está no es un error.
    """
    respuesta = Response(status_code=status.HTTP_204_NO_CONTENT)
    # Con los MISMOS atributos con los que se emitió. No es cosmética: con otros,
    # el navegador no la reconoce como la misma y sobreviviría hasta su
    # `Max-Age` de 30 minutos: todo daría 401 (la fila ya está cerrada y la
    # custodia purgada), pero el panel seguiría creyéndose en modo soporte.
    borrar_cookie_de_sesion(respuesta, COOKIE_IMPERSONACION)

    cookie = request.cookies.get(COOKIE_IMPERSONACION)
    if not cookie:
        return respuesta

    try:
        claims = decodificar_token_impersonacion(settings, cookie)
    except HTTPException:
        # Cookie basura o caducada: no hay nada que cerrar, pero se borra igual.
        return respuesta

    sid = claims["sid"]

    # La credencial del salón deja de existir en el backend Y en GoTrue: purga
    # de la custodia + logout_local (scope=local) best-effort. Nunca se pide
    # aquí el token de vuelta ni se registra en ningún sitio.
    await expulsar_token_tenant(settings, sid)

    # La fila deja de estar viva, que es lo que relee cada petición en modo
    # soporte (cargar_contexto_sesion). Si ya lo estaba —caducada o revocada
    # por el dueño— no se vuelve a tocar ni a registrar: la salida es
    # idempotente.
    ahora = _ahora()
    try:
        filas = await db.select(
            "impersonation_sessions",
            {"select": "id,salon_id,started_at,ended_at", "id": f"eq.{sid}", "limit": "1"},
        )
        if filas and not filas[0].get("ended_at"):
            iniciada = datetime.fromisoformat(filas[0]["started_at"])
            duracion = int((ahora - iniciada).total_seconds())
            # Guarda optimista (mismo patrón que `_marcar_codigo_usado`): entre
            # este SELECT y este UPDATE puede colarse `revocar_acceso_soporte`
            # (soporte.py) o el marcado perezoso de caducidad
            # (app/core/impersonation.py). Repetir `ended_at=is.null` en el
            # WHERE hace que, si alguno de los dos ganó la carrera, este UPDATE
            # no afecte a ninguna fila — y por tanto no le pise su motivo más
            # preciso ("revocada_por_salon"/"caducada") con "salida".
            afectadas = await db.update(
                "impersonation_sessions",
                {"id": f"eq.{sid}", "ended_at": "is.null"},
                {"ended_at": ahora.isoformat(), "ended_reason": "salida"},
            )
            # El log solo se escribe si de verdad fuimos quienes cerramos la
            # sesión: `admin_logs` es append-only, así que una entrada de más
            # —duplicada con la que ya dejó quien ganó la carrera— no se puede
            # corregir después.
            if afectadas:
                await append_admin_log(
                    db,
                    admin_id=admin.sub,
                    accion="impersonation_end",
                    ip=client_ip(request),
                    target_salon_id=str(filas[0]["salon_id"]),
                    detalle={"sid": sid, "duracion_segundos": duracion, "motivo": "salida"},
                )
    except PostgrestError:
        # Best-effort: la sesión ya está fuera de la custodia y GoTrue, que es
        # lo que de verdad protege al dueño. Un hipo de PostgREST aquí no
        # puede convertir una salida en un 502 para el super admin.
        pass

    return respuesta
