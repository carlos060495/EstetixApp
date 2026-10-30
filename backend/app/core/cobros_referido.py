"""CÓMO COBRA UN REFERIDO: su cuenta de Stripe Connect (0072), en Accounts v2.

05-10-2026: el referido le da sus datos bancarios y de identidad a
STRIPE, no a nosotros. Aquí no hay ni un dato bancario: ni el IBAN, ni sus
últimos dígitos guardados (se le piden a Stripe al pintar el portal).

ACCOUNTS V2, Y SOLO V2. La primera versión creaba la cuenta con
`POST /v1/accounts` y en producción Stripe la rechazó: «Stripe no longer
recommends Accounts v1 for new Connect integrations. Create connected accounts
with POST /v2/core/accounts instead». No se activa v1: aquí no
queda ni un camino v1 para crear, enlazar o decidir. (La única llamada v1 es de
LECTURA y está explicada en `ultimos_digitos`.)

LA CUENTA. Con la configuración `recipient` («destinatario»): recibe lo que le
mandemos y nada más. Ni `merchant` (cobrar a sus propios clientes) ni
`customer`. De `recipient` se pide UNA capacidad,
`stripe_balance.stripe_transfers` —que le lleguen nuestras transferencias a su
saldo de Stripe—, la equivalente a la `transfers` de v1. Los pagos de ese saldo
a su banco los gestiona él desde su panel ligero de Stripe
(`dashboard: "express"`). Comisiones y pérdidas, de la plataforma
(`defaults.responsibilities`), como en el «Express» de siempre.

EL PAÍS va en `identity.country` al crear la cuenta, y SOLO uno de la lista de
`core/paises_cobro.py`: quien sea de fuera se para aquí (`PaisNoAdmitido`),
antes de que exista nada en Stripe.

QUIÉN DECIDE SI SE LE PUEDE PAGAR: STRIPE. Lo que se sabe de la cuenta se
guarda como COPIA, con la fecha en que se le preguntó a Stripe
(`guardar_copia`), y solo sirve para ENSEÑAR en qué punto está
(`estado_para_mostrar`). La decisión, `se_le_puede_pagar`, se le pregunta a
Stripe en el momento, y mira SOLO el estado de `stripe_transfers` (ni
`payouts_enabled` ni `charges_enabled`, que son de v1 y no dicen si se le puede
transferir). Lo vigila `tests/test_cobros_referido.py`.

EL IDENTIFICADOR DE LA CUENTA (`acct_…`, la misma forma que en v1) NO SALE POR
NINGUNA RUTA: solo lo usa este módulo para hablar con Stripe.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import stripe

from app.billing.stripe_objects import stripe_get as _g
from app.core.stripe_con_plazo import cliente_stripe
from app.core.config import Settings
from app.core.paises_cobro import se_puede_cobrar_en
from app.db.postgrest_client import PostgrestClient

logger = logging.getLogger("estetix.referidos")

SIN_CONECTAR = "sin_conectar"
PENDIENTE = "pendiente"
LISTA = "lista"

_TABLA = "referido_cobros"

# EL SITIO WEB QUE STRIPE PIDE EN EL ALTA (06-10-2026). Stripe pide «un
# sitio web que muestre los productos o servicios que vendes o promocionas», y
# un vendedor particular no tiene ninguno; la descripción que ofrece en su
# lugar, cada uno se la inventaba y Stripe rechaza las vagas. Lo que promociona
# un referido es EstetixApp: es la respuesta verdadera. Fijo y público —no
# `frontend_base_url`, que en local es `localhost` y Stripe no lo aceptaría—.
SITIO_QUE_PROMOCIONA = "https://www.estetixapp.es"

# Lo que se le pide a Stripe de la cuenta para la copia: sin `include`, v2
# devuelve la cuenta casi vacía.
_INCLUIR = ["configuration.recipient", "requirements"]


class PaisNoAdmitido(ValueError):
    """El país no está en `core/paises_cobro.py`: no se crea nada en Stripe."""


def _cliente(settings: Settings) -> stripe.StripeClient:
    if not settings.stripe_secret_key:
        raise RuntimeError("Falta STRIPE_SECRET_KEY")
    return cliente_stripe(settings.stripe_secret_key)


def _transferencias(cuenta):
    """`configuration.recipient.capabilities.stripe_balance.stripe_transfers`."""
    ruta = cuenta
    for clave in ("configuration", "recipient", "capabilities", "stripe_balance", "stripe_transfers"):
        ruta = _g(ruta, clave) if ruta is not None else None
    return ruta


# ── La cuenta y el enlace ───────────────────────────────────────────────────


async def cuenta_de(db: PostgrestClient, referido_id: str) -> str | None:
    filas = await db.select(_TABLA, {"select": "cuenta_stripe", "referido_id": f"eq.{referido_id}", "limit": "1"})
    return filas[0]["cuenta_stripe"] if filas else None


async def es_de_un_referido(db: PostgrestClient, cuenta_id: str) -> bool:
    filas = await db.select(_TABLA, {"select": "referido_id", "cuenta_stripe": f"eq.{cuenta_id}", "limit": "1"})
    return bool(filas)


async def crear_cuenta(
    db: PostgrestClient, settings: Settings, referido_id: str, email: str, nombre: str, pais: str
) -> str:
    """Crea su cuenta de Connect la primera vez. Con clave de idempotencia por
    referido: un doble clic o dos pestañas no crean dos cuentas en Stripe."""
    if not se_puede_cobrar_en(pais):
        raise PaisNoAdmitido(pais)
    cliente = _cliente(settings)
    cuenta = await cliente.v2.core.accounts.create_async(
        {
            "contact_email": email,
            "display_name": nombre,
            "dashboard": "express",
            "identity": {"country": pais.upper()},
            "configuration": {
                "recipient": {"capabilities": {"stripe_balance": {"stripe_transfers": {"requested": True}}}}
            },
            "defaults": {
                "responsibilities": {"fees_collector": "application", "losses_collector": "application"},
                # «Tu sitio web»: lo que promociona el referido es EstetixApp.
                "profile": {"business_url": SITIO_QUE_PROMOCIONA},
            },
            "metadata": {"referido_id": referido_id},
            "include": _INCLUIR,
        },
        {"idempotency_key": f"referido-cuenta-{referido_id}"},
    )
    cuenta_id = _g(cuenta, "id")
    # La base tiene la última palabra: `referido_id` es la clave primaria. Si
    # otra petición la escribió entre medias, la de Stripe es la misma cuenta
    # (misma clave de idempotencia) y no pasa nada.
    try:
        await db.insert(_TABLA, {"referido_id": referido_id, "cuenta_stripe": cuenta_id})
    except Exception:  # noqa: BLE001 - se relee abajo; si no está, que suba
        existente = await cuenta_de(db, referido_id)
        if existente is None:
            raise
        return existente
    await guardar_copia(db, cuenta_id, cuenta, datetime.now(timezone.utc))
    return cuenta_id


async def enlace_de_alta(settings: Settings, cuenta_id: str) -> str:
    """Un enlace NUEVO a la pantalla de Stripe. Caduca en minutos y es de un
    solo uso: si caduca o se recarga, Stripe manda al referido a `refresh_url`,
    y su portal pide otro (`?stripe=otra`). Volver a entrar a medias es pedir
    otro enlace para la MISMA cuenta: Stripe sigue donde lo dejó.

    `eventually_due` + `future_requirements: include`: le pide de una vez todo
    lo que Stripe acabará pidiendo, y no a plazos, para que más adelante no le
    paren los cobros por un papel."""
    cliente = _cliente(settings)
    base = settings.frontend_base_url.rstrip("/")
    enlace = await cliente.v2.core.account_links.create_async({
        "account": cuenta_id,
        "use_case": {
            "type": "account_onboarding",
            "account_onboarding": {
                "configurations": ["recipient"],
                "refresh_url": f"{base}/referidos/portal?stripe=otra",
                "return_url": f"{base}/referidos/portal?stripe=vuelta",
                "collection_options": {"fields": "eventually_due", "future_requirements": "include"},
            },
        },
    })
    return _g(enlace, "url")


async def leer_cuenta(settings: Settings, cuenta_id: str):
    """La cuenta tal como la tiene Stripe AHORA (la usan `sincronizar` y el
    webhook, que solo trae el aviso, no la cuenta)."""
    return await _cliente(settings).v2.core.accounts.retrieve_async(cuenta_id, {"include": _INCLUIR})


# ── La copia (para enseñar) ─────────────────────────────────────────────────


def copia_de_la_cuenta(cuenta) -> dict:
    """Lo que se guarda de lo que dice Stripe. Ni un dato bancario.

    - `copia_transferencias`: el estado de `stripe_transfers` (active, pending,
      restricted, unsupported).
    - `copia_pausado_por`: el porqué de Stripe cuando no está activa
      (`status_details[].code`: requirements_past_due, unsupported_country…).
    - `copia_pide`: lo que le toca mandar A ÉL (`awaiting_action_from: user`);
      lo que está revisando Stripe no es algo que pueda hacer el referido.
    - `copia_fecha_limite`: la primera fecha en que algo pendiente le afectará.
    """
    transferencias = _transferencias(cuenta)
    detalles = list(_g(transferencias, "status_details") or []) if transferencias is not None else []
    requisitos = _g(cuenta, "requirements") or {}
    entradas = list(_g(requisitos, "entries") or [])
    pide = list(dict.fromkeys(
        _g(e, "description") for e in entradas
        if _g(e, "awaiting_action_from") == "user" and _g(e, "description")
    ))
    limite = _g(_g(_g(requisitos, "summary") or {}, "minimum_deadline") or {}, "time")
    return {
        "copia_transferencias": _g(transferencias, "status") if transferencias is not None else None,
        "copia_pide": pide,
        "copia_pausado_por": _g(detalles[0], "code") if detalles else None,
        "copia_fecha_limite": limite or None,
    }


async def guardar_copia(db: PostgrestClient, cuenta_id: str, cuenta, dicho_el: datetime) -> bool:
    """Escribe la copia SOLO si es más nueva que la que hay: dos lecturas a la
    vez (el webhook y la vuelta de Stripe) no pueden pisar una nueva con una
    vieja."""
    cambios = copia_de_la_cuenta(cuenta) | {
        "copia_de": dicho_el.isoformat(),
        "copia_recibida_at": datetime.now(timezone.utc).isoformat(),
    }
    filas = await db.update(
        _TABLA,
        {"cuenta_stripe": f"eq.{cuenta_id}", "or": f"(copia_de.is.null,copia_de.lte.{dicho_el.isoformat()})"},
        cambios,
    )
    return bool(filas)


def estado_para_mostrar(fila: dict | None) -> dict:
    """En qué punto está, PARA ENSEÑARLO. Sale de la copia, que puede estar
    vieja: nunca decide nada (ver `se_le_puede_pagar`)."""
    if not fila:
        return {"estado": SIN_CONECTAR, "pausado": False, "fecha_limite": None, "copia_de": None}
    pide = list(fila.get("copia_pide") or [])
    activa = fila.get("copia_transferencias") == "active"
    return {
        "estado": LISTA if activa and not pide else PENDIENTE,
        # Stripe ha parado (o aún no ha abierto) lo que le mandemos.
        "pausado": bool(fila.get("copia_pausado_por")) or not activa,
        "fecha_limite": fila.get("copia_fecha_limite"),
        "copia_de": fila.get("copia_de"),
    }


_COLUMNAS_COPIA = "copia_transferencias,copia_pide,copia_pausado_por,copia_fecha_limite,copia_de"


async def estado_del_referido(db: PostgrestClient, referido_id: str) -> dict:
    filas = await db.select(_TABLA, {
        "select": _COLUMNAS_COPIA, "referido_id": f"eq.{referido_id}", "limit": "1",
    })
    return estado_para_mostrar(filas[0] if filas else None)


async def estados_de_todos(db: PostgrestClient) -> dict[str, str]:
    """Para el panel: el estado de cada referido, sin su cuenta de Stripe."""
    # Todas, pasen o no de mil (`select` se quedaría en mil sin avisar).
    filas = await db.select_todo(_TABLA, {"select": f"referido_id,{_COLUMNAS_COPIA}", "order": "referido_id.asc"})
    return {str(f["referido_id"]): estado_para_mostrar(f)["estado"] for f in filas}


async def ultimos_digitos(settings: Settings, cuenta_id: str) -> str | None:
    """«Cobras en la cuenta terminada en ••34»: se le pregunta a Stripe al
    pintarlo y NO se guarda. Si Stripe no contesta, se pinta sin dígitos.

    Es la ÚNICA llamada v1, y es de lectura: la cuenta v2 no trae la cuenta
    bancaria a la que Stripe le paga, y la lectura v1 de la misma cuenta
    (`acct_…`, mismo identificador) sí la trae, en `external_accounts`."""
    try:
        cuenta = await _cliente(settings).v1.accounts.retrieve_async(cuenta_id)
    except stripe.StripeError:
        logger.exception("referidos: Stripe no dio la cuenta para los últimos dígitos")
        return None
    externas = _g(_g(cuenta, "external_accounts") or {}, "data") or []
    return _g(externas[0], "last4") if externas else None


async def sincronizar(db: PostgrestClient, settings: Settings, cuenta_id: str) -> None:
    """Al volver de Stripe el webhook puede no haber llegado todavía: se le
    pregunta a Stripe y se guarda la copia, con la hora de la pregunta."""
    dicho_el = datetime.now(timezone.utc)
    cuenta = await leer_cuenta(settings, cuenta_id)
    await guardar_copia(db, cuenta_id, cuenta, dicho_el)


# ── La decisión (se le pregunta a Stripe) ───────────────────────────────────


async def se_le_puede_pagar(settings: Settings, cuenta_stripe: str) -> bool:
    """¿Se le puede mandar dinero AHORA? Lo dice Stripe, en el momento:
    `stripe_transfers` activa. Ni una columna nuestra: la copia puede estar
    vieja. La usan los pagos."""
    cuenta = await _cliente(settings).v2.core.accounts.retrieve_async(
        cuenta_stripe, {"include": ["configuration.recipient"]}
    )
    return _g(_transferencias(cuenta), "status") == "active"
