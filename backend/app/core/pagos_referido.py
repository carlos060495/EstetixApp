"""PAGAR A UN REFERIDO (0076). El dinero sale de verdad, así que cada paso
está pensado para que no salga dos veces.

06-10-2026:

  · PAGAR ES UN ACTO MANUAL. Solo lo dispara una persona —el owner— desde el
    panel, después de ver una confirmación (`propuesta`). Nada en el backend
    llama a `pagar` ni a `enviar` por su cuenta: ni un cron, ni un webhook, ni
    al llegar a una cifra (lo vigila `tests/test_pagos_referido.py`).
  · LA ORDEN SE ESCRIBE ANTES DE MOVER EL DINERO (`ordenar_pago`, en la base),
    y la transferencia lleva su id como clave de idempotencia y como
    `transfer_group`. Si algo se cae entre las dos cosas, la orden se queda
    «sin resolver», CUENTA COMO PAGADA (no se puede ordenar otra) y se resuelve
    con `comprobar`: se busca en Stripe la transferencia de ese grupo; si no
    está, se reintenta con la MISMA clave.
  · «¿SE LE PUEDE PAGAR?» SE LE PREGUNTA A STRIPE EN EL MOMENTO
    (`cobros_referido.se_le_puede_pagar`), nunca a nuestra copia. Y el saldo
    de nuestra cuenta, también a Stripe (`Balance`).
  · UNA MONEDA POR PAGO.

Las transferencias a una cuenta conectada son INMEDIATAS: o Stripe la crea, o
la rechaza en el momento (sin saldo, cuenta no lista…). No hay un «falló
después». Lo único que puede pasar después es que se REVIERTA, y eso llega por
el webhook de facturación (`transfer.reversed` → `apuntar_reversion`).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import stripe

from app.billing.stripe_objects import stripe_get as _g
from app.core import cobros_referido
from app.core.config import Settings
from app.db.postgrest_client import PostgrestClient

logger = logging.getLogger("estetix.referidos")

# Lo que la pantalla de confirmación dice de cada moneda.
SE_PUEDE = "se_puede"
NADA = "nada_que_pagar"
POR_DEBAJO = "por_debajo_del_minimo"
SIN_MINIMO = "sin_minimo"
SIN_CUENTA = "sin_cuenta_de_cobro"
NO_LE_DEJA = "stripe_no_le_deja_cobrar"
SIN_SALDO = "sin_saldo"
PENDIENTE = "hay_uno_pendiente"

# Una clave de idempotencia de Stripe vale 24 h. Pasado eso, antes de
# reintentar se busca la transferencia por su grupo (ver `comprobar`).
_VIDA_DE_LA_CLAVE = timedelta(hours=24)
# Una orden recién hecha puede seguir en camino: no se «comprueba» encima.
_DEJA_TERMINAR = timedelta(seconds=60)


def _stripe(settings: Settings) -> None:
    if not settings.stripe_secret_key:
        raise RuntimeError("Falta STRIPE_SECRET_KEY")
    stripe.api_key = settings.stripe_secret_key


def clave_de(pago_id: str) -> str:
    """La clave de idempotencia de la transferencia: el id de la orden."""
    return f"pago-referido-{pago_id}"


async def _saldo_disponible(settings: Settings, moneda: str) -> int:
    _stripe(settings)
    saldo = await stripe.Balance.retrieve_async()
    return sum(int(_g(x, "amount") or 0) for x in (_g(saldo, "available") or []) if _g(x, "currency") == moneda)


async def _deuda(db: PostgrestClient, referido_id: str, moneda: str, hasta: int | None = None) -> dict:
    filas = await db.rpc("deuda_con_referido", {"p_referido": referido_id, "p_moneda": moneda, "p_hasta": hasta})
    return filas[0] if filas else {"neto": 0, "pagado": 0, "debe": 0, "hasta_comision_id": None}


async def pagos(db: PostgrestClient, referido_id: str) -> list[dict]:
    return await db.rpc("pagos_que_cuentan", {"p_referido": referido_id})


async def propuesta(db: PostgrestClient, settings: Settings, referido_id: str) -> list[dict]:
    """La pantalla de confirmación, por moneda: cuánto se le debe, qué
    comisiones cubre y si se puede pagar AHORA (y si no, por qué). Lo de Stripe
    —si su cuenta puede cobrar, nuestro saldo— se pregunta en este momento."""
    filas = await db.select_todo("comisiones", {
        "select": "id,moneda,cuenta_nombre,tipo,motivo,comision_centimos,ocurrido_at",
        "referido_id": f"eq.{referido_id}", "order": "id.asc",
    })
    hechos = await pagos(db, referido_id)
    # El mínimo vive en la base, en un solo sitio (`minimos_de_pago`, 0076).
    minimos = {m["moneda"]: int(m["centimos"]) for m in await db.rpc("minimos_de_pago_vigentes", {}) if m}
    cuenta = await cobros_referido.cuenta_de(db, referido_id)
    puede: bool | None = None
    salida = []
    for moneda in sorted({f["moneda"] for f in filas}):
        deuda = await _deuda(db, referido_id, moneda)
        desde = max((int(p["hasta_comision_id"]) for p in hechos
                     if p["moneda"] == moneda and p["estado"] != "rechazado"), default=0)
        cubre = [f for f in filas if f["moneda"] == moneda and desde < int(f["id"]) <= int(deuda["hasta_comision_id"] or 0)]
        debe = int(deuda["debe"])
        estado = SE_PUEDE
        saldo = None
        si_se_salta = None
        if any(p["moneda"] == moneda and p["estado"] == "pendiente" for p in hechos):
            estado = PENDIENTE
        elif debe <= 0:
            estado = NADA
        elif moneda not in minimos:
            estado = SIN_MINIMO
        if estado == SE_PUEDE:
            # Lo que diría Stripe: si está por debajo del mínimo, también se
            # mira, para decir si saltárselo serviría de algo (0077).
            if not cuenta:
                estado = SIN_CUENTA
            else:
                if puede is None:
                    puede = await cobros_referido.se_le_puede_pagar(settings, cuenta)
                if not puede:
                    estado = NO_LE_DEJA
                else:
                    saldo = await _saldo_disponible(settings, moneda)
                    if saldo < debe:
                        estado = SIN_SALDO
            if debe < minimos[moneda]:
                estado, si_se_salta = POR_DEBAJO, estado
        salida.append({
            "moneda": moneda, "debe": debe, "neto": int(deuda["neto"]), "pagado": int(deuda["pagado"]),
            "hasta_comision_id": deuda["hasta_comision_id"], "minimo": minimos.get(moneda), "saldo": saldo,
            "estado": estado, "si_se_salta": si_se_salta, "cubre": cubre,
        })
    return salida


async def pagar(db: PostgrestClient, settings: Settings, referido_id: str, moneda: str, importe: int, hasta: int,
                admin_id: str, admin_email: str, saltar_minimo_motivo: str | None = None) -> dict:
    """Lo que hace el botón «Enviar» de la confirmación. En este orden:
    1. Stripe: ¿puede cobrar AHORA? ¿tenemos saldo? (si no, no se ordena nada);
    2. la ORDEN, en la base, antes de mover un céntimo (y solo si lo que se
       confirmó sigue siendo lo que se debe);
    3. la transferencia, con la orden como clave.

    `saltar_minimo_motivo` (0077): el botón de la confirmación APARTE de pagar
    por debajo del mínimo. El motivo queda escrito en la orden."""
    cuenta = await cobros_referido.cuenta_de(db, referido_id)
    if not cuenta:
        return {"resultado": SIN_CUENTA}
    if not await cobros_referido.se_le_puede_pagar(settings, cuenta):
        return {"resultado": NO_LE_DEJA}
    if await _saldo_disponible(settings, moneda) < importe:
        return {"resultado": SIN_SALDO}
    filas = await db.rpc("ordenar_pago_o_saltar_el_minimo", {
        "p_referido": referido_id, "p_moneda": moneda, "p_importe": importe, "p_hasta": hasta,
        "p_destino": cuenta, "p_admin": admin_id, "p_admin_email": admin_email,
        "p_saltar_minimo": saltar_minimo_motivo,
    })
    orden = filas[0] if filas else {}
    if orden.get("resultado") != "ordenado":
        return {"resultado": orden.get("resultado") or "?"}
    return await enviar(db, settings, str(orden["pago_id"]))


async def _la_orden(db: PostgrestClient, pago_id: str) -> dict | None:
    filas = await db.select("pagos_a_referidos", {
        "select": "id,referido_id,destino_stripe,moneda,importe_centimos,ordenado_at",
        "id": f"eq.{pago_id}", "limit": "1",
    })
    return filas[0] if filas else None


async def enviar(db: PostgrestClient, settings: Settings, pago_id: str) -> dict:
    """La transferencia de UNA orden que ya está escrita. Sin orden, nada.

    · Stripe la crea → `enviado`, con su `tr_…`.
    · Stripe la rechaza (una respuesta de Stripe que dice que no) → `rechazado`,
      con su mensaje: el importe deja de contar como pagado.
    · No se sabe (la red, un 5xx, un tiempo agotado) → NO se apunta nada: la
      orden sigue sin resolver, cuenta como pagada y se resuelve con
      `comprobar`. Apuntarla como rechazada sería invitar a pagarla otra vez."""
    orden = await _la_orden(db, pago_id)
    if orden is None:
        raise ValueError(f"No hay ninguna orden {pago_id}: sin orden no se mueve dinero.")
    _stripe(settings)
    try:
        transferencia = await stripe.Transfer.create_async(
            amount=int(orden["importe_centimos"]),
            currency=orden["moneda"],
            destination=orden["destino_stripe"],
            transfer_group=pago_id,
            metadata={"pago_id": pago_id, "referido_id": str(orden["referido_id"])},
            description="Comisiones de EstetixApp",
            idempotency_key=clave_de(pago_id),
        )
    except (stripe.APIConnectionError, stripe.RateLimitError) as e:
        logger.warning("pago %s: no se sabe si salió (%s); queda sin resolver", pago_id, type(e).__name__)
        return {"resultado": "sin_resolver", "pago_id": pago_id}
    except (stripe.InvalidRequestError, stripe.PermissionError) as e:
        # Stripe contestó, y dijo que NO: no se creó nada.
        await db.rpc("resolver_pago", {"p_pago": pago_id, "p_resultado": "rechazado", "p_transferencia": None,
                                       "p_detalle": (e.user_message or str(e))[:500], "p_evento": None})
        return {"resultado": "rechazado", "pago_id": pago_id, "motivo": e.code or "rechazado",
                "mensaje": e.user_message or str(e)}
    except stripe.StripeError as e:
        # Cualquier otra cosa (un 5xx de Stripe): no se sabe. Sin resolver.
        logger.warning("pago %s: Stripe no contestó claro (%s); queda sin resolver", pago_id, type(e).__name__)
        return {"resultado": "sin_resolver", "pago_id": pago_id}
    await db.rpc("resolver_pago", {"p_pago": pago_id, "p_resultado": "enviado",
                                   "p_transferencia": _g(transferencia, "id"), "p_detalle": None, "p_evento": None})
    return {"resultado": "enviado", "pago_id": pago_id, "transferencia": _g(transferencia, "id")}


async def comprobar(db: PostgrestClient, settings: Settings, pago_id: str, ahora: datetime | None = None) -> dict:
    """Una orden SIN RESOLVER (se cayó algo a mitad): ¿salió o no?

    1. Se busca en Stripe una transferencia de ese grupo (`transfer_group` =
       la orden). Si está, salió: `enviado`.
    2. Si no está, NO salió: se vuelve a intentar con la MISMA clave. Dentro de
       las 24 h de la clave, si la primera llamada llegó a Stripe, Stripe
       devuelve aquella misma transferencia y no crea otra."""
    orden = await _la_orden(db, pago_id)
    if orden is None:
        raise ValueError(f"No hay ninguna orden {pago_id}.")
    hechos = {p["pago_id"]: p for p in await pagos(db, str(orden["referido_id"]))}
    if hechos.get(pago_id, {}).get("estado") != "pendiente":
        return {"resultado": "ya_resuelto", "pago_id": pago_id}
    ahora = ahora or datetime.now(timezone.utc)
    ordenado = datetime.fromisoformat(str(orden["ordenado_at"]).replace("Z", "+00:00"))
    if ahora - ordenado < _DEJA_TERMINAR:
        return {"resultado": "todavia_en_camino", "pago_id": pago_id}
    _stripe(settings)
    encontradas = await stripe.Transfer.list_async(transfer_group=pago_id, limit=10)
    lista = _g(encontradas, "data") or []
    if lista:
        tr = lista[0]
        await db.rpc("resolver_pago", {"p_pago": pago_id, "p_resultado": "enviado", "p_transferencia": _g(tr, "id"),
                                       "p_detalle": "encontrada en Stripe al comprobar", "p_evento": None})
        return {"resultado": "enviado", "pago_id": pago_id, "transferencia": _g(tr, "id")}
    if ahora - ordenado > _VIDA_DE_LA_CLAVE:
        logger.info("pago %s: la clave ya caducó y no hay transferencia: se envía ahora", pago_id)
    return await enviar(db, settings, pago_id)


async def revertida(db: PostgrestClient, settings: Settings, transferencia_id: str, evento_id: str | None) -> str:
    """`transfer.reversed`: lo revertido EN TOTAL, preguntado a Stripe ahora."""
    _stripe(settings)
    tr = await stripe.Transfer.retrieve_async(transferencia_id)
    r = await db.rpc("apuntar_reversion", {"p_transferencia": transferencia_id,
                                           "p_total": int(_g(tr, "amount_reversed") or 0), "p_evento": evento_id})
    return str(r[0]) if r else "?"
