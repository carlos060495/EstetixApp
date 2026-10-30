"""Nuestra firma en Stripe, y de dónde vino un cambio.

═══ LO MEDIDO (28-09-2026, Stripe en modo prueba) ═══

Cada evento trae `request = {id, idempotency_key}`. Para el mismo cambio
(quitar la marca de baja) por tres caminos:

  · nuestra API con una clave propia  → `id: req_…`, la clave es la nuestra;
  · la API sin clave                  → `id: req_…`, una UUID que se inventa el SDK;
  · el portal de clientes, pulsado    → `id: null`,  una UUID.

O sea: lo que viene de una llamada a la API trae `id`, y lo que no (el portal,
o algo que Stripe hace solo) viene sin él. Y lo NUESTRO solo se reconoce si lo
firmamos: por eso toda escritura nuestra sobre una suscripción o su calendario
lleva una clave que empieza por `estetix-` (`clave_nuestra`). Lo vigila
`tests/test_la_voluntad_de_irse.py`, que recorre el código.

LO QUE NO SE HA PODIDO MEDIR: el panel de Stripe (hace falta una sesión en
él). Si trae `id`, cae en «otra llamada a la API»; si no, con el portal.
El texto de la alarma lo dice así, sin afinar más de lo medido.
"""

from __future__ import annotations

import uuid

PREFIJO = "estetix-"

Origen = str  # "nuestro" | "otra_api" | "fuera_de_la_api" | "desconocido"

DESCRIPCION = {
    "nuestro": "lo cambió nuestro propio código",
    "otra_api": "lo cambió una llamada a la API que no es nuestra (el panel de Stripe u otra integración)",
    "fuera_de_la_api": "lo cambió algo que no es una llamada a la API: el portal de clientes de Stripe, o Stripe por su cuenta",
    "desconocido": "no se sabe quién lo cambió: el evento que lo quitó no es este",
}


def clave_nuestra(operacion: str, base: str | None = None) -> str:
    """La clave de idempotencia de una escritura nuestra.

    Con `base` (la clave que manda el navegador), repetir la petición repite la
    clave y Stripe no hace nada dos veces. Sin ella, una nueva cada vez: no hay
    reintento que proteger, pero la escritura queda FIRMADA.
    """
    return f"{PREFIJO}{operacion}-{base or uuid.uuid4().hex}"


def origen_del_cambio(request) -> Origen:
    """De dónde vino el evento que hizo el cambio, por su `request`."""
    if not isinstance(request, dict):
        request = {
            "id": getattr(request, "id", None),
            "idempotency_key": getattr(request, "idempotency_key", None),
        } if request is not None else {}
    clave = request.get("idempotency_key") or ""
    if clave.startswith(PREFIJO):
        return "nuestro"
    if request.get("id"):
        return "otra_api"
    if request:
        return "fuera_de_la_api"
    return "desconocido"
