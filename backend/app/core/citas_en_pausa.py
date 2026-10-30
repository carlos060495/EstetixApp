"""Las citas que ya estaban cogidas en un centro que el plan ha dejado en pausa.

EL PROBLEMA QUE RESUELVE, y es el peor de toda la fase: si no se puede entrar al
centro pausado, tampoco se puede ver su agenda. La clienta que tiene cita el día
22 se presenta y no la espera nadie. La pausa puede quitar funciones; lo que no
puede es esconder un compromiso que el salón ya adquirió.

De ahí las tres formas en que esto sale a la superficie, que son tres porque una
sola no basta:

  · el LISTADO en Ajustes › Centros, para poder verlas y llamar una por una;
  · un AVISO en el panel de Suscripción, porque a Ajustes › Centros no entra
    nadie salvo para configurar, y si la titular no abre esa pestaña en dos
    semanas la clienta sigue plantada;
  · un CORREO cuando la pausa aterriza, que es el único que llega sola.

══ POR QUÉ NO SE REUTILIZA `impacto.py` ══

A primera vista es el mismo problema, pero no lo es. `impacto`
contesta «¿qué citas caben HOY y con este cambio ya no?», y para eso construye
un `Marco` —horario del salón, festivos, horario de cada ficha, quién está en
pausa— y las pregunta una a una. Ese marco no tiene el concepto de «el centro
entero deja de atender»: las citas del centro pausado siguen cayendo dentro del
horario de su salón y con su profesional disponible, así que `que_quedan_fuera`
devolvería CERO. Reutilizarlo daría una respuesta vacía y creíble.

Lo que sí se reutiliza, y es la mitad del fichero: `citas_por_venir` —que ya
define exactamente qué cuenta como «cita que espera a alguien»: por venir, y ni
cancelada ni no presentada— y `describir`, que es lo que la pantalla necesita
para que alguien reconozca una cita. Repetir esos dos criterios aquí sería
abrirlos a separarse.

══ EL TOPE DE MIL FILAS ══

`select` de PostgREST devuelve como mucho `db-max-rows` (mil) y no avisa de que
ha recortado, así que `len(filas)` es un número que parece exacto y no lo es.
Ya mordió una vez en el aviso de impacto. Aquí el total y la última fecha salen
de `count()` y de una consulta ordenada al revés —las dos exactas pase lo que
pase— y solo la lista que se PINTA lleva tope, que es donde un tope se nota poco
y se dice.

══ CON QUÉ CREDENCIALES ══

Con el token del inquilino, no con `service_role`. La pausa es una frontera
COMERCIAL: esos centros siguen siendo suyos, `salones_visibles()` sigue
devolviéndolos y sus datos los puede leer. Que no pueda trabajar en ellos no
significa que no pueda saber lo que tiene pendiente.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app.core.citas import ESTADOS_QUE_NO_OCUPAN
from app.core.impacto import citas_por_venir, describir
from app.db.postgrest_client import PostgrestClient

# Cuántas se pintan. Con más que esto la lista deja de ayudar y lo que hace
# falta es el teléfono de la clienta, no otra pantalla de scroll.
TOPE_QUE_SE_PINTA = 50


@dataclass(frozen=True)
class CitasPendientes:
    """Lo que hay pendiente en los centros en pausa de una cuenta."""

    total: int
    # La MÁS LEJANA: es la que dice hasta cuándo dura el problema, y es el dato
    # que hace que alguien actúe («hasta el 14 de octubre» se entiende; «tienes
    # citas pendientes» no).
    ultima: str | None
    # Las más PRÓXIMAS, ya descritas. Se ordenan por lo que viene antes porque
    # es el orden en el que hay que llamar.
    citas: list[dict]

    @property
    def hay(self) -> bool:
        return self.total > 0


VACIO = CitasPendientes(total=0, ultima=None, citas=[])


async def pendientes_en_los_centros_en_pausa(
    db: PostgrestClient,
    centros_en_pausa: tuple[str, ...] | list[str],
    *,
    ahora: datetime | None = None,
) -> CitasPendientes:
    """Cuántas, hasta cuándo, y las más próximas para poder llamarlas."""
    centros = [str(c) for c in centros_en_pausa if c]
    if not centros:
        return VACIO

    ahora = ahora or datetime.now(timezone.utc)
    filtro = {
        "salon_id": f"in.({','.join(centros)})",
        "inicio": f"gte.{ahora.isoformat()}",
        # EL MISMO CRITERIO QUE `impacto`, importado y no copiado: una cita
        # cancelada no espera a nadie.
        "estado": f"not.in.({','.join(ESTADOS_QUE_NO_OCUPAN)})",
    }

    total = await db.count("appointments", filtro)
    if not total:
        return VACIO

    # La última, con una consulta que pide UNA fila: así es exacta aunque haya
    # diez mil, y no depende de que quepan todas.
    ultimas = await db.select(
        "appointments", {**filtro, "select": "inicio", "order": "inicio.desc", "limit": "1"}
    )
    ultima = str(ultimas[0]["inicio"]) if ultimas else None

    proximas = await db.select(
        "appointments",
        {
            **filtro,
            "select": "id,client_id,employee_id,service_id,inicio,fin,estado,canal,nombre_reserva",
            "order": "inicio.asc",
            "limit": str(TOPE_QUE_SE_PINTA),
        },
    )
    return CitasPendientes(total=total, ultima=ultima, citas=await describir(db, proximas))


# Se reexporta para que quien lea este módulo vea de dónde sale el criterio de
# «cita que espera a alguien» sin tener que buscarlo.
__all__ = [
    "CitasPendientes",
    "TOPE_QUE_SE_PINTA",
    "VACIO",
    "citas_por_venir",
    "pendientes_en_los_centros_en_pausa",
]
