"""LAS CORRECCIONES DE FICHAJE, ENCIMA DE LO QUE MARCÓ EL RELOJ (0061).

`time_entries` es lo que se fichó y no se toca nunca (la base rechaza el
UPDATE). Una corrección es una fila de `correcciones_de_fichaje`, que tampoco se
toca, y cambia UNA marca: la añade ('nueva'), le pone otra hora ('cambia') o la
anula ('anula'). Este módulo junta las dos cosas —`aplicar`— y dice qué
correcciones serían imposibles —`problemas` y `nuevos_problemas`—.

Todo es puro: recibe filas y devuelve marcas. Lo que lee y escribe está en
`routers/employees.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.core import zona as zonas

# La misma que el historial (`routers/employees.py`): más de 16 horas no es una
# jornada. Se repite aquí y no se importa para no atar un módulo puro al router;
# un test comprueba que son iguales.
SESION_MAXIMA = timedelta(hours=16)


@dataclass
class Marca:
    """Una marca tal y como cuenta AHORA, con su historia."""

    id: str
    tipo: str
    ts: datetime  # la hora que cuenta
    ts_reloj: datetime | None  # la que marcó el reloj; None si la marca es 'nueva'
    anulada: bool = False
    correcciones: list[dict] = field(default_factory=list)

    @property
    def nueva(self) -> bool:
        return self.ts_reloj is None

    @property
    def corregida(self) -> bool:
        return bool(self.correcciones)


def _orden(fila: dict) -> tuple:
    return (str(fila.get("ocurrido_at") or ""), int(fila.get("id") or 0))


def aplicar(fichajes: list[dict], correcciones: list[dict]) -> list[Marca]:
    """Las marcas del reloj con las correcciones aplicadas EN EL ORDEN EN QUE SE
    HICIERON. Las anuladas siguen en la lista —con `anulada`— para que el
    historial las enseñe; quien suma las salta. Ordenadas por la hora que cuenta."""
    marcas: dict[str, Marca] = {}
    for f in fichajes:
        ts = zonas.instante_de(f["ts"])
        marcas[str(f["id"])] = Marca(id=str(f["id"]), tipo=f["tipo"], ts=ts, ts_reloj=ts)
    for c in sorted(correcciones, key=_orden):
        mid = str(c["marca_id"])
        if c["accion"] == "nueva":
            marcas[mid] = Marca(id=mid, tipo=c["tipo"], ts=zonas.instante_de(c["ts_despues"]), ts_reloj=None)
        m = marcas.get(mid)
        if m is None:
            # Una corrección de una marca que no se ha leído (fuera de la
            # ventana): no se puede aplicar a nada que se vaya a enseñar.
            continue
        if c["accion"] == "cambia":
            m.ts = zonas.instante_de(c["ts_despues"])
        elif c["accion"] == "anula":
            m.anulada = True
        m.correcciones.append(c)
    return sorted(marcas.values(), key=lambda m: (m.ts, m.id))


def como_filas(marcas: list[Marca]) -> list[dict]:
    """Lo que cuenta, en la forma que espera el historial: sin las anuladas."""
    return [{"id": m.id, "tipo": m.tipo, "ts": m.ts.isoformat()} for m in marcas if not m.anulada]


# ═══ LAS CORRECCIONES IMPOSIBLES ═══


@dataclass(frozen=True)
class Problema:
    clave: str  # 'salida_antes_de_entrada', 'marca_suelta', 'solape', 'demasiado_larga'
    marca_id: str
    mensaje: str


def problemas(marcas: list[Marca], z: ZoneInfo) -> list[Problema]:
    """Lo que no puede ser en una secuencia de marcas, recorriéndola como el
    quiosco: fuera → entrada → (pausa → reanudación)* → salida → fuera.

      · una salida sin una entrada antes (si la entrada viene después ese mismo
        día, es una salida ANTERIOR a la entrada);
      · una pausa o una reanudación sin sesión;
      · una entrada, ese mismo día, mientras otra sesión sigue abierta: dos
        sesiones que se SOLAPAN (si la abierta es de otro día o de hace más de
        `SESION_MAXIMA`, se dejó sin cerrar, y eso no es solape: como en el
        historial);
      · una sesión de más de `SESION_MAXIMA`: una jornada de 30 horas no existe.
    """
    activas = [m for m in marcas if not m.anulada]
    out: list[Problema] = []
    abierta: Marca | None = None

    def hh(ts: datetime) -> str:
        return zonas.hhmm(ts, z)

    for i, m in enumerate(activas):
        if abierta is not None and m.tipo == "entrada" and (
            m.ts - abierta.ts > SESION_MAXIMA
            or zonas.fecha_local(m.ts, z) != zonas.fecha_local(abierta.ts, z)
        ):
            # La anterior se quedó sin cerrar —es de otro día, o de hace más de
            # `SESION_MAXIMA`—: no es solape. La misma regla que el historial.
            abierta = None
        if m.tipo == "entrada":
            if abierta is not None:
                out.append(Problema(
                    "solape", m.id,
                    f"La entrada de las {hh(m.ts)} cae dentro de la sesión que empezó a las "
                    f"{hh(abierta.ts)} y todavía no tiene salida: dos sesiones no pueden solaparse.",
                ))
            abierta = m
        elif m.tipo == "salida":
            if abierta is None:
                despues = next((x for x in activas[i + 1:] if x.tipo == "entrada"
                                and zonas.fecha_local(x.ts, z) == zonas.fecha_local(m.ts, z)), None)
                if despues is not None:
                    out.append(Problema(
                        "salida_antes_de_entrada", m.id,
                        f"La salida de las {hh(m.ts)} es anterior a la entrada de las {hh(despues.ts)}.",
                    ))
                else:
                    out.append(Problema("marca_suelta", m.id,
                                        f"Una salida a las {hh(m.ts)} sin ninguna entrada antes."))
                continue
            duracion = m.ts - abierta.ts
            if duracion > SESION_MAXIMA:
                horas = int(duracion.total_seconds() // 3600)
                out.append(Problema(
                    "demasiado_larga", m.id,
                    f"Una sesión de {horas} horas (entrada {hh(abierta.ts)}, salida {hh(m.ts)} "
                    f"del {zonas.dia_dd_mm(zonas.fecha_local(m.ts, z))}): más de "
                    f"{int(SESION_MAXIMA.total_seconds() // 3600)} horas seguidas no es una jornada.",
                ))
            abierta = None
        elif abierta is None:
            out.append(Problema("marca_suelta", m.id,
                                f"Una {'pausa' if m.tipo == 'pausa' else 'reanudación'} a las {hh(m.ts)} sin sesión."))
    return out


def nuevos_problemas(antes: list[Marca], despues: list[Marca], z: ZoneInfo) -> list[Problema]:
    """Los problemas que trae la corrección: los que hay después y no había
    antes. Lo que ya estaba mal en el día no impide arreglar otra cosa; lo que
    la corrección estropea, sí."""
    ya = {(p.clave, p.marca_id) for p in problemas(antes, z)}
    return [p for p in problemas(despues, z) if (p.clave, p.marca_id) not in ya]
