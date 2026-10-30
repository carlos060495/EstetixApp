"""LAS COMISIONES DE LOS REFERIDOS (0075): calcular y apuntar lo que se debe.

06-10-2026. NO SE PAGA NADA: solo se apunta.

DE DÓNDE SALE CADA NÚMERO: DE STRIPE, EN EL MOMENTO. El aviso del webhook solo
dice «mira esta factura» o «mira este cobro»; lo que se apunta se le pregunta
a Stripe al tratarlo, igual que el estado de las cuentas de cobro. Así un aviso
viejo, repetido o fuera de orden no puede apuntar otra cosa que la verdad de
hoy.

  · `devengar_de_la_factura` — `invoice.paid`. Cada PAGO de la factura que
    Stripe da por cobrado y que tiene un cobro de verdad detrás (`ch_…`/`py_…`)
    devenga una vez. Lo pagado fuera de Stripe o una factura de 0 € no.
  · `ajustar_el_cobro` — devoluciones y reclamaciones. Lo que ya no se debe es
    lo devuelto más lo reclamado sin ganar, según Stripe hoy; la base escribe
    solo la diferencia con lo ya corregido (`ajustar_comision`).

LA BASE ES LO QUE PAGA EL SALÓN SIN IMPUESTOS, en la moneda del cobro, y sin
descontar lo que nos cobra Stripe. Si la factura lleva impuestos, se quita su
parte en proporción a lo pagado. Las monedas no se mezclan ni se convierten.

QUIÉN Y CUÁNTO lo decide la base (`devengar_comision`): la cuenta sale del
cliente de Stripe, el referido de la atribución y el porcentaje del congelado.
Aquí no se calcula ningún porcentaje.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import stripe

from app.billing.stripe_objects import stripe_get as _g
from app.core import zona as zonas
from app.core.config import Settings
from app.db.postgrest_client import PostgrestClient

logger = logging.getLogger("estetix.referidos")

# Una reclamación con estos estados NO se ha llevado el dinero: ganada, una
# consulta que se cerró sin llegar a disputa, o evitada antes de cobrarse.
RECLAMACION_SIN_DINERO = frozenset({"won", "warning_closed", "prevented"})


def _stripe(settings: Settings) -> None:
    if not settings.stripe_secret_key:
        raise RuntimeError("Falta STRIPE_SECRET_KEY")
    stripe.api_key = settings.stripe_secret_key


def _id(valor) -> str | None:
    """Un campo «expandible» de Stripe: el id, venga expandido o no."""
    if valor is None or isinstance(valor, str):
        return valor
    return _g(valor, "id")


def _cuando(ts) -> str:
    return datetime.fromtimestamp(int(ts), timezone.utc).isoformat() if ts else datetime.now(timezone.utc).isoformat()


def base_sin_impuestos(factura, cobrado: int) -> int:
    """Lo cobrado en este pago, sin la parte de impuestos de la factura.

    Hoy nuestros precios no llevan impuesto (`routers/billing.py`) y la base es
    lo cobrado. Si un día la factura los lleva, se quita la parte que les toca
    a este pago, en proporción: `total_excluding_tax / total`."""
    total = int(_g(factura, "total") or 0)
    sin = _g(factura, "total_excluding_tax")
    if total <= 0 or sin is None or int(sin) >= total:
        return cobrado
    return min(cobrado, max(0, round(cobrado * int(sin) / total)))


def _cobro_del_pago(pago) -> tuple[str | None, str | None]:
    """(cobro, payment_intent) de un pago de factura. Sin cobro de Stripe
    —pagado fuera (`payment_record`)— no hay nada."""
    detalle = _g(pago, "payment") or {}
    tipo = _g(detalle, "type")
    if tipo == "charge":
        return _id(_g(detalle, "charge")), None
    if tipo == "payment_intent":
        pi = _g(detalle, "payment_intent")
        return _id(_g(pi, "latest_charge")) if not isinstance(pi, str) else None, _id(pi)
    return None, None


async def devengar_de_la_factura(db: PostgrestClient, settings: Settings, factura_id: str,
                                 evento_id: str | None) -> list[str]:
    """`invoice.paid`: apunta la comisión de cada pago cobrado de verdad.
    Devuelve lo que dijo la base por cada pago (para el registro y los tests)."""
    _stripe(settings)
    factura = await stripe.Invoice.retrieve_async(factura_id)
    if _g(factura, "status") != "paid":
        return ["no_pagada"]
    cliente = _id(_g(factura, "customer"))
    pagos = await stripe.InvoicePayment.list_async(
        invoice=factura_id, status="paid", limit=100, expand=["data.payment.payment_intent"]
    )
    dichos = []
    for pago in _g(pagos, "data") or []:
        cobro, intento = _cobro_del_pago(pago)
        cobrado = int(_g(pago, "amount_paid") or 0)
        if not cobro or cobrado <= 0:
            # Pagado fuera de Stripe, o sin dinero: no hay cobro real detrás.
            dichos.append("sin_cobro")
            continue
        filas = await db.rpc("devengar_comision", {
            "p_cliente_stripe": cliente,
            "p_factura": factura_id,
            "p_pago": _g(pago, "id"),
            "p_cobro": cobro,
            "p_payment_intent": intento,
            "p_moneda": _g(pago, "currency") or _g(factura, "currency"),
            "p_importe_cobrado": cobrado,
            "p_base": base_sin_impuestos(factura, cobrado),
            "p_pagado_at": _cuando(_g(_g(pago, "status_transitions") or {}, "paid_at")),
            "p_evento": evento_id,
        })
        dicho = (filas[0] or {}).get("resultado") if filas else None
        dichos.append(dicho or "?")
        if dicho in ("devengada", "ya_estaba"):
            # Si el cobro se devolvió o se reclamó antes de que llegara este
            # aviso, la corrección se escribe ya: no espera a otro aviso.
            await ajustar_el_cobro(db, settings, cobro, evento_id)
    return dichos


async def devengar_lo_cobrado(db: PostgrestClient, settings: Settings, cuenta_id: str,
                              evento: str) -> dict[str, int]:
    """LA REPESCA (0078) DEVENGA DESDE EL ALTA: los cobros que ya ocurrieron.

    NO ES OTRO CÁLCULO: es `devengar_de_la_factura` —lo mismo que hace el
    webhook con cada `invoice.paid`— aplicado a TODAS las facturas pagadas del
    cliente de Stripe de la cuenta, página a página, sin filtrar por fecha (el
    cliente nace con el alta: no hay facturas de antes). Con cada una se ajusta
    su cobro, así que una devolución que ya hubiera se corrige como hoy. Repetirlo
    no suma nada: la base no devenga dos veces el mismo cobro.

    Devuelve cuántos pagos dijo la base de cada cosa (devengada, ya_estaba…)."""
    filas = await db.select("accounts", {"select": "stripe_customer_id", "id": f"eq.{cuenta_id}", "limit": "1"})
    cliente = (filas[0] if filas else {}).get("stripe_customer_id")
    cuenta: dict[str, int] = {}
    if not cliente:
        return cuenta  # sin cliente de Stripe no ha pagado nunca nada
    _stripe(settings)
    despues_de = None
    while True:
        pagina = await stripe.Invoice.list_async(
            customer=cliente, status="paid", limit=100, **({"starting_after": despues_de} if despues_de else {})
        )
        facturas = _g(pagina, "data") or []
        for factura in facturas:
            for dicho in await devengar_de_la_factura(db, settings, _g(factura, "id"), evento):
                cuenta[dicho] = cuenta.get(dicho, 0) + 1
        if not _g(pagina, "has_more") or not facturas:
            return cuenta
        despues_de = _g(facturas[-1], "id")


async def ajustar_el_cobro(db: PostgrestClient, settings: Settings, cobro_id: str,
                           evento_id: str | None) -> str:
    """Devoluciones y reclamaciones: deja la comisión de este cobro como dice
    Stripe HOY. La fila contraria (o a favor) la escribe la base."""
    _stripe(settings)
    cobro = await stripe.Charge.retrieve_async(cobro_id)
    disputas = await stripe.Dispute.list_async(charge=cobro_id, limit=100)
    reclamado = sum(int(_g(d, "amount") or 0) for d in (_g(disputas, "data") or [])
                    if _g(d, "status") not in RECLAMACION_SIN_DINERO)
    filas = await db.rpc("ajustar_comision", {
        "p_cobro": cobro_id,
        "p_devuelto": int(_g(cobro, "amount_refunded") or 0),
        "p_reclamado": reclamado,
        "p_ocurrido_at": datetime.now(timezone.utc).isoformat(),
        "p_evento": evento_id,
    })
    return ((filas[0] or {}).get("resultado") if filas else None) or "?"


def cobro_del_evento(evento) -> str | None:
    """El cobro de un aviso de devolución o reclamación."""
    obj = _g(_g(evento, "data") or {}, "object") or {}
    tipo = _g(evento, "type") or ""
    if tipo.startswith("charge.dispute.") or tipo.startswith("refund."):
        return _id(_g(obj, "charge"))
    if tipo == "charge.refunded":
        return _g(obj, "id")
    return None


# ── Lo que se ve ────────────────────────────────────────────────────────────

_COLUMNAS = ("id,cuenta_id,cuenta_nombre,tipo,motivo,moneda,importe_cobrado_centimos,base_centimos,pct,"
             "comision_centimos,corrige_a,devuelto_centimos,reclamado_centimos,ocurrido_at,stripe_factura,"
             "stripe_cobro")


async def filas_del_referido(db: PostgrestClient, referido_id: str) -> list[dict]:
    return await db.select_todo("comisiones", {
        "select": _COLUMNAS, "referido_id": f"eq.{referido_id}", "order": "id.asc",
    })


def _mes(iso: str) -> str:
    """El mes en hora peninsular (la de la plataforma), como AAAA-MM."""
    dia = zonas.fecha_local(iso, zonas.zona(zonas.POR_DEFECTO))
    return f"{dia.year:04d}-{dia.month:02d}"


def resumen(filas: list[dict], *, con_stripe: bool = False, pct_hoy: dict[str, object] | None = None,
            pagos: list[dict] | None = None, cambios: list[dict] | None = None, con_motivo: bool = False) -> dict:
    """Devengado, corregido, neto, pagado y lo que se debe — POR MONEDA, sin
    mezclarlas —; el detalle por salón y por mes; y los movimientos, con las
    correcciones y su porqué, y los pagos.

    PAGADO ES LA SUMA DE LOS PAGOS (`pagos_que_cuentan`, 0076): su importe,
    salvo rechazado (0) o revertido (menos lo revertido); uno sin resolver
    cuenta entero. Ninguna fila de comisión cambia al pagar: lo que se debe es
    una resta.

    `con_stripe`: el panel lleva el cobro de Stripe de cada movimiento, para
    cuadrarlo con ellos. El portal del referido, no.

    `pct_hoy` (cuenta → %): en «por salón», el porcentaje con el que va HOY
    cada salón, no el de su último cobro; si no, tras un cambio se leería el %
    viejo justo debajo del aviso del nuevo.

    `cambios` (0077): cada cambio deliberado de % es un apunte más de los
    movimientos, en su fecha, entre las comisiones y los pagos. Su MOTIVO solo
    con `con_motivo` (el panel): el portal del referido nunca lo lleva."""
    por_moneda: dict[str, dict] = {}
    por_salon: dict[tuple[str, str], dict] = {}
    por_mes: dict[tuple[str, str], int] = {}
    for f in filas:
        m = f["moneda"]
        imp = int(f["comision_centimos"])
        t = por_moneda.setdefault(m, {"moneda": m, "devengado": 0, "corregido": 0, "pagado": 0})
        t["devengado" if f["tipo"] == "devengo" else "corregido"] += imp
        s = por_salon.setdefault((str(f["cuenta_id"]), m), {
            "salon": f["cuenta_nombre"], "moneda": m, "devengado": 0, "corregido": 0, "pct": f["pct"],
        })
        s["salon"] = f["cuenta_nombre"]
        s["devengado" if f["tipo"] == "devengo" else "corregido"] += imp
        clave = (_mes(f["ocurrido_at"]), m)
        por_mes[clave] = por_mes.get(clave, 0) + imp
    for p in pagos or []:
        t = por_moneda.setdefault(p["moneda"], {"moneda": p["moneda"], "devengado": 0, "corregido": 0, "pagado": 0})
        t["pagado"] += int(p["cuenta_centimos"])
    for t in por_moneda.values():
        t["neto"] = t["devengado"] + t["corregido"]
        t["debemos"] = t["neto"] - t["pagado"]
    for (cuenta, _m), s in por_salon.items():
        s["neto"] = s["devengado"] + s["corregido"]
        if pct_hoy and cuenta in pct_hoy:
            s["pct"] = pct_hoy[cuenta]
    devengos = {f["id"]: f for f in filas if f["tipo"] == "devengo"}
    movimientos = []
    for f in reversed(filas):
        origen = devengos.get(f["corrige_a"]) if f["tipo"] == "correccion" else None
        movimientos.append({
            "salon": f["cuenta_nombre"], "tipo": f["tipo"], "motivo": f["motivo"], "moneda": f["moneda"],
            "comision": int(f["comision_centimos"]), "pct": f["pct"], "ocurrido_at": f["ocurrido_at"],
            "cobrado": f["importe_cobrado_centimos"] if f["tipo"] == "devengo" else None,
            # De una corrección: a qué cobro corrige y lo que decía Stripe.
            "del_cobro_del": origen["ocurrido_at"] if origen else None,
            "devuelto": f["devuelto_centimos"], "reclamado": f["reclamado_centimos"],
            **({"stripe_cobro": f["stripe_cobro"], "stripe_factura": f["stripe_factura"]} if con_stripe else {}),
        })
    # Los pagos, entre los movimientos (los rechazados no: no salió nada).
    for p in pagos or []:
        if p["estado"] == "rechazado":
            continue
        movimientos.append({
            "salon": "", "tipo": "pago", "motivo": p["estado"], "moneda": p["moneda"],
            "comision": int(p["importe_centimos"]), "pct": None,
            "ocurrido_at": p.get("resuelto_at") or p["ordenado_at"],
            "cobrado": None, "del_cobro_del": None, "devuelto": None, "reclamado": None,
            "revertido": int(p["revertido_centimos"] or 0) or None,
            "periodo_desde": p["periodo_desde"], "periodo_hasta": p["periodo_hasta"],
            **({"stripe_cobro": p.get("stripe_transferencia") or "", "stripe_factura": None} if con_stripe else {}),
        })
    # Los cambios de porcentaje, como un apunte más (0077).
    for g in _grupos_de_cambio(cambios or []):
        movimientos.append({
            "salon": "", "tipo": "cambio", "motivo": "cambio_de_porcentaje", "moneda": "", "comision": 0,
            "pct": None, "ocurrido_at": g["el"], "cobrado": None, "del_cobro_del": None, "devuelto": None,
            "reclamado": None, "salones": g["salones"], "pct_antes": g["pct_antes"], "pct_despues": g["pct_despues"],
            **({"stripe_cobro": "", "stripe_factura": None} if con_stripe else {}),
            **({"por_que": g["motivo"]} if con_motivo else {}),
        })
    movimientos.sort(key=lambda m: str(m["ocurrido_at"]), reverse=True)
    return {
        "totales": sorted(por_moneda.values(), key=lambda t: t["moneda"]),
        "por_salon": sorted(por_salon.values(), key=lambda s: (s["salon"].lower(), s["moneda"])),
        "por_mes": [{"mes": k[0], "moneda": k[1], "neto": v} for k, v in sorted(por_mes.items(), reverse=True)],
        "movimientos": movimientos,
    }


# ── El porcentaje de cada salón y sus cambios ──────────────────────────────


async def salones_y_cambios(db: PostgrestClient, referido_id: str) -> tuple[list[dict], list[dict]]:
    """Los salones de un referido con el % con el que va HOY cada uno y de
    dónde sale (el de su alta, o el último cambio deliberado, con su motivo);
    y todos los cambios, del más nuevo al más viejo. Lo que la base usa para
    devengar es lo mismo (`porcentaje_del_salon`): el último cambio, o el alta."""
    # Solo los que cuentan, como el panel (`salones_de_un_referido`, 0075): un
    # alta deshecha deja su atribución y no es un salón de nadie.
    altas = await db.rpc("salones_de_un_referido", {"p_referido": referido_id})
    cambios = await db.select_todo("cambios_de_porcentaje", {
        "select": "id,lote,cuenta_id,cuenta_nombre,pct_antes,pct_despues,motivo,hecho_por_email,hecho_at",
        "referido_id": f"eq.{referido_id}", "order": "hecho_at.asc,id.asc",
    })
    ultimo = {str(c["cuenta_id"]): c for c in cambios}
    salones = []
    for a in altas:
        c = ultimo.get(str(a["cuenta_id"]))
        salones.append({
            "cuenta_id": str(a["cuenta_id"]), "salon": a["cuenta_nombre"], "llego_el": a["atribuida_el"],
            "pct_al_llegar": a["pct"], "pct_al_llegar_de": a["pct_de_donde"],
            "pct": c["pct_despues"] if c else a["pct"],
            "cambiado_el": c["hecho_at"] if c else None,
            "motivo": c["motivo"] if c else None,
            "cambiado_por": c["hecho_por_email"] if c else None,
        })
    return salones, list(reversed(cambios))


# Cuánto tiempo se queda arriba el aviso del último cambio (0077).
DIAS_DEL_AVISO = 30


def _grupos_de_cambio(cambios: list[dict]) -> list[dict]:
    """«Tu comisión en estos salones pasó del X % al Y % el día Z»: una línea
    por acción y por porcentaje de partida, en el orden en que llegan."""
    grupos: dict[tuple, dict] = {}
    for c in cambios:
        clave = (str(c["lote"]), str(c["pct_antes"]), str(c["pct_despues"]))
        g = grupos.setdefault(clave, {"salones": [], "pct_antes": c["pct_antes"], "pct_despues": c["pct_despues"],
                                      "el": c["hecho_at"], "motivo": c.get("motivo")})
        g["salones"].append(c["cuenta_nombre"])
    return list(grupos.values())


def avisos_de_cambio(cambios: list[dict], ahora: datetime | None = None) -> list[dict]:
    """Lo de arriba, encima de las cifras (0077): COMO MUCHO UNO, el más
    reciente, y solo si es de los últimos 30 días. Todos están además en los
    movimientos. `cambios` llega del más nuevo al más viejo. Sin el motivo."""
    grupos = _grupos_de_cambio(cambios)
    if not grupos:
        return []
    g = grupos[0]
    ahora = ahora or datetime.now(timezone.utc)
    el = datetime.fromisoformat(str(g["el"]).replace("Z", "+00:00"))
    if ahora - el > timedelta(days=DIAS_DEL_AVISO):
        return []
    return [{k: v for k, v in g.items() if k != "motivo"}]
