"""Métricas, facturación y auditoría del panel de plataforma.

Sustituye a `mock-super-admin.ts` en Dashboard, Facturación y Configuración.

LO QUE NO HAY, Y POR QUÉ: no se exponen MRR mensual, churn ni cohortes.
`subscriptions` tiene `unique(salon_id)` y el webhook la actualiza IN-PLACE, así
que del estado de ayer no queda rastro; `stripe_events` solo guarda el id del
evento, no su contenido. Reconstruir esas series exigiría o consultar Stripe o
una tabla de snapshots — ninguna de las dos entra aquí. El panel enseña un
estado «todavía no hay histórico» en su sitio, que es información verdadera, en
vez de una curva bonita que no significa nada.

Todo son lecturas, así que las tres rutas están abiertas a los tres roles del
panel, `viewer` incluido. Se lee con la service key por el mismo motivo que
`admin_tenants`: el super admin no pertenece a ningún salón y la RLS por
salon_id no le dejaría ver nada.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import logging

import stripe
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core import publico
from app.billing.plans import EMPLOYEE_LIMITS, PLAN_ORDER
from app.billing.prices import plan_interval_for_price
from app.billing.stripe_objects import stripe_get as _g
from app.core.config import Settings, get_settings
from app.billing.precios import TARIFA, euros_al_mes
from app.core import zona as zonas
from app.core import zona_de_la_cuenta as cuentas_zona
from app.core.cuenta import con_la_cuenta_dentro
from app.core.admin_deps import AdminClaims, get_admin_db, require_admin_roles
from app.db.postgrest_client import PostgrestClient, PostgrestError, trozos_en
from app.core import plazo_de_datos as plazos
from app.core import simulacro as simulacros
from app.tareas import plazo_de_datos as tarea_plazo
from app.tareas.recordatorios import (
    CADENCIA_CRON,
    ESTADOS_VIVOS,
    SIN_LATIDO,
    TOLERANCIA_ATRASO,
    momento_en_que_debio_salir,
)
from app.schemas.admin_plataforma import (
    AdminEquipoFila,
    AlertaPlataforma,
    CuentaEnPlazo,
    FilaSimulacro,
    PlazoDeDatosOut,
    SimulacroOut,
    SimulacrosOut,
    AlertaTarea,
    ConfiguracionOut,
    FacturacionOut,
    LogFila,
    LogsOut,
    MetricasOut,
    SaludTareas,
    MrrPorPlan,
    PlanTarifa,
    SuscripcionFila,
)

logger = logging.getLogger("estetix.plataforma")

router = APIRouter(prefix="/admin", tags=["admin-plataforma"], dependencies=[publico.PLATAFORMA])

# Lectura: los tres roles del panel. No hay ninguna mutación en este módulo.
CUALQUIER_ADMIN = ("owner", "support", "viewer")

# Estados de Stripe que representan una suscripción que ESTÁ pagando. `trialing`
# queda fuera a propósito: una prueba todavía no factura, y meterla en el MRR es
# el error clásico que hace parecer que se ingresa más de lo que se ingresa.
ESTADOS_COBRANDO = frozenset({"active"})
# Cobro fallido pero suscripción viva: dinero en riesgo, contado aparte.
ESTADOS_EN_RIESGO = frozenset({"past_due"})

# Cuántos días antes se avisa de que una prueba se acaba.
DIAS_AVISO_TRIAL = 7

# `moneda` entra en la lista con la reestructuración de precios: sin ella el
# MRR sumaría dólares como si fueran euros.
_COLUMNAS_SUSCRIPCION = (
    # `account_id` es la llave; `salon_id` se sigue trayendo porque la alerta de
    # pago fallido apunta a un salón, que es lo que el operador reconoce.
    "account_id,salon_id,plan,intervalo,moneda,estado,trial_ends_at,"
    # `stripe_subscription_id` es con lo que el vigía cruza nuestra fila con la
    # de Stripe.
    "current_period_end,cancel_at_period_end,stripe_subscription_id,"
    # La alarma de la baja (0052) y la voluntad de irse que vigila (0053).
    "alarma_cancelacion,baja_solicitada_at"
)

# Cuántas suscripciones se le piden a Stripe de una vez para comparar. Es el
# máximo de la API. Con más de esto habría que paginar, y entonces esto dejaría
# de ser barato: cuando llegue el día, el vigía se muda a una tarea de fondo.
_TOPE_VIGIA = 100

# Cuántos salones se nombran en el detalle de la alerta. El resto se cuentan.
_NOMBRES_EN_LA_ALERTA = 5


def _ahora() -> datetime:
    # Envuelto para que los tests muevan el reloj sin parchear datetime.
    return datetime.now(timezone.utc)


def _fecha(valor) -> datetime | None:
    if not valor:
        return None
    try:
        return datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    except ValueError:
        return None


def _sin_las_cuentas_suspendidas(salones: list[dict]) -> list[dict]:
    """Fuera los centros cuya CUENTA está suspendida.

    Mismo criterio que el cron: la suspensión dejó de ser del centro en la
    0040, y se descarta en Python porque filtrar sobre un recurso embebido pide
    una sintaxis que el doble de los tests no aplica.
    """
    # Ni los de una DEMO (0080): no mandan recordatorios, así que no pueden
    # llegar tarde a ninguno.
    return [s for s in salones
            if not (s.get("accounts") or {}).get("suspendido") and not (s.get("accounts") or {}).get("es_demo")]


async def _cargar(db: PostgrestClient) -> tuple[list[dict], list[dict]]:
    """Salones y suscripciones. Dos consultas planas que se cruzan en Python,
    igual que en `admin_tenants`.

    TODAS, NO LAS MIL PRIMERAS: `select` se queda en mil sin avisar, y el MRR
    del panel saldría bajo sin que nada se pusiera en rojo."""
    try:
        salones = await db.select_todo(
            "salons",
            {
                "select": "id,nombre_comercial,created_at,account_id,sigue_al_bajar,zona_horaria,accounts(plan,es_demo)",
                "order": "created_at.desc,id.asc",
            },
        )
        suscripciones = await db.select_todo(
            "subscriptions", {"select": _COLUMNAS_SUSCRIPCION, "order": "account_id.asc"}
        )
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudieron cargar los datos de plataforma.",
        ) from e
    # UNA DEMO NO ES UN CLIENTE (0080): fuera de las cifras de la plataforma.
    return [con_la_cuenta_dentro(f) for f in salones if not (f.get("accounts") or {}).get("es_demo")], suscripciones


def _resumen_por_plan(activas: list[dict]) -> tuple[list[MrrPorPlan], float, int]:
    """Reparto del MRR por plan, el total, y cuántas no se pudieron valorar.

    Se recorren los planes en el orden de la jerarquía (`PLAN_ORDER`) y no por
    orden de aparición: así el donut y la tabla salen siempre en el mismo orden
    aunque cambien los datos.
    """
    mrr_por_plan: dict[str, float] = {p: 0.0 for p in PLAN_ORDER}
    salones_por_plan: dict[str, int] = {p: 0 for p in PLAN_ORDER}
    sin_tarifa = 0

    for s in activas:
        plan = s.get("plan")
        importe = euros_al_mes(plan, s.get("intervalo"), s.get("moneda"))
        if importe is None:
            sin_tarifa += 1
            # Si el plan sí se reconoce, el salón se cuenta igual: existe, solo
            # que no sabemos cuánto aporta. Perderlo de la cuenta de salones
            # sería un segundo error encima del primero.
            if plan in salones_por_plan:
                salones_por_plan[plan] += 1
            continue
        mrr_por_plan[plan] += importe
        salones_por_plan[plan] += 1

    total = sum(mrr_por_plan.values())
    filas = [
        MrrPorPlan(
            plan=plan,
            salones=salones_por_plan[plan],
            mrr=round(mrr_por_plan[plan], 2),
            # Sobre el MRR, no sobre el número de salones: es el reparto del
            # dinero lo que interesa. Con MRR cero, cero por ciento — nunca una
            # división entre cero.
            porcentaje=round(mrr_por_plan[plan] / total * 100, 1) if total else 0.0,
        )
        for plan in PLAN_ORDER
    ]
    return filas, round(total, 2), sin_tarifa


async def _salud_de_las_tareas(db: PostgrestClient, ahora) -> SaludTareas:
    """Cuánto hace que el cron no pasa, y cuánto se ha quedado por el camino.

    TRES NÚMEROS Y CADA UNO TAPA UN AGUJERO DISTINTO:

      · `hace_minutos` — que el cron NO se esté ejecutando. Es el único que
        detecta el silencio: un cron caído no genera errores, así que si no se
        mide el tiempo desde la última pasada, nada lo delata.
      · `fallidos` — que se ejecute pero los correos no salgan. Sellar antes de
        mandar evita duplicados a cambio de poder perder envíos; esto los cuenta.
      · `atrasadas` — el contrapeso de los dos anteriores. Se calcula desde las
        CITAS, no desde la tarea, así que sigue siendo verdad aunque el latido
        mienta, aunque la tabla esté vacía o aunque una pasada termine diciendo
        que todo fue bien. Es el único que habría cazado la banda con suelo: el
        cron corría, latía, y decía `procesados: 0` sin equivocarse.

    Cualquier fallo leyendo esto devuelve el vigía en blanco y NO tumba las
    métricas: el panel es la pantalla desde la que se mira todo lo demás.
    """
    try:
        filas = await db.select(
            "tareas_latido", {"select": "ultima_at,procesados,fallidos",
                              "tarea": "eq.recordatorios_email", "limit": "1"}
        )
    except PostgrestError:
        logger.exception("Panel: no se pudo leer el latido de las tareas.")
        return SaludTareas()

    fila = filas[0] if filas else {}
    ultima = _fecha(fila.get("ultima_at"))
    hace = int((ahora - ultima).total_seconds() // 60) if ultima else None
    atrasadas, atraso_max = await _recordatorios_atrasados(db, ahora)
    perdidos = await _envios_perdidos(db, ahora)

    return SaludTareas(
        ultima_at=fila.get("ultima_at"),
        hace_minutos=hace,
        procesados=int(fila.get("procesados") or 0),
        fallidos=int(fila.get("fallidos") or 0),
        atrasadas=atrasadas,
        atraso_max_minutos=atraso_max,
        fallidos_definitivos=perdidos,
    )


async def _envios_perdidos(db: PostgrestClient, ahora) -> int:
    """Recordatorios que ya no van a salir. Se cuentan desde las CITAS.

    POR QUÉ NO VALE EL CONTADOR DEL LATIDO: porque se sobrescribe en cada
    pasada. Un correo que se perdió a las 10:16 desaparecía de la alarma a las
    10:31, y una alarma que dura quince minutos no es una alarma — es un
    parpadeo que hay que tener la suerte de estar mirando.

    QUÉ CUENTA COMO PERDIDO: sellado y con motivo anotado. El sello dice que
    nadie va a volver a intentarlo y el motivo dice que no salió; las dos cosas
    a la vez solo ocurren cuando se dio por perdido. Un fallo que se está
    reintentando tiene el sello SUELTO, así que no entra aquí — ese está
    llegando tarde y lo cuenta `atrasadas`.

    Y SOLO DE CITAS QUE TODAVÍA NO HAN PASADO: esto es una alarma sobre alguien
    que va a quedarse sin su aviso, no un libro de cuentas. Lo de ayer ya no se
    puede arreglar, y dejarlo dentro haría que el número no bajara nunca.
    """
    try:
        salones = await db.select_todo(
            "salons",
            {
                "select": "id,accounts(suspendido,es_demo)",
                "recordatorios_email_activo": "eq.true",
                "order": "id.asc",
            },
        )
        salones = _sin_las_cuentas_suspendidas(salones)
        if not salones:
            return 0
        # EN TROZOS: son todos los salones de la plataforma con recordatorios.
        # En una sola URL, a partir de ~210 la petición fallaba y este vigía
        # decía 0 sin que nadie se enterase (`trozos_en`).
        perdidos: list[dict] = []
        for en in trozos_en(s["id"] for s in salones):
            if len(perdidos) >= 500:
                break
            perdidos += await db.select(
                "appointments",
                {
                    "select": "id",
                    "salon_id": en,
                    "recordatorio_enviado_at": "not.is.null",
                    "recordatorio_error": "not.is.null",
                    "estado": f"in.({','.join(ESTADOS_VIVOS)})",
                    "inicio": f"gt.{ahora.isoformat()}",
                    "limit": "500",
                },
            )
        perdidos = perdidos[:500]
    except PostgrestError:
        logger.exception("Panel: no se pudieron contar los envíos perdidos.")
        return 0
    return len(perdidos)


async def _recordatorios_atrasados(db: PostgrestClient, ahora) -> tuple[int, int | None]:
    """Recordatorios que tendrían que estar mandados y no lo están.

    ES UNA ALARMA DE SALUD, NO UN CONTADOR DE CALIDAD DE LOS DATOS, y esa es la
    diferencia con lo que contaba antes. Antes contaba «citas próximas sin
    sello», sin mirar si a alguna le tocaba de verdad: en un salón donde 9 de
    cada 11 clientes no tienen correo, el número se quedaba distinto de cero
    para siempre. Un indicador que nunca baja a cero se aprende a ignorar, y el
    día que signifique algo nadie lo va a mirar.

    EN FUNCIONAMIENTO NORMAL ESTO ES CERO. Si no lo es, algo está roto.

    Por eso se descuenta todo lo que nunca iba a salir, y se descuenta usando la
    MISMA regla que la pasada (`momento_en_que_debio_salir`) en vez de una copia:

      · Salón con el interruptor apagado, o suspendido — ni entra en la consulta.
      · Cliente sin correo o dado de baja, o cita reservada con demasiada prisa.
      · Cita que todavía no ha entrado en el techo de su salón.
      · Cita retenida por las horas de silencio: espera su turno, no llega tarde.

    LO QUE SÍ CUENTA aunque sorprenda: un cliente con
    `recordatorios_email_rechazado_at`. Esa columna apaga la pregunta del bot,
    no los envíos; si el salón le apuntó el correo a mano, ese recordatorio SALE
    y por tanto puede llegar tarde. Descontarlo aquí dejaría ciega la alarma
    justo para esa persona.

    Cualquier fallo devuelve 0 y se anota: el vigía no puede tumbar el panel.
    """
    try:
        salones = await db.select_todo(
            "salons",
            {
                "select": "id,recordatorio_horas,zona_horaria,accounts(suspendido,es_demo)",
                "recordatorios_email_activo": "eq.true",
                "order": "id.asc",
            },
        )
        salones = _sin_las_cuentas_suspendidas(salones)
        if not salones:
            return (0, None)

        horas_de = {s["id"]: int(s.get("recordatorio_horas") or 24) for s in salones}
        zona_de = {s["id"]: zonas.zona(s.get("zona_horaria")) for s in salones}
        # El techo más lejano de todos, para acotar la consulta a lo que como
        # mucho puede ser elegible en algún salón.
        techo = ahora + timedelta(hours=max(horas_de.values()))

        citas: list[dict] = []
        # EN TROZOS, por lo mismo que `_envios_perdidos` (`trozos_en`).
        for en in trozos_en(horas_de):
            citas += await _citas_sin_sello(db, en, ahora, techo)
    except PostgrestError:
        logger.exception("Panel: no se pudo contar el atraso de recordatorios.")
        return (0, None)
    return _atrasos(citas, horas_de, zona_de, ahora)


async def _citas_sin_sello(db: PostgrestClient, en: str, ahora, techo) -> list[dict]:
    return await db.select(
        "appointments",
        {
            "select": (
                "salon_id,inicio,created_at,"
                "clients(email,recordatorios_baja_at)"
            ),
            "salon_id": en,
            "recordatorio_enviado_at": "is.null",
            "estado": f"in.({','.join(ESTADOS_VIVOS)})",
            "inicio": f"gt.{ahora.isoformat()}",
            "and": f"(inicio.lte.{techo.isoformat()})",
            # Un tope alto a propósito: lo normal es cero, y si esto se
            # llena es que lleva días roto — el número sería un mínimo, y un
            # mínimo de 500 ya dice todo lo que hay que saber.
            "limit": "500",
        },
    )


def _atrasos(citas: list[dict], horas_de: dict, zona_de: dict, ahora) -> tuple[int, int | None]:
    # Cuántas y desde cuándo la peor. El «desde cuándo» es lo que distingue
    # «acaba de empezar» de «lleva dos días así», y sale de la misma cuenta.
    atrasos = [
        ahora - momento
        for c in citas
        if (
            momento := momento_en_que_debio_salir(
                c, horas_de.get(c.get("salon_id"), 24), ahora, zona_de[c["salon_id"]]
            )
        )
        is not None
        and (ahora - momento) > TOLERANCIA_ATRASO
    ]
    if not atrasos:
        return (0, None)
    return (len(atrasos), int(max(atrasos).total_seconds() // 60))


async def _divergencias_con_stripe(
    suscripciones: list[dict], nombres: dict[str, str], settings: Settings
) -> list[AlertaTarea]:
    """Lo que cobramos frente a lo que damos. Una alerta, o nada.

    POR QUÉ ESTO EXISTE Y VIVE AQUÍ. Desde que las bajadas se programan, hay un
    momento en el que el calendario de Stripe se dispara y el webhook tiene que
    enterarse. Si ese aviso no llega, Stripe cobra un plan y la aplicación da
    otro; y eso no produce ningún error, produce silencio. Ya pasó con la
    moneda: se descubrió porque rompió una subida de plan, no porque nadie lo
    estuviera mirando.

    UNA SOLA LLAMADA para todas las suscripciones, no una por cuenta: así el
    panel no se hace más lento cuantos más clientes haya. Con más de
    `_TOPE_VIGIA` suscripciones esto dejaría de valer y habría que paginar o
    mudarlo a una tarea de fondo.

    NO PODER PREGUNTAR NO ES UNA DIVERGENCIA. Si Stripe no responde, el panel
    sale igual y sin alerta: tumbarlo dejaría a ciegas también el latido del
    cron, que es lo más grave de este bloque.
    """
    # LA ALARMA DE LA BAJA, GUARDADA (0052). Va primero y no depende de poder
    # preguntarle a Stripe: cuando el webhook corrige lo que pedimos, la base y
    # Stripe vuelven a coincidir y la comparación de abajo ya no lo vería. Lo
    # que queda es esta alarma, con los dos valores.
    # LAS BAJAS CONTRADICHAS van en su PROPIA alerta (0053): no son «se cobra
    # una cosa y se da otra», son «el salón pidió irse y se le va a cobrar», y
    # el remedio no es «Sincronizar» —eso le daría la razón a Stripe— sino
    # hablar con el salón.
    bajas: list[str] = [
        f"{nombres.get(fila.get('salon_id'), '—')}: {fila['alarma_cancelacion']}"
        for fila in suscripciones
        if fila.get("alarma_cancelacion")
    ]
    diferencias: list[str] = []

    lista = None
    if settings.stripe_secret_key:
        stripe.api_key = settings.stripe_secret_key
        try:
            lista = await stripe.Subscription.list_async(status="all", limit=_TOPE_VIGIA)
        except stripe.StripeError:
            logger.warning("Panel: no se pudo comparar las suscripciones con Stripe.")

    en_stripe = {_g(s, "id"): s for s in (_g(lista, "data") or []) if _g(s, "id")} if lista else {}
    for fila in suscripciones:
        suya = en_stripe.get(fila.get("stripe_subscription_id"))
        if suya is None:
            # No la conocemos en este modo de Stripe (prueba/real) o es de otra
            # cuenta: comparar lo que no se ha podido leer sería inventar ruido.
            continue
        salon = nombres.get(fila.get("salon_id"), "—")
        items = _g(_g(suya, "items") or {}, "data") or []
        precio = _g(_g(items[0], "price") if items else {}, "id")
        mapping = plan_interval_for_price(settings, precio) if precio else None
        plan_stripe = mapping[0] if mapping else None
        estado_stripe = _g(suya, "status")

        if precio and mapping is None:
            # UN PRECIO QUE NO CONOCEMOS. Antes esto se saltaba en silencio: el
            # `if` de abajo exige `plan_stripe`, que aquí es None, así que la
            # fila pasaba de largo y el vigía decía que todo cuadraba.
            #
            # Es el caso que va a morder el día que se cree el catálogo LIVE:
            # un precio nuevo en Stripe que `billing/prices.py` no tiene. La
            # cuenta estaría pagando algo que nosotros no sabemos traducir a un
            # plan, y `lo_que_incluye_el_plan` lo trataría como «no resuelto» —
            # concediéndolo todo y chillando en el log del backend, que es
            # donde no lo ve nadie. Aquí sí se ve.
            diferencias.append(
                f"{salon}: Stripe cobra un precio que no conocemos ({precio})"
            )
        elif plan_stripe and plan_stripe != fila.get("plan"):
            diferencias.append(
                f"{salon}: damos {fila.get('plan')} y Stripe cobra {plan_stripe}"
            )
        elif estado_stripe and estado_stripe != fila.get("estado"):
            diferencias.append(
                f"{salon}: aquí está {fila.get('estado')} y en Stripe {estado_stripe}"
            )
        elif (
            fila.get("baja_solicitada_at")
            and not fila.get("alarma_cancelacion")
            and not _g(suya, "cancel_at_period_end")
            and estado_stripe not in ("canceled", "incomplete_expired")
        ):
            # LA VOLUNTAD CONTRA STRIPE, en directo: por si el webhook que
            # tenía que dar la alarma no llegó. El salón pidió irse y Stripe
            # ya no lo va a cancelar.
            bajas.append(
                f"{salon}: pidió irse y en Stripe ya no se cancela (se le cobrará la "
                "renovación). No llegó el aviso de Stripe que lo cuenta."
            )
        elif bool(_g(suya, "cancel_at_period_end")) != bool(fila.get("cancel_at_period_end")):
            # LA BAJA. Aquí, si el webhook no llegó: la pantalla diría «se
            # cancela el…» a quien sigue pagando, o nada a quien se va.
            diferencias.append(
                f"{salon}: aquí se cancela al terminar="
                f"{str(bool(fila.get('cancel_at_period_end'))).lower()} y en Stripe "
                f"{str(bool(_g(suya, 'cancel_at_period_end'))).lower()}"
            )

    alertas: list[AlertaTarea] = []
    if bajas:
        n = len(bajas)
        alertas.append(
            AlertaTarea(
                tipo="baja_contradicha",
                gravedad="critica",
                titulo=(
                    f"{n} salón pidió irse y Stripe ya no lo cancela"
                    if n == 1
                    else f"{n} salones pidieron irse y Stripe ya no los cancela"
                ),
                detalle=(
                    f"{_resumen(bajas)}. Si fue el portal y lo hizo el propio salón, es "
                    "legítimo: el portal sigue abierto. Si no, se le va a cobrar una "
                    "renovación que no quiere: hay que hablar con el salón antes."
                ),
            )
        )
    if diferencias:
        n = len(diferencias)
        alertas.append(
            AlertaTarea(
                tipo="plan_divergente",
                gravedad="critica",
                titulo=(
                    f"{n} suscripci{'ones' if n != 1 else 'ón'} "
                    f"no dice{'n' if n != 1 else ''} lo mismo que Stripe"
                ),
                detalle=(
                    f"{_resumen(diferencias)}. Se cobra una cosa y se da otra: normalmente es un "
                    "aviso de Stripe que no llegó. Desde la ficha del salón, «Sincronizar» "
                    "vuelve a preguntárselo a Stripe y lo deja al día."
                ),
            )
        )
    return alertas


def _resumen(lineas: list[str]) -> str:
    de_mas = len(lineas) - _NOMBRES_EN_LA_ALERTA
    texto = "; ".join(lineas[:_NOMBRES_EN_LA_ALERTA])
    return texto + (f"; y {de_mas} más" if de_mas > 0 else "")


def _alertas_de_las_tareas(salud: SaludTareas) -> list[AlertaTarea]:
    """Qué hay que mirar HOY de las tareas programadas. Vacía si nada.

    TRES SEÑALES Y LA PRIMERA ES LA QUE DUELE. Si el cron no corre, no sale ni
    un recordatorio y ninguna de las otras dos se va a enterar a tiempo: el
    atraso necesita que exista una cita elegible para crecer, y los fallos solo
    se cuentan de pasadas que ocurren. Por eso el silencio va primero y va en
    rojo.

    NADA CUANDO TODO ESTÁ SANO, que es el requisito de verdad. Un panel que
    siempre enseña algo enseña ruido, y el día que haya una avería nadie la va a
    distinguir del ruido de siempre.
    """
    alertas: list[AlertaTarea] = []

    if salud.ultima_at is None:
        alertas.append(
            AlertaTarea(
                tipo="sin_latido",
                gravedad="critica",
                titulo="El cron de recordatorios no ha corrido nunca",
                detalle=(
                    "No hay ninguna pasada registrada. Mientras siga así no sale "
                    "ni un recordatorio: revisa que el servicio exista en Railway "
                    "y que su horario esté puesto."
                ),
            )
        )
    elif salud.hace_minutos is not None and salud.hace_minutos > _MINUTOS_SIN_LATIDO:
        alertas.append(
            AlertaTarea(
                tipo="sin_latido",
                gravedad="critica",
                titulo="El cron de recordatorios lleva parado",
                detalle=(
                    f"La última pasada fue hace {_hace(salud.hace_minutos)}, y debería "
                    f"haber una cada {int(CADENCIA_CRON.total_seconds() // 60)} minutos. "
                    "Mientras siga parado no sale ningún recordatorio."
                ),
            )
        )

    if salud.fallidos_definitivos:
        n = salud.fallidos_definitivos
        alertas.append(
            AlertaTarea(
                tipo="envios_fallidos",
                gravedad="critica",
                titulo=f"{n} recordatorio{'s' if n != 1 else ''} que ya no va{'n' if n != 1 else ''} a salir",
                detalle=(
                    ("Se dio" if n == 1 else "Se dieron")
                    + " por perdido"
                    + ("" if n == 1 else "s")
                    + ": o el fallo no permitía reintentar sin arriesgar un correo "
                    "duplicado, o se agotaron los intentos. El motivo de cada uno "
                    "queda anotado en su cita, y son citas que aún no han pasado."
                ),
            )
        )

    if salud.atrasadas:
        cuantas = salud.atrasadas
        alertas.append(
            AlertaTarea(
                tipo="recordatorios_atrasados",
                gravedad="aviso",
                titulo=(
                    f"{cuantas} recordatorio{'s' if cuantas != 1 else ''} "
                    f"{'llevan' if cuantas != 1 else 'lleva'} sin salir"
                ),
                detalle=(
                    (
                        f"El más antiguo debió salir hace {_hace(salud.atraso_max_minutos)}. "
                        if salud.atraso_max_minutos is not None
                        else ""
                    )
                    + "En funcionamiento normal esto es cero: el cron pasa cada "
                    f"{int(CADENCIA_CRON.total_seconds() // 60)} minutos."
                ),
            )
        )

    return alertas


# El umbral del latido, en minutos, sacado de la cadencia real del cron.
_MINUTOS_SIN_LATIDO = int(SIN_LATIDO.total_seconds() // 60)


def _hace(minutos: int | None) -> str:
    """«40 minutos», «3 h 20 min», «2 días». Para leerlo, no para calcular."""
    if minutos is None:
        return "un rato"
    if minutos < 60:
        return f"{minutos} minuto{'s' if minutos != 1 else ''}"
    horas, resto = divmod(minutos, 60)
    if horas < 24:
        return f"{horas} h" if not resto else f"{horas} h {resto} min"
    dias, horas = divmod(horas, 24)
    return f"{dias} día{'s' if dias != 1 else ''}" + (f" y {horas} h" if horas else "")


@router.get(
    "/metricas",
    response_model=MetricasOut,
    summary="KPIs de plataforma (foto actual)",
    description=(
        "Solo métricas con dato real HOY. No incluye MRR mensual, churn ni "
        "cohortes: `subscriptions` se actualiza in-place y no queda histórico."
    ),
)
async def metricas(
    _admin: AdminClaims = Depends(require_admin_roles(*CUALQUIER_ADMIN)),
    db: PostgrestClient = Depends(get_admin_db),
    settings: Settings = Depends(get_settings),
) -> MetricasOut:
    salones, suscripciones = await _cargar(db)
    nombres = {s["id"]: s["nombre_comercial"] for s in salones}

    ahora = _ahora()
    inicio_mes = ahora.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    activas = [s for s in suscripciones if s.get("estado") in ESTADOS_COBRANDO]
    en_riesgo = [s for s in suscripciones if s.get("estado") in ESTADOS_EN_RIESGO]
    trials = [s for s in suscripciones if s.get("estado") == "trialing"]

    por_plan, mrr, sin_tarifa = _resumen_por_plan(activas)
    riesgo = sum(
        euros_al_mes(s.get("plan"), s.get("intervalo"), s.get("moneda")) or 0.0
        for s in en_riesgo
    )

    altas = 0
    for s in salones:
        creado = _fecha(s.get("created_at"))
        if creado and creado >= inicio_mes:
            altas += 1

    salud = await _salud_de_las_tareas(db, ahora)
    alertas_tareas = _alertas_de_las_tareas(salud)
    # El vigía de la divergencia va el PRIMERO de su bloque: un plan que no
    # coincide con Stripe es dinero mal cobrado, y mientras esté así cada día
    # cuenta.
    alertas_tareas[0:0] = await _divergencias_con_stripe(suscripciones, nombres, settings)

    alertas: list[AlertaPlataforma] = []
    for s in en_riesgo:
        sid = s["salon_id"]
        alertas.append(
            AlertaPlataforma(
                tipo="pago_fallido",
                salon_id=sid,
                salon=nombres.get(sid, "—"),
                detalle="El último cobro ha fallado y Stripe está reintentando.",
            )
        )
    limite = ahora + timedelta(days=DIAS_AVISO_TRIAL)
    for s in trials:
        fin = _fecha(s.get("trial_ends_at"))
        if not fin or fin > limite:
            continue
        dias = (fin - ahora).days
        sid = s["salon_id"]
        alertas.append(
            AlertaPlataforma(
                tipo="trial_termina",
                salon_id=sid,
                salon=nombres.get(sid, "—"),
                detalle=(
                    "La prueba termina hoy."
                    if dias <= 0
                    else f"La prueba termina en {dias} {'día' if dias == 1 else 'días'}."
                ),
            )
        )

    return MetricasOut(
        tareas=salud,
        alertas_tareas=alertas_tareas,
        salones_total=len(salones),
        salones_de_pago=len(activas),
        trials_activos=len(trials),
        altas_este_mes=altas,
        mrr=mrr,
        arr=round(mrr * 12, 2),
        suscripciones_sin_tarifa=sin_tarifa,
        mrr_en_riesgo=round(riesgo, 2),
        por_plan=por_plan,
        alertas=alertas,
    )


@router.get(
    "/facturacion",
    response_model=FacturacionOut,
    summary="Suscripciones y reparto del MRR (foto actual)",
)
async def facturacion(
    _admin: AdminClaims = Depends(require_admin_roles(*CUALQUIER_ADMIN)),
    db: PostgrestClient = Depends(get_admin_db),
) -> FacturacionOut:
    salones, suscripciones = await _cargar(db)
    # unique(account_id): como mucho una suscripción por cuenta.
    por_cuenta = {s["account_id"]: s for s in suscripciones}
    # `_cargar` trae TODOS los centros: de ahí sale el de referencia de cada cuenta.
    zona_cuentas = cuentas_zona.por_cuenta(salones)

    filas: list[SuscripcionFila] = []
    for salon in salones:
        sus = por_cuenta.get(salon.get("account_id"))
        # Los salones SIN suscripción salen igual, con estado nulo: en el panel
        # de plataforma «este salón no ha llegado a pagar nunca» es justo la
        # fila que interesa ver, no una que convenga esconder.
        filas.append(
            SuscripcionFila(
                salon_id=salon["id"],
                salon=salon["nombre_comercial"],
                plan=(sus or {}).get("plan") or salon.get("plan"),
                intervalo=(sus or {}).get("intervalo"),
                estado=(sus or {}).get("estado"),
                euros_al_mes=euros_al_mes(
                    (sus or {}).get("plan"),
                    (sus or {}).get("intervalo"),
                    (sus or {}).get("moneda"),
                ),
                proximo_cobro=_fecha((sus or {}).get("current_period_end")),
                cancela_al_terminar=bool((sus or {}).get("cancel_at_period_end")),
                cuenta=vars(cuentas_zona.de(zona_cuentas, salon.get("account_id"))),
            )
        )

    activas = [s for s in suscripciones if s.get("estado") in ESTADOS_COBRANDO]
    por_plan, mrr, sin_tarifa = _resumen_por_plan(activas)
    return FacturacionOut(
        filas=filas, por_plan=por_plan, mrr=mrr, suscripciones_sin_tarifa=sin_tarifa
    )


@router.get(
    "/logs",
    response_model=LogsOut,
    summary="Log de auditoría del panel",
    description=(
        "`admin_logs` es append-only a nivel de base de datos (0004): ni el "
        "propietario puede modificar o borrar una línea. Aquí solo se lee, por "
        "`admin_logs_vista` (0091): cada apunte con su resultado, y las lecturas "
        "de soporte juntas por sesión y ruta, con cuántas veces."
    ),
)
async def logs(
    accion: str | None = Query(default=None, max_length=60),
    salon_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _admin: AdminClaims = Depends(require_admin_roles(*CUALQUIER_ADMIN)),
    db: PostgrestClient = Depends(get_admin_db),
) -> LogsOut:
    filtros: dict[str, str] = {}
    if accion:
        filtros["accion"] = f"eq.{accion}"
    if salon_id:
        filtros["target_salon_id"] = f"eq.{salon_id}"

    try:
        # Se pide una fila de más que el `limit` para saber si hay siguiente
        # página sin pagar un count exacto; el total se completa abajo.
        filas = await db.select(
            "admin_logs_vista",
            {
                "select": "id,admin_id,accion,target_salon_id,detalle,ip,timestamp,ultima,veces,resultado",
                "order": "ultima.desc",
                "limit": str(limit),
                "offset": str(offset),
                **filtros,
            },
        )
        # El total se CUENTA: traer los ids para contarlos se quedaba en mil.
        total = await db.count("admin_logs_vista", filtros)
        admins = await db.select("admin_users", {"select": "id,email"})
        salones = await db.select_todo("salons", {"select": "id,nombre_comercial", "order": "id.asc"})
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo cargar el log de auditoría.",
        ) from e

    email_de = {a["id"]: a.get("email") for a in admins}
    nombre_de = {s["id"]: s["nombre_comercial"] for s in salones}

    return LogsOut(
        filas=[
            LogFila(
                id=f["id"],
                admin_email=email_de.get(f.get("admin_id")),
                accion=f["accion"],
                salon=nombre_de.get(f.get("target_salon_id")),
                ip=f.get("ip"),
                timestamp=_fecha(f["timestamp"]) or _ahora(),
                detalle=f.get("detalle") or {},
                ultima=_fecha(f.get("ultima")),
                veces=f.get("veces") or 1,
                resultado=f.get("resultado"),
            )
            for f in filas
        ],
        total=total,
    )


@router.get(
    "/configuracion",
    response_model=ConfiguracionOut,
    summary="Equipo del panel y tarifas vigentes (solo lectura)",
)
async def configuracion(
    _admin: AdminClaims = Depends(require_admin_roles(*CUALQUIER_ADMIN)),
    db: PostgrestClient = Depends(get_admin_db),
) -> ConfiguracionOut:
    try:
        # El hash de la contraseña y el secreto TOTP NO se piden: no hacen falta
        # para pintar la lista, y lo que no se pide no se puede filtrar.
        equipo = await db.select(
            "admin_users",
            {"select": "id,email,nombre,rol,totp_enabled,ultimo_acceso", "order": "email.asc"},
        )
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudo cargar el equipo."
        ) from e

    return ConfiguracionOut(
        equipo=[
            AdminEquipoFila(
                id=a["id"],
                email=a["email"],
                nombre=a.get("nombre"),
                rol=a["rol"],
                totp_enabled=bool(a.get("totp_enabled")),
                ultimo_acceso=_fecha(a.get("ultimo_acceso")),
            )
            for a in equipo
        ],
        tarifas=[
            # Se enseña lo que se VENDE: mensual en las dos monedas. El anual
            # sigue existiendo en `TARIFA` para poder contar las suscripciones
            # vivas, pero ponerlo en la pantalla de tarifas invitaría a
            # ofrecerlo, y ya no se ofrece.
            PlanTarifa(
                plan=plan,
                eur=TARIFA[(plan, "mensual", "eur")],
                usd=TARIFA[(plan, "mensual", "usd")],
                max_empleados=EMPLOYEE_LIMITS[plan],
            )
            for plan in PLAN_ORDER
        ],
    )


async def _cuentas_en_plazo(db: PostgrestClient, settings: Settings) -> list[CuentaEnPlazo]:
    """Los relojes en marcha, ya con fechas en el calendario de cada cuenta."""
    ahora = _ahora()
    try:
        lista = await tarea_plazo.relojes(db, ahora)
        nombres, zona, correos = await tarea_plazo.datos_de_las_cuentas(db, sorted({r.account_id for r in lista}))
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo leer el plazo de los datos.",
        ) from e
    salida = []
    for r in lista:
        z, p = r.zona_horaria, r.plazo
        salida.append(CuentaEnPlazo(
            account_id=r.account_id, cuenta=nombres.get(r.account_id), terminada_el=p.terminada_el,
            se_borra_el=p.se_borra_el, dias_restantes=p.dias_restantes, fuera_de_plazo=r.fuera_de_plazo,
            zona_horaria=z, origen=r.origen,
            avisos_enviados=[a for a, enviado in r.enviados.items() if enviado],
            con_correo=bool(correos.get(r.account_id)),
        ))
    salida.sort(key=lambda c: (c.dias_restantes, c.cuenta or ""))
    return salida


@router.get(
    "/plazo-de-datos",
    response_model=PlazoDeDatosOut,
    summary="Cuentas con el reloj de los datos en marcha (solo lectura)",
)
async def plazo_de_datos(
    _admin: AdminClaims = Depends(require_admin_roles(*CUALQUIER_ADMIN)),
    db: PostgrestClient = Depends(get_admin_db),
    settings: Settings = Depends(get_settings),
) -> PlazoDeDatosOut:
    """El contador: qué cuentas tienen el reloj en marcha y cuántos días les quedan.

    LEE CON LA MISMA CONSULTA QUE LA TAREA DE LOS AVISOS (`relojes`): lo que
    enseña el panel y a quien escribe la tarea no pueden ser dos listas. Y el día
    que exista el borrado, tendrá que salir de aquí también. No borra nada.
    """
    return PlazoDeDatosOut(cuentas=await _cuentas_en_plazo(db, settings), plazo_dias=plazos.PLAZO_DIAS)


async def _simulacro(db: PostgrestClient, account_id: str, en_plazo: dict[str, CuentaEnPlazo]) -> SimulacroOut:
    try:
        sim = await simulacros.simular(db, account_id)
        nombre = (await db.select("accounts", {"select": "nombre", "id": f"eq.{account_id}", "limit": "1"}))
    except PostgrestError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudo hacer el simulacro.") from e
    c = en_plazo.get(account_id)
    situacion = "sin_reloj" if c is None else ("fuera_de_plazo" if c.fuera_de_plazo else "en_plazo")
    return SimulacroOut(
        account_id=account_id,
        cuenta=(nombre[0].get("nombre") if nombre else None),
        situacion=situacion,
        dias_restantes=c.dias_restantes if c else None,
        se_borra_el=c.se_borra_el if c else None,
        filas_a_borrar=sim.filas_a_borrar,
        problemas=len(sim.problemas),
        filas=[FilaSimulacro(tabla=f.tabla, filas=f.filas, que_haria=f.que_haria, detalle=f.detalle,
                             decidido=f.decidido, problema=f.problema, en_la_baja=f.en_la_baja)
               for f in sim.filas],
    )


@router.get(
    "/plazo-de-datos/simulacros",
    response_model=SimulacrosOut,
    summary="Simulacro de todas las cuentas fuera de plazo (no borra nada)",
)
async def simulacros_fuera_de_plazo(
    _admin: AdminClaims = Depends(require_admin_roles(*CUALQUIER_ADMIN)),
    db: PostgrestClient = Depends(get_admin_db),
    settings: Settings = Depends(get_settings),
) -> SimulacrosOut:
    """Lo que borraría HOY el borrado, si existiera: una lista por cuenta fuera de
    plazo. Sale de `relojes`, la misma que tendrá que usar el borrado."""
    cuentas = await _cuentas_en_plazo(db, settings)
    por_cuenta = {c.account_id: c for c in cuentas}
    # LA MISMA LISTA QUE BORRARÍA EL BORRADO (0068): no se decide aquí.
    try:
        fuera = [str(f["account_id"]) for f in await db.rpc("cuentas_fuera_de_plazo", {"p_ahora": _ahora().isoformat()})]
    except PostgrestError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudo leer el plazo de los datos.") from e
    return SimulacrosOut(simulacros=[await _simulacro(db, a, por_cuenta) for a in fuera])


@router.get(
    "/plazo-de-datos/simulacro/{account_id}",
    response_model=SimulacroOut,
    summary="Simulacro de una cuenta concreta, esté o no fuera de plazo (no borra nada)",
    description="Acepta el id de la cuenta o el de uno de sus centros: la ficha del salón en el panel tiene el del centro.",
)
async def simulacro_de_una_cuenta(
    account_id: str,
    _admin: AdminClaims = Depends(require_admin_roles(*CUALQUIER_ADMIN)),
    db: PostgrestClient = Depends(get_admin_db),
    settings: Settings = Depends(get_settings),
) -> SimulacroOut:
    """Para mirar una cuenta concreta. Dice también si está en plazo, fuera o sin
    reloj: el simulacro de una cuenta viva enseña lo que se borraría SI lo
    estuviera, que es justo lo que hay que mirar antes de que exista el borrado."""
    try:
        existe = await db.select("accounts", {"select": "id", "id": f"eq.{account_id}", "limit": "1"})
        if not existe:
            # ¿Es un centro? Entonces, su cuenta.
            centro = await db.select("salons", {"select": "account_id", "id": f"eq.{account_id}", "limit": "1"})
            if centro and centro[0].get("account_id"):
                account_id = str(centro[0]["account_id"])
                existe = [{"id": account_id}]
    except PostgrestError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudo leer la cuenta.") from e
    if not existe:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No existe esa cuenta ni ese centro.")
    por_cuenta = {c.account_id: c for c in await _cuentas_en_plazo(db, settings)}
    return await _simulacro(db, account_id, por_cuenta)
