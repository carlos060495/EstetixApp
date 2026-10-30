"""JWT verification and role/plan gating dependencies.

The frontend never decides authorization on its own — every sensitive
endpoint here re-verifies the Supabase-issued JWT's signature and re-derives
salon_id/rol from its claims (never from a request body or query param).
"""

from __future__ import annotations

import logging

import anyio.to_thread

import jwt
from fastapi import Depends, Header, HTTPException, Request, status
from passlib.hash import argon2
from pydantic import BaseModel

from app.billing.lo_que_incluye_el_plan import centros_de_la_cuenta, lo_que_incluye
from app.billing.estado_suscripcion import motivo_del_corte
from app.core.centro_activo import CABECERA_CENTRO, resolver_centro_activo
from app.core import claves_de_sesion, demo, mostrador
from app.core.bot_tenant_token import BotSinAcceso, SalonSinBot, cliente_del_salon
from app.core.sesion_referido import exigir_origen_permitido
from app.core.config import Settings, get_settings
from app.core.cuenta import leer_cuenta
from app.core.impersonation import (
    COOKIE_IMPERSONACION,
    ImpersonationContext,
    cargar_contexto_sesion,
    decodificar_token_impersonacion,
)
from app.core.tenant_token_broker import obtener_token_tenant
from app.db.postgrest_client import PostgrestClient, PostgrestError
from app.db.storage_client import StorageClient

logger = logging.getLogger("estetix.security")


# Same parameters as the seed hashes (m=65536, t=3, p=4, Argon2id).
_argon2id = argon2.using(type="ID")


def hash_pin(pin: str) -> str:
    """Argon2id hash for kiosk PINs; the raw PIN is never stored or logged."""
    return _argon2id.hash(pin)


def verify_pin(pin: str, pin_hash: str) -> bool:
    """Constant-time Argon2id verification for kiosk PINs."""
    try:
        return _argon2id.verify(pin, pin_hash)
    except (TypeError, ValueError):
        return False


# ── ARGON2 NO PUEDE CORRER EN EL BUCLE DE EVENTOS ──────────────────────────
#
# Argon2 es caro A PROPÓSITO: ahí está su valor contra quien prueba PINES. En
# esta máquina, cada comprobación son 78 ms de CPU. El problema no es el coste,
# es DÓNDE se pagaba: `verify_pin(...)` se llamaba sin `await` dentro de
# funciones `async`, así que esos 78 ms no eran «esta petición tarda un poco»
# sino «el backend entero se para». Medido con un latido cada 50 ms durante una
# ráfaga de doce: el bucle se quedaba parado hasta 428 ms de golpe, y en esos
# milisegundos NO avanza ninguna otra petición de ningún otro salón — ni
# siquiera las conexiones a medio abrir, que fue lo que las hacía caducar.
#
# Estas dos lo sacan a un hilo. El coste en CPU es el mismo; lo que cambia es
# que el resto del mundo sigue moviéndose mientras tanto.
#
# HAY UN GUARDA que impide volver atrás: `tests/test_argon2_no_para_el_backend.py`
# falla si un router vuelve a llamar a las versiones síncronas.
async def verificar_pin(pin: str, pin_hash: str) -> bool:
    """`verify_pin` sin parar el bucle. Es la que usan los routers."""
    return await anyio.to_thread.run_sync(verify_pin, pin, pin_hash)


async def hashear_pin(pin: str) -> str:
    """`hash_pin` sin parar el bucle. Es la que usan los routers."""
    return await anyio.to_thread.run_sync(hash_pin, pin)


class CurrentUser(BaseModel):
    sub: str
    salon_id: str
    rol: str
    email: str | None = None
    # Presente solo en sesiones de soporte. Los endpoints normales lo ignoran;
    # lo consumen la dependencia de bloqueo y la auditoría.
    impersonation: ImpersonationContext | None = None
    # SE PIDIÓ UN CENTRO QUE NO ERA SUYO y se ha vuelto al propio. No es un
    # error —la petición sigue— pero hay que DECIRLO: `/auth/me` y
    # `/salon/centros` lo devuelven para que la pantalla borre el centro
    # guardado y avise. Sin esto, la vuelta atrás sería silenciosa, que es lo
    # que no puede ser.
    centro_rechazado: bool = False
    # EL CENTRO AL QUE IBA ESTÁ EN PAUSA porque el plan ya no lo incluye. Otro
    # motivo que `centro_rechazado` y otro texto: aquí no se ha perdido nada.
    centro_en_pausa: bool = False
    # Los centros de la cuenta que el plan deja fuera. Viaja en la sesión para
    # que `/salon/centros` pueda marcarlos SIN una consulta más y sin un filtro
    # propio: el listado sigue siendo el que devuelve la RLS, tal cual.
    centros_en_pausa: tuple[str, ...] = ()
    # ESTA PETICIÓN MIRA LA AGENDA DE UN CENTRO EN PAUSA (0088): solo lectura,
    # solo por las rutas de la agenda. La pantalla pinta la barra con esto.
    viendo_centro_en_pausa: bool = False
    # LA CUENTA ESTÁ SUSPENDIDA y aun así esta petición ha pasado. Solo puede
    # ser `True` por la puerta de `get_current_user_aunque_suspendida`; por
    # `get_current_user` una cuenta suspendida no llega nunca aquí.
    cuenta_suspendida: bool = False
    # UN SALÓN DE MUESTRA (0080): nada sale al mundo desde aquí (`core/demo.py`).
    # Lo pone la base (`accounts.es_demo`), nunca la petición.
    es_demo: bool = False
    # LA CUENTA ESTÁ CORTADA (0086): sin suscripción viva. Solo puede ser `True`
    # en una ruta que lo admite (`admite_cuenta_cortada`) o por la puerta de la
    # suspensión; por cualquier otra, una cuenta cortada no llega aquí.
    cuenta_cortada: bool = False
    motivo_corte: str | None = None
    # LA PETICIÓN LLEGA DEL MOSTRADOR (0090): el id del dispositivo. El navegador
    # no tiene token; `_cliente_de` acuña el del salón aquí.
    mostrador_id: str | None = None


