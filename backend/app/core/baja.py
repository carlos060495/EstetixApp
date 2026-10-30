"""Dar de baja: la gente se va, no se borra (migración 0062).

UN SOLO BOTÓN, Y DECIDE EL SERVIDOR (02-10-2026). Quien pulsa «Dar de
baja» no tiene que saber qué es una clave ajena:

  · si la persona NO tiene nada —ni fichajes, ni citas pasadas, ni ausencias,
    ni correcciones— la ficha se borra del todo: no hay nada que conservar;
  · si tiene algo, la ficha se queda con estado 'baja' y su historial se puede
    seguir abriendo.

En los dos casos sus citas POR VENIR se quedan sin profesional (después de que
quien administra las haya visto, como siempre).

EL ACCESO NO ENTRA AQUÍ. Solo la administradora tiene cuenta (0063, 0069): una
ficha o no está unida a nadie que entre en la app, o es la de la propia
administradora, y a ella no se la da de baja así (lo impiden este módulo y el
trigger de la 0062). Hasta la 0069 había un tercer caso —una empleada con
cuenta, a la que se le retiraba el acceso y se la baneaba en Auth— que ya no
puede existir.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from app.db.postgrest_client import PostgrestClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Historia:
    fichajes: int
    citas_pasadas: int
    ausencias: int
    correcciones: int

    @property
    def tiene_algo(self) -> bool:
        return bool(self.fichajes or self.citas_pasadas or self.ausencias or self.correcciones)


async def contar_historia(
    db: PostgrestClient, service: PostgrestClient, salon_id: str, employee_id: str, ahora: datetime
) -> Historia:
    """Lo que la base no dejaría borrar, contado.

    Las correcciones se leen con service_role (la sesión no tiene permiso sobre
    ese registro, 0061) y no tienen clave ajena: sin contarlas aquí, borrar a
    alguien sin fichajes pero con una corrección «nueva» la dejaría huérfana.
    """
    de_ella = {"employee_id": f"eq.{employee_id}"}
    return Historia(
        fichajes=await db.count("time_entries", {**de_ella, "salon_id": f"eq.{salon_id}"}),
        citas_pasadas=await db.count(
            "appointments", {**de_ella, "salon_id": f"eq.{salon_id}", "inicio": f"lt.{ahora.isoformat()}"}
        ),
        ausencias=await db.count("employee_schedule_exceptions", de_ella),
        correcciones=await service.count(
            "correcciones_de_fichaje", {**de_ella, "salon_id": f"eq.{salon_id}"}
        ),
    )


@dataclass(frozen=True)
class Acceso:
    """De quién es la ficha, en la app.

    «sin_usuario»: no está unida a nadie que entre en la app (todas, salvo una).
    «administradora»: es la de la dueña de la cuenta; no se le da de baja así.
    """

    que: str
    user_id: str | None = None


async def acceso_de_la_ficha(service: PostgrestClient, ficha: dict) -> Acceso:
    user_id = ficha.get("user_id")
    if not user_id:
        return Acceso("sin_usuario")
    filas = await service.select("users", {"select": "id", "id": f"eq.{user_id}", "limit": "1"})
    # Una ficha con usuario es la de la administradora: es el único que hay (0069).
    return Acceso("administradora", user_id) if filas else Acceso("sin_usuario")
