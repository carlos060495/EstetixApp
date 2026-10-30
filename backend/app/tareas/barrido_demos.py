"""EL BARRIDO DE LOS SALONES DE MUESTRA (0080): borra las demos caducadas.

Toda la decisión es de la base (`barrer_demos`): qué está caducado, que sea una
demo, el tope por pasada y la constancia. Aquí solo se la llama.

NACE APAGADO. Mientras `barrido_demos_interruptor` esté apagado, la base se
niega (55000) y esta tarea lo apunta y sigue: no es un fallo, es lo previsto.
Corre en la misma ejecución que los recordatorios (`tareas/recordatorios.main`),
para no depender de otro cron en Railway.
"""

from __future__ import annotations

import asyncio
import logging

from app.core.config import Settings, get_settings
from app.core.security import get_service_postgrest_client
from app.db.postgrest_client import PostgrestClient, PostgrestError

logger = logging.getLogger("estetix.barrido_demos")


async def ejecutar(settings: Settings | None = None, servicio: PostgrestClient | None = None) -> dict:
    settings = settings or get_settings()
    servicio = servicio or get_service_postgrest_client(settings)
    try:
        filas = await servicio.rpc("barrer_demos", {})
    except PostgrestError as e:
        if "55000" in str(e) or "APAGADO" in str(e):
            logger.info("Barrido de demos: apagado; no se ha borrado nada.")
            return {"estado": "apagado"}
        logger.exception("Barrido de demos: la pasada falló.")
        return {"estado": "error"}
    fila = filas[0] if filas else {}
    resumen = {"estado": "hecho", "borradas": fila.get("borradas", 0), "quedan": fila.get("quedan", 0)}
    logger.info("Barrido de demos: %s", resumen)
    return resumen


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    print(f"barrido_demos: {asyncio.run(ejecutar())}")


if __name__ == "__main__":
    main()