def _extract_bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales inválidas",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return authorization.split(" ", 1)[1]


# Definida antes que get_current_user porque esta depende de aquella: en modo
# soporte hay que releer la sesión ANTES de saber de qué salón es la petición.
def get_service_postgrest_client(
    settings: Settings = Depends(get_settings),
) -> PostgrestClient:
    """Cliente PostgREST con la SECRET key y SIN JWT de usuario: la RLS se
    evalúa como service_role (bypass). Solo para cuatro flujos.

    Los tres primeros son flujos SIN sesión de tenant todavía: webhook de
    Stripe, alta self-service (/auth/signup) y la comprobación de vida de una
    sesión de soporte (get_current_user), que ocurre ANTES de saber a qué salón
    pertenece la petición.

    El cuarto es distinto y por eso se explica aquí: la REVOCACIÓN del acceso de
    soporte (app/routers/soporte.py) escribe con service role aunque el salón ya
    esté identificado. No es un descuido ni un atajo. `support_access_codes` e
    `impersonation_sessions` no son datos del salón: son la prueba de que el
    acceso fue consentido y de cuándo terminó, y el propio admin del salón no
    debe poder reescribir esa prueba (borrar un `used_at`, estirar un
    `expires_at`, cambiar el `ended_reason` de una sesión). Por eso la migración
    0021 le deja RLS de SELECT+INSERT sobre los códigos y de SELECT sobre las
    sesiones, y las revocaciones —que son escrituras legítimas del producto—
    pasan por aquí. Al perderse la RLS se pierde también el filtro por
    salon_id, así que esas escrituras lo repiten explícitamente en el WHERE.

    Fuera de esos cuatro casos: nunca lo uses para leer o escribir datos de un
    salón ya identificado. Eso va siempre por get_postgrest_client, con RLS.
    """
    return PostgrestClient(settings.supabase_url, settings.supabase_secret_key)


CODIGO_SESION_SIN_COMPROBAR = "sesion_sin_comprobar"

MENSAJE_SESION_SIN_COMPROBAR = (
    "No hemos podido comprobar tu sesión ahora mismo. Espera unos segundos y vuelve a intentarlo."
)


def _usuario_desde_bearer(token: str, settings: Settings) -> CurrentUser:
    """Verifica firma y claims del JWT de Supabase. No toca la BD NI LA RED.

    La clave sale de memoria: la deja ahí `claves_de_sesion.preparar`, que
    quien llama espera ANTES (ver `app/core/claves_de_sesion.py`). Por eso esto
    puede seguir siendo síncrono sin parar el bucle.
    """
    signing_key = claves_de_sesion.clave(settings.supabase_jwks_url, token)
    if signing_key is None and claves_de_sesion.sin_respuesta(settings.supabase_jwks_url):
        # NO ES UN 401. La sesión puede ser buena: lo que falta es Supabase. Un
        # 401 haría que el navegador la diera por caducada y mandara al login.
        logger.warning("Sesión sin comprobar: Supabase no entrega las claves.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"codigo": CODIGO_SESION_SIN_COMPROBAR, "mensaje": MENSAJE_SESION_SIN_COMPROBAR},
            headers={"Retry-After": "5"},
        )
    try:
        if signing_key is None:
            raise jwt.InvalidKeyError("la clave del token no está entre las de Supabase")
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256", "ES256"],
            audience="authenticated",
            issuer=f"{settings.supabase_url}/auth/v1",
        )
    except jwt.PyJWTError as e:
        # El motivo, nunca el token. Distingue «caducó» de «mal firmado», que es
        # justo lo que no se sabía cuando alguien decía «no puedo entrar».
        logger.warning("JWT de salón rechazado: %s", e)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales inválidas",
            headers={"WWW-Authenticate": "Bearer"},
        )

    salon_id = payload.get("salon_id")
    rol = payload.get("rol")
    if not salon_id or not rol:
        # Token bien firmado pero sin claims de tenant (p. ej. un token del
        # panel usado contra la API de salón).
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Token sin contexto de salón"
        )

    return CurrentUser(sub=payload["sub"], salon_id=salon_id, rol=rol, email=payload.get("email"))


CODIGO_SUSPENDIDA = "cuenta_suspendida"

MENSAJE_SUSPENDIDA = (
    "La cuenta de este salón está suspendida. Ponte en contacto con soporte para reactivarla."
)


CODIGO_REVOCADA = "sesion_revocada"

MENSAJE_REVOCADA = "Tu acceso ya no está activo. Vuelve a iniciar sesión."


# TODOS SUS CENTROS ESTÁN EN PAUSA. Solo le puede pasar a quien tiene ficha
# únicamente en un centro que el plan ya no incluye: una administradora ve toda
# su cuenta y el reparto garantiza que siempre queda uno activo.
#
# SE CORTA CON UN MOTIVO Y NO SE LA DEJA ENTRAR A MEDIAS. Llevarla a un centro
# donde la RLS no le devuelve una sola fila la dejaría delante de una aplicación
# vacía sin un solo error: un fallo que no salta en rojo y que nadie sabe
# describir. Es el mismo trato que la cuenta suspendida —403 con código, para
# que el frontend sepa DISTINGUIRLO— y por la misma razón: sus credenciales son
# correctas y volver a entrar no cambiaría nada.
CODIGO_CENTRO_EN_PAUSA = "centro_en_pausa"

