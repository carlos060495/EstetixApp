"""LAS ALTAS QUE NADIE CONFIRMÓ CADUCAN (0087) y su correo queda libre.

Toda la decisión es de la base (`caducar_altas_sin_confirmar`): qué es un alta
a medias, el plazo, el tope por pasada, que no se toque una cuenta con trabajo
dentro y la constancia. Aquí solo se la llama. Corre en la misma ejecución que
los recordatorios (`tareas/recordatorios.main`), para no depender de otro cron.
"""

from __future__ import annotations

import asyncio
import logging

from app.core.config import Settings, get_settings
from app.core.security import get_service_postgrest_client
from app.db.postgrest_client import PostgrestClient, PostgrestError

logger = logging.getLogger("estetix.altas_sin_confirmar")


async def ejecutar(settings: Settings | None = None, servicio: PostgrestClient | None = None) -> dict:
    settings = settings or get_settings()
    servicio = servicio or get_service_postgrest_client(settings)
    try:
        filas = await servicio.rpc("caducar_altas_sin_confirmar", {})
    except PostgrestError:
        logger.exception("Altas sin confirmar: la pasada falló.")
        return {"estado": "error"}
    fila = filas[0] if filas else {}
    resumen = {"estado": "hecho", "caducadas": fila.get("caducadas", 0), "saltadas": fila.get("saltadas", 0),
               "quedan": fila.get("quedan", 0)}
    logger.info("Altas sin confirmar: %s", resumen)
    return resumen


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    print(f"altas_sin_confirmar: {asyncio.run(ejecutar())}")


if __name__ == "__main__":
    main()
