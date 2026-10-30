"""EN QUÉ ZONA SE DICEN LAS FECHAS DE UNA CUENTA (panel de plataforma).

Una cuenta puede tener centros en zonas distintas, y sus fechas —fin de la
prueba, fin del periodo, próximo cobro, el rastro del dinero y de las
descargas— son de la cuenta, no de un centro. Se dicen en la zona del CENTRO
QUE SE QUEDA AL BAJAR DE PLAN (`centro_que_se_queda`): no es un concepto nuevo,
es el que ya significa «este es el centro de referencia de esta cuenta».
02-10-2026: «el panel tiene que ver lo que ve el salón».

`por` dice de dónde sale, y la pantalla lo enseña:

  · «marcado»: la titular lo marcó al bajar de plan (`salons.sigue_al_bajar`);
  · «mas_antiguo»: ninguno marcado, y la regla de siempre elige el más antiguo;
  · «sin_centro»: la cuenta no tiene ningún centro. NO SE ADIVINA: península,
    y la pantalla lo dice.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.billing.lo_que_incluye_el_plan import centro_que_se_queda
from app.core import zona as zonas

# Lo que hace falta de cada fila de `salons` para decidirlo.
COLUMNAS = "id,nombre_comercial,account_id,created_at,sigue_al_bajar,zona_horaria"


@dataclass(frozen=True)
class ZonaDeLaCuenta:
    zona_horaria: str
    centro_id: str | None
    centro: str | None
    por: Literal["marcado", "mas_antiguo", "sin_centro"]


def zona_de_la_cuenta(centros: list[dict]) -> ZonaDeLaCuenta:
    ref = centro_que_se_queda(centros)
    if ref is None:
        return ZonaDeLaCuenta(zonas.POR_DEFECTO, None, None, "sin_centro")
    # La zona se valida igual que en todas partes: una desconocida es un error.
    return ZonaDeLaCuenta(
        zona_horaria=zonas.zona(ref.get("zona_horaria") or zonas.POR_DEFECTO).key,
        centro_id=str(ref["id"]),
        centro=ref.get("nombre_comercial"),
        por="marcado" if ref.get("sigue_al_bajar") else "mas_antiguo",
    )


def por_cuenta(salones: list[dict]) -> dict[str, ZonaDeLaCuenta]:
    """Todas las cuentas de una lista de `salons` (con `COLUMNAS`)."""
    agrupados: dict[str, list[dict]] = {}
    for s in salones:
        if s.get("account_id"):
            agrupados.setdefault(str(s["account_id"]), []).append(s)
    return {cuenta: zona_de_la_cuenta(filas) for cuenta, filas in agrupados.items()}


def de(cuentas: dict[str, ZonaDeLaCuenta], account_id: str | None) -> ZonaDeLaCuenta:
    """La de una cuenta; si la cuenta no tiene centros en la lista, «sin_centro»."""
    return cuentas.get(str(account_id)) or zona_de_la_cuenta([])