# SIN EL PORQUÉ, y es deliberado: esto lo lee quien trabaja en el centro, no
# quien paga. Que una empleada se entere por nuestra pantalla de que su jefa ha
# bajado de plan no le corresponde a este software.
MENSAJE_CENTRO_EN_PAUSA = (
    "Tu centro está en pausa ahora mismo. No se ha borrado nada. "
    "Habla con la responsable de la cuenta para reactivarlo."
)


# MIRANDO LA AGENDA DE UN CENTRO EN PAUSA (0088) no se escribe nada: ni en él
# ni, por la vuelta al centro activo, en otro.
CODIGO_SOLO_LECTURA_EN_PAUSA = "solo_lectura_centro_en_pausa"
MENSAJE_SOLO_LECTURA_EN_PAUSA = (
    "Ese centro está en pausa: puedes ver su agenda, pero no cambiar nada. "
    "Vuelve a tu centro activo para trabajar."
)


# ═══ EL CORTE POR NO PAGAR (0086) ═══════════════════════════════════════════
#
# Una cuenta sin suscripción viva —cancelada, impagada, nunca activada— NO USA
# LA APLICACIÓN. La frontera es la base (`centros_que_trabajan()`, 0086): esto
# es la segunda puerta, la que contesta con un motivo legible antes de llegar a
# ella.
#
# CERRADO POR DEFECTO. Lo comprueba la sesión, así que toda ruta nueva nace
# cortada. Pasa solo la que lo dice en voz alta con `admite_cuenta_cortada`:
#
#   · «agenda» — VER la agenda: solo lecturas (GET), y la base decide qué
#     vuelve (las fichas de las clientas no).
#   · «salida» — reactivar el plan o actualizar la tarjeta. Darse de baja y
#     descargar los datos ya pasan por la puerta de la suspensión.
#
# `tests/test_el_corte_por_no_pagar.py` recorre TODAS las rutas: una «agenda»
# que no sea GET, o una marca con otro motivo, lo pone en rojo.
CODIGO_CORTADA = "cuenta_cortada"

MENSAJE_CORTADA = (
    "Tu cuenta no tiene una suscripción activa. Puedes ver tu agenda, reactivar el plan, "
    "darte de baja o descargar tus datos."
)

MOTIVOS_PARA_CORTADA = frozenset({"agenda", "salida"})


# DÓNDE CORTA LA BASE CADA CORTE DE AQUÍ (0086). La frontera es la base; lo que
# contesta el backend es un aviso legible antes de llegar a ella. Cada código
# con el que la sesión se niega a seguir dice qué función de la base hace
# cumplir lo mismo cuando alguien le pide los datos directamente.
# `tests/test_el_corte_por_no_pagar.py` descubre los códigos leyendo este
# fichero —no de una lista— y se pone rojo si aparece uno que no esté aquí; y
# `tests/test_el_corte_vive_en_la_base_base_real.py` comprueba contra la base
# que cada función de verdad corta.
CORTES_EN_LA_BASE: dict[str, str | None] = {
    # Sin su fila en `users` (dada de baja, borrada en Auth): no ve ningún centro.
    "sesion_revocada": "salones_visibles",
    "cuenta_suspendida": "centros_que_trabajan",
    "cuenta_cortada": "centros_que_trabajan",
    # `cuenta_trabaja` solo deja pasar una demo SIN caducar.
    "demo_terminada": "centros_que_trabajan",
    # El centro que el plan deja fuera (0088): `centros_que_trabajan` lo saca de
    # la lista, así que no se escribe en él y no se lee nada que no sea agenda.
    "centro_en_pausa": "centros_que_trabajan",
    "solo_lectura_centro_en_pausa": "centros_que_trabajan",
}


# LOS CORTES QUE NO PASAN POR LA BASE porque quien los recibe NO TIENE TOKEN con
# el que pedirle nada (0090). El mostrador: su navegador no guarda sesión de la
# base, así que lo que el backend le niega no lo puede pedir por otro lado. Lo
# vigila `tests/test_mostrador.py` (ningún token sale hacia el navegador).
CORTES_SIN_TOKEN: dict[str, str] = {
    mostrador.CODIGO_PIDE_PIN: "core/mostrador.py",
    # El mostrador retirado (29-10-2026): su cookie ya no es nadie, y el
    # navegador nunca tuvo un token con el que ir a la base.
    mostrador.CODIGO_RETIRADO: "core/mostrador.py",
}


def admite_cuenta_cortada(motivo: str):
    """Marca una ruta como usable con la cuenta cortada. Ver el bloque de arriba."""
    if motivo not in MOTIVOS_PARA_CORTADA:
        raise ValueError(f"Motivo desconocido para una cuenta cortada: {motivo!r}")

    def decorar(f):
        f.__cuenta_cortada__ = motivo
        return f

    return decorar


def motivo_para_cortada(ruta) -> str | None:
    """El motivo que declara una ruta para dejar pasar a una cuenta cortada."""
    return getattr(getattr(ruta, "endpoint", None), "__cuenta_cortada__", None)


def _demo_caducada(cuenta: dict) -> bool:
    from datetime import datetime, timezone

    vida = cuenta.get("demos") or {}
    if isinstance(vida, list):
        vida = vida[0] if vida else {}
    caduca = vida.get("caduca_at")
    if not caduca:
        return True  # una demo sin su ciclo de vida no es una demo viva
    return datetime.fromisoformat(str(caduca).replace("Z", "+00:00")) <= datetime.now(timezone.utc)


