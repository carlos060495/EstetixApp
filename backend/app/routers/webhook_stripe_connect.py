"""El webhook de Stripe CONNECT (0072): lo que Stripe dice de las cuentas de
cobro de los referidos. Eventos de Accounts v2.

NO ES EL DE LA FACTURACIÓN (`/webhooks/stripe`). Con Accounts v2 los avisos de
las cuentas conectadas NO llegan por «cuentas conectadas»: son eventos v2 de la
PROPIA plataforma («Tu cuenta»), «thin» (un aviso con el identificador de la
cuenta, sin la cuenta dentro). Se reciben en un destino propio, con SU PROPIO
secreto (`STRIPE_CONNECT_WEBHOOK_SECRET`), y se verifican con
`StripeClient.parse_event_notification`, no con `Webhook.construct_event`.

Los que se escuchan (los tres que se dan de alta en el destino):

  · v2.core.account[configuration.recipient].capability_status_updated
    — cambia `stripe_transfers`: activa, pendiente, restringida…
  · v2.core.account[requirements].updated — le pide (o le aprueba) un papel.
  · v2.core.account.closed — la cuenta se cierra.

Cualquier otro tipo se da por bueno (200) sin tocar nada.

Como el aviso no trae la cuenta, se le pide a Stripe ENTERA, en ese momento, y
se guarda la copia con la hora de la pregunta: una copia más vieja no pisa a
una más nueva. Por eso no hace falta apuntar los avisos vistos: repetir uno no
cambia nada. Nada decide con esta copia si se le puede pagar
(`core/cobros_referido.py`).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import stripe
from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse, Response

from app.core import cobros_referido, publico
from app.core.config import Settings, get_settings
from app.core.sesion_referido import servicio
from app.db.postgrest_client import PostgrestError
from app.core.stripe_con_plazo import cliente_stripe

logger = logging.getLogger("estetix.referidos")

router = APIRouter(tags=["webhooks"])

EVENTOS = frozenset({
    "v2.core.account[configuration.recipient].capability_status_updated",
    "v2.core.account[requirements].updated",
    "v2.core.account.closed",
})


@router.post("/webhooks/stripe-connect", dependencies=[publico.WEBHOOK])
async def stripe_connect_webhook(request: Request, settings: Settings = Depends(get_settings)) -> Response:
    if not settings.stripe_connect_webhook_secret or not settings.stripe_secret_key:
        return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            content={"detail": "Webhook de Connect no configurado."})
    payload = await request.body()
    try:
        # Con el cliente compartido: plazo, y sin montar una conexión nueva en cada aviso.
        aviso = cliente_stripe(settings.stripe_secret_key).parse_event_notification(
            payload, request.headers.get("stripe-signature") or "", settings.stripe_connect_webhook_secret
        )
    except (ValueError, KeyError, stripe.SignatureVerificationError):
        return JSONResponse(status_code=400, content={"detail": "Firma inválida"})

    relacionado = getattr(aviso, "related_object", None)
    if aviso.type not in EVENTOS or relacionado is None or relacionado.type != "v2.core.account":
        # No nos toca: se da por bueno para que Stripe no lo reintente.
        return Response(status_code=200)

    cuenta_id = relacionado.id
    db = servicio(settings)
    try:
        # Una cuenta que no es de ningún referido no se le pide a Stripe.
        if not await cobros_referido.es_de_un_referido(db, cuenta_id):
            return Response(status_code=200)
        dicho_el = datetime.now(timezone.utc)
        cuenta = await cobros_referido.leer_cuenta(settings, cuenta_id)
        await cobros_referido.guardar_copia(db, cuenta_id, cuenta, dicho_el)
    except (PostgrestError, stripe.StripeError):
        # No se ha guardado: que Stripe lo reintente.
        logger.exception("referidos: no se pudo guardar la copia del webhook de Connect")
        return JSONResponse(status_code=502, content={"detail": "No se pudo guardar."})
    return Response(status_code=200)
