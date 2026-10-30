"""Corregir y anular un movimiento de caja (0092).

Las dos cosas pasan por una función de la base, en una sola transacción: la
fila, el stock y la línea del registro entran juntas o no entra ninguna. Solo
las puede llamar `service_role`, así que aquí se llaman con el cliente de
servicio DESPUÉS de que el router haya comprobado, con la sesión y su RLS, que
el movimiento es del centro de quien pregunta. Las funciones repiten además el
filtro por `salon_id`.

Los errores de la base llegan con nombre (`no_encontrado`, `ya_anulado`,
`sin_cambios`, `campo_no_corregible`, `stock_insuficiente`) y aquí se
convierten en lo que la persona necesita leer: qué ha pasado y qué hacer.
"""

import json
import logging
from typing import Literal

from fastapi import HTTPException, status

from app.billing.rastro import instantanea, usuario_de_la_sesion
from app.core.security import CurrentUser
from app.db.postgrest_client import PostgrestClient, PostgrestError

logger = logging.getLogger(__name__)

Movimiento = Literal["ingreso", "gasto"]

_NOMBRE = {"ingreso": "ingreso", "gasto": "gasto"}

_CAMPOS = {
    "fecha": "la fecha",
    "concepto": "el concepto",
    "importe": "el importe",
    "cantidad": "la cantidad",
    "categoria": "la categoría",
    "tipo": "el tipo",
    "estado": "el estado",
    "provider_id": "el proveedor",
}


def _error_de_la_base(e: PostgrestError, movimiento: Movimiento) -> HTTPException:
    """El error con nombre de la función, dicho para la persona."""
    # PostgREST manda {code, message, hint, details}; la función pone el nombre
    # del error en `message` y los datos en `hint`.
    try:
        cuerpo = json.loads(e.detail or "")
    except ValueError:
        cuerpo = {}
    if not isinstance(cuerpo, dict):
        cuerpo = {}
    codigo = cuerpo.get("message") or "" if cuerpo.get("code") == "P0001" else ""
    pista = cuerpo.get("hint") or ""
    que = _NOMBRE[movimiento]
    if codigo == "no_encontrado":
        return HTTPException(status.HTTP_404_NOT_FOUND, detail=f"No encontramos ese {que}. Recarga la página.")
    if codigo == "ya_anulado":
        return HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Este {que} ya está anulado: no se puede cambiar. Recarga la página para verlo.",
        )
    if codigo == "sin_cambios":
        return HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="No has cambiado nada. Cambia algún dato antes de guardar la corrección.",
        )
    if codigo == "campo_no_corregible":
        campo = _CAMPOS.get(pista, pista or "ese dato")
        return HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"En este {que} no se puede corregir {campo}.",
        )
    if codigo == "stock_insuficiente":
        try:
            datos = json.loads(pista)
        except ValueError:
            datos = {}
        producto = datos.get("producto") or "el producto"
        stock = datos.get("stock")
        unidades = datos.get("unidades")
        return HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "codigo": "stock_insuficiente",
                "mensaje": (
                    f"No se puede: habría que restar {unidades} uds de «{producto}» "
                    f"y solo quedan {stock}. Revisa el stock en Stock antes de seguir."
                ),
            },
        )
    logger.error("Caja: la base rechazó la operación (%s)", e.detail)
    return HTTPException(
        status.HTTP_502_BAD_GATEWAY,
        detail="No se ha podido guardar. No se ha cambiado nada: inténtalo de nuevo en unos segundos.",
    )


async def _quien(service: PostgrestClient, user: CurrentUser) -> dict:
    usuario_id = await usuario_de_la_sesion(service, user.sub) or user.sub
    nombres = await instantanea(service, account_id=None, salon_id=None, usuario_id=usuario_id)
    return {
        "p_usuario": usuario_id,
        "p_usuario_nombre": nombres.get("usuario_nombre"),
        "p_usuario_email": nombres.get("usuario_email") or user.email,
    }


async def anular(
    service: PostgrestClient, user: CurrentUser, movimiento: Movimiento, movimiento_id: str, motivo: str
) -> dict:
    try:
        filas = await service.rpc(
            "anular_movimiento_de_caja",
            {
                "p_salon": user.salon_id,
                "p_movimiento": movimiento,
                "p_id": movimiento_id,
                "p_motivo": motivo,
                **await _quien(service, user),
            },
        )
    except PostgrestError as e:
        raise _error_de_la_base(e, movimiento) from e
    return filas[0] if filas else {}


async def corregir(
    service: PostgrestClient,
    user: CurrentUser,
    movimiento: Movimiento,
    movimiento_id: str,
    cambios: dict,
    motivo: str,
) -> dict:
    try:
        filas = await service.rpc(
            "corregir_movimiento_de_caja",
            {
                "p_salon": user.salon_id,
                "p_movimiento": movimiento,
                "p_id": movimiento_id,
                "p_cambios": cambios,
                "p_motivo": motivo,
                **await _quien(service, user),
            },
        )
    except PostgrestError as e:
        raise _error_de_la_base(e, movimiento) from e
    return filas[0] if filas else {}


async def registro(service: PostgrestClient, salon_id: str, movimiento: Movimiento, movimiento_id: str) -> list[dict]:
    """Las correcciones y la anulación de un movimiento, de la más antigua a la última."""
    # TODAS: el registro de un movimiento no tiene techo (cada corrección suma una).
    return await service.select_todo(
        "correcciones_de_caja",
        {
            "select": "ocurrido_at,accion,antes,despues,motivo,stock_movido,"
            "stock_sin_ajustar,stock_sin_ajustar_detalle,usuario_nombre,usuario_email",
            "salon_id": f"eq.{salon_id}",
            "movimiento": f"eq.{movimiento}",
            "movimiento_id": f"eq.{movimiento_id}",
            "order": "ocurrido_at.asc,id.asc",
        },
    )