def cuenta_de_la_ficha(fila: dict) -> dict:
    """La cuenta que PostgREST cuelga de la ficha, aplanada.

    `users → salons → accounts`. Se aplana aquí, una vez, porque la leen dos
    cosas con criterios opuestos: la SUSPENSIÓN, que falla cerrado, y el PLAN,
    que falla abierto. Que compartan la lectura y no el criterio.
    """
    salon = (fila or {}).get("salons") or {}
    if isinstance(salon, list):
        salon = salon[0] if salon else {}
    cuenta = salon.get("accounts") or {}
    if isinstance(cuenta, list):
        cuenta = cuenta[0] if cuenta else {}
    return cuenta


async def _exigir_sesion_valida(
    sub: str, salon_id: str, service_db: PostgrestClient, *, aunque_suspendida: bool = False,
    aunque_cortada: bool = False,
) -> dict | None:
    """Dos puertas en UNA consulta: que la persona siga existiendo, y que su
    cuenta no esté suspendida.

    LO QUE FALTABA. Hasta aquí esto solo miraba la suspensión, así que un token
    firmado seguía valiendo aunque su usuario ya no existiera. Medido tras
    borrar una cuenta en Auth: `/auth/me` y `/salon/profile` daban 404 —esos sí
    releen `users`— pero `/appointments`, `/services` y `/salon/schedule`
    contestaban 200 con el token viejo. O sea que revocar a alguien le quitaba
    la parte que se le nota y le dejaba la que importa, hasta que caducara el
    token.

    Ahora se pregunta por el USUARIO y no por el salón, y el salón llega por el
    embed. Sigue siendo una sola consulta —la misma que ya se hacía en cada
    petición— y responde a las dos cosas a la vez:

      · si no hay fila, o su `auth_user_id` ya no es este, o su `salon_id` no es
        el del token → la sesión no vale;
      · y si la hay, viene colgada la suspensión de su cuenta.

    LOS DOS MOTIVOS SE DISTINGUEN en el `detail`, y no por gusto: «suspendida»
    manda al usuario a hablar con soporte con unas credenciales que son
    correctas, y «revocada» lo manda a /login. Confundirlos deja a alguien
    reintentando en la pantalla equivocada.

    ESTO NO ES LA CADUCIDAD DE SESIÓN. Un token robado sigue valiendo hasta que
    expire mientras su usuario exista; eso es otra tarea. Lo que se cierra aquí
    es que un acceso REVOCADO siga sirviendo.
    """
    try:
        filas = await service_db.select(
            "users",
            {
                # La clave ajena va nombrada: hasta la 0069 `users` llegaba a
                # `salons` por dos caminos —su propia columna y `user_salons`— y
                # PostgREST se negaba a elegir (PGRST201). Nombrarla sigue
                # siendo lo correcto: el que vale aquí es el centro de su ficha.
                # `rol` y `account_id` viajan de vuelta porque los necesita
                # la resolución del centro activo, y pedirlos aquí no cuesta
                # una consulta más: es la misma.
                # `plan` y los centros de la CUENTA viajan de vuelta por el
                # mismo motivo que `rol` y `account_id`: los necesita la
                # resolución del centro activo y NO cuesta una consulta más.
                # Medido el 18-09-2026 contra el PostgREST local sobre una
                # cuenta de dos centros: 7,51 ms sin ellos, 7,71 ms con ellos.
                # Esta consulta corre en cada petición; si algún día deja de
                # salir gratis, el plan B es pedir los centros aparte y solo
                # cuando la cuenta tenga más de uno.
                "select": (
                    "id,rol,salon_id,account_id,"
                    "salons!users_salon_id_fkey("
                    "accounts(suspendido,plan,es_demo,demos(caduca_at),subscriptions(estado),"
                    "salons(id,created_at,sigue_al_bajar))"
                    ")"
                ),
                "auth_user_id": f"eq.{sub}",
                "salon_id": f"eq.{salon_id}",
                # DAR DE BAJA QUITA EL ACCESO (0062). Sin fila → sesión
                # revocada, en la misma consulta y en esta misma petición: no
                # se espera a que el token caduque. Lo vigila
                # tests/test_baja_quita_el_acceso_base_real.py.
                "acceso_retirado_el": "is.null",
                # SOLO ENTRA LA ADMINISTRADORA (0012, cerrado en la 0063). La
                # puerta de verdad es el hook del token, que no le emite uno a
                # nadie más; esto es para el token que ya estuviera emitido.
                # Lo vigila tests/test_solo_entra_la_administradora_base_real.py.
                "rol": "eq.admin",
                "limit": "1",
            },
        )
    except PostgrestError:
        # No se puede comprobar → no se bloquea. Una caída de PostgREST no debe
        # dejar fuera a todo el mundo; el 502 lo dará el endpoint real al
        # intentar leer sus datos con la misma BD caída. Es el mismo criterio
        # que ya tenía la comprobación de suspensión: no convertir una avería
        # nuestra en una expulsión. Sin ficha, quien llama se queda en su
        # centro de siempre: durante una caída no se cambia de centro.
        return None

    if not filas:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"codigo": CODIGO_REVOCADA, "mensaje": MENSAJE_REVOCADA},
            headers={"WWW-Authenticate": "Bearer"},
        )

    cuenta = cuenta_de_la_ficha(filas[0])
    # UNA DEMO CADUCADA (0080) ya no entra, aunque su token siga firmado: el
    # gancho del token no le da otro, y este la corta en esta misma petición.
    if cuenta.get("es_demo") and _demo_caducada(cuenta):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"codigo": demo.CODIGO_DEMO_TERMINADA, "mensaje": demo.MENSAJE_DEMO_TERMINADA},
        )
    if cuenta.get("suspendido") and not aunque_suspendida:
        # El código va en `detail` como diccionario y no como texto suelto
        # porque el frontend tiene que poder DISTINGUIRLO: una cuenta
        # suspendida no es una sesión caducada.
        #
        # SE PREGUNTA POR LA CUENTA, NO POR EL CENTRO. Se suspende a quien no
        # paga, y quien paga es la cuenta: dejar apagado un centro y encendido
        # el otro del mismo dueño no es una suspensión, es media.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"codigo": CODIGO_SUSPENDIDA, "mensaje": MENSAJE_SUSPENDIDA},
        )
    if motivo_del_corte_de(cuenta) is not None and not (aunque_cortada or aunque_suspendida):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"codigo": CODIGO_CORTADA, "mensaje": MENSAJE_CORTADA},
        )

    return filas[0]


