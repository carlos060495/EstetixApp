"""Programa de referidos: registro público y gestión desde el panel.

Un referido es una entidad de PLATAFORMA, no de un salón: no lleva `salon_id`
ni RLS por inquilino (ver la migración 0029). Todo lo de aquí entra con la
service key, que es la única credencial que las tablas `referidos` y
`plataforma_config` aceptan — tienen RLS activada sin políticas, así que un
token de salón no pasa ni para leer.

Hay dos puertas y conviene no confundirlas:
  · `POST /referidos/registro` es PÚBLICA (sin sesión) y solo puede crear una
    solicitud en estado `pendiente`. Frenada por IP.
  · `/admin/referidos/*` va detrás del guard de `admin_users`, con `viewer` en
    solo lectura.

Aquí se registran, se aprueban —como persona o como AGENCIA (0077)— y se les
asigna un porcentaje; se ve lo que lleva devengado cada uno (`/{id}/comisiones`,
0075), el desglose por trabajador de una agencia (`/{id}/trabajadores`), se
les paga (0076) y se fija el mínimo de pago por moneda (`/config/minimos`).
Y el owner REPESCA la atribución de un salón cuyo alta dejó apuntado un código
que no valía (`/repescas`, 0078): devenga desde el alta.

Y una tercera puerta pública: `POST /referidos/comprobar-codigo`, para que el
formulario de alta avise de un código que no existe ANTES de enviar. Solo dice
sí o no; frenada por IP.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import stripe
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.core import publico
from app.core.admin_audit import append_admin_log, client_ip
from app.core.admin_deps import AdminClaims, get_admin_db, require_admin_roles
from app.core.admin_security import VentanaPorIP
from app.core.acceso_referido import mandar_enlace
from app.core import agencias, atribucion, cobros_referido, comisiones, pagos_referido
from app.core.codigo_referido import generar_codigo_referido
from app.core.config import Settings, get_settings
from app.db.postgrest_client import PostgrestClient, PostgrestError
from app.schemas.referidos import (
    CambiarMinimoIn,
    ComprobarRepescaIn,
    RepescablesOut,
    RepescaOut,
    RepescarIn,
    VendedorDeRepescaOut,
    ComprobarCodigoIn,
    ComprobarCodigoOut,
    MinimosOut,
    TrabajadoresOut,
    PagarIn,
    PropuestaDePagoOut,
    ResultadoPagoOut,
    CambiarPorcentajeIn,
    CambiarPorcentajeOut,
    ComisionesPanelOut,
    AprobarReferidoIn,
    CambiarTipoIn,
    ComisionGlobalIn,
    ComisionGlobalOut,
    EditarReferidoIn,
    RechazarReferidoIn,
    ReferidoOut,
    ReferidosListOut,
    RegistroReferidoIn,
    RegistroReferidoOut,
)

logger = logging.getLogger("estetix.referidos")

# Dos routers y no uno: el guard de `/admin` de app/main.py mira el prefijo de
# la ruta. Si el registro público colgara de `/admin`, pediría cookie de admin;
# si las rutas del panel NO colgaran de ahí, quedarían sin guard.
router = APIRouter(prefix="/referidos", tags=["referidos"], dependencies=[publico.CUALQUIERA])
router_admin = APIRouter(prefix="/admin/referidos", tags=["admin-referidos"], dependencies=[publico.PLATAFORMA])

CUALQUIER_ADMIN = ("owner", "support", "viewer")
# Lo que toca dinero ya ganado o lo envía: cambiar el % de salones que ya
# llegaron (0075) y pagar (0076).
SOLO_EL_OWNER = ("owner",)
ADMIN_MUTABLE = ("owner", "support")

ESTADOS = frozenset({"pendiente", "aprobado", "rechazado"})

# Cinco altas por IP y hora. Un formulario público sin freno se llena de basura
# en un fin de semana; cinco es de sobra para una persona real que se equivoca
# de correo y lo reintenta, y corta un bot en seco.
MAX_REGISTROS_POR_IP = 5
VENTANA_REGISTRO_MINUTOS = 60
_freno_registro = VentanaPorIP(
    max_fails=MAX_REGISTROS_POR_IP, window_minutes=VENTANA_REGISTRO_MINUTOS
)

# Cuántas veces se reintenta si el código generado ya existe. Con 32^6
# combinaciones no debería pasar nunca; el bucle está para que, si pasa, salga
# un código nuevo en vez de un 500.
INTENTOS_CODIGO = 8

# Sin datos bancarios: el IBAN se fue (0073) y el referido cobra por Stripe
# Connect (0072). Su cuenta de Stripe tampoco sale de aquí: solo su estado.
_COLUMNAS = (
    "id,nombre,email,codigo,comision_pct,estado,tipo,tipo_declarado,nombre_comercial,"
    "created_at,aprobado_at,aprobado_por,contrasena_puesta_at"
)

# Comprobar un código desde el formulario de alta: 30 por IP y ventana. Un
# código son 32^6 combinaciones; a este ritmo, adivinar uno no sale a cuenta.
_freno_codigo = VentanaPorIP(max_fails=30, window_minutes=10)


def _db_caida(e: Exception, que: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY, detail=f"No se pudo {que}."
    )


# ── Configuración de plataforma ─────────────────────────────────────────────


async def _comision_global(db: PostgrestClient) -> Decimal:
    """El % por defecto. Si la fila no estuviera (base sin migrar), se devuelve
    0 en vez de reventar: el panel enseña un 0 raro y se investiga, que es
    mejor que tumbar la lista entera de referidos."""
    try:
        filas = await db.select(
            "plataforma_config", {"select": "comision_referido_pct", "limit": "1"}
        )
    except PostgrestError as e:
        raise _db_caida(e, "leer la configuración de la plataforma") from e
    if not filas:
        return Decimal("0")
    return Decimal(str(filas[0]["comision_referido_pct"]))


async def _altas_que_cuentan(db: PostgrestClient) -> dict[str, int]:
    """Cuántas altas ha traído cada referido (0070), de la ÚNICA lectura que
    vale: `altas_que_cuentan`. NO se cuentan las filas de `altas_atribuidas` a
    ciegas: un alta deshecha deja su atribución (es inmutable) y no debe sumar.
    Cuenta si la cuenta existe o consta como borrada a los 90 días. Lo vigila
    `test_la_atribucion_base_real.py`."""
    try:
        filas = await db.rpc("altas_que_cuentan", {})
    except PostgrestError as e:
        raise _db_caida(e, "contar las altas") from e
    return {str(f["referido_id"]): int(f["altas"]) for f in filas if f}


async def _pcts_de_sus_salones(db: PostgrestClient) -> dict[str, list[dict]]:
    """Con qué % van HOY los salones de cada referido (0077), para avisar en
    su ficha si no coincide con el de los próximos."""
    try:
        filas = await db.rpc("porcentajes_de_sus_salones", {})
    except PostgrestError as e:
        raise _db_caida(e, "leer los porcentajes de sus salones") from e
    por: dict[str, list[dict]] = {}
    for f in filas:
        if f:
            por.setdefault(str(f["referido_id"]), []).append({"pct": f["pct"], "salones": int(f["salones"])})
    return por


async def _duenos_de_salon(db: PostgrestClient) -> dict[str, list[dict]]:
    """Los referidos cuyo correo es el de la administradora de un salón (0079).
    Lo descubre la base recorriendo las administradoras que existen
    (`referidos_duenos_de_salon`); aquí no hay ninguna lista."""
    try:
        filas = await db.rpc("referidos_duenos_de_salon", {})
    except PostgrestError as e:
        raise _db_caida(e, "comprobar si es dueño de un salón") from e
    por: dict[str, list[dict]] = {}
    for f in filas:
        if f:
            por.setdefault(str(f["referido_id"]), []).append(
                {"cuenta_id": str(f["cuenta_id"]), "cuenta": f.get("cuenta_nombre"), "salones": f.get("salones") or []})
    return por


async def _cambios_de_tipo(db: PostgrestClient, rid: str | None = None) -> dict[str, list[dict]]:
    """Sus conversiones entre persona y agencia (0079), la más reciente primero."""
    params = {"select": "referido_id,tipo_antes,tipo_despues,motivo,hecho_por_email,hecho_at",
              "order": "hecho_at.desc,id.desc"}
    if rid:
        params["referido_id"] = f"eq.{rid}"
    try:
        filas = await db.select_todo("cambios_de_tipo", params)
    except PostgrestError as e:
        raise _db_caida(e, "leer los cambios de tipo") from e
    por: dict[str, list[dict]] = {}
    for f in filas:
        por.setdefault(str(f.pop("referido_id")), []).append(f)
    return por


def _a_salida(fila: dict, global_pct: Decimal, altas: dict[str, int],
              cobros: dict[str, str] | None = None, pcts: dict[str, list[dict]] | None = None,
              duenos: dict[str, list[dict]] | None = None,
              cambios: dict[str, list[dict]] | None = None) -> ReferidoOut:
    propio = fila.get("comision_pct")
    efectiva = Decimal(str(propio)) if propio is not None else global_pct
    datos = {k: v for k, v in fila.items() if k != "contrasena_puesta_at"}
    rid = str(fila["id"])
    return ReferidoOut(
        **datos, comision_efectiva_pct=efectiva, altas_atribuidas=altas.get(rid, 0),
        con_contrasena=bool(fila.get("contrasena_puesta_at")),
        cobros=(cobros or {}).get(rid, cobros_referido.SIN_CONECTAR),
        pcts_de_sus_salones=(pcts or {}).get(rid, []),
        duenos_de_salon=(duenos or {}).get(rid, []),
        cambios_de_tipo=(cambios or {}).get(rid, []),
    )


async def _salida(db: PostgrestClient, rid: str) -> ReferidoOut:
    return _a_salida(await _cargar(db, rid), await _comision_global(db), await _altas_que_cuentan(db),
                     pcts=await _pcts_de_sus_salones(db), duenos=await _duenos_de_salon(db),
                     cambios=await _cambios_de_tipo(db, rid))


async def _dar_acceso(db: PostgrestClient, settings: Settings, rid: str) -> str:
    """El correo con el enlace para ponerse contraseña (0071). Antes lo hacía
    una persona a mano. Si no sale, la aprobación NO se deshace: el panel lo
    dice y deja reenviarlo. `enviado` · `no_enviado` · `no_creado`."""
    try:
        filas = await db.select("referidos", {"select": "id,nombre,email", "id": f"eq.{rid}", "limit": "1"})
        if not filas:
            return "no_creado"
        return "enviado" if await mandar_enlace(db, settings, filas[0], "bienvenida") else "no_enviado"
    except PostgrestError:
        logger.exception("referidos: no se pudo crear el enlace de bienvenida")
        return "no_creado"


# ── Registro público ────────────────────────────────────────────────────────


@router.post(
    "/registro",
    response_model=RegistroReferidoOut,
    status_code=status.HTTP_201_CREATED,
)
async def registrar_referido(
    payload: RegistroReferidoIn,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> RegistroReferidoOut:
    """Alta de una solicitud de referido. Siempre en estado `pendiente`.

    El estado y el código NO se aceptan del cliente aunque los mande: los pone
    el panel al aprobar. Es el mismo criterio que el resto de la API — quien
    llama no decide su propio nivel de privilegio.
    """
    ip = client_ip(request)
    bloqueado, reintentar_en = _freno_registro.is_blocked(ip)
    if bloqueado:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Demasiadas solicitudes desde esta conexión. Inténtalo más tarde.",
            headers={"Retry-After": str(reintentar_en)},
        )

    # Con la secret key: estas tablas no aceptan ninguna otra credencial, y
    # aquí no hay sesión de ningún tipo (la ruta es pública).
    db = PostgrestClient(settings.supabase_url, settings.supabase_secret_key)
    email = payload.email.strip().lower()

    try:
        existentes = await db.select(
            "referidos", {"select": "id", "email": f"ilike.{email}", "limit": "1"}
        )
    except PostgrestError as e:
        raise _db_caida(e, "comprobar el correo") from e

    if existentes:
        # Se cuenta también el intento repetido: si no, reenviar el mismo
        # correo mil veces saldría gratis y el freno no serviría de nada.
        _freno_registro.add_failure(ip)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ya hay una solicitud registrada con este correo.",
        )

    try:
        await db.insert(
            "referidos",
            {
                "nombre": payload.nombre,
                "email": email,
                "estado": "pendiente",
                # LO QUE DECLARA, en sus columnas (0079). NUNCA en `tipo`: el
                # tipo lo elige el panel al aprobar. Lo vigila
                # `test_lo_que_declara_quien_se_apunta.py`.
                "tipo_declarado": payload.tipo_declarado,
                "nombre_comercial": payload.nombre_comercial,
            },
        )
    except PostgrestError as e:
        # El índice único es la barrera de verdad: entre la comprobación de
        # arriba y este INSERT caben dos peticiones a la vez. Si choca, es que
        # ya existe, y se responde lo mismo que arriba.
        if "duplicate key" in str(e).lower() or "23505" in str(e):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Ya hay una solicitud registrada con este correo.",
            ) from e
        raise _db_caida(e, "registrar la solicitud") from e

    _freno_registro.add_failure(ip)
    # Sin el correo ni el nombre en el log: es un dato personal de alguien que
    # todavía no es cliente de nada.
    logger.info("referido_registrado")
    return RegistroReferidoOut(
        mensaje=(
            "Hemos recibido tu solicitud. Queda pendiente de aprobación: "
            "te escribiremos en cuanto la revisemos."
        )
    )


@router.post("/comprobar-codigo", response_model=ComprobarCodigoOut)
async def comprobar_codigo(
    payload: ComprobarCodigoIn,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> ComprobarCodigoOut:
    """¿Vale este código de alta? (0077). Para avisar en el formulario antes de
    enviar: de un referido o de un trabajador de agencia, activo. SOLO SÍ O NO:
    ni de quién es, ni si existió. La comparación es la del alta
    (`codigo_de_alta_vale`, en la base). Que no valga NUNCA para el alta."""
    ip = client_ip(request)
    bloqueado, reintentar_en = _freno_codigo.is_blocked(ip)
    if bloqueado:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                            detail="Demasiadas comprobaciones. Inténtalo más tarde.",
                            headers={"Retry-After": str(reintentar_en)})
    _freno_codigo.add_failure(ip)
    db = PostgrestClient(settings.supabase_url, settings.supabase_secret_key)
    try:
        r = await db.rpc("codigo_de_alta_vale", {"p_codigo": payload.codigo})
    except PostgrestError as e:
        raise _db_caida(e, "comprobar el código") from e
    return ComprobarCodigoOut(vale=bool(r and r[0] is True))


# ── Panel Super Admin ───────────────────────────────────────────────────────


@router_admin.get("", response_model=ReferidosListOut)
async def listar_referidos(
    estado: str | None = Query(default=None),
    _admin: AdminClaims = Depends(require_admin_roles(*CUALQUIER_ADMIN)),
    db: PostgrestClient = Depends(get_admin_db),
) -> ReferidosListOut:
    if estado is not None and estado not in ESTADOS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Estado no válido."
        )

    params: dict[str, str] = {"select": _COLUMNAS, "order": "created_at.desc"}
    if estado:
        params["estado"] = f"eq.{estado}"

    try:
        filas = await db.select("referidos", params)
    except PostgrestError as e:
        raise _db_caida(e, "cargar los referidos") from e

    global_pct = await _comision_global(db)
    altas = await _altas_que_cuentan(db)
    pcts = await _pcts_de_sus_salones(db)
    duenos = await _duenos_de_salon(db)
    cambios = await _cambios_de_tipo(db)
    try:
        cobros = await cobros_referido.estados_de_todos(db)
    except PostgrestError:
        logger.exception("referidos: no se pudo leer el estado de cobro")
        cobros = {}
    return ReferidosListOut(
        items=[_a_salida(f, global_pct, altas, cobros, pcts, duenos, cambios) for f in filas],
        total=len(filas),
        comision_global_pct=global_pct,
    )


@router_admin.get("/{referido_id}/comisiones", response_model=ComisionesPanelOut)
async def comisiones_del_referido(
    referido_id: UUID,
    _admin: AdminClaims = Depends(require_admin_roles(*CUALQUIER_ADMIN)),
    db: PostgrestClient = Depends(get_admin_db),
) -> ComisionesPanelOut:
    """Lo que lleva devengado, lo corregido, lo pagado (hoy 0) y lo que se le
    debe, por moneda; el detalle por salón y los movimientos, con el cobro de
    Stripe de cada uno (0075). Solo lectura: aquí no se paga nada."""
    await _cargar(db, str(referido_id))
    try:
        filas = await comisiones.filas_del_referido(db, str(referido_id))
        salones, cambios = await comisiones.salones_y_cambios(db, str(referido_id))
    except PostgrestError as e:
        raise _db_caida(e, "cargar las comisiones") from e
    return ComisionesPanelOut(
        **comisiones.resumen(filas, con_stripe=True, pct_hoy={x["cuenta_id"]: x["pct"] for x in salones},
                             pagos=await pagos_referido.pagos(db, str(referido_id)),
                             cambios=cambios, con_motivo=True),
        avisos_de_cambio=comisiones.avisos_de_cambio(cambios),
        salones=salones,
        cambios=[{**c, "salon": c["cuenta_nombre"]} for c in cambios],
    )


@router_admin.get("/{referido_id}/trabajadores", response_model=TrabajadoresOut)
async def trabajadores_de_la_agencia(
    referido_id: UUID,
    _admin: AdminClaims = Depends(require_admin_roles(*CUALQUIER_ADMIN)),
    db: PostgrestClient = Depends(get_admin_db),
) -> TrabajadoresOut:
    """La ficha de una agencia (0077): sus trabajadores y, por cada uno, cuántos
    salones trajo, cuántos pagan hoy y lo devengado por ellos. Un desglose del
    total que ya existe, no otra contabilidad. Solo lectura: los da de alta y
    de baja la agencia, desde su portal."""
    fila = await _cargar(db, str(referido_id))
    if fila.get("tipo") != "agencia":
        return TrabajadoresOut(trabajadores=[], directos=[])
    try:
        return TrabajadoresOut(**await agencias.trabajadores(db, str(referido_id)))
    except PostgrestError as e:
        raise _db_caida(e, "cargar los trabajadores") from e


@router_admin.post("/{referido_id}/porcentaje-de-salones", response_model=CambiarPorcentajeOut)
async def cambiar_porcentaje_de_salones(
    referido_id: UUID,
    payload: CambiarPorcentajeIn,
    request: Request,
    # SOLO EL OWNER (06-10-2026): cambia lo que le debemos a alguien
    # por trabajo que ya hizo. Soporte no.
    admin: AdminClaims = Depends(require_admin_roles(*SOLO_EL_OWNER)),
    db: PostgrestClient = Depends(get_admin_db),
) -> CambiarPorcentajeOut:
    """CAMBIAR EL % DE SALONES QUE YA LLEGARON (0075, 06-10-2026). Una
    acción deliberada, con motivo escrito obligatorio. Vale para lo que esos
    salones generen desde ahora: lo devengado no se toca. Queda en
    `cambios_de_porcentaje` (nadie puede editarlo ni borrarlo) y el referido lo
    ve en su portal, suba o baje. Lo decide y lo escribe la base
    (`cambiar_porcentaje_de_salones`): aquí solo se le pasa quién lo hace."""
    rid = str(referido_id)
    await _cargar(db, rid)
    if payload.todos == bool(payload.cuentas):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Elige los salones, o «todos los suyos»; no las dos cosas.",
        )
    try:
        filas = await db.rpc("cambiar_porcentaje_de_salones", {
            "p_referido": rid,
            "p_cuentas": None if payload.todos else [str(c) for c in payload.cuentas],
            "p_pct": float(payload.pct),
            "p_motivo": payload.motivo,
            "p_admin": admin.sub,
            "p_admin_email": admin.email,
        })
    except PostgrestError as e:
        if e.status_code == 400:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="Algún salón elegido no es de este referido.") from e
        raise _db_caida(e, "cambiar el porcentaje") from e
    cambiados = [f for f in filas if f]
    try:
        await append_admin_log(
            db, admin_id=admin.sub, accion="cambiar_porcentaje_de_salones", ip=client_ip(request),
            detalle={"referido_id": rid, "salones": len(cambiados), "pct": str(payload.pct)},
        )
    except PostgrestError:
        pass
    return CambiarPorcentajeOut(
        cambiados=[{"salon": f["cuenta_nombre"], "pct_antes": f["pct_antes"], "pct_despues": f["pct_despues"]}
                   for f in cambiados],
    )


# ── Pagar (0076) ────────────────────────────────────────────────────────────


def _stripe_caido(e: Exception) -> HTTPException:
    logger.exception("referidos: Stripe no contestó al preparar o enviar un pago")
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                         detail="Stripe no ha contestado. No se ha enviado nada nuevo; inténtalo en un momento.")


@router_admin.get("/{referido_id}/pago", response_model=PropuestaDePagoOut)
async def propuesta_de_pago(
    referido_id: UUID,
    _admin: AdminClaims = Depends(require_admin_roles(*SOLO_EL_OWNER)),
    db: PostgrestClient = Depends(get_admin_db),
    settings: Settings = Depends(get_settings),
) -> PropuestaDePagoOut:
    """LA CONFIRMACIÓN: cuánto, a quién, en qué moneda, qué comisiones cubre, y
    si se puede pagar AHORA (preguntado a Stripe) o por qué no. No mueve nada."""
    rid = str(referido_id)
    fila = await _cargar(db, rid)
    try:
        monedas = await pagos_referido.propuesta(db, settings, rid)
        hechos = await pagos_referido.pagos(db, rid)
    except PostgrestError as e:
        raise _db_caida(e, "preparar el pago") from e
    except (stripe.StripeError, RuntimeError) as e:
        raise _stripe_caido(e) from e
    return PropuestaDePagoOut(
        referido=fila["nombre"],
        monedas=[{**m, "cubre": [{**c, "salon": c["cuenta_nombre"], "comision": c["comision_centimos"]}
                                 for c in m["cubre"]]} for m in monedas],
        pagos=[{**x, "pago_id": str(x["pago_id"])} for x in hechos],
    )


@router_admin.post("/{referido_id}/pagos", response_model=ResultadoPagoOut)
async def pagar_a_un_referido(
    referido_id: UUID,
    payload: PagarIn,
    request: Request,
    admin: AdminClaims = Depends(require_admin_roles(*SOLO_EL_OWNER)),
    db: PostgrestClient = Depends(get_admin_db),
    settings: Settings = Depends(get_settings),
) -> ResultadoPagoOut:
    """EL BOTÓN «ENVIAR» DE LA CONFIRMACIÓN. Lo pulsa una persona, el owner; es
    lo único que dispara un pago. La orden se escribe antes de mover el dinero
    (`core/pagos_referido.py`)."""
    rid = str(referido_id)
    await _cargar(db, rid)
    try:
        r = await pagos_referido.pagar(db, settings, rid, payload.moneda, payload.importe_centimos,
                                       payload.hasta_comision_id, admin.sub, admin.email,
                                       saltar_minimo_motivo=payload.saltar_minimo_motivo)
    except PostgrestError as e:
        raise _db_caida(e, "ordenar el pago") from e
    except (stripe.StripeError, RuntimeError) as e:
        raise _stripe_caido(e) from e
    try:
        await append_admin_log(db, admin_id=admin.sub, accion="pagar_a_un_referido", ip=client_ip(request),
                               detalle={"referido_id": rid, "moneda": payload.moneda,
                                        "importe_centimos": payload.importe_centimos,
                                        "saltando_el_minimo": payload.saltar_minimo_motivo is not None,
                                        "resultado": r.get("resultado"), "pago_id": r.get("pago_id")})
    except PostgrestError:
        pass
    return ResultadoPagoOut(**r)


@router_admin.post("/pagos/{pago_id}/comprobar", response_model=ResultadoPagoOut)
async def comprobar_un_pago(
    pago_id: UUID,
    request: Request,
    admin: AdminClaims = Depends(require_admin_roles(*SOLO_EL_OWNER)),
    db: PostgrestClient = Depends(get_admin_db),
    settings: Settings = Depends(get_settings),
) -> ResultadoPagoOut:
    """Una orden sin resolver (se cayó algo a mitad): se mira en Stripe si
    salió y, si no, se reintenta con la misma clave. También lo pulsa el owner."""
    try:
        r = await pagos_referido.comprobar(db, settings, str(pago_id))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ese pago no existe.") from e
    except PostgrestError as e:
        raise _db_caida(e, "comprobar el pago") from e
    except (stripe.StripeError, RuntimeError) as e:
        raise _stripe_caido(e) from e
    try:
        await append_admin_log(db, admin_id=admin.sub, accion="comprobar_pago_a_referido", ip=client_ip(request),
                               detalle={"pago_id": str(pago_id), "resultado": r.get("resultado")})
    except PostgrestError:
        pass
    return ResultadoPagoOut(**r)


# ── Repescar una atribución (0078) ──────────────────────────────────────────
#
# Un salón cuyo alta dejó apuntado un código que no valía (lo tecleó mal): el
# owner le pone el bueno. SOLO EL OWNER, las cuatro rutas, y con motivo. Devenga
# DESDE EL ALTA: los cobros que ya ocurrieron se devengan en el acto con la
# función de siempre (`comisiones.devengar_lo_cobrado`). Plazos, rastro y «ya
# tiene una» los decide la base (`core/atribucion.repescar`).

_NO_SE_REPESCA = {
    "sin_rastro": (status.HTTP_404_NOT_FOUND, "Esa alta no dejó apuntado ningún código: no hay nada que repescar."),
    "sin_cuenta": (status.HTTP_404_NOT_FOUND, "Esa cuenta ya no existe."),
    "ya_atribuida": (status.HTTP_409_CONFLICT, "Ese salón ya tiene una atribución."),
    "fuera_de_plazo": (status.HTTP_409_CONFLICT, "Han pasado más de 90 días desde el alta: ya no se repesca."),
    "no_vale": (status.HTTP_404_NOT_FOUND, "Ese código no es de ningún vendedor activo."),
    "ha_cambiado": (status.HTTP_409_CONFLICT, "Ese código ya no es de la persona que viste. Compruébalo otra vez."),
}


async def _devengar_desde_el_alta(db: PostgrestClient, settings: Settings, cuenta_id: str,
                                  evento: str) -> tuple[dict[str, int], bool]:
    try:
        return await comisiones.devengar_lo_cobrado(db, settings, cuenta_id, evento), False
    except (stripe.StripeError, RuntimeError, PostgrestError):
        logger.exception("referidos: la repesca de %s no pudo devengar lo ya cobrado", cuenta_id)
        return {}, True


@router_admin.get("/repescas", response_model=RepescablesOut)
async def salones_para_repescar(
    _admin: AdminClaims = Depends(require_admin_roles(*SOLO_EL_OWNER)),
    db: PostgrestClient = Depends(get_admin_db),
) -> RepescablesOut:
    try:
        return RepescablesOut(items=await atribucion.salones_para_repescar(db))
    except PostgrestError as e:
        raise _db_caida(e, "cargar los salones para repescar") from e


@router_admin.post("/repescas/comprobar", response_model=VendedorDeRepescaOut)
async def comprobar_vendedor_de_repesca(
    payload: ComprobarRepescaIn,
    _admin: AdminClaims = Depends(require_admin_roles(*SOLO_EL_OWNER)),
    db: PostgrestClient = Depends(get_admin_db),
) -> VendedorDeRepescaOut:
    """De quién es el código bueno, para enseñar su nombre antes de confirmar."""
    try:
        v = await atribucion.vendedor_de_un_codigo(db, payload.codigo)
    except PostgrestError as e:
        raise _db_caida(e, "comprobar el código") from e
    if v is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NO_SE_REPESCA["no_vale"][1])
    return VendedorDeRepescaOut(**v)


@router_admin.post("/repescas/{codigo_que_no_valio_id}", response_model=RepescaOut)
async def repescar_atribucion(
    codigo_que_no_valio_id: int,
    payload: RepescarIn,
    request: Request,
    admin: AdminClaims = Depends(require_admin_roles(*SOLO_EL_OWNER)),
    db: PostgrestClient = Depends(get_admin_db),
    settings: Settings = Depends(get_settings),
) -> RepescaOut:
    try:
        r = await atribucion.repescar(db, codigo_que_no_valio_id, payload.codigo, str(payload.referido_id),
                                      str(payload.trabajador_id) if payload.trabajador_id else None,
                                      payload.motivo, admin.sub, admin.email)
    except PostgrestError as e:
        raise _db_caida(e, "repescar la atribución") from e
    resultado = r.get("resultado")
    if resultado != "repescada":
        codigo, texto = _NO_SE_REPESCA.get(resultado, (status.HTTP_409_CONFLICT, "No se puede repescar."))
        raise HTTPException(status_code=codigo, detail=texto)
    cuenta = str(r["cuenta_id"])
    cobros, pendientes = await _devengar_desde_el_alta(db, settings, cuenta, f"repesca:{codigo_que_no_valio_id}")
    try:
        await append_admin_log(db, admin_id=admin.sub, accion="repescar_atribucion", ip=client_ip(request),
                               detalle={"cuenta_id": cuenta, "codigo_que_no_valio_id": codigo_que_no_valio_id,
                                        "referido_id": str(payload.referido_id), "cobros": cobros,
                                        "cobros_pendientes": pendientes})
    except PostgrestError:
        pass
    return RepescaOut(nombre=r["referido_nombre"], trabajador_nombre=r.get("trabajador_nombre"),
                      devenga_desde=r["devenga_desde"], cuenta_id=cuenta, cobros=cobros, cobros_pendientes=pendientes)


@router_admin.post("/repescas/cuenta/{cuenta_id}/cobros", response_model=RepescaOut)
async def volver_a_buscar_los_cobros(
    cuenta_id: UUID,
    request: Request,
    admin: AdminClaims = Depends(require_admin_roles(*SOLO_EL_OWNER)),
    db: PostgrestClient = Depends(get_admin_db),
    settings: Settings = Depends(get_settings),
) -> RepescaOut:
    """A una repesca se le cayó Stripe a mitad: se vuelven a buscar sus cobros.
    Solo de una REPESCA (las demás devengan por el webhook) y sin sumar dos veces."""
    cuenta = str(cuenta_id)
    try:
        a = await atribucion.atribucion_de(db, cuenta)
    except PostgrestError as e:
        raise _db_caida(e, "leer la atribución") from e
    if not a or a.get("origen") != "repesca":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ese salón no viene de una repesca.")
    cobros, pendientes = await _devengar_desde_el_alta(db, settings, cuenta, "repesca:reintento")
    try:
        await append_admin_log(db, admin_id=admin.sub, accion="repesca_volver_a_buscar_cobros",
                               ip=client_ip(request), detalle={"cuenta_id": cuenta, "cobros": cobros})
    except PostgrestError:
        pass
    return RepescaOut(nombre="", devenga_desde=a["devenga_desde"], cuenta_id=cuenta, cobros=cobros,
                      cobros_pendientes=pendientes)


async def _cargar(db: PostgrestClient, referido_id: str) -> dict:
    try:
        filas = await db.select(
            "referidos", {"select": _COLUMNAS, "id": f"eq.{referido_id}", "limit": "1"}
        )
    except PostgrestError as e:
        raise _db_caida(e, "cargar el referido") from e
    if not filas:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Referido no encontrado."
        )
    return filas[0]


async def _codigo_libre(db: PostgrestClient) -> str:
    """Un código que no esté ya en uso.

    Se comprueba antes de escribir, y aun así el índice único de la BD sigue
    siendo la barrera real: dos aprobaciones a la vez podrían generar el mismo
    y una de las dos fallará, que es lo correcto.
    """
    for _ in range(INTENTOS_CODIGO):
        codigo = generar_codigo_referido()
        try:
            chocan = await db.select(
                "referidos", {"select": "id", "codigo": f"eq.{codigo}", "limit": "1"}
            )
        except PostgrestError as e:
            raise _db_caida(e, "generar el código") from e
        if not chocan:
            return codigo
    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail="No se pudo generar un código libre. Vuelve a intentarlo.",
    )


@router_admin.post("/{referido_id}/aprobar", response_model=ReferidoOut)
async def aprobar_referido(
    referido_id: UUID,
    payload: AprobarReferidoIn,
    request: Request,
    admin: AdminClaims = Depends(require_admin_roles(*ADMIN_MUTABLE)),
    db: PostgrestClient = Depends(get_admin_db),
    settings: Settings = Depends(get_settings),
) -> ReferidoOut:
    rid = str(referido_id)
    actual = await _cargar(db, rid)

    if actual["estado"] == "aprobado":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Este referido ya está aprobado."
        )

    # Un rechazado que se aprueba después CONSERVA su código si ya tenía uno;
    # solo se genera cuando no hay. Reemitirlo dejaría circulando un código
    # viejo que ya no apunta a nadie.
    codigo = actual.get("codigo") or await _codigo_libre(db)

    cambios = {
        "estado": "aprobado",
        "codigo": codigo,
        # Persona o agencia (0077): se elige aquí. Una agencia con trabajadores
        # no puede volver a ser individual (lo impide la base).
        "tipo": payload.tipo,
        # None a propósito cuando no se indica: significa "usa el global", y es
        # distinto de copiar el global de hoy (ver la migración 0029).
        "comision_pct": (
            float(payload.comision_pct) if payload.comision_pct is not None else None
        ),
        "aprobado_at": datetime.now(timezone.utc).isoformat(),
        "aprobado_por": admin.sub,
    }
    try:
        await db.update("referidos", {"id": f"eq.{rid}"}, cambios)
    except PostgrestError as e:
        if "23514" in str(e) or "trabajadores" in str(e):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail="Tiene trabajadores: solo se puede aprobar como agencia.") from e
        raise _db_caida(e, "aprobar el referido") from e

    try:
        await append_admin_log(
            db,
            admin_id=admin.sub,
            accion="aprobar_referido",
            ip=client_ip(request),
            detalle={
                "referido_id": rid,
                "codigo": codigo,
                "tipo": payload.tipo,
                "comision_pct": (
                    str(payload.comision_pct) if payload.comision_pct is not None else None
                ),
            },
        )
    except PostgrestError:
        # La aprobación ya está hecha; no se deshace por no poder escribir el
        # log. Mismo criterio que `extend_trial` y la suspensión de cuentas.
        pass

    correo = await _dar_acceso(db, settings, rid)
    salida = await _salida(db, rid)
    return salida.model_copy(update={"correo_de_acceso": correo})


# Lo que contesta la base al convertir, en palabras de la pantalla.
_NO_SE_CONVIERTE = {
    "no_aprobado": (status.HTTP_409_CONFLICT,
                    "Solo se convierte un referido aprobado. Uno pendiente se decide al aprobarlo."),
    "ya_es": (status.HTTP_409_CONFLICT, "Ya es de ese tipo."),
}


def _quedan(n: int) -> str:
    if n == 1:
        return "le queda 1 trabajador activo"
    return f"le quedan {n} trabajadores activos"


@router_admin.post("/{referido_id}/tipo", response_model=ReferidoOut)
async def cambiar_tipo(
    referido_id: UUID,
    payload: CambiarTipoIn,
    request: Request,
    # SOLO EL OWNER: decide con quién tratamos y si puede tener trabajadores.
    admin: AdminClaims = Depends(require_admin_roles(*SOLO_EL_OWNER)),
    db: PostgrestClient = Depends(get_admin_db),
) -> ReferidoOut:
    """Convertir un referido entre persona y agencia (0079), con motivo escrito.
    NO cambia nada de lo suyo —código, atribuciones, porcentajes, devengado,
    cuenta de Stripe, saldo—: solo `tipo`. De agencia a persona, solo sin
    trabajadores activos. Lo decide y lo escribe la base
    (`cambiar_tipo_de_referido`), que deja quién, cuándo y por qué."""
    rid = str(referido_id)
    try:
        filas = await db.rpc("cambiar_tipo_de_referido", {
            "p_referido": rid, "p_tipo": payload.tipo, "p_motivo": payload.motivo,
            "p_admin": admin.sub, "p_admin_email": admin.email,
        })
    except PostgrestError as e:
        raise _db_caida(e, "cambiar el tipo") from e
    r = filas[0] if filas else {}
    resultado = r.get("resultado")
    if resultado == "no_existe":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Referido no encontrado.")
    if resultado == "tiene_trabajadores":
        n = int(r.get("trabajadores_activos") or 0)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(f"No puede volver a ser una persona: {_quedan(n)}. "
                    f"Antes, la agencia tiene que {'darlo' if n == 1 else 'darlos'} de baja desde su portal."),
        )
    if resultado in _NO_SE_CONVIERTE:
        codigo, texto = _NO_SE_CONVIERTE[resultado]
        raise HTTPException(status_code=codigo, detail=texto)
    if resultado != "cambiado":
        raise _db_caida(RuntimeError(str(resultado)), "cambiar el tipo")
    try:
        await append_admin_log(db, admin_id=admin.sub, accion="cambiar_tipo_de_referido", ip=client_ip(request),
                               detalle={"referido_id": rid, "tipo": payload.tipo})
    except PostgrestError:
        pass
    return await _salida(db, rid)


@router_admin.post("/{referido_id}/rechazar", response_model=ReferidoOut)
async def rechazar_referido(
    referido_id: UUID,
    payload: RechazarReferidoIn,
    request: Request,
    admin: AdminClaims = Depends(require_admin_roles(*ADMIN_MUTABLE)),
    db: PostgrestClient = Depends(get_admin_db),
) -> ReferidoOut:
    rid = str(referido_id)
    actual = await _cargar(db, rid)

    if actual["estado"] == "rechazado":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Este referido ya está rechazado."
        )

    # El código NO se borra al rechazar: si se liberase, podría reasignarse a
    # otra persona y los enlaces antiguos apuntarían al referido equivocado.
    # Quien decide si un código sigue valiendo es el estado, no su existencia.
    try:
        await db.update("referidos", {"id": f"eq.{rid}"}, {"estado": "rechazado"})
    except PostgrestError as e:
        raise _db_caida(e, "rechazar el referido") from e

    # Su portal se cierra YA (0071). Bastaría con el estado —la sesión lo mira
    # en cada petición—, pero así tampoco quedan sesiones ni enlaces vivos.
    try:
        await db.delete("referido_sesiones", {"referido_id": f"eq.{rid}"})
        await db.update(
            "referido_enlaces",
            {"referido_id": f"eq.{rid}", "usado_at": "is.null", "anulado_at": "is.null"},
            {"anulado_at": datetime.now(timezone.utc).isoformat()},
        )
    except PostgrestError:
        logger.exception("referidos: no se pudieron cerrar las sesiones de %s", rid)

    try:
        await append_admin_log(
            db,
            admin_id=admin.sub,
            accion="rechazar_referido",
            ip=client_ip(request),
            detalle={"referido_id": rid, "motivo": payload.motivo},
        )
    except PostgrestError:
        pass

    return await _salida(db, rid)


@router_admin.post("/{referido_id}/reenviar-acceso", response_model=ReferidoOut)
async def reenviar_acceso(
    referido_id: UUID,
    request: Request,
    admin: AdminClaims = Depends(require_admin_roles(*ADMIN_MUTABLE)),
    db: PostgrestClient = Depends(get_admin_db),
    settings: Settings = Depends(get_settings),
) -> ReferidoOut:
    """Otro enlace de bienvenida (0071): el anterior queda anulado. Para cuando
    el correo no salió, caducó o lo perdió."""
    rid = str(referido_id)
    actual = await _cargar(db, rid)
    if actual["estado"] != "aprobado":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Solo se le manda acceso a un referido aprobado.")
    correo = await _dar_acceso(db, settings, rid)
    try:
        await append_admin_log(db, admin_id=admin.sub, accion="reenviar_acceso_referido",
                               ip=client_ip(request), detalle={"referido_id": rid, "correo": correo})
    except PostgrestError:
        pass
    salida = await _salida(db, rid)
    return salida.model_copy(update={"correo_de_acceso": correo})


@router_admin.patch("/{referido_id}", response_model=ReferidoOut)
async def editar_referido(
    referido_id: UUID,
    payload: EditarReferidoIn,
    request: Request,
    admin: AdminClaims = Depends(require_admin_roles(*ADMIN_MUTABLE)),
    db: PostgrestClient = Depends(get_admin_db),
) -> ReferidoOut:
    rid = str(referido_id)
    await _cargar(db, rid)

    # `exclude_unset` es lo que distingue "no me lo mandes" de "ponlo a null":
    # sin él, no habría forma de devolver a alguien al % global.
    cambios = payload.model_dump(exclude_unset=True)
    if "comision_pct" in cambios and cambios["comision_pct"] is not None:
        cambios["comision_pct"] = float(cambios["comision_pct"])
    if not cambios:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No hay nada que cambiar."
        )

    try:
        await db.update("referidos", {"id": f"eq.{rid}"}, cambios)
    except PostgrestError as e:
        raise _db_caida(e, "guardar los cambios") from e

    try:
        await append_admin_log(
            db,
            admin_id=admin.sub,
            accion="editar_referido",
            ip=client_ip(request),
            # Los campos, no sus valores.
            detalle={"referido_id": rid, "campos": sorted(cambios)},
        )
    except PostgrestError:
        pass

    return await _salida(db, rid)


# ── El % global ─────────────────────────────────────────────────────────────


async def _leer_comision_global(db: PostgrestClient) -> ComisionGlobalOut:
    try:
        filas = await db.select(
            "plataforma_config",
            {"select": "comision_referido_pct,actualizado_at", "limit": "1"},
        )
    except PostgrestError as e:
        raise _db_caida(e, "leer la configuración de la plataforma") from e
    if not filas:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Falta la fila de configuración de la plataforma.",
        )
    return ComisionGlobalOut(**filas[0])


@router_admin.put("/config/comision", response_model=ComisionGlobalOut)
async def guardar_comision_global(
    payload: ComisionGlobalIn,
    request: Request,
    admin: AdminClaims = Depends(require_admin_roles(*ADMIN_MUTABLE)),
    db: PostgrestClient = Depends(get_admin_db),
) -> ComisionGlobalOut:
    # `id=eq.true` y no un UPDATE a secas: la tabla es de una fila por diseño,
    # pero un UPDATE sin WHERE es una costumbre que tarde o temprano se copia a
    # una tabla que sí tiene muchas.
    try:
        await db.update(
            "plataforma_config",
            {"id": "eq.true"},
            {
                "comision_referido_pct": float(payload.comision_referido_pct),
                "actualizado_at": datetime.now(timezone.utc).isoformat(),
                "actualizado_por": admin.sub,
            },
        )
    except PostgrestError as e:
        raise _db_caida(e, "guardar el porcentaje") from e

    try:
        await append_admin_log(
            db,
            admin_id=admin.sub,
            accion="cambiar_comision_global",
            ip=client_ip(request),
            detalle={"comision_referido_pct": str(payload.comision_referido_pct)},
        )
    except PostgrestError:
        pass

    return await _leer_comision_global(db)


# ── El mínimo de pago (0077) ────────────────────────────────────────────────


@router_admin.get("/config/minimos", response_model=MinimosOut)
async def leer_minimos(
    _admin: AdminClaims = Depends(require_admin_roles(*CUALQUIER_ADMIN)),
    db: PostgrestClient = Depends(get_admin_db),
) -> MinimosOut:
    """El mínimo vigente de cada moneda y todos sus cambios, del más nuevo al
    más viejo. Vive en la base (`minimos_de_pago`), en un solo sitio."""
    try:
        minimos = await db.rpc("minimos_de_pago_vigentes", {})
        cambios = await db.select_todo("cambios_de_minimo", {
            "select": "moneda,centimos_antes,centimos_despues,motivo,hecho_por_email,hecho_at",
            "order": "hecho_at.desc,id.desc",
        })
    except PostgrestError as e:
        raise _db_caida(e, "leer los mínimos") from e
    return MinimosOut(minimos=[m for m in minimos if m], cambios=cambios)


@router_admin.put("/config/minimos/{moneda}", response_model=MinimosOut)
async def cambiar_minimo(
    moneda: str,
    payload: CambiarMinimoIn,
    request: Request,
    # SOLO EL OWNER, como pagar: decide cuándo sale dinero.
    admin: AdminClaims = Depends(require_admin_roles(*SOLO_EL_OWNER)),
    db: PostgrestClient = Depends(get_admin_db),
) -> MinimosOut:
    """Cambiar (o fijar por primera vez) el mínimo de una moneda, con motivo
    escrito obligatorio. Queda en `cambios_de_minimo`: quién, cuándo, de cuánto
    a cuánto. Lo decide y lo escribe la base (`cambiar_minimo_de_pago`)."""
    moneda = moneda.strip().lower()
    if len(moneda) != 3 or not moneda.isascii() or not moneda.isalpha():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Moneda no válida.")
    try:
        await db.rpc("cambiar_minimo_de_pago", {
            "p_moneda": moneda, "p_centimos": payload.centimos, "p_motivo": payload.motivo,
            "p_admin": admin.sub, "p_admin_email": admin.email,
        })
    except PostgrestError as e:
        if e.status_code == 400:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="Ese ya es el mínimo de esa moneda.") from e
        raise _db_caida(e, "cambiar el mínimo") from e
    try:
        await append_admin_log(db, admin_id=admin.sub, accion="cambiar_minimo_de_pago", ip=client_ip(request),
                               detalle={"moneda": moneda, "centimos": payload.centimos})
    except PostgrestError:
        pass
    return await leer_minimos(_admin=admin, db=db)
