"""Gestión de salones desde el panel Super Admin.

Se lee con la service key: el super admin no pertenece a ningún salón, así que la
RLS por `salon_id` no le dejaría ver nada (ver app/core/admin_deps.py).

Los datos se obtienen con tres consultas planas —salons, subscriptions,
employees— que se cruzan en Python, en lugar de recursos embebidos o agregados de
PostgREST: `PostgrestClient` es un wrapper fino y esa sintaxis es frágil de
construir y de fingir en los tests. El volumen esperado es de decenas de salones.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

import stripe
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.core import publico
from app.billing.firma import clave_nuestra
from app.billing.stripe_objects import current_period_end_ts
from app.core import zona as zonas
from app.core.admin_audit import append_admin_log, client_ip
from app.core.admin_deps import AdminClaims, get_admin_db, require_admin_roles
from app.core.config import Settings, get_settings
from app.core import zona_de_la_cuenta as cuentas_zona
from app.core.cuenta import con_la_cuenta_dentro
from app.db.postgrest_client import PostgrestClient, PostgrestError, trozos_en
from app.routers.billing import SUSCRIPCION_EXISTE
from app.schemas.admin_tenants import (
    DecisionDeDineroOut,
    DecisionesDeDineroOut,
    DescargaDeDatosOut,
    DescargasDeDatosOut,
    ExtendTrialIn,
    ReactivarIn,
    SuspenderIn,
    TenantDetailOut,
    TenantListItem,
    TenantListOut,
    TenantSalonOut,
    TenantSubscriptionOut,
)

logger = logging.getLogger("estetix.admin_tenants")

router = APIRouter(prefix="/admin", tags=["admin-tenants"], dependencies=[publico.PLATAFORMA])

# Lectura: abierta a los tres roles del panel, incluido viewer.
CUALQUIER_ADMIN = ("owner", "support", "viewer")
# Mutación: viewer es de solo lectura.
ADMIN_MUTABLE = ("owner", "support")

# Tope de sentido común: una prueba de más de un año es casi siempre un error de
# tecleo, y Stripe cobraría con un año de retraso.
MAX_DIAS_PRUEBA = 365

# Estados que reporta Stripe, más el pseudo-estado de los salones que todavía no
# tienen fila en subscriptions.
ESTADOS_VALIDOS = frozenset(
    {
        "trialing",
        "active",
        "past_due",
        "canceled",
        "unpaid",
        "incomplete",
        "incomplete_expired",
        "paused",
        "sin_suscripcion",
    }
)

# Nueve de estas columnas viven en `accounts` desde la 0040 y se traen por
# embed. La forma de la respuesta NO cambia: se aplanan antes de devolverlas,
# para que el panel siga viendo un salón con su plan y su estado. Un salón y su
# cuenta siguen siendo uno a uno; el día que dejen de serlo, esta pantalla es de
# las que hay que rehacer.
_COLUMNAS_SALON = (
    "id,nombre_comercial,email,telefono,direccion,created_at,account_id,zona_horaria,"
    "accounts(cif,plan,pending_plan,stripe_customer_id,suspendido,suspendido_at)"
)
_COLUMNAS_SUSCRIPCION = (
    "estado,plan,intervalo,trial_ends_at,current_period_end,"
    "stripe_customer_id,stripe_subscription_id,cancel_at_period_end"
)


async def _cuenta_del_tenant(db: PostgrestClient, salon_id: str) -> str:
    """La cuenta del salón que el operador tiene abierto en el panel."""
    try:
        filas = await db.select(
            "salons", {"select": "account_id", "id": f"eq.{salon_id}", "limit": "1"}
        )
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudo leer la cuenta."
        ) from e
    if not filas:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Salón no encontrado.")
    return filas[0]["account_id"]


# Caracteres con significado propio en la sintaxis de filtros de PostgREST: si
# llegasen desde el buscador romperían la expresión `or=(...)`.
_CARACTERES_PELIGROSOS = str.maketrans("", "", ",()*")


def _sanear_busqueda(q: str) -> str:
    return q.translate(_CARACTERES_PELIGROSOS).strip()


@router.get("/tenants", response_model=TenantListOut)
async def list_tenants(
    q: str | None = Query(default=None, max_length=120),
    estado: str | None = Query(default=None),
    _admin: AdminClaims = Depends(require_admin_roles(*CUALQUIER_ADMIN)),
    db: PostgrestClient = Depends(get_admin_db),
) -> TenantListOut:
    if estado is not None and estado not in ESTADOS_VALIDOS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Estado de suscripción no válido.",
        )

    params: dict[str, str] = {
        "select": "id,nombre_comercial,email,created_at,account_id,zona_horaria,accounts(plan,es_demo)",
        "order": "created_at.desc,id.asc",
    }
    termino = _sanear_busqueda(q or "")
    if termino:
        params["or"] = f"(nombre_comercial.ilike.*{termino}*,email.ilike.*{termino}*)"

    try:
        # UNA DEMO NO ES UN CLIENTE (0080): no sale aquí. Las demos se cuentan
        # aparte, en su propia sección del panel (`/admin/demos`).
        # TODOS, no los mil primeros: es la lista de clientes de la plataforma.
        salones = [con_la_cuenta_dentro(f) for f in await db.select_todo("salons", params)
                   if not (f.get("accounts") or {}).get("es_demo")]
        suscripciones = await db.select_todo(
            "subscriptions", {"select": "account_id,estado,trial_ends_at", "order": "account_id.asc"}
        )
        empleados = await db.select_todo(
            "employees", {"select": "salon_id", "estado": "eq.activo", "order": "id.asc"}
        )
        # TODOS los centros, no solo los que casan con la búsqueda: el centro de
        # referencia de una cuenta puede no estar entre los encontrados.
        zona_cuentas = cuentas_zona.por_cuenta(
            await db.select_todo("salons", {"select": cuentas_zona.COLUMNAS, "order": "id.asc"})
        )
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudieron cargar los salones.",
        ) from e

    # subscriptions tiene unique(account_id): como mucho una fila por cuenta.
    por_cuenta = {s["account_id"]: s for s in suscripciones}
    activos: dict[str, int] = {}
    for empleado in empleados:
        activos[empleado["salon_id"]] = activos.get(empleado["salon_id"], 0) + 1

    items: list[TenantListItem] = []
    for salon in salones:
        suscripcion = por_cuenta.get(salon.get("account_id"))
        estado_suscripcion = suscripcion["estado"] if suscripcion else None

        if estado == "sin_suscripcion":
            if suscripcion is not None:
                continue
        elif estado is not None and estado_suscripcion != estado:
            continue

        items.append(
            TenantListItem(
                id=salon["id"],
                nombre_comercial=salon["nombre_comercial"],
                email=salon.get("email"),
                plan=salon["plan"],
                estado_suscripcion=estado_suscripcion,
                trial_ends_at=suscripcion.get("trial_ends_at") if suscripcion else None,
                created_at=salon["created_at"],
                empleados_activos=activos.get(salon["id"], 0),
                zona_horaria=salon.get("zona_horaria") or zonas.POR_DEFECTO,
                cuenta=vars(cuentas_zona.de(zona_cuentas, salon.get("account_id"))),
            )
        )

    return TenantListOut(items=items, total=len(items))


async def _load_tenant_detail(db: PostgrestClient, salon_id: str) -> TenantDetailOut:
    """Detalle completo de un salón.

    Se extrae aparte porque la acción de ampliar la prueba devuelve este mismo
    cuerpo, para que el panel refresque sin una segunda petición.
    """
    try:
        salones = await db.select(
            "salons", {"select": _COLUMNAS_SALON, "id": f"eq.{salon_id}", "limit": "1"}
        )
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudo cargar el salón."
        ) from e

    if not salones:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Salón no encontrado.")

    salon = con_la_cuenta_dentro(salones[0])

    try:
        suscripciones = await db.select(
            "subscriptions",
            {
                "select": _COLUMNAS_SUSCRIPCION,
                "account_id": f"eq.{salon['account_id']}",
                "limit": "1",
            },
        )
        empleados = await db.select(
            "employees",
            {"select": "id", "salon_id": f"eq.{salon_id}", "estado": "eq.activo"},
        )
        centros_de_la_cuenta = await db.select_todo(
            "salons",
            {"select": cuentas_zona.COLUMNAS, "account_id": f"eq.{salon['account_id']}", "order": "id.asc"},
        )
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo cargar el detalle del salón.",
        ) from e

    return TenantDetailOut(
        salon=TenantSalonOut(**salon),
        suscripcion=TenantSubscriptionOut(**suscripciones[0]) if suscripciones else None,
        empleados_activos=len(empleados),
        cuenta=vars(cuentas_zona.zona_de_la_cuenta(centros_de_la_cuenta)),
    )


@router.get("/tenants/{tenant_id}", response_model=TenantDetailOut)
async def get_tenant(
    tenant_id: UUID,
    _admin: AdminClaims = Depends(require_admin_roles(*CUALQUIER_ADMIN)),
    db: PostgrestClient = Depends(get_admin_db),
) -> TenantDetailOut:
    return await _load_tenant_detail(db, str(tenant_id))


# SIN EMBED de `users` desde la 0057: la tabla ya no tiene clave ajena que
# PostgREST pueda seguir. El nombre sale de la instantánea de la fila; solo las
# filas anteriores a ella (sin instantánea) lo buscan aparte.
_COLUMNAS_DECISION = (
    "id,ocurrido_at,suceso,origen,motivo,plan_antes,plan_despues,programado_para,"
    "stripe_peticion,stripe_evento,usuario_id,usuario_nombre,usuario_email"
)


async def _con_nombre(db: PostgrestClient, filas: list[dict]) -> list[dict]:
    """Las filas con su nombre: el de la instantánea y, si una fila es anterior
    a la 0057 y no la tiene, el de ahora (si el usuario sigue existiendo)."""
    sin = sorted({f["usuario_id"] for f in filas if f.get("usuario_id") and not f.get("usuario_nombre")
                  and not f.get("usuario_email")})
    ahora = {}
    if sin:
        usuarios = []
        for en in trozos_en(sin):  # el historial de una cuenta no tiene techo
            usuarios += await db.select("users", {"select": "id,nombre,email", "id": en})
        ahora = {u["id"]: u for u in usuarios}
    out = []
    for f in filas:
        f = dict(f)
        u = ahora.get(f.pop("usuario_id", None)) or {}
        f["usuario_nombre"] = f.get("usuario_nombre") or u.get("nombre")
        f["usuario_email"] = f.get("usuario_email") or u.get("email")
        out.append(f)
    return out


@router.get("/tenants/{tenant_id}/decisiones", response_model=DecisionesDeDineroOut)
async def get_decisiones_de_dinero(
    tenant_id: UUID,
    _admin: AdminClaims = Depends(require_admin_roles(*CUALQUIER_ADMIN)),
    db: PostgrestClient = Depends(get_admin_db),
) -> DecisionesDeDineroOut:
    """EL RASTRO DE LAS DECISIONES DE DINERO de la cuenta del salón (0054).

    En orden cronológico: se lee como una historia, de la primera decisión a
    la última. Solo lectura, y solo desde el panel: el salón todavía no lo ve.
    """
    account_id = await _cuenta_del_tenant(db, str(tenant_id))
    try:
        filas = await db.select(
            "decisiones_de_dinero",
            {
                "select": _COLUMNAS_DECISION,
                "account_id": f"eq.{account_id}",
                "order": "ocurrido_at.asc,id.asc",
            },
        )
        filas = await _con_nombre(db, filas)
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudo leer el historial."
        ) from e
    return DecisionesDeDineroOut(items=[DecisionDeDineroOut(**f) for f in filas])


@router.get("/tenants/{tenant_id}/descargas", response_model=DescargasDeDatosOut)
async def get_descargas_de_datos(
    tenant_id: UUID,
    _admin: AdminClaims = Depends(require_admin_roles(*CUALQUIER_ADMIN)),
    db: PostgrestClient = Depends(get_admin_db),
) -> DescargasDeDatosOut:
    """EL RASTRO DE LAS DESCARGAS DE DATOS de la cuenta del salón (0056):
    las entregadas y los intentos rechazados, en orden cronológico.

    El nombre sale de la instantánea de la fila (0057): sigue ahí aunque el
    usuario ya no exista.
    """
    account_id = await _cuenta_del_tenant(db, str(tenant_id))
    try:
        filas = await db.select(
            "descargas_de_datos",
            {
                "select": "id,ocurrido_at,resultado,origen,usuario_id,usuario_nombre,usuario_email,"
                          "centros,filas,bytes",
                "account_id": f"eq.{account_id}",
                "order": "ocurrido_at.asc,id.asc",
            },
        )
        filas = await _con_nombre(db, filas)
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudo leer el historial."
        ) from e
    return DescargasDeDatosOut(items=[DescargaDeDatosOut(**f) for f in filas])


def _a_utc(valor: datetime) -> datetime:
    """Una fecha sin zona horaria se interpreta como UTC: es lo que guarda la BD
    y lo que espera Stripe, y así no depende de dónde esté el navegador."""
    return zonas.instante_o_utc(valor)


def _fin_de_prueba_actual(suscripcion: dict, ahora: datetime) -> datetime | None:
    bruto = suscripcion.get("trial_ends_at")
    if not bruto:
        return None
    try:
        return _a_utc(datetime.fromisoformat(bruto))
    except ValueError:
        return None


@router.post("/tenants/{tenant_id}/extend-trial", response_model=TenantDetailOut)
async def extend_trial(
    tenant_id: UUID,
    payload: ExtendTrialIn,
    request: Request,
    admin: AdminClaims = Depends(require_admin_roles(*ADMIN_MUTABLE)),
    db: PostgrestClient = Depends(get_admin_db),
    settings: Settings = Depends(get_settings),
) -> TenantDetailOut:
    """Amplía el periodo de prueba de un salón.

    Escribe en Stripe (fuente de verdad de la facturación) Y en la BD. Es la
    ÚNICA excepción autorizada a la regla de que solo el webhook toca
    `subscriptions`: se guarda exactamente el mismo valor que el webhook
    escribirá al recibir `customer.subscription.updated`, así que ambos
    convergen, y así el panel y la app del salón reflejan el cambio al instante
    aunque el webhook tarde o no esté escuchando.
    """
    if not settings.stripe_secret_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="La facturación no está configurada.",
        )

    salon_id = str(tenant_id)
    detalle_actual = await _load_tenant_detail(db, salon_id)  # 404 si no existe
    account_id = await _cuenta_del_tenant(db, salon_id)

    try:
        suscripciones = await db.select(
            "subscriptions",
            {
                "select": "estado,trial_ends_at,stripe_subscription_id",
                "account_id": f"eq.{account_id}",
                "limit": "1",
            },
        )
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo leer la suscripción de la cuenta.",
        ) from e

    if not suscripciones:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Este salón todavía no tiene ninguna suscripción.",
        )

    suscripcion = suscripciones[0]
    estado = suscripcion.get("estado")
    if estado != "trialing":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "La prueba solo se puede ampliar mientras la suscripción está en "
                f"periodo de prueba (estado actual: {estado or 'desconocido'})."
            ),
        )

    subscription_id = suscripcion.get("stripe_subscription_id")
    if not subscription_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="La suscripción no está enlazada con Stripe.",
        )

    ahora = datetime.now(timezone.utc)
    anterior = _fin_de_prueba_actual(suscripcion, ahora)

    if payload.nueva_fecha is not None:
        nueva = _a_utc(payload.nueva_fecha)
    else:
        # Los días se suman al fin de prueba ACTUAL, no a hoy: "amplía 7 días"
        # significa una semana más de la que ya tenía. Si no hubiera fecha
        # previa (raro estando en trialing), se cuenta desde ahora.
        base = anterior or ahora
        nueva = base + timedelta(days=payload.dias_extra or 0)

    # Stripe razona en segundos enteros: truncando aquí, lo que guardamos en BD
    # es exactamente lo que Stripe confirma, sin desfases de microsegundos entre
    # trial_ends_at y current_period_end.
    nueva = nueva.replace(microsecond=0)

    if nueva <= ahora:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La nueva fecha de fin de prueba tiene que estar en el futuro.",
        )
    if nueva > ahora + timedelta(days=MAX_DIAS_PRUEBA):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"La prueba no puede extenderse más de {MAX_DIAS_PRUEBA} días desde hoy.",
        )

    stripe.api_key = settings.stripe_secret_key
    try:
        actualizada = await stripe.Subscription.modify_async(
            subscription_id,
            trial_end=int(nueva.timestamp()),
            # Mover el fin de prueba no debe generar ajustes en la factura:
            # solo se retrasa el primer cobro.
            proration_behavior="none",
            idempotency_key=clave_nuestra("alargar-prueba"),
        )
    except stripe.StripeError as e:
        logger.warning("Stripe rechazó extend_trial: %s", e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Stripe no aceptó la nueva fecha de prueba.",
        ) from e

    # Mientras la suscripción está en prueba, Stripe mantiene current_period_end
    # pegado a trial_end: al mover uno mueve el otro. Si solo guardásemos
    # trial_ends_at, el panel enseñaría "Fin de prueba" el 21 y "Próxima
    # renovación" el 14, dando a entender que se cobra antes de tiempo.
    # Se toma el valor que confirma Stripe; si no viniera en la respuesta, el
    # nuevo fin de prueba es el valor correcto por definición.
    cpe_ts = current_period_end_ts(actualizada)
    nuevo_periodo = datetime.fromtimestamp(cpe_ts, tz=timezone.utc) if cpe_ts else nueva

    # A partir de aquí la prueba YA está ampliada en Stripe. Pase lo que pase con
    # la BD, la acción tiene que quedar auditada.
    ip = client_ip(request)
    detalle_log: dict = {
        "anterior": anterior.isoformat() if anterior else None,
        "nueva": nueva.isoformat(),
        "dias_extra": payload.dias_extra,
    }

    error_bd: Exception | None = None
    try:
        await db.update(
            "subscriptions",
            {"account_id": f"eq.{account_id}"},
            {
                "trial_ends_at": nueva.isoformat(),
                "current_period_end": nuevo_periodo.isoformat(),
            },
        )
    except PostgrestError as e:
        error_bd = e
        detalle_log["bd_error"] = True

    try:
        await append_admin_log(
            db,
            admin_id=admin.sub,
            accion="extend_trial",
            ip=ip,
            target_salon_id=salon_id,
            detalle=detalle_log,
        )
    except PostgrestError:
        # No se puede deshacer lo hecho en Stripe por no poder escribir el log,
        # pero tampoco se oculta: si además falló la BD, el 502 de abajo avisa.
        pass

    if error_bd is not None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "La prueba se amplió en Stripe, pero no se pudo guardar la fecha en la "
                "base de datos. El webhook de Stripe la sincronizará en unos minutos."
            ),
        ) from error_bd

    return await _load_tenant_detail(db, salon_id)


# ── Suspender y reactivar ───────────────────────────────────────────────────
#
# Suspender = cortar el acceso + pausar el cobro. Las dos cosas, porque una sola
# es una trampa: bloquear sin pausar sigue cobrando a quien no puede entrar, y
# pausar sin bloquear regala el producto. Es REVERSIBLE por diseño; casi todas
# las suspensiones reales (un impago que se resuelve, un aviso) acaban en
# reactivación.


async def _suscripcion_pausable(db: PostgrestClient, account_id: str) -> str | None:
    """El id de la suscripción de Stripe sobre la que se puede pausar el cobro,
    o None si esta cuenta no tiene ninguna sobre la que actuar.

    None NO es un error: hay cuentas sin suscripción (alta a medias, prueba
    caducada, cancelado) y a esas hay que poder suspenderlas igual — solo que
    ahí no hay nada que pausar y no se toca Stripe.
    """
    try:
        filas = await db.select(
            "subscriptions",
            {
                "select": "estado,stripe_subscription_id",
                "account_id": f"eq.{account_id}",
                "limit": "1",
            },
        )
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo leer la suscripción del salón.",
        ) from e

    if not filas:
        return None
    fila = filas[0]
    if fila.get("estado") not in SUSCRIPCION_EXISTE:
        return None
    return fila.get("stripe_subscription_id") or None


async def _pausar_cobro(subscription_id: str, settings: Settings) -> None:
    """Pausa el cobro dejando la suscripción viva.

    `behavior='void'` es la elección deliberada: durante la pausa Stripe sigue
    emitiendo las facturas del periodo pero las anula, así que al reactivar NO
    llega una factura acumulada por los meses suspendidos. Con 'keep_as_draft' o
    'mark_uncollectible' el cliente reactivado recibiría justo eso.
    """
    stripe.api_key = settings.stripe_secret_key
    try:
        await stripe.Subscription.modify_async(
            subscription_id,
            pause_collection={"behavior": "void"},
            idempotency_key=clave_nuestra("pausar-cobro"),
        )
    except stripe.StripeError as e:
        logger.warning("Stripe rechazó _pausar_cobro: %s", e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Stripe no aceptó pausar el cobro de esta suscripción.",
        ) from e


async def _reanudar_cobro(subscription_id: str, settings: Settings) -> None:
    """Quita la pausa. `pause_collection=""` (cadena vacía) es como la API de
    Stripe expresa "borra este campo": mandar None lo dejaría igual."""
    stripe.api_key = settings.stripe_secret_key
    try:
        await stripe.Subscription.modify_async(
            subscription_id, pause_collection="", idempotency_key=clave_nuestra("reanudar-cobro")
        )
    except stripe.StripeError as e:
        logger.warning("Stripe rechazó _reanudar_cobro: %s", e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Stripe no aceptó reanudar el cobro de esta suscripción.",
        ) from e


async def _guardar_y_auditar(
    db: PostgrestClient,
    request: Request,
    admin: AdminClaims,
    salon_id: str,
    account_id: str,
    cambios: dict,
    accion: str,
    detalle: dict,
) -> None:
    """Escribe el flag y deja el rastro. Mismo criterio que `extend_trial`: si lo
    de Stripe ya está hecho, la acción queda auditada pase lo que pase con la BD,
    y el fallo de BD se reporta en vez de tragárselo."""
    error_bd: Exception | None = None
    try:
        # LA SUSPENSIÓN ES DE LA CUENTA. El rastro de auditoría sigue apuntando
        # al salón, que es lo que el operador tenía delante cuando pulsó.
        await db.update("accounts", {"id": f"eq.{account_id}"}, cambios)
    except PostgrestError as e:
        error_bd = e
        detalle = {**detalle, "bd_error": True}

    try:
        await append_admin_log(
            db,
            admin_id=admin.sub,
            accion=accion,
            ip=client_ip(request),
            target_salon_id=salon_id,
            detalle=detalle,
        )
    except PostgrestError:
        pass

    if error_bd is not None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "El cobro se actualizó en Stripe, pero no se pudo guardar el estado de "
                "la cuenta en la base de datos. Vuelve a intentarlo."
            ),
        ) from error_bd


@router.post("/tenants/{tenant_id}/suspend", response_model=TenantDetailOut)
async def suspend_tenant(
    tenant_id: UUID,
    payload: SuspenderIn,
    request: Request,
    admin: AdminClaims = Depends(require_admin_roles(*ADMIN_MUTABLE)),
    db: PostgrestClient = Depends(get_admin_db),
    settings: Settings = Depends(get_settings),
) -> TenantDetailOut:
    salon_id = str(tenant_id)
    detalle_actual = await _load_tenant_detail(db, salon_id)  # 404 si no existe

    if detalle_actual.salon.suspendido:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Esta cuenta ya está suspendida.",
        )

    account_id = await _cuenta_del_tenant(db, salon_id)
    subscription_id = await _suscripcion_pausable(db, account_id)

    # Primero Stripe, luego la BD: si Stripe falla no se ha bloqueado a nadie y
    # el operador puede reintentar sin dejar la cuenta a medias. Al revés
    # dejaríamos un salón sin acceso al que se le sigue cobrando.
    if subscription_id:
        # Solo se exige Stripe cuando hay algo que pausar: un salón sin
        # suscripción tiene que poder suspenderse aunque la facturación no esté
        # configurada.
        if not settings.stripe_secret_key:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="La facturación no está configurada.",
            )
        await _pausar_cobro(subscription_id, settings)

    await _guardar_y_auditar(
        db,
        request,
        admin,
        salon_id,
        account_id,
        cambios={
            "suspendido": True,
            "suspendido_at": datetime.now(timezone.utc).isoformat(),
            "suspendido_por": admin.sub,
        },
        accion="suspend_account",
        detalle={
            "motivo": payload.motivo,
            "cobro_pausado": bool(subscription_id),
            "stripe_subscription_id": subscription_id,
        },
    )
    return await _load_tenant_detail(db, salon_id)


@router.post("/tenants/{tenant_id}/reactivate", response_model=TenantDetailOut)
async def reactivate_tenant(
    tenant_id: UUID,
    payload: ReactivarIn,
    request: Request,
    admin: AdminClaims = Depends(require_admin_roles(*ADMIN_MUTABLE)),
    db: PostgrestClient = Depends(get_admin_db),
    settings: Settings = Depends(get_settings),
) -> TenantDetailOut:
    salon_id = str(tenant_id)
    detalle_actual = await _load_tenant_detail(db, salon_id)  # 404 si no existe

    if not detalle_actual.salon.suspendido:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Esta cuenta no está suspendida.",
        )

    account_id = await _cuenta_del_tenant(db, salon_id)
    subscription_id = await _suscripcion_pausable(db, account_id)
    if subscription_id:
        if not settings.stripe_secret_key:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="La facturación no está configurada.",
            )
        await _reanudar_cobro(subscription_id, settings)

    await _guardar_y_auditar(
        db,
        request,
        admin,
        salon_id,
        account_id,
        # `suspendido_por` y `suspendido_at` se limpian: dejarlos puestos con
        # `suspendido=false` haría creer al siguiente que la cuenta sigue
        # suspendida desde esa fecha.
        cambios={"suspendido": False, "suspendido_at": None, "suspendido_por": None},
        accion="reactivate_account",
        detalle={
            "motivo": payload.motivo,
            "cobro_reanudado": bool(subscription_id),
            "stripe_subscription_id": subscription_id,
        },
    )
    return await _load_tenant_detail(db, salon_id)
