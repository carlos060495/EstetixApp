"""El WhatsApp del salón, para la pantalla «Asistente IA».

POR QUÉ EXISTE: la página decía «próximamente» cuando el bot ya estaba
construido y atendiendo. Para decir la verdad hay que saber si el salón tiene
su número conectado y en qué estado está.

DESDE LA 0085, EL SALÓN CONECTA SU PROPIO NÚMERO desde aquí, con el diálogo de
Meta (Embedded Signup), y lo desconecta. Lo de Meta vive en
`core/whatsapp_conexion.py`; esto solo es la puerta.

CUATRO ESTADOS: sin conectar, atendiendo, apagado y CON PROBLEMA. El último es
el que no existía: cuando Meta rechaza los envíos (token caducado, número
bloqueado), el salón se quedaba mudo y solo quedaba una línea en un registro.
Ahora `enviar_texto` lo apunta en la fila del número y esto lo enseña: qué
pasa, desde cuándo y qué hacer.

CREDENCIALES: el estado se lee con el token del salón y RLS. Las credenciales
no: ni este salón ni ningún otro las lee; conectar y desconectar pasan por dos
funciones de la base que solo puede llamar el backend.

VARIAS FILAS POR SALÓN A PROPÓSITO: la 0024 permite dos sedes con dos números.
Se devuelve la lista entera y no «el» número.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from app.core import demo, publico
from app.billing.plans import FEATURE_MIN_PLAN
from app.core import whatsapp_conexion
from app.core.config import Settings, get_settings
from app.core.security import CurrentUser, get_postgrest_client, get_service_postgrest_client, require_plan
from app.core.whatsapp_cloud import VERSION_GRAPH
from app.db.postgrest_client import PostgrestClient, PostgrestError
from app.schemas.bot import (
    ConectarIn,
    DatosDeMeta,
    DesconectarIn,
    EstadoAsistente,
    NumeroConectado,
    ProblemaDeEnvio,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["bot"], dependencies=[publico.ADMINISTRADORA])

# Mismo plan que la pantalla que lo consume. El muro del frontend es solo UX
# (DIRECTRICES §4.5); quien de verdad lo impide es esto.
exige_plan_asistente = require_plan(FEATURE_MIN_PLAN["asistente"])


def explicar_problema(codigo: str | None) -> tuple[str, str]:
    """(qué pasa, qué hacer) para un código de error de Meta, en castellano llano.

    Los códigos son los de la Graph API: https://developers.facebook.com/docs/whatsapp/cloud-api/support/error-codes
    """
    base = (codigo or "").split("/")[0]
    if base in ("190", "102", "10", "200", "3"):
        return ("Meta ha dejado de aceptar la conexión de tu WhatsApp: ha caducado o se ha retirado el permiso.",
                "Desconecta tu número y vuelve a conectarlo desde aquí.")
    if base in ("131031", "368", "131048"):
        return ("Meta ha bloqueado o limitado tu cuenta de WhatsApp Business.",
                "Revísalo en WhatsApp Manager, dentro de tu cuenta de Meta. Cuando lo desbloqueen, el asistente "
                "vuelve a contestar solo.")
    if base in ("133010", "133000", "131030"):
        return ("Tu número no está activado en WhatsApp Business.",
                "Desconecta tu número y vuelve a conectarlo desde aquí.")
    if base == "131042":
        return ("Meta no puede cobrar los mensajes de tu cuenta de WhatsApp Business.",
                "Revisa el método de pago en tu cuenta de Meta Business.")
    if base == "registro":
        return ("Meta no ha dejado activar tu número al conectarlo.",
                "Desconecta tu número y vuelve a conectarlo. Si vuelve a pasar, escríbenos con el detalle de abajo.")
    if base == "sin_credenciales":
        return ("Tu número no tiene permiso para enviar mensajes.",
                "Desconecta tu número y vuelve a conectarlo desde aquí.")
    if base == "credenciales":
        return ("No podemos leer los permisos guardados de tu número. Es un problema nuestro.",
                "Escríbenos a soporte@estetixapp.es y lo arreglamos.")
    if base == "red":
        return ("No hemos podido contactar con Meta para enviar.",
                "Suele arreglarse solo en unos minutos. Si sigue así, escríbenos.")
    return ("Meta está rechazando los mensajes de tu asistente.",
            "Escríbenos a soporte@estetixapp.es con el código de abajo y lo miramos.")


@router.get("/bot/estado", response_model=EstadoAsistente)
async def estado_asistente(
    user: CurrentUser = Depends(exige_plan_asistente),
    db: PostgrestClient = Depends(get_postgrest_client),
    settings: Settings = Depends(get_settings),
) -> dict:
    """¿Está el WhatsApp de este salón conectado, y funcionando?"""
    try:
        filas = await db.select(
            "salon_whatsapp_config",
            {
                "select": "id,phone_number_id,display_phone_number,is_active,conectado_at,"
                          "problema_desde,problema_codigo,problema_detalle",
                "salon_id": f"eq.{user.salon_id}",
                "phone_number_id": "not.is.null",
                "order": "created_at.asc",
            },
        )
        fallos = await db.select(
            "whatsapp_envios_fallidos",
            # Los de los últimos 90 días (lo más viejo lo borra `apuntar_envio`), con tope.
            {"select": "config_id,ocurrido_at", "salon_id": f"eq.{user.salon_id}",
             "order": "ocurrido_at.desc", "limit": "500"},
        ) if any(f.get("problema_desde") for f in filas) else []
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo leer el estado del asistente.",
        ) from e

    numeros = []
    for f in filas:
        problema = None
        if f.get("problema_desde"):
            que_pasa, que_hacer = explicar_problema(f.get("problema_codigo"))
            problema = ProblemaDeEnvio(
                desde=f["problema_desde"],
                que_pasa=que_pasa,
                que_hacer=que_hacer,
                codigo=f.get("problema_codigo"),
                detalle=f.get("problema_detalle"),
                envios_fallidos=sum(1 for x in fallos if x.get("config_id") == f["id"]
                                    and str(x.get("ocurrido_at")) >= str(f["problema_desde"])),
            )
        numeros.append(NumeroConectado(
            id=f["id"],
            # `display_phone_number` puede venir vacío: se enseña el hueco.
            numero=(f.get("display_phone_number") or None),
            activo=bool(f.get("is_active")),
            propio=bool(f.get("conectado_at")),
            problema=problema,
        ))
    if not numeros:
        estado = "sin_conectar"
    elif any(n.problema and n.activo for n in numeros):
        estado = "con_problema"
    elif any(n.activo for n in numeros):
        estado = "atendiendo"
    else:
        estado = "apagado"
    return {
        "estado": estado,
        "conectado": bool(numeros),
        "activo": any(n.activo for n in numeros),
        "numeros": numeros,
        "puede_conectar": whatsapp_conexion.disponible(settings) and not user.es_demo,
    }


@router.get("/bot/whatsapp/meta", response_model=DatosDeMeta)
async def datos_de_meta(
    _user: CurrentUser = Depends(exige_plan_asistente),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Lo que el botón necesita para abrir el diálogo de Meta. Son datos públicos."""
    return {
        "disponible": whatsapp_conexion.disponible(settings),
        "app_id": settings.whatsapp_app_id or None,
        "config_id": settings.whatsapp_es_config_id or None,
        "version": VERSION_GRAPH,
    }