def motivo_del_corte_de(cuenta: dict) -> str | None:
    """El corte de la cuenta colgada de la ficha. `None` = trabaja."""
    subs = cuenta.get("subscriptions") or []
    if isinstance(subs, dict):
        subs = [subs]
    return motivo_del_corte(
        [s.get("estado") for s in subs if isinstance(s, dict)],
        suspendida=bool(cuenta.get("suspendido")),
        es_demo=bool(cuenta.get("es_demo")),
        demo_vigente=bool(cuenta.get("es_demo")) and not _demo_caducada(cuenta),
    )


async def get_current_user(
    request: Request,
    authorization: str | None = Header(default=None),
    x_centro_activo: str | None = Header(default=None, alias=CABECERA_CENTRO),
    settings: Settings = Depends(get_settings),
    service_db: PostgrestClient = Depends(get_service_postgrest_client),
) -> CurrentUser:
    """Identidad de la petición: sesión normal del salón o sesión de soporte.

    El orden importa: si viene `Authorization`, manda ese token. La cookie de
    soporte es el camino alternativo para cuando el navegador NO tiene ninguna
    credencial de Supabase, que es justo el caso de la impersonación.
    """
    return await _usuario_de_la_peticion(
        request, authorization, x_centro_activo, settings, service_db, aunque_suspendida=False
    )


# ═══ LA PUERTA DE LAS CUENTAS SUSPENDIDAS ═══════════════════════════════════
#
# SALTA EL MURO DE SUSPENSIÓN, NO LA AUTENTICACIÓN. Todo lo demás se comprueba
# igual que en `get_current_user`: un token de Supabase válido, que la persona
# siga existiendo, que su ficha sea de ese centro (la sesión revocada sigue
# siendo un 401) y, con `require_admin_aunque_suspendida`, que sea la
# administradora. Lo ÚNICO que no se hace es cortar porque la cuenta esté
# suspendida.
#
# POR QUÉ EXISTE (25-09-2026): una cuenta suspendida no entra en la app
# ni en el portal de Stripe, así que no podía darse de baja; al levantarle la
# suspensión se le volvía a cobrar sin haber podido irse. La puerta es para que
# NADIE QUEDE ATRAPADO PAGANDO, no para gestionar la cuenta: deja pasar la
# vista previa de la baja y la baja, y nada más. Reanudar NO: volver a abrir la
# relación con una cuenta que suspendimos es una conversación, no un botón.
#
# QUIÉN LA USA lo vigila `tests/test_la_puerta_de_la_suspension.py`, que recorre
# el grafo de dependencias de TODAS las rutas de la app y se pone rojo si
# cualquier otra ruta que no sea `GET /billing/baja` o `POST /billing/baja`
# llega a una de estas tres funciones, directa o indirectamente.
#
# Las tres llevan `_aunque_suspendida` en el nombre, y el guarda las descubre
# por ese nombre: una cuarta que se añada entra sola en la vigilancia.


async def get_current_user_aunque_suspendida(
    request: Request,
    authorization: str | None = Header(default=None),
    x_centro_activo: str | None = Header(default=None, alias=CABECERA_CENTRO),
    settings: Settings = Depends(get_settings),
    service_db: PostgrestClient = Depends(get_service_postgrest_client),
) -> CurrentUser:
    """`get_current_user` sin el muro de suspensión. Ver el bloque de arriba."""
    return await _usuario_de_la_peticion(
        request, authorization, x_centro_activo, settings, service_db, aunque_suspendida=True
    )


CODIGO_SOLO_EN_DEMO = "solo_en_demo"


def solo_en_una_demo(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """Lo que SOLO existe dentro de un salón de muestra (0081): elegir el plan sin
    Stripe. A una cuenta de verdad, 403: su plan solo llega de Stripe."""
    if not user.es_demo:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"codigo": CODIGO_SOLO_EN_DEMO, "mensaje": "Esto solo existe en un salón de muestra."},
        )
    return user


