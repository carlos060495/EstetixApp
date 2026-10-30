"""El rastro de las decisiones de dinero (0054): quién, cuándo, desde dónde.

Una fila por cada baja, reanudación, cambio de plan o anulación de un cambio
programado. La base impide editarla o borrarla (permisos + trigger); aquí solo
se ESCRIBE, y siempre DESPUÉS de que Stripe acepte: una fila apuntada antes
podría contar algo que no pasó.

DOS FORMAS DE LLEGAR:

  · Desde nuestros endpoints, con el usuario de la sesión y el `req_…` de la
    llamada a Stripe (`peticion_de`), que es el mismo que el evento traerá
    luego en `request.id`.
  · Desde el webhook, para lo que NO hacemos nosotros (el portal, el panel de
    Stripe, otra integración) o lo que hace Stripe por un calendario nuestro.
    Lo nuestro no se apunta dos veces: se reconoce por la firma (`firma.py`).

LO QUE NO SE APUNTA NUNCA: la IP (decidido; ver la cabecera de la 0054).

UNA INSTANTÁNEA DE LOS NOMBRES (0057). Los ids se guardan sueltos, sin clave
ajena, y además el nombre de la cuenta, el del centro y el nombre y el correo
de la persona EN ESE MOMENTO. Dentro de 90 días esa fila de usuario ya no
existirá, y el registro tiene que seguir diciendo quién fue. La usan los dos
registros: este y el de descargas (`routers/datos.py`).

SI NO SE PUEDE ESCRIBIR. En un endpoint, la decisión ya está hecha en Stripe:
devolver un error haría que el salón la repitiera. Se deja entera en el log
de error —para poder recuperarla a mano— y la petición sigue. En el webhook,
en cambio, se deja subir: Stripe reintenta, y el índice único por evento
impide que el reintento deje dos filas.
"""

from __future__ import annotations

import logging

from app.db.postgrest_client import PostgrestClient, PostgrestError

logger = logging.getLogger("estetix.rastro")

TABLA = "decisiones_de_dinero"

# Para los motivos escritos en palabras: el plan como lo lee una persona.
NOMBRE_DEL_PLAN = {"basico": "Básico", "pro": "Pro", "elite": "Elite"}


def peticion_de(objeto) -> str | None:
    """El `req_…` de la respuesta de Stripe, si la trae."""
    respuesta = getattr(objeto, "last_response", None)
    return getattr(respuesta, "request_id", None) if respuesta is not None else None


async def usuario_de_la_sesion(servicio: PostgrestClient, auth_user_id: str | None) -> str | None:
    """`users.id` de quien tiene la sesión (el token trae el id de Auth)."""
    if not auth_user_id:
        return None
    try:
        filas = await servicio.select(
            "users", {"select": "id", "auth_user_id": f"eq.{auth_user_id}", "limit": "1"}
        )
    except PostgrestError:
        logger.exception("Rastro: no se pudo resolver el usuario %s.", auth_user_id)
        return None
    return filas[0]["id"] if filas else None


async def instantanea(
    servicio: PostgrestClient,
    *,
    account_id: str | None,
    salon_id: str | None,
    usuario_id: str | None,
) -> dict:
    """Los nombres de ahora de lo que la fila va a nombrar por id.

    Si alguna lectura falla, ese nombre va vacío y la fila se escribe igual: un
    nombre que falta es peor que ninguno, pero perder la fila es peor aún.
    """

    async def uno(tabla: str, columnas: str, id_: str | None) -> dict:
        if not id_:
            return {}
        try:
            filas = await servicio.select(tabla, {"select": columnas, "id": f"eq.{id_}", "limit": "1"})
        except PostgrestError:
            logger.exception("Rastro: no se pudo leer %s %s para la instantánea.", tabla, id_)
            return {}
        return filas[0] if filas else {}

    cuenta = await uno("accounts", "nombre", account_id)
    centro = await uno("salons", "nombre_comercial", salon_id)
    usuario = await uno("users", "nombre,email", usuario_id)
    return {
        "cuenta_nombre": cuenta.get("nombre"),
        "centro_nombre": centro.get("nombre_comercial"),
        "usuario_nombre": usuario.get("nombre"),
        "usuario_email": usuario.get("email"),
    }


async def apuntar_decision(
    servicio: PostgrestClient,
    *,
    account_id: str,
    suceso: str,
    origen: str,
    salon_id: str | None = None,
    usuario_id: str | None = None,
    motivo: str | None = None,
    plan_antes: str | None = None,
    plan_despues: str | None = None,
    programado_para: str | None = None,
    stripe_peticion: str | None = None,
    stripe_evento: str | None = None,
    centros_en_pausa: tuple[str, ...] | list[str] | None = None,
    desde_el_webhook: bool = False,
) -> None:
    fila = {
        "account_id": account_id,
        "salon_id": salon_id,
        "suceso": suceso,
        "origen": origen,
        "usuario_id": usuario_id,
        "motivo": motivo or None,
        "plan_antes": plan_antes,
        "plan_despues": plan_despues,
        "programado_para": programado_para,
        "stripe_peticion": stripe_peticion,
        "stripe_evento": stripe_evento,
        **await instantanea(servicio, account_id=account_id, salon_id=salon_id, usuario_id=usuario_id),
    }
    if centros_en_pausa is not None:
        # Qué centros quedaron (o quedarán) en pausa con esta decisión (0088).
        fila["centros_en_pausa"] = list(centros_en_pausa)
    try:
        await servicio.insert(TABLA, fila, select="id")
    except PostgrestError as e:
        if desde_el_webhook:
            if e.status_code == 409:
                return  # el mismo evento, reintentado: ya está apuntado
            raise
        logger.error("RASTRO NO ESCRITO (la decisión SÍ se hizo en Stripe): %s", fila)
