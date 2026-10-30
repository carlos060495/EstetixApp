"""EL BORRADO A 90 DÍAS, EN LA CADENA DE TAREAS (0089).

El procedimiento es el de la cabecera de la 0068, el mismo orden y por el mismo
motivo: lo de fuera ANTES, porque al borrar en la base desaparecen los
identificadores con los que se limpia el exterior.

  1. La pasada (`proxima_pasada_de_borrado`): las más antiguas, hasta el tope
     de la fila del interruptor, con sus clientes de Stripe y sus logos. APAGADO
     SE NIEGA AQUÍ, antes de tocar nada de fuera: no es un fallo, es lo previsto.
  2. Por cada cuenta, Stripe (borrar el cliente; «ya no existe» vale) y Storage
     (borrar cada logo; un 404 vale). Si algo de eso falla, la cuenta no se
     prepara y espera a la siguiente pasada: borrarla en la base perdería el id
     con el que limpiar Stripe.
  3. La función, con las preparadas. Cada cuenta va en su bloque: la que falla
     vuelve entera y dice por qué; las demás se borran.

Toda la decisión de qué se borra es de la base. Aquí solo se limpia lo de fuera
y se la llama.
"""

from __future__ import annotations

import asyncio
import logging

import stripe

from app.core.config import Settings, get_settings
from app.core.security import get_service_postgrest_client
from app.core import stripe_con_plazo  # noqa: F401 — pone el plazo de Stripe a todo el SDK
from app.db.postgrest_client import PostgrestClient, PostgrestError
from app.db.storage_client import StorageClient, StorageError

logger = logging.getLogger("estetix.borrado_a_90_dias")

BUCKET_LOGOS = "salon-assets"


async def _borrar_cliente(clave: str, cliente: str) -> None:
    await stripe.Customer.delete_async(cliente, api_key=clave)


async def _limpiar_stripe(settings: Settings, clientes: list[str]) -> None:
    if not clientes:
        return
    if not settings.stripe_secret_key:
        raise RuntimeError("sin clave de Stripe: no se puede borrar su cliente")
    for cliente in clientes:
        try:
            await _borrar_cliente(settings.stripe_secret_key, cliente)
        except stripe.InvalidRequestError as e:
            if getattr(e, "code", None) != "resource_missing":  # ya no existe: es lo que se quería
                raise


async def _limpiar_storage(almacen: StorageClient, logos: list[str]) -> None:
    for ruta in logos:
        await almacen.delete(BUCKET_LOGOS, ruta)  # un 404 ya lo da por bueno


async def ejecutar(
    settings: Settings | None = None,
    servicio: PostgrestClient | None = None,
    almacen: StorageClient | None = None,
) -> dict:
    settings = settings or get_settings()
    servicio = servicio or get_service_postgrest_client(settings)
    almacen = almacen or StorageClient(settings.supabase_url, settings.supabase_secret_key)
    try:
        pasada = await servicio.rpc("proxima_pasada_de_borrado", {})
    except PostgrestError as e:
        if "55000" in str(e) or "APAGADO" in str(e):
            logger.info("Borrado a 90 días: apagado; no se ha borrado nada.")
            return {"estado": "apagado"}
        logger.exception("Borrado a 90 días: no se pudo leer la pasada.")
        return {"estado": "error"}
    if not pasada:
        return {"estado": "hecho", "borradas": 0, "con_error": 0, "quedan": 0}

    preparadas: list[str] = []
    errores: dict[str, str] = {}
    for c in pasada:
        cuenta = str(c["account_id"])
        try:
            await _limpiar_stripe(settings, c.get("clientes_stripe") or [])
            await _limpiar_storage(almacen, c.get("logos") or [])
        except (stripe.StripeError, StorageError, RuntimeError) as e:
            errores[cuenta] = f"fuera: {type(e).__name__}"
            logger.warning("Borrado a 90 días: la cuenta %s no se prepara (%s); espera a la siguiente.",
                           cuenta, type(e).__name__)
            continue
        preparadas.append(cuenta)

    borradas = 0
    if preparadas:
        try:
            filas = await servicio.rpc("borrar_cuentas_fuera_de_plazo", {"p_preparadas": preparadas})
        except PostgrestError:
            logger.exception("Borrado a 90 días: la función falló; no se ha borrado nada en la base.")
            return {"estado": "error", "preparadas": len(preparadas)}
        for f in filas:
            if f.get("error"):
                errores[str(f["account_id"])] = f["error"]
                logger.warning("Borrado a 90 días: la cuenta %s no se borró: %s", f["account_id"], f["error"])
            else:
                borradas += 1
    resumen = {
        "estado": "hecho",
        "borradas": borradas,
        "con_error": len(errores),
        "quedan": int(pasada[0].get("fuera_de_plazo") or 0) - borradas,
    }
    logger.info("Borrado a 90 días: %s", resumen)
    return resumen


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    print(f"borrado_a_90_dias: {asyncio.run(ejecutar())}")


if __name__ == "__main__":
    main()