def sin_demo(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """Lo que un salón de muestra no puede hacer aunque no salga nada (0080):
    decir quién lo trajo, subir ficheros. Un 403 con `codigo: demo`."""
    demo.no_en_una_demo(user.es_demo, "solo_clientes")
    return user


def require_admin_aunque_suspendida(
    user: CurrentUser = Depends(get_current_user_aunque_suspendida),
) -> CurrentUser:
    """La administradora de la cuenta, esté suspendida o no. Es la única que tiene
    sesión (0063, 0069): no hay ningún otro rol que dejar fuera."""
    return user


async def dispositivo_del_mostrador(
    request: Request,
    settings: Settings = Depends(get_settings),
    service_db: PostgrestClient = Depends(get_service_postgrest_client),
) -> mostrador.Dispositivo:
    """Las PUERTAS del mostrador (abrir y cerrar la gestión, salir del modo): las
    autoriza la cookie del dispositivo, no una sesión. Sin dispositivo, 401."""
    try:
        dispositivo = await mostrador.dispositivo_de(service_db, request.cookies.get(mostrador.COOKIE))
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"codigo": CODIGO_SESION_SIN_COMPROBAR, "mensaje": MENSAJE_SESION_SIN_COMPROBAR},
            headers={"Retry-After": "5"},
        ) from e
    if dispositivo is None:
        raise await _sin_dispositivo(service_db, request.cookies.get(mostrador.COOKIE))
    exigir_origen_permitido(request, settings)
    await _usar_el_dispositivo(request, service_db, dispositivo)
    return dispositivo


async def _sin_dispositivo(service_db: PostgrestClient, valor: str | None) -> HTTPException:
    """El 401 de una cookie de mostrador que ya no vale. Si alguien lo retiró, se
    dice (la pantalla lo explica); si no es de nadie, el 401 de siempre."""
    try:
        quitado = bool(valor) and await mostrador.retirado(service_db, valor)
    except PostgrestError:
        quitado = False
    if quitado:
        return HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"codigo": mostrador.CODIGO_RETIRADO, "mensaje": mostrador.MENSAJE_RETIRADO},
        )
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"codigo": CODIGO_REVOCADA, "mensaje": MENSAJE_REVOCADA},
    )


async def _usar_el_dispositivo(request: Request, service_db: PostgrestClient, dispositivo: mostrador.Dispositivo) -> None:
    """Apunta el uso y, si toca, deja dicho que la respuesta renueve la cookie
    (lo hace el middleware de `main.py`, que es quien tiene la respuesta)."""
    if await mostrador.apuntar_uso(service_db, dispositivo):
        request.state.renovar_mostrador = request.cookies.get(mostrador.COOKIE)


async def _usuario_del_mostrador(
    request: Request, settings: Settings, service_db: PostgrestClient, valor: str
) -> tuple[CurrentUser, str]:
    """Quién pide desde un dispositivo mostrador, y en qué centro. Aquí se decide
    si la ruta pasa: la lista, o la gestión abierta. Ver `core/mostrador.py`."""
    try:
        dispositivo = await mostrador.dispositivo_de(service_db, valor)
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"codigo": CODIGO_SESION_SIN_COMPROBAR, "mensaje": MENSAJE_SESION_SIN_COMPROBAR},
            headers={"Retry-After": "5"},
        ) from e
    if dispositivo is None:
        # Retirado, o una cookie que no es de nadie: sin sesión ninguna.
        raise await _sin_dispositivo(service_db, valor)
    # La cookie va sola en cada petición del mismo sitio: lo que cambia algo
    # tiene que venir además del frontend, igual que en soporte y en referidos.
    exigir_origen_permitido(request, settings)
    if not mostrador.es_ruta_del_mostrador(request.method, request.scope.get("route")):
        try:
            abierta = await mostrador.usar_la_gestion(
                service_db, dispositivo, renovar=request.headers.get(mostrador.CABECERA_USO) == "1"
            )
        except PostgrestError:
            abierta = False  # sin poder comprobarlo, cerrada: falla cerrado
        if not abierta:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"codigo": mostrador.CODIGO_PIDE_PIN, "mensaje": mostrador.MENSAJE_PIDE_PIN},
            )
    await _usar_el_dispositivo(request, service_db, dispositivo)
    usuario = CurrentUser(
        sub=dispositivo.auth_user_id,
        salon_id=dispositivo.salon_de_la_ficha,
        rol="admin",
        email=dispositivo.email,
        mostrador_id=dispositivo.id,
    )
    return usuario, dispositivo.salon_id


