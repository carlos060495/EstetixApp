"""LLEVARSE LOS DATOS: el volcado de la cuenta, como descarga directa.

Medido en producción el 30-09-2026 con la cuenta más grande (2 centros, 272
filas): 1,2 s y 28 KB. Muy por debajo del tope fijado para hacerlo en
directo (10 s y 50 MB), así que no hay cola ni fichero aparte: se pulsa y se
baja. El ZIP se arma en memoria. El día que una cuenta se acerque al tope, este
es el sitio que hay que rehacer.

QUIÉN: la administradora, en todos los planes (llevarse lo tuyo no es una
función de pago), VOLVIENDO A ESCRIBIR SU CONTRASEÑA DE INICIO DE SESIÓN,
siempre (01-10-2026). No el PIN de gestión: una barrera que se apaga
sola cuando el dato no está configurado no es una barrera, y la contraseña la
tiene toda cuenta. Se comprueba contra el servicio de autenticación en ese
momento, nunca contra una copia nuestra (`core/contrasena.py`).

Y UNA CUENTA SUSPENDIDA TAMBIÉN, por la misma puerta estrecha que la baja
(`*_aunque_suspendida`, con su guarda en `tests/test_la_puerta_de_la_suspension.py`).
Tiene sesión, así que puede volver a identificarse.

SOPORTE NO: la descarga está en BLOQUEADOS (`impersonation_policy.py`). Sacar
del salón los datos personales de sus clientas no es algo que el dueño delegue
al pedir ayuda.

QUEDA RASTRO (0056, `descargas_de_datos`): cada descarga entregada y cada
intento rechazado por la contraseña. Si la fila de una entregada no se puede
escribir, el ZIP NO se entrega: una salida de datos sin constancia no sale.

SE LEE CON LA CLAVE DE SERVICIO, ACOTADO A SU CUENTA (0086). Hasta la 0086 se
leía con el token del salón y la RLS hacía de frontera. Desde que la base corta
a una cuenta sin suscripción viva o suspendida, ese token ya no lee sus clientas
—ni su caja, ni su stock—, y llevarse los datos es precisamente una de las tres
salidas que una cuenta cortada conserva SIEMPRE. La cuenta sale de la sesión
(`id_de_cuenta` con el token, que sí lee su propio centro), y cada tabla se
filtra por los centros de esa cuenta o por la cuenta misma: lo vigila
`tests/test_llevarse_los_datos_base_real.py`, que comprueba que en el ZIP no
entra nada de otra cuenta.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app.core import demo, publico
from app.billing.rastro import instantanea, usuario_de_la_sesion
from app.core.admin_security import AdminLoginRateLimiter
from app.core.config import Settings, get_settings
from app.core.contrasena import comprobar_contrasena, minutos_hasta
from app.core.cuenta import id_de_cuenta
from app.core.security import (
    CurrentUser,
    get_postgrest_client_aunque_suspendida,
    get_service_postgrest_client,
    require_admin_aunque_suspendida,
)
from app.core import zona as zonas
from app.core.volcado import Datos, construir_zip
from app.db.postgrest_client import PostgrestClient, PostgrestError

router = APIRouter(prefix="/cuenta/datos", tags=["cuenta"], dependencies=[publico.ADMINISTRADORA_AUNQUE_SUSPENDIDA])
logger = logging.getLogger("estetix.datos")

# ── El límite ──────────────────────────────────────────────────────────────
#
# TRES POR HORA Y CUENTA, UNA A LA VEZ, Y COMO MUCHO DOS A LA VEZ EN TODO EL
# PROCESO. Tres dejan repetir una descarga que falló o que se guardó donde no
# era; nadie necesita más en una hora. La cuenta es la unidad y no la sesión:
# dos administradoras de la misma cuenta comparten el cupo. Se cuenta AL
# EMPEZAR, no al terminar bien: si no, una descarga que falla a propósito se
# podría repetir sin fin. Una contraseña mal puesta no gasta cupo: tiene su
# propio contador y su espera, los del PIN de gestión.
#
# Vive en memoria, como el de los PDF del RGPD: vale para un proceso. Con varias
# réplicas el tope real sería tres por réplica, que sigue siendo poco.
POR_HORA = 3
_cupo = AdminLoginRateLimiter(max_fails=POR_HORA, window_minutes=60)
_en_curso: set[str] = set()
_a_la_vez = asyncio.Semaphore(2)

PAGINA = 1000  # el `max_rows` de PostgREST: se pide por páginas hasta vaciar
REGISTRO = "descargas_de_datos"

CODIGO_FALTA = "falta_contrasena"
CODIGO_INCORRECTA = "contrasena_incorrecta"
CODIGO_EN_ESPERA = "contrasena_en_espera"


class DatosPreviewOut(BaseModel):
    centros: int


# ── Leer ───────────────────────────────────────────────────────────────────


async def _todas(db: PostgrestClient, tabla: str, columnas: str, filtro: dict, orden: str) -> list[dict]:
    filas: list[dict] = []
    while True:
        lote = await db.select(
            tabla,
            {"select": columnas, **filtro, "order": orden, "limit": str(PAGINA), "offset": str(len(filas))},
        )
        filas += lote
        if len(lote) < PAGINA:
            return filas


def _en(ids) -> str:
    return f"in.({','.join(ids)})"


async def leer_datos(db: PostgrestClient, account_id: str, servicio: PostgrestClient | None = None) -> Datos:
    centros = await db.select(
        "salons",
        {"select": "id,nombre_comercial,direccion,telefono,email,created_at,reservas_online_activo,"
                   "recordatorios_email_activo,recordatorio_horas,zona_horaria",
         "account_id": f"eq.{account_id}", "order": "created_at.asc,id.asc"},
    )
    por_salon = {"salon_id": _en(c["id"] for c in centros)}

    async def t(tabla, columnas, orden="id.asc", filtro=por_salon):
        return await _todas(db, tabla, columnas, filtro, orden)

    # UNA LISTA DE COLUMNAS POR TABLA, nunca `*`: lo que no está aquí no sale.
    # Y `*` además fallaría: `employees.pin_fichaje_hash` no se deja leer.
    (clientas, citas, servicios, categorias, empleadas, puestos, fichajes, ventas, productos,
     conversaciones, mensajes, rechazos_recordatorio, gastos, proveedores, horario_centro, excepciones_centro,
     usuarios, bot, cuenta) = await asyncio.gather(
        # Visitas y gastado, calculados de las citas (vista 0058), no las
        # columnas que no escribía nadie.
        t("clientas_con_actividad", "id,salon_id,nombre,telefono,email,cumpleanos,alergias,notas,vip,"
                     "total_gastado,visitas,created_at,recordatorios_baja_at", "created_at.asc,id.asc"),
        t("appointments", "id,salon_id,client_id,employee_id,service_id,inicio,fin,estado,notas,canal,"
                          "created_at,nombre_reserva", "inicio.asc,id.asc"),
        t("services", "id,salon_id,category_id,nombre,precio,duracion_min,visible_online", "nombre.asc,id.asc"),
        t("service_categories", "id,salon_id,nombre"),
        t("employees", "id,salon_id,nombre,puesto_id,telefono,email,estado,comision_tipo,"
                       "comision_servicios,comision_productos", "nombre.asc,id.asc"),
        t("puestos", "id,salon_id,nombre"),
        t("time_entries", "id,salon_id,employee_id,tipo,ts", "ts.asc,id.asc"),
        t("incomes", "id,salon_id,fecha,concepto,tipo,importe,product_id,cantidad,anulado_at,anulado_motivo",
          "fecha.asc,id.asc"),
        t("products", "id,salon_id,category_id,nombre,marca,codigo_barras,precio,precio_coste,stock,"
                      "stock_min,venta_publico,consumo_interno", "nombre.asc,id.asc"),
        t("bot_sessions", "id,salon_id,customer_phone"),
        t("bot_messages", "id,salon_id,session_id,role,content,created_at", "session_id.asc,created_at.asc,id.asc"),
        # Quién dijo «no» cuando el asistente le ofreció el recordatorio por correo
        # (0037). La baja del pie de los correos ya viene con la clienta.
        t("clients", "id,salon_id,recordatorios_email_rechazado_at", "recordatorios_email_rechazado_at.asc,id.asc",
          {**por_salon, "recordatorios_email_rechazado_at": "not.is.null"}),
        t("expenses", "id,salon_id,fecha,categoria,provider_id,tipo,estado,importe,product_id,cantidad,anulado_at,"
                      "anulado_motivo", "fecha.asc,id.asc"),
        t("providers", "id,salon_id,nombre,categoria,telefono,email,notas,created_at", "nombre.asc,id.asc"),
        t("salon_schedule", "id,salon_id,dia_semana,cerrado,apertura,cierre,tiene_pausa,pausa_inicio,pausa_fin",
          "dia_semana.asc,id.asc"),
        t("salon_schedule_exceptions", "id,salon_id,fecha_inicio,fecha_fin,cerrado,apertura,cierre,tiene_pausa,"
                                       "pausa_inicio,pausa_fin,motivo", "fecha_inicio.asc,id.asc"),
        t("users", "id,salon_id,nombre,email,rol,created_at", "created_at.asc,id.asc", {"account_id": f"eq.{account_id}"}),
        t("salon_whatsapp_config", "id,salon_id,bot_tono,bot_politicas"),
        db.select("accounts", {"select": "nombre,cif", "id": f"eq.{account_id}", "limit": "1"}),
    )
    # LAS CORRECCIONES DE FICHAJE (0061): son parte del registro de jornada y
    # salen con él. El registro no tiene políticas, así que se lee con la clave
    # de servicio, y solo de los centros que la RLS acaba de devolver.
    correcciones_fichaje = (
        await _todas(servicio, "correcciones_de_fichaje",
                     "id,salon_id,employee_id,ocurrido_at,accion,marca_id,tipo,ts_antes,ts_despues,motivo,"
                     "usuario_nombre,usuario_email,empleada_nombre,sobre_si_misma",
                     por_salon, "ocurrido_at.asc,id.asc")
        if servicio is not None and centros else []
    )
    # LAS CORRECCIONES Y ANULACIONES DE CAJA (0092), con el mismo criterio: el
    # registro es parte de la caja y sale con ella.
    correcciones_caja = (
        await _todas(servicio, "correcciones_de_caja",
                     "id,salon_id,ocurrido_at,movimiento,movimiento_id,accion,antes,despues,motivo,stock_movido,"
                     "stock_sin_ajustar,stock_sin_ajustar_detalle,usuario_nombre,usuario_email",
                     por_salon, "ocurrido_at.asc,id.asc")
        if servicio is not None and centros else []
    )
    # Lo de cada empleada cuelga de la empleada, no del centro.
    por_empleada = {"employee_id": _en(e["id"] for e in empleadas)} if empleadas else None
    if por_empleada:
        horario_empleadas, excepciones_empleadas, empleada_servicios = await asyncio.gather(
            t("employee_schedule", "id,employee_id,dia_semana,cerrado,apertura,cierre,tiene_pausa,pausa_inicio,"
                                   "pausa_fin", "dia_semana.asc,id.asc", por_empleada),
            t("employee_schedule_exceptions", "id,employee_id,fecha_inicio,fecha_fin,cerrado,apertura,cierre,"
                                              "tiene_pausa,pausa_inicio,pausa_fin,motivo", "fecha_inicio.asc,id.asc",
              por_empleada),
            t("employee_services", "employee_id,service_id", "employee_id.asc,service_id.asc", por_empleada),
        )
    else:
        horario_empleadas, excepciones_empleadas, empleada_servicios = [], [], []
    return Datos(
        centros=centros, clientas=clientas, citas=citas, servicios=servicios, categorias=categorias,
        empleadas=empleadas, puestos=puestos, fichajes=fichajes, ventas=ventas, productos=productos,
        conversaciones=conversaciones, mensajes=mensajes, rechazos_recordatorio=rechazos_recordatorio, gastos=gastos,
        proveedores=proveedores, horario_centro=horario_centro, excepciones_centro=excepciones_centro,
        horario_empleadas=horario_empleadas, excepciones_empleadas=excepciones_empleadas,
        empleada_servicios=empleada_servicios, usuarios=usuarios, bot=bot,
        correcciones_fichaje=correcciones_fichaje,
        correcciones_caja=correcciones_caja,
        cuenta=cuenta[0] if cuenta else {},
    )


# ── El rastro ──────────────────────────────────────────────────────────────


async def apuntar_descarga(
    servicio: PostgrestClient, user: CurrentUser, account_id: str, resultado: str, **cuanto
) -> None:
    """Una fila en `descargas_de_datos` (0056). Lanza si no se puede escribir:
    quien llama decide si eso impide la entrega (sí, si es una entregada)."""
    usuario_id = await usuario_de_la_sesion(servicio, user.sub)
    await servicio.insert(
        REGISTRO,
        {
            "account_id": account_id,
            "salon_id": user.salon_id,
            "usuario_id": usuario_id,
            "resultado": resultado,
            "origen": "muro_suspension" if user.cuenta_suspendida else "app",
            **cuanto,
            # Los nombres de ahora (0057): la fila tiene que seguir diciendo
            # quién fue cuando esa persona, ese centro o esa cuenta ya no estén.
            **await instantanea(servicio, account_id=account_id, salon_id=user.salon_id, usuario_id=usuario_id),
        },
        select="id",
    )


async def _apuntar_rechazo(servicio, user, account_id, resultado) -> None:
    try:
        await apuntar_descarga(servicio, user, account_id, resultado)
    except PostgrestError:
        # El rechazo se mantiene aunque no quede fila: no se deja pasar a nadie
        # porque el registro falle. Queda en el log.
        logger.error("DESCARGA RECHAZADA SIN RASTRO (%s, cuenta %s).", resultado, account_id)


# ── Las rutas ──────────────────────────────────────────────────────────────


@router.get("", response_model=DatosPreviewOut, summary="Antes de descargar: cuántos centros entran")
async def preview(
    # LA PUERTA DE LA SUSPENSIÓN, como la baja: una cuenta suspendida también.
    user: CurrentUser = Depends(require_admin_aunque_suspendida),
    db: PostgrestClient = Depends(get_postgrest_client_aunque_suspendida),
) -> DatosPreviewOut:
    account_id = await id_de_cuenta(db, user.salon_id)
    centros = await db.select("salons", {"select": "id", "account_id": f"eq.{account_id}"}) if account_id else []
    return DatosPreviewOut(centros=len(centros))


async def _contrasena_del_cuerpo(request: Request) -> str:
    """La contraseña, leída A MANO y no con un modelo: un 422 de validación de
    FastAPI devuelve lo recibido en el mensaje de error, y aquí lo recibido es
    una contraseña. Cualquier cuerpo que no sirva recibe el mismo texto fijo."""
    try:
        cuerpo = json.loads(await request.body() or b"{}")
    except (ValueError, UnicodeDecodeError):
        cuerpo = None
    contrasena = cuerpo.get("contrasena") if isinstance(cuerpo, dict) else None
    if not isinstance(contrasena, str) or not contrasena or len(contrasena) > 200:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"codigo": CODIGO_FALTA, "mensaje": "Escribe tu contraseña."},
        )
    return contrasena


@router.post(
    "/descarga",
    response_class=Response,
    summary="Descargar todos los datos de la cuenta (ZIP de CSV)",
    description=(
        "Cuerpo: `{\"contrasena\": \"...\"}`, la de inicio de sesión, obligatoria. "
        "Un ZIP con un CSV por cada cosa del salón, de todos los centros de la "
        "cuenta, y un LEEME.txt. Solo la administradora. Tres descargas por hora "
        "y cuenta; la contraseña lleva el contador de intentos del PIN."
    ),
    openapi_extra={"requestBody": {"required": True, "content": {"application/json": {"schema": {
        "type": "object", "required": ["contrasena"], "properties": {"contrasena": {"type": "string"}}}}}}},
    responses={
        200: {"content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}}},
        422: {"description": "Falta la contraseña o no es la correcta"},
        429: {"description": "En espera por intentos, o límite de descargas (cabecera Retry-After)"},
    },
)
async def descargar(
    request: Request,
    user: CurrentUser = Depends(require_admin_aunque_suspendida),
    db: PostgrestClient = Depends(get_postgrest_client_aunque_suspendida),
    servicio: PostgrestClient = Depends(get_service_postgrest_client),
    settings: Settings = Depends(get_settings),
) -> Response:
    # EN UNA DEMO (0081): FUNCIONA con la contraseña de muestra, que la pantalla
    # enseña. Los datos son inventados: no hay nada que proteger.
    demo.funciona_en_la_muestra("contrasena_de_muestra")
    contrasena = await _contrasena_del_cuerpo(request)
    account_id = await id_de_cuenta(db, user.salon_id)
    if not account_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No encontramos tu cuenta.")

    # LA CONTRASEÑA PRIMERO: una mal puesta no gasta cupo.
    r = await comprobar_contrasena(settings, servicio, account_id, user.sub, contrasena)
    del contrasena
    if r.estado == "fallo":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No hemos podido comprobar tu contraseña ahora. Inténtalo de nuevo en unos minutos.",
        )
    if r.estado == "en_espera":
        await _apuntar_rechazo(servicio, user, account_id, "en_espera")
        minutos = minutos_hasta(r.bloqueado_hasta)
        cuando = "1 minuto" if minutos == 1 else f"{minutos} minutos"
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"codigo": CODIGO_EN_ESPERA, "mensaje": f"Demasiados intentos. Vuelve a probar en {cuando}.",
                    "bloqueado_hasta": r.bloqueado_hasta},
            headers={"Retry-After": str(minutos * 60)},
        )
    if r.estado == "incorrecta":
        await _apuntar_rechazo(servicio, user, account_id, "contrasena_incorrecta")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"codigo": CODIGO_INCORRECTA, "mensaje": "La contraseña no es correcta.",
                    "intentos_restantes": r.intentos_restantes},
        )

    bloqueada, espera = _cupo.is_blocked(account_id)
    if bloqueada or account_id in _en_curso:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                "Ya estás descargando tus datos. Espera a que termine."
                if account_id in _en_curso
                else f"Has descargado tus datos {POR_HORA} veces en la última hora. Vuelve a intentarlo más tarde."
            ),
            headers={"Retry-After": str(espera or 30)},
        )
    _cupo.add_failure(account_id)
    _en_curso.add(account_id)
    try:
        async with _a_la_vez:
            try:
                datos = await leer_datos(servicio, account_id, servicio)
            except PostgrestError as e:
                logger.warning("Volcado de %s: no se pudo leer (%s).", account_id, e)
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="No hemos podido reunir tus datos. Inténtalo de nuevo en unos minutos.",
                ) from e
            ahora = datetime.now(timezone.utc)
            contenido = await run_in_threadpool(construir_zip, datos, ahora)
        # SIN FILA NO HAY ZIP: la salida queda registrada antes de salir.
        try:
            await apuntar_descarga(
                servicio, user, account_id, "entregada",
                centros=len(datos.centros), filas=datos.filas(), bytes=len(contenido),
            )
        except PostgrestError as e:
            logger.error("Volcado de %s: NO se entrega, no se pudo registrar (%s).", account_id, e)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="No hemos podido preparar la descarga. Inténtalo de nuevo en unos minutos.",
            ) from e
    finally:
        _en_curso.discard(account_id)

    logger.info("Volcado de la cuenta %s: %d bytes, %d centros.", account_id, len(contenido), len(datos.centros))
    # El día del centro desde el que se descarga, no el del servidor.
    nombre = f"estetixapp-mis-datos-{zonas.hoy(await zonas.zona_del_centro(db, user.salon_id), ahora).isoformat()}.zip"
    return Response(
        content=contenido,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"', "Cache-Control": "no-store"},
    )
