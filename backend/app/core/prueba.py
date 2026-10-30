"""LOS DÍAS DE PRUEBA, de su único sitio: `plataforma_config.dias_de_prueba` (0068).

Los usan la prueba que se abre en Stripe, el muro (`/auth/me`) y —en la base—
el reloj de quien nunca pagó (`relojes_del_plazo`). Hasta la 0068 venían de
`TRIAL_DEFAULT_DAYS`, una variable de Railway que ningún guarda podía ver: si
allí valía otra cosa, Stripe y el reloj decían fechas distintas. Ahora el dato
vive en un solo sitio y no se vigila desde lejos.

SIN VALOR DE RESERVA. Si la fila no está, LANZA: inventarse un 14 aquí sería
volver a tener dos fuentes.
"""

from __future__ import annotations

from app.db.postgrest_client import PostgrestClient


class SinDiasDePrueba(RuntimeError):
    """`plataforma_config` no tiene fila: la base está a medio migrar."""


async def dias_de_prueba(servicio: PostgrestClient) -> int:
    filas = await servicio.select("plataforma_config", {"select": "dias_de_prueba", "limit": "1"})
    if not filas or filas[0].get("dias_de_prueba") is None:
        raise SinDiasDePrueba("plataforma_config no tiene dias_de_prueba (¿falta la 0068?)")
    return int(filas[0]["dias_de_prueba"])