async def _usuario_de_la_peticion(
    request: Request,
    authorization: str | None,
    x_centro_activo: str | None,
    settings: Settings,
    service_db: PostgrestClient,
    *,
    aunque_suspendida: bool,
) -> CurrentUser:
    # EL MOSTRADOR PRIMERO, y gana al token (0090, ver `core/mostrador.py`): en
    # un dispositivo mostrador, iniciar sesión «normal» no devuelve la sesión
    # completa, porque la cookie sigue viajando y manda.
    del_mostrador = request.cookies.get(mostrador.COOKIE)
    if del_mostrador or authorization:
        # Se fija también aquí —a None— para que `request.state.impersonation`
        # exista SIEMPRE: quien lo consuma (auditoría, bloqueo de acciones) no
        # debe tener que distinguir "no hay soporte" de "el atributo no está".
        request.state.impersonation = None
        if del_mostrador:
            # El centro es el del dispositivo; la cabecera del navegador no cuenta.
            usuario, x_centro_activo = await _usuario_del_mostrador(request, settings, service_db, del_mostrador)
        else:
            token = _extract_bearer_token(authorization)
            # La red, aquí y esperando; la verificación de abajo ya no sale de memoria.
            await claves_de_sesion.preparar(token, settings.supabase_jwks_url)
            usuario = _usuario_desde_bearer(token, settings)
        # Los usuarios PROPIOS del salón (incluido el quiosco de fichaje, que
        # entra por este mismo camino) sí quedan bloqueados.
        ficha = await _exigir_sesion_valida(
            usuario.sub, usuario.salon_id, service_db, aunque_suspendida=aunque_suspendida,
            aunque_cortada=motivo_para_cortada(request.scope.get("route")) is not None,
        )

        # EL CENTRO ACTIVO, y con él `salon_id` deja de significar «el centro de
        # tu ficha» para significar «en el que estás trabajando». Los 188 sitios
        # que lo leen no se enteran: siguen recibiendo un centro en el que esta
        # persona puede estar.
        #
        # Sin cabecera no se consulta nada y todo queda como estaba, que es el
        # caso de todas las cuentas de hoy.
        if ficha is not None:
            # EL PLAN ES FRONTERA, y se resuelve aquí porque aquí es donde se
            # decide en qué centro corre la petición. Todo lo que el panel
            # escribe lo escribe en `user.salon_id`; si este valor nunca es un
            # centro en pausa, ninguno de los 188 sitios que lo leen puede
            # escribir en uno, y no hay que acordarse de nada en ninguno de
            # ellos. Poner la comprobación en cada endpoint es exactamente la
            # forma de que un día falte en uno.
            cuenta = cuenta_de_la_ficha(ficha)
            incluido = lo_que_incluye(cuenta.get("plan"), centros_de_la_cuenta(cuenta))
            centro, rechazado, en_pausa = await resolver_centro_activo(
                service_db, pedido=x_centro_activo, ficha=ficha, incluido=incluido
            )
            if rechazado and usuario.mostrador_id:
                # EL CENTRO DEL MOSTRADOR YA NO ES SUYO. Un mostrador no se muda
                # solo a otro centro: deja de valer y se dice.
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={"codigo": CODIGO_REVOCADA, "mensaje": MENSAJE_REVOCADA},
                )
            # LA AGENDA DE UN CENTRO EN PAUSA SE VE (0088), como la de una
            # cuenta cortada: solo por las rutas que lo dicen con
            # `admite_cuenta_cortada("agenda")`, que son lecturas. Cualquier
            # otra ruta sigue llevándola al centro activo, así que nada escribe
            # en el pausado. El centro pedido es suyo: está en el reparto de su
            # propia cuenta. Y la base lo corta igual (`centros_que_trabajan`).
            pide_uno_en_pausa = (
                en_pausa
                and isinstance(x_centro_activo, str)
                and x_centro_activo in incluido.centros_en_pausa
            )
            viendo_en_pausa = (
                pide_uno_en_pausa and motivo_para_cortada(request.scope.get("route")) == "agenda"
            )
            if pide_uno_en_pausa and not viendo_en_pausa:
                # QUIEN MIRA UN CENTRO EN PAUSA NO TRABAJA EN OTRO. Llevar la
                # petición al centro activo sin decirlo es el fallo caro de los
                # dos centros: una cita donde nadie la espera, o la pantalla del
                # pausado enseñando datos del otro. Lo que no es agenda, 403,
                # igual que en una cuenta cortada.
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={
                        "codigo": CODIGO_SOLO_LECTURA_EN_PAUSA,
                        "mensaje": MENSAJE_SOLO_LECTURA_EN_PAUSA,
                    },
                )
            if viendo_en_pausa:
                centro, en_pausa = x_centro_activo, False
            elif not incluido.esta_activo(centro):
                # No había ninguno activo al que llevarla. Ver
                # CODIGO_CENTRO_EN_PAUSA: se corta con un motivo, nunca se la
                # deja delante de una aplicación vacía.
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={
                        "codigo": CODIGO_CENTRO_EN_PAUSA,
                        "mensaje": MENSAJE_CENTRO_EN_PAUSA,
                    },
                )
            usuario = usuario.model_copy(
                update={
                    "salon_id": centro,
                    "centro_rechazado": rechazado,
                    "centro_en_pausa": en_pausa,
                    "centros_en_pausa": incluido.centros_en_pausa,
                    "viendo_centro_en_pausa": viendo_en_pausa,
                    "cuenta_suspendida": bool(cuenta.get("suspendido")),
                    "es_demo": bool(cuenta.get("es_demo")),
                    "motivo_corte": motivo_del_corte_de(cuenta),
                    "cuenta_cortada": motivo_del_corte_de(cuenta) is not None,
                }
            )
        # Lo que dure esta petición, se actúa por una demo (o no): lo leen las
        # salidas al mundo (`core/demo.py`). Sin ficha (base caída) no se sabe,
        # y entonces se corta igual: mejor un correo que no sale que uno de más.
        demo.apuntar(usuario.es_demo if ficha is not None else True)
        return usuario

    cookie = request.cookies.get(COOKIE_IMPERSONACION)
    if not cookie:
        request.state.impersonation = None
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales inválidas",
            headers={"WWW-Authenticate": "Bearer"},
        )

    claims = decodificar_token_impersonacion(settings, cookie)
    contexto = await cargar_contexto_sesion(settings, service_db, claims)
    request.state.impersonation = contexto
    # Aquí y no antes: es el primer punto con la sesión comprobada viva. Si no
    # se puede apuntar, 503 y el endpoint no corre. Import tardío: la política
    # importa este módulo.
    from app.core.impersonation_policy import apuntar_antes_de_atender

    await apuntar_antes_de_atender(request, settings, contexto)

    # A PROPÓSITO no se comprueba aquí la suspensión del salón. Un salón
    # suspendido es justo uno de los que MÁS necesita que soporte pueda entrar:
    # a mirar qué pasó, a exportar datos, a comprobar antes de reactivar. Si
    # esta comprobación se sube a la cabecera de la función "para no repetirla",
    # se cierra la puerta de soporte a las cuentas suspendidas. Hay un test que
    # lo afirma: test_soporte_si_entra_en_un_salon_suspendido.
    #
    # SOPORTE DENTRO DE UNA DEMO (0080): tampoco sale nada.
    es_demo = await _el_centro_es_de_una_demo(service_db, contexto.salon_id)
    demo.apuntar(es_demo)

    # El rol es SIEMPRE "admin": la sesión suplanta al usuario administrador del
    # salón, que es el único con quien tiene sentido dar soporte.
    return CurrentUser(
        sub=contexto.tenant_auth_user_id,
        salon_id=contexto.salon_id,
        rol="admin",
        email=contexto.tenant_email,
        impersonation=contexto,
        es_demo=es_demo,
    )


