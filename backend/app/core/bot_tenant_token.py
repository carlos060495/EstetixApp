"""Acceso a los datos de un salón desde el webhook de WhatsApp, con RLS.

EL PROBLEMA: a este webhook lo llama Meta, no una persona. No trae token, no
trae sesión y no trae cookie. Y sin embargo tiene que escribir en las tablas de
UN salón concreto.

LA SOLUCIÓN, que NO es nueva: es exactamente el mismo mecanismo que la sesión
de soporte de Super Admin. Se acuña un access_token real de un usuario
del salón contra GoTrue y se entra por PostgREST con él, de modo que la RLS del
inquilino se evalúa igual que si el mensaje lo hubiera escrito ese usuario desde
la app. El acuñado literal lo hace `acunar_token_tenant`, en
`tenant_token_broker.py`, compartido con la impersonación —incluida la
comprobación de identidad contra el `sub` que devuelve GoTrue—.

Lo que este módulo añade encima es la custodia: la de soporte va por sesión
(`sid`) y muere cuando el admin cierra; la del bot va por SALÓN y solo caduca,
porque un webhook no tiene sesión que cerrar.

QUÉ NO PASA POR AQUÍ (y por qué): las consultas de arranque —de qué salón es
este número de Meta, de qué cuenta es ese salón y qué usuario la representa—
se hacen con service_role, porque son justamente las que AVERIGUAN el salón: no
hay todavía un `salon_id` con el que acuñar nada. Es la misma frontera que ya
tiene `routers/impersonation.py`. Están acotadas a tres selects, sobre tres
tablas, y las dos de aquí se cachean con el token, así que ni siquiera se
repiten en cada mensaje. Todo lo demás —sesiones y mensajes del bot— va con el
token del salón.

EL TOKEN ES DE LA CUENTA, NO DEL CENTRO. Ver `_identidad_del_salon`: con dos
centros, este token ve los dos, y cada consulta que se haga con él tiene que
llevar su `salon_id`. Como esa frontera no puede estar en la base, la vigila un
test: `tests/test_consultas_publicas_filtran_por_centro.py` se pone rojo si
alguien añade una consulta alcanzable desde este token sin filtrar por centro.

REGLA DURA, heredada de la sesión de soporte: el token es una credencial
completa de un usuario del salón. No se persiste, no sale en ninguna respuesta
y no se escribe en ningún log, ni truncado ni dentro del mensaje de una
excepción. Vive en memoria del proceso y caduca solo.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock

from app.core.config import Settings
from app.core.tenant_token_broker import (
    TokenTenantNoAcunado,
    _cliente_auth,
    acunar_token_tenant,
)
from app.db.postgrest_client import PostgrestClient, PostgrestError

# Por debajo de la caducidad real del access_token de GoTrue (1 h por defecto),
# con margen de sobra para que un token entregado justo antes de vencer no
# llegue caducado a PostgREST.
_MINUTOS_CUSTODIA = 30

# salon_id -> (identidad, token, vence)
_custodia: dict[str, tuple["IdentidadSalon", str, datetime]] = {}
_candados_salon: dict[str, asyncio.Lock] = {}
# Protege los dos diccionarios de arriba. Es un Lock de threading a propósito:
# solo se mantiene durante accesos síncronos al diccionario, JAMÁS a través de
# un `await`, así que no puede bloquear el loop de eventos.
_lock = Lock()


class SalonSinBot(Exception):
    """El salón no puede atender el bot y NO es un fallo pasajero.

    Un salón sin usuario administrador enlazado con Supabase Auth no va a tener
    uno porque Meta reintente el webhook. El llamador responde 200 y se calla:
    reintentar sería quemar la cuota de Meta contra un problema de
    configuración que solo se arregla desde el panel.
    """


class BotSinAcceso(Exception):
    """No se pudo abrir el acceso al salón, y PUEDE que sea pasajero.

    GoTrue caído, PostgREST caído, un timeout. Aquí sí interesa que Meta
    reintente, así que el llamador responde 5xx. Va vacía, como
    `TokenTenantNoAcunado`: nada de emails ni de tokens dentro.
    """


@dataclass(frozen=True)
class IdentidadSalon:
    """Con qué usuario entra el bot en un salón."""

    salon_id: str
    email: str
    auth_user_id: str


def _ahora() -> datetime:
    # Envuelto en función para que los tests controlen el reloj sin parchear
    # datetime, igual que en tenant_token_broker.
    return datetime.now(timezone.utc)


def _purgar_caducados(ahora: datetime) -> None:
    """Purga perezosa: sin tarea de fondo, que con varias réplicas complica el
    despliegue. Cada acuñado barre lo caducado de CUALQUIER salón."""
    with _lock:
        for s in [s for s, (_, _, vence) in _custodia.items() if vence <= ahora]:
            del _custodia[s]


def _candado_para(salon_id: str) -> asyncio.Lock:
    with _lock:
        candado = _candados_salon.get(salon_id)
        if candado is None:
            candado = asyncio.Lock()
            _candados_salon[salon_id] = candado
        return candado


async def _identidad_del_salon(servicio: PostgrestClient, salon_id: str) -> IdentidadSalon:
    """Usuario con el que el bot entra en el salón: el admin de SU CUENTA.

    ANTES SE BUSCABA POR CENTRO, y el segundo centro nacía mudo. La regla era
    «un admin con `users.salon_id` = este centro», pero `users.salon_id` es la
    ficha de la persona, y la del titular apunta al primer centro. Un centro
    abierto con «Nuevo centro» no tiene a nadie con su `salon_id`, así que su
    bot callaba, su widget daba 503 y sus recordatorios fallaban, con Elite
    pagado. Medido el 16-09-2026.

    Y NO HACE FALTA UN ADMIN POR CENTRO: desde la 0039 la RLS abre al admin
    todos los centros de su cuenta (`salones_visibles()`). Es la misma persona
    que entra en los dos desde el panel.

    LO QUE ESTO SIGNIFICA PARA QUIEN CONSULTA: el token ve la CUENTA, no el
    centro. La RLS separa una cuenta de otra; lo que separa un centro de su
    hermano es el `salon_id` explícito de cada consulta. Ya era así para el
    primer centro, y por eso su widget enseñaba los servicios del segundo.

    Si hay varios admins gana el más antiguo, que es quien abrió la cuenta.
    Cualquier regla vale mientras sea DETERMINISTA; lo que no puede pasar es
    que dos mensajes seguidos del mismo cliente entren como usuarios distintos
    y el historial quede firmado por dos manos.
    """
    try:
        salones = await servicio.select(
            "salons", {"select": "account_id", "id": f"eq.{salon_id}", "limit": "1"}
        )
        cuenta = (salones[0].get("account_id") if salones else None) or None
        if cuenta is None:
            raise SalonSinBot
        filas = await servicio.select(
            "users",
            {
                "select": "email,auth_user_id",
                "account_id": f"eq.{cuenta}",
                "rol": "eq.admin",
                "order": "created_at.asc",
            },
        )
    except PostgrestError as e:
        raise BotSinAcceso from e

    for fila in filas:
        if fila.get("auth_user_id") and fila.get("email"):
            return IdentidadSalon(
                salon_id=salon_id,
                email=fila["email"],
                auth_user_id=fila["auth_user_id"],
            )
    raise SalonSinBot


async def cliente_del_salon(
    settings: Settings, servicio: PostgrestClient, salon_id: str
) -> PostgrestClient:
    """Cliente PostgREST que entra al salón como uno de sus usuarios.

    Todo lo que se haga con él pasa por la RLS del inquilino: si el código
    pidiera una fila de OTRO salón, PostgREST no la devolvería. Es la
    diferencia con `get_service_postgrest_client`, que salta la RLS y que aquí
    solo se usa —vía `servicio`— para las dos consultas de arranque.
    """
    ahora = _ahora()
    _purgar_caducados(ahora)

    with _lock:
        guardado = _custodia.get(salon_id)
        if guardado and guardado[2] > ahora:
            return _con_token(settings, guardado[1])

    candado = _candado_para(salon_id)
    async with candado:
        # Mientras esperábamos el candado, otro mensaje del mismo salón ha
        # podido acuñar ya: se reaprovecha en vez de golpear GoTrue otra vez.
        # Con una ráfaga de mensajes esto es la diferencia entre una sesión y
        # una por mensaje.
        ahora = _ahora()
        with _lock:
            guardado = _custodia.get(salon_id)
            if guardado and guardado[2] > ahora:
                return _con_token(settings, guardado[1])

        identidad = await _identidad_del_salon(servicio, salon_id)
        try:
            token = await acunar_token_tenant(
                _cliente_auth(settings), identidad.email, auth_user_id=identidad.auth_user_id
            )
        except TokenTenantNoAcunado as e:
            # Se traduce y se pierde el detalle A PROPÓSITO: lo único que el
            # llamador necesita saber es si reintentar. El email y el token no
            # cruzan esta frontera ni dentro de una excepción.
            raise BotSinAcceso from e

        with _lock:
            _custodia[salon_id] = (
                identidad,
                token,
                _ahora() + timedelta(minutes=_MINUTOS_CUSTODIA),
            )
        return _con_token(settings, token)


def _con_token(settings: Settings, token: str) -> PostgrestClient:
    return PostgrestClient(settings.supabase_url, settings.supabase_secret_key, token)


def reiniciar_custodia() -> None:
    """Solo para tests: deja la custodia vacía entre casos."""
    with _lock:
        _custodia.clear()
        _candados_salon.clear()
