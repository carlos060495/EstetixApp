"""Custodia del access_token del salón durante una sesión de soporte.

REGLA DURA: este token es una credencial completa del dueño. No se persiste en
BD, no se devuelve en ninguna respuesta, no se escribe en ningún log —ni
truncado, ni en un mensaje de excepción—. Vive en memoria del proceso y muere
con la sesión.

La caché es por proceso: con varias réplicas cada una acuña la suya. Es
correcto —solo genera alguna sesión de más en GoTrue, que caduca sola porque
nunca pedimos refresh token— y evita tener que compartir la credencial.

CONCURRENCIA — por qué hay dos mecanismos y no uno:
  - `purgar_token_tenant` es síncrono por contrato (lo consume código que no
    es `async`) y por tanto NO puede esperar un `asyncio.Lock`. No existe
    entonces un candado único capaz de cubrir "acuñar + purgar" de punta a
    punta sin volver `async` una función que no debe serlo.
  - Para el doble acuñado (dos `obtener_token_tenant` concurrentes del MISMO
    sid) SÍ hay dos lados `async`, así que un `asyncio.Lock` por sid basta:
    serializa las peticiones y la segunda reaprovecha lo que dejó la primera.
  - Para la carrera con `purgar_token_tenant` (que puede colarse mientras el
    acuñado está esperando a GoTrue, con el candado del sid libre porque
    `purgar_token_tenant` nunca lo toma) se usa un contador de generación por
    sid: si al terminar de acuñar la generación cambió, alguien purgó esa
    sesión mientras tanto y NO se reinserta el token en la custodia. La
    petición que ya estaba en vuelo igual recibe su token —ya se pagó el
    viaje a GoTrue por ella—, pero además se cierra ese token "de más" con
    `logout_local` (anti-resurrección) para que no quede una sesión viva en
    GoTrue que nadie —ni la custodia, que ya lo descartó— vaya a purgar
    nunca. Ese cierre es best-effort: un fallo suyo no propaga al llamador.

PURGA PEREZOSA: no hay tarea de fondo (con varias réplicas un job de limpieza
complica el despliegue sin necesidad). En su lugar, cada llamada a
`obtener_token_tenant` barre —antes de mirar su propio sid— cualquier entrada
ya caducada de CUALQUIER sid. Así una sesión de soporte que nadie cierra
explícitamente (el admin cierra la pestaña) no queda residente para siempre.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from threading import Lock

from fastapi import HTTPException, status

from app.core.config import Settings
from app.core.impersonation import MINUTOS_SESION
from app.db.supabase_auth_client import SupabaseAuthAdminClient, SupabaseAuthError

logger = logging.getLogger("estetix.tenant_token")

# Alineado con la duración de la sesión de soporte (app.core.impersonation):
# la sesión dura como mucho MINUTOS_SESION y no se prorroga, así que una
# entrada más longeva que eso no puede servir a ninguna petición legítima.
_MINUTOS_CUSTODIA = MINUTOS_SESION

_custodia: dict[str, tuple[str, datetime]] = {}
# Cuenta las purgas de cada sid. Un acuñado en vuelo compara la generación que
# vio al empezar contra la que hay al terminar: si difieren, se coló una purga
# en medio y no debe reinsertar el token (ver nota de concurrencia arriba).
_generacion: dict[str, int] = {}
_candados_sid: dict[str, asyncio.Lock] = {}
# Protege las tres estructuras de arriba. A propósito es un Lock de threading
# y no un asyncio.Lock: solo se mantiene durante accesos síncronos al
# diccionario, JAMÁS a través de un `await`, así que no puede bloquear el
# loop de eventos ni interferir con `_candados_sid`.
_lock = Lock()

# No se purgan las entradas de `_candados_sid`/`_generacion` en sí: son un
# Lock/entero por sid, no la credencial, y viven acotadas por cuántos sids
# distintos ha visto el proceso (una sesión de soporte por vez, por admin).
# Borrar el Lock de un sid mientras un acuñado sigue en vuelo con una
# referencia local a ese mismo objeto reabriría la ventana del doble acuñado,
# así que no compensa el riesgo para ahorrar unos bytes.


def _cliente_auth(settings: Settings) -> SupabaseAuthAdminClient:
    # Envuelto en función para que los tests lo sustituyan sin tocar la red.
    return SupabaseAuthAdminClient(settings.supabase_url, settings.supabase_secret_key)


def _ahora() -> datetime:
    # Envuelto en función para que los tests controlen el reloj sin parchear datetime.
    return datetime.now(timezone.utc)


def _extraer_hashed_token(payload: dict) -> str | None:
    propiedades = payload.get("properties") or payload
    valor = propiedades.get("hashed_token")
    return valor if isinstance(valor, str) and valor else None


class TokenTenantNoAcunado(Exception):
    """No se pudo acuñar el token del salón.

    Va VACÍA a propósito: quien la lanza no le mete el email, ni el `sub`, ni
    por supuesto el token. El llamador decide qué respuesta dar (aquí un 502
    con mensaje genérico; el webhook de WhatsApp, un 200 silencioso), pero
    nadie puede filtrar un dato por el camino aunque imprima la excepción.
    """


async def acunar_token_tenant(
    cliente: SupabaseAuthAdminClient, email: str, *, auth_user_id: str
) -> str:
    """Acuña un access_token real de un usuario del salón contra GoTrue.

    Es EL mecanismo: `generate_link(magiclink)` para obtener un `hashed_token`
    y `verify` para canjearlo por una sesión. El token que sale es el de un
    usuario de verdad, con su claim `salon_id`, así que todo lo que pase por
    PostgREST con él queda sujeto a la RLS del inquilino igual que la app.

    Lo comparten la sesión de soporte de Super Admin (`obtener_token_tenant`,
    aquí abajo) y el webhook de WhatsApp (`app/core/bot_tenant_token.py`).
    Está extraído en una función y no copiado porque la comprobación de
    IDENTIDAD de más abajo es la parte que no se puede olvidar en ninguno de
    los dos sitios, y una copia se olvida tarde o temprano.

    `auth_user_id` es keyword-only para que no se pueda colar por error en el
    sitio del email. Nunca se pide refresh token: la sesión caduca sola.
    """
    try:
        generado = await cliente.generate_magiclink(email)
        hashed = _extraer_hashed_token(generado)
        if not hashed:
            logger.warning("Token de inquilino: GoTrue no devolvió hashed_token.")
            raise TokenTenantNoAcunado
        sesion = await cliente.verify_magiclink(hashed)
    except SupabaseAuthError as e:
        # Sin el token ni el email: lo que hace falta saber es que GoTrue dijo
        # que no y con qué código.
        logger.warning("Token de inquilino: GoTrue rechazó el acuñado (%s).", e)
        raise TokenTenantNoAcunado from e

    token = sesion.get("access_token")
    if not isinstance(token, str) or not token:
        logger.warning("Token de inquilino: la sesión de GoTrue vino sin access_token.")
        raise TokenTenantNoAcunado

    # IDENTIDAD: se acuña eligiendo un `auth_user_id` concreto, pero GoTrue
    # acuña por EMAIL. Si `public.users.email` divergiera del email real en
    # GoTrue, `generate_link(magiclink)` puede CREAR un usuario nuevo (una
    # sesión que no ve nada, más un usuario espurio en GoTrue) o, con datos
    # corruptos, acuñar la identidad de OTRO salón: el backend creería
    # `salon_id = A` mientras PostgREST devolvería los datos de B.
    #
    # Sin `sub` la respuesta es la misma que con un `sub` distinto: "no sé a
    # quién pertenece esto" nunca puede ser "adelante". La credencial que se
    # llegó a acuñar se cierra en GoTrue (best-effort) para no dejarla viva.
    acunado_para = (sesion.get("user") or {}).get("id")
    if not isinstance(acunado_para, str) or acunado_para != auth_user_id:
        try:
            await cliente.logout_local(token)
        except Exception:
            logger.exception("Token de inquilino: no se pudo cerrar la credencial espuria.")
        # LA MÁS IMPORTANTE DE LAS CUATRO: aquí GoTrue ha acuñado una identidad
        # que NO es la que se pidió. Puede ser un email divergente entre
        # `public.users` y GoTrue, y puede ser algo peor. Se registra sin decir
        # de quién es ninguna de las dos identidades.
        logger.error("Token de inquilino: GoTrue acuñó una identidad distinta de la pedida.")
        raise TokenTenantNoAcunado

    return token


def _purgar_caducados(ahora: datetime) -> None:
    """Retira de la custodia toda entrada cuyo TTL ya venció."""
    with _lock:
        caducados = [s for s, (_, vence) in _custodia.items() if vence <= ahora]
        for s in caducados:
            del _custodia[s]


def _candado_para(sid: str) -> asyncio.Lock:
    with _lock:
        candado = _candados_sid.get(sid)
        if candado is None:
            candado = asyncio.Lock()
            _candados_sid[sid] = candado
        return candado


async def obtener_token_tenant(
    settings: Settings, sid: str, tenant_email: str, *, tenant_auth_user_id: str
) -> str:
    """Token del salón para esta sesión de soporte, acuñándolo si hace falta.

    `tenant_auth_user_id` es la identidad ELEGIDA al abrir la sesión
    (`routers/impersonation.py`), y va keyword-only para que no se pueda colar
    por error en el sitio del email. Se comprueba contra el `sub` que devuelve
    GoTrue antes de custodiar nada: ver la nota de IDENTIDAD más abajo.
    """
    ahora = _ahora()
    _purgar_caducados(ahora)

    with _lock:
        guardado = _custodia.get(sid)
        if guardado and guardado[1] > ahora:
            return guardado[0]

    candado = _candado_para(sid)
    async with candado:
        # Puede que, mientras esperábamos el candado, otra petición
        # concurrente del mismo sid ya haya acuñado: reaprovechamos su
        # resultado en vez de volver a golpear GoTrue.
        ahora = _ahora()
        with _lock:
            guardado = _custodia.get(sid)
            if guardado and guardado[1] > ahora:
                return guardado[0]
            generacion_inicial = _generacion.get(sid, 0)

        cliente = _cliente_auth(settings)
        # El acuñado propiamente dicho —incluida la comprobación de identidad—
        # vive en `acunar_token_tenant`, compartido con el webhook de WhatsApp.
        # Aquí solo se traduce su fallo al 502 con el mensaje genérico del
        # módulo: ni el token, ni el email, ni el `sub` recibido entran en el
        # error ni en ningún log.
        try:
            token = await acunar_token_tenant(
                cliente, tenant_email, auth_user_id=tenant_auth_user_id
            )
        except TokenTenantNoAcunado as e:
            # El motivo ya quedó escrito donde se abandonó el acuñado (arriba);
            # aquí solo se anota que la sesión de soporte no llegó a abrirse.
            logger.warning("Sesión de soporte: no se pudo acuñar el token del salón.")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="No se pudo abrir la sesión de soporte.",
            ) from e

        # LA BASE TIENE QUE RECONOCER A SOPORTE (0086). Este token es una sesión
        # real de la titular, y con la cuenta cortada o suspendida la base le
        # cerraría todo lo que no es la agenda. Se apunta su `session_id` en la
        # sesión de soporte, y `centros_que_trabajan()` lo reconoce mientras esa
        # sesión siga viva. Antes de entregar el token: si no se apunta, soporte
        # entraría a ciegas en una cuenta cortada sin saber por qué.
        await _apuntar_la_sesion_de_soporte(settings, sid, token)

        with _lock:
            sesion_purgada_en_vuelo = _generacion.get(sid, 0) != generacion_inicial
            if not sesion_purgada_en_vuelo:
                _custodia[sid] = (token, _ahora() + timedelta(minutes=_MINUTOS_CUSTODIA))

        if sesion_purgada_en_vuelo:
            # Anti-resurrección: `purgar_token_tenant` se coló mientras
            # acuñábamos. No reinsertamos el token en la custodia, y además
            # cerramos esta sesión "de más" en GoTrue para no dejarla viva
            # para siempre. Es limpieza best-effort: si GoTrue no responde,
            # el sistema ya la considera cerrada (no queda en custodia ni hay
            # endpoint que la use) y de todos modos caducará sola porque
            # nunca pedimos refresh token, así que un fallo aquí NO debe
            # propagar al llamador —que ya tiene su token y nada puede hacer
            # con un error de limpieza—. Nunca se registra el token, ni
            # siquiera en el mensaje de una excepción que se descarta.
            try:
                await cliente.logout_local(token)
            except Exception:
                pass
        return token


async def _apuntar_la_sesion_de_soporte(settings: Settings, sid: str, token: str) -> None:
    """`impersonation_sessions.auth_session_id` = el `session_id` del token (0086).

    Solo se LEE el claim del token que GoTrue acaba de emitir: no se verifica la
    firma porque no se decide nada con él, solo se apunta en nuestra fila (con la
    clave de servicio: la titular no puede escribir en esa tabla). Si falla, se
    corta la sesión de soporte con el mismo 502 que si no se hubiera podido
    acuñar: entrar sin poder ver lo que hay que mirar no es entrar.
    """
    import jwt

    from app.db.postgrest_client import PostgrestClient, PostgrestError

    session_id = jwt.decode(token, options={"verify_signature": False}).get("session_id")
    try:
        filas = await PostgrestClient(settings.supabase_url, settings.supabase_secret_key).update(
            "impersonation_sessions", {"id": f"eq.{sid}"}, {"auth_session_id": session_id}
        )
    except PostgrestError:
        filas = []
    if not session_id or not filas:
        logger.error("Sesión de soporte: no se pudo apuntar su session_id; no se abre.")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo abrir la sesión de soporte.",
        )


def purgar_token_tenant(sid: str) -> str | None:
    """Olvida el token y lo devuelve, para poder cerrar su sesión en GoTrue."""
    with _lock:
        _generacion[sid] = _generacion.get(sid, 0) + 1
        guardado = _custodia.pop(sid, None)
    return guardado[0] if guardado else None


async def expulsar_token_tenant(settings: Settings, sid: str) -> None:
    """Saca el token de la custodia Y cierra su sesión en GoTrue.

    Se llama en cuanto el backend detecta que la sesión de soporte ha muerto
    (revocada por el dueño, caducada o desaparecida de la BD). Sin esto la
    credencial del dueño seguiría residente en memoria y viva en GoTrue hasta
    que la purga perezosa la tirase, y esa purga solo se dispara al acuñar
    OTRA sesión: la revocación dejaría de ser inmediata justo en el punto en
    el que más importa que lo sea.
    """
    token = purgar_token_tenant(sid)
    if not token:
        return
    # Best-effort, por el mismo motivo que la anti-resurrección de arriba: si
    # GoTrue no responde, el sistema ya da la sesión por cerrada (ya no está en
    # custodia) y el token caduca solo porque nunca pedimos refresh token. Un
    # fallo aquí NO debe tumbar el 401 que el llamador está a punto de dar.
    # Nunca se registra el token, ni siquiera en el mensaje de una excepción.
    try:
        await _cliente_auth(settings).logout_local(token)
    except Exception:
        pass


def reiniciar_custodia() -> None:
    """Solo para tests: deja la custodia vacía entre casos."""
    with _lock:
        _custodia.clear()
        _generacion.clear()
        _candados_sid.clear()