async def _el_centro_es_de_una_demo(service_db: PostgrestClient, salon_id: str) -> bool:
    try:
        filas = await service_db.select(
            "salons", {"select": "accounts(es_demo)", "id": f"eq.{salon_id}", "limit": "1"}
        )
    except PostgrestError:
        return True  # sin saberlo, se corta: ver arriba
    cuenta = (filas[0].get("accounts") if filas else None) or {}
    return bool(cuenta.get("es_demo"))


async def get_postgrest_client(
    user: CurrentUser = Depends(get_current_user),
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> PostgrestClient:
    return await _cliente_de(user, authorization, settings)


async def get_postgrest_client_aunque_suspendida(
    user: CurrentUser = Depends(get_current_user_aunque_suspendida),
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> PostgrestClient:
    """El cliente del inquilino para la puerta de la suspensión. Hace falta
    porque `get_postgrest_client` depende de `get_current_user`, que corta a la
    cuenta suspendida: sin este, la ruta de la baja se cerraría sola por su
    propio cliente de base de datos. La RLS sigue decidiendo qué vuelve."""
    return await _cliente_de(user, authorization, settings)


async def _cliente_de(
    user: CurrentUser, authorization: str | None, settings: Settings
) -> PostgrestClient:
    # En ambos caminos el Authorization que llega a PostgREST es un JWT de
    # Supabase de un usuario real del salón, así que RLS —no este cliente— es
    # lo que decide qué vuelve. En modo soporte el token lo custodia el
    # backend: NUNCA ha pasado por el navegador.
    if user.mostrador_id is not None:
        # EL MOSTRADOR (0090): tampoco. El token del salón, de la custodia por
        # centro que ya usan el bot y el widget. Un Authorization que llegue a
        # la vez no se mira: la cookie del mostrador manda.
        try:
            return await cliente_del_salon(settings, get_service_postgrest_client(settings), user.salon_id)
        except (SalonSinBot, BotSinAcceso) as e:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"codigo": CODIGO_SESION_SIN_COMPROBAR, "mensaje": MENSAJE_SESION_SIN_COMPROBAR},
                headers={"Retry-After": "5"},
            ) from e
    if user.impersonation is not None:
        token = await obtener_token_tenant(
            settings,
            user.impersonation.sid,
            user.impersonation.tenant_email,
            # La identidad elegida al abrir la sesión: el broker comprueba que
            # sea la que GoTrue acaba de acuñar, que va por email.
            tenant_auth_user_id=user.impersonation.tenant_auth_user_id,
        )
    else:
        token = _extract_bearer_token(authorization)
    return PostgrestClient(settings.supabase_url, settings.supabase_secret_key, user_jwt=token)


def get_storage_client(settings: Settings = Depends(get_settings)) -> StorageClient:
    # Storage no tiene RLS por JWT de usuario: el aislamiento por tenant lo
    # impone el backend construyendo las rutas desde el salon_id del token
    # verificado. El cliente usa siempre la secret key (bucket privado).
    return StorageClient(settings.supabase_url, settings.supabase_secret_key)


def require_plan(min_plan: str):
    """Gating premium revalidado en BACKEND (DIRECTRICES §4.5): exige que el
    plan de la CUENTA esté al nivel de `min_plan` o por encima en la jerarquía
    (basico < pro < elite). El plan sale de accounts.plan —desde la 0040—,
    fuente de verdad DESDE Stripe, nunca del cliente.

    YA SE APLICA A RUTAS REALES: la bandeja de conversaciones
    (routers/bot_conversaciones.py) y el estado del asistente
    (routers/bot_asistente.py). El comentario anterior decía que no se usaba en
    ninguna, y llevaba tiempo siendo falso.
    """

    async def _dependency(
        user: CurrentUser = Depends(get_current_user),
        db: PostgrestClient = Depends(get_postgrest_client),
    ) -> CurrentUser:
        try:
            cuenta = await leer_cuenta(db, user.salon_id, "plan")
        except PostgrestError:
            # ANTES ERA UN 502 Y AHORA SE DEJA PASAR, y el cambio es deliberado.
            # Una avería nuestra dejaba sin Asistente y sin bandeja a quien SÍ
            # los tiene contratados; que alguien use diez minutos una función de
            # más mientras PostgREST vuelve es incomparablemente más barato.
            #
            # OJO AL LÍMITE: esto vale para «qué incluye el plan» y NO para «la
            # cuenta está viva». La suspensión y la cancelación siguen fallando
            # cerradas donde están (`_exigir_sesion_valida`,
            # `estado_suscripcion`): una cuenta cancelada que falla abierta es
            # servicio gratis indefinido.
            logger.error(
                "No se pudo leer el plan de %s para exigir %r: SE DEJA PASAR.",
                user.salon_id,
                min_plan,
                exc_info=True,
            )
            return user

        # Un plan que no conocemos tampoco niega: `lo_que_incluye` lo trata como
        # «no resuelto» y ya ha chillado. Un precio nuevo en Stripe no puede
        # degradar a una cuenta que paga.
        if not lo_que_incluye(cuenta.get("plan")).alcanza(min_plan):
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail="Esta función requiere un plan superior",
            )
        return user

    return _dependency