@router.post("/bot/whatsapp/conectar", response_model=EstadoAsistente)
async def conectar_whatsapp(
    payload: ConectarIn,
    user: CurrentUser = Depends(exige_plan_asistente),
    db: PostgrestClient = Depends(get_postgrest_client),
    servicio: PostgrestClient = Depends(get_service_postgrest_client),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Conecta el número que el salón acaba de elegir en el diálogo de Meta."""
    # UNA DEMO NO CONECTA NADA (0080): ni aquí ni en la base (`conectar_whatsapp`).
    demo.no_en_una_demo(user.es_demo, "whatsapp")
    try:
        await whatsapp_conexion.conectar(
            settings, servicio, salon_id=user.salon_id, code=payload.code, waba_id=payload.waba_id,
            phone_number_id=payload.phone_number_id, coexistencia=payload.coexistencia,
        )
    except whatsapp_conexion.ConexionError as e:
        raise HTTPException(status_code=e.status, detail=e.mensaje) from None
    except PostgrestError:
        logger.exception("WhatsApp: no se pudo guardar la conexión.")
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail="No hemos podido guardar la conexión. Inténtalo de nuevo.") from None
    return await estado_asistente(user=user, db=db, settings=settings)


@router.post("/bot/whatsapp/desconectar", response_model=EstadoAsistente)
async def desconectar_whatsapp(
    payload: DesconectarIn,
    user: CurrentUser = Depends(exige_plan_asistente),
    db: PostgrestClient = Depends(get_postgrest_client),
    servicio: PostgrestClient = Depends(get_service_postgrest_client),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Deja de atender por ese número y retira sus credenciales."""
    demo.no_en_una_demo(user.es_demo, "whatsapp")
    try:
        await whatsapp_conexion.desconectar(settings, servicio, salon_id=user.salon_id, config_id=payload.numero_id)
    except PostgrestError as e:
        # La base dice «ese número no está conectado en este salón» (42501): no
        # se distingue de «no existe», a propósito.
        if "42501" in (e.detail or "") or e.status_code in (400, 403):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail="Ese número no está conectado en tu salón.") from None
        logger.exception("WhatsApp: no se pudo desconectar.")
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail="No hemos podido desconectar el número. Inténtalo de nuevo.") from None
    return await estado_asistente(user=user, db=db, settings=settings)
