"""Qué citas YA DADAS deja fuera un cambio. Punto único para las tres puertas.

Tres cambios distintos llevan al mismo problema —una clienta con cita a una hora
en la que ya no hay nadie— y los tres pasan por aquí, con el mismo cálculo y el
mismo protocolo:

  · el horario de una ficha y sus ausencias o cambios (0045);
  · poner a alguien en pausa;
  · el horario del salón y sus festivos.

══ EL CÁLCULO ══

Un `Marco` es todo lo que decide si una cita cabe: el horario del salón, sus
festivos, el horario propio de cada ficha y quién está en pausa. Se construye el
marco de AHORA y el de DESPUÉS del cambio, y se pregunta cita a cita.

QUEDA AFECTADA LA CITA QUE HOY CABE Y DESPUÉS NO. No la que ya estaba fuera: una
cita antigua que quedó fuera de horario hace meses no es culpa de este cambio, y
enseñarla aquí sería ruido que acostumbra a pulsar «guardar» sin leer.

Solo citas POR VENIR y que ocupan hora (ni canceladas ni no presentadas): una
cita de ayer no se puede atender tarde, y una cancelada no espera a nadie.

══ EL PROTOCOLO: NADA SE GUARDA SIN HABER VISTO LO QUE DEJA FUERA ══

  1. Se intenta guardar sin `citas_vistas`. Si el cambio deja citas fuera, NO SE
     GUARDA: 409 con la lista (`codigo = citas_afectadas`).
  2. La pantalla la enseña. Si quien administra sigue, reenvía el mismo cambio
     con los ids que ha visto.
  3. Se RECALCULA en el servidor. Si ahora hay alguna que no estaba en lo que
     vio —entró una por WhatsApp mientras decidía—, otra vez 409 con la lista
     nueva. Solo si todo lo afectado está entre lo visto, se guarda.

Eso cierra la rendija entre «te enseño el impacto» y «guardo». Queda la de
dentro de la propia petición —los milisegundos entre este cálculo y la
escritura—: una cita que entrara justo ahí no se habría enseñado. No la cierra
la base porque la restricción de `appointments` no conoce horarios; si pasa, la
cita queda en la agenda como cualquier otra fuera de horario.

══ LAS CITAS NO SE TOCAN ══

Ni se borran ni se mueven. Siguen donde estaban hasta que alguien les cambie la
hora o la profesional. Se bloquea el cambio hasta que se ha visto; nunca el
dato que ya estaba.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

from zoneinfo import ZoneInfo

from app.core import zona as zonas
from app.core.citas import ESTADOS_QUE_NO_OCUPAN
from app.core.horario_activo import cargar_excepciones, cargar_horario, dentro_del_horario
from app.core.horario_empleada import (
    SIN_HORARIO_PROPIO,
    HorarioPropio,
    cabe_en_su_horario,
    cargar_horarios_propios,
)
from app.db.postgrest_client import PostgrestClient
from app.schemas.excepciones import ExcepcionOut
from app.schemas.horario import HorarioSemana

CODIGO = "citas_afectadas"

_SELECT_CITA = "id,client_id,employee_id,service_id,inicio,fin,estado,canal,nombre_reserva"


# POR QUÉ SE QUEDAN SIN ATENDER. El protocolo es uno; lo que cambia es qué le
# pasa a la cita, y la pantalla lo dice distinto:
#
#   · FUERA_DE_HORARIO — la cita sigue con su profesional, a una hora en la que
#     ya no se atiende (horarios, festivos, pausa).
#   · SIN_PROFESIONAL — la cita pierde a su profesional (borrar una ficha:
#     `appointments.employee_id` queda a null).
FUERA_DE_HORARIO = "fuera_de_horario"
SIN_PROFESIONAL = "sin_profesional"
EN_PAUSA = "en_pausa"


class CitasAfectadas(Exception):
    """El cambio deja citas fuera y no están todas entre las vistas.

    No es una `HTTPException` a propósito, igual que `CitaNoValida`: la regla no
    sabe de FastAPI. El router la traduce con `a_http`.
    """

    def __init__(self, citas: list[dict], habia_vistas: bool, motivo: str = FUERA_DE_HORARIO):
        self.citas = citas
        self.habia_vistas = habia_vistas
        self.motivo = motivo
        super().__init__(f"{len(citas)} citas afectadas")


@dataclass(frozen=True)
class Marco:
    """Todo lo que decide si una cita cabe."""

    semana_salon: HorarioSemana
    excepciones_salon: tuple[ExcepcionOut, ...]
    # La zona del centro (0060): con ella se decide si una cita cae en su horario.
    zona: ZoneInfo
    propios: dict[str, HorarioPropio] = field(default_factory=dict)
    en_pausa: frozenset[str] = frozenset()

    def cabe(self, cita: dict) -> bool:
        inicio = datetime.fromisoformat(str(cita["inicio"]))
        fin = datetime.fromisoformat(str(cita["fin"]))
        if not dentro_del_horario(inicio, fin, self.semana_salon, list(self.excepciones_salon), z=self.zona):
            return False
        ficha = cita.get("employee_id")
        if ficha:
            if ficha in self.en_pausa:
                return False
            if not cabe_en_su_horario(inicio, fin, self.propios.get(ficha, SIN_HORARIO_PROPIO), z=self.zona):
                return False
        return True

    # Los cambios que se simulan. Devuelven un marco nuevo: el de ahora no se toca.

    def con_semana_del_salon(self, semana: HorarioSemana) -> Marco:
        return replace(self, semana_salon=semana)

    def con_excepciones_del_salon(self, excepciones: Iterable[ExcepcionOut]) -> Marco:
        return replace(self, excepciones_salon=tuple(excepciones))

    def con_horario_propio(self, ficha: str, propio: HorarioPropio) -> Marco:
        return replace(self, propios={**self.propios, ficha: propio})

    def con_pausa(self, ficha: str) -> Marco:
        return replace(self, en_pausa=self.en_pausa | {ficha})


async def cargar_marco(
    db: PostgrestClient, salon_id: str, fichas: Iterable[str] | None = None
) -> Marco:
    """El marco de AHORA para un salón. `fichas=None` = todas las del salón."""
    if fichas is None:
        filas = await db.select("employees", {"select": "id,estado", "salon_id": f"eq.{salon_id}"})
    else:
        ids = sorted(set(fichas))
        filas = (
            await db.select(
                "employees",
                {"select": "id,estado", "salon_id": f"eq.{salon_id}", "id": f"in.({','.join(ids)})"},
            )
            if ids
            else []
        )
    return Marco(
        semana_salon=await cargar_horario(db, salon_id),
        excepciones_salon=tuple(await cargar_excepciones(db, salon_id)),
        zona=await zonas.zona_del_centro(db, salon_id),
        propios=await cargar_horarios_propios(db, [f["id"] for f in filas]),
        # Quien está de baja tampoco atiende: para una cita es lo mismo.
        en_pausa=frozenset(f["id"] for f in filas if f.get("estado") in ("pausa", "baja")),
    )


async def citas_por_venir(
    db: PostgrestClient,
    salon_id: str,
    *,
    employee_id: str | None = None,
    ahora: datetime | None = None,
) -> list[dict]:
    """Las citas de este salón que aún no han empezado y ocupan hora."""
    ahora = ahora or datetime.now(timezone.utc)
    params = {
        "select": _SELECT_CITA,
        "salon_id": f"eq.{salon_id}",
        "inicio": f"gte.{ahora.isoformat()}",
        "estado": f"not.in.({','.join(ESTADOS_QUE_NO_OCUPAN)})",
        "order": "inicio.asc,id.asc",
    }
    if employee_id:
        params["employee_id"] = f"eq.{employee_id}"
    # TODAS: son todas las citas futuras de un centro, sin techo de fecha.
    return await db.select_todo("appointments", params)


def que_quedan_fuera(citas: Iterable[dict], antes: Marco, despues: Marco) -> list[dict]:
    """Las que hoy caben y con el cambio no. Función PURA."""
    return [c for c in citas if antes.cabe(c) and not despues.cabe(c)]


async def exigir_que_se_hayan_visto(
    db: PostgrestClient,
    afectadas: list[dict],
    vistas: Iterable[str] | None,
    motivo: str = FUERA_DE_HORARIO,
) -> None:
    """Deja seguir solo si todo lo afectado está entre lo visto. Si no, lanza."""
    if not afectadas:
        return
    vistas = set(vistas or ())
    if all(str(c["id"]) in vistas for c in afectadas):
        return
    raise CitasAfectadas(await describir(db, afectadas), habia_vistas=bool(vistas), motivo=motivo)


def motivo_para_resolver(cita: dict, marco: Marco) -> str | None:
    """¿Necesita esta cita que alguien haga algo? Y por qué. Función PURA.

    Es la otra cara del aviso: el aviso bloquea el cambio hasta que se han visto
    las citas, y esto es lo que permite ENCONTRARLAS después, que es lo que
    promete «guardar y ocuparme de ellas».

      · SIN_PROFESIONAL — no tiene a nadie (se borró su ficha, o entró así).
      · EN_PAUSA — su profesional está en pausa: la agenda no le pinta columna.
      · FUERA_DE_HORARIO — fuera del horario del salón o del de su profesional.

    `None`: la cita está bien donde está.
    """
    ficha = cita.get("employee_id")
    if not ficha:
        return SIN_PROFESIONAL
    if ficha in marco.en_pausa:
        return EN_PAUSA
    if not marco.cabe(cita):
        return FUERA_DE_HORARIO
    return None


async def describir(db: PostgrestClient, citas: list[dict]) -> list[dict]:
    """Lo que la pantalla necesita para que alguien reconozca cada cita.

    Tres consultas planas y no un embed: son pocas filas, y así el nombre que se
    enseña es el de la ficha de la clienta, la del servicio y la de la
    profesional sin depender de cómo resuelva cada relación PostgREST.
    """

    async def nombres(tabla: str, ids: set[str]) -> dict[str, str]:
        ids = {i for i in ids if i}
        if not ids:
            return {}
        filas = await db.select(tabla, {"select": "id,nombre", "id": f"in.({','.join(sorted(ids))})"})
        return {str(f["id"]): f.get("nombre") or "" for f in filas}

    # LA CLIENTA, POR LA LECTURA DE LA AGENDA (0086) y no por su ficha: con la
    # cuenta cortada la ficha da 403, y aquí solo hace falta el nombre.
    ids_clientas = sorted({str(c["client_id"]) for c in citas if c.get("client_id")})
    clientas = {
        str(f["id"]): f.get("nombre") or ""
        for f in (await db.rpc("clientas_de_la_agenda", {"p_ids": ids_clientas}) if ids_clientas else [])
    }
    servicios = await nombres("services", {str(c.get("service_id") or "") for c in citas})
    fichas = await nombres("employees", {str(c.get("employee_id") or "") for c in citas})

    return [
        {
            "id": str(c["id"]),
            "inicio": str(c["inicio"]),
            "fin": str(c["fin"]),
            "estado": c.get("estado"),
            "canal": c.get("canal"),
            # `nombre_reserva` va aparte y no fundido con la ficha: una reserva
            # hecha para otra persona no es de la titular de la ficha, y la
            # pantalla decide cómo decirlo con `lib/nombre-cita`.
            "nombre_reserva": c.get("nombre_reserva"),
            "clienta": clientas.get(str(c.get("client_id") or "")),
            "servicio": servicios.get(str(c.get("service_id") or "")),
            "profesional": fichas.get(str(c.get("employee_id") or "")),
            "employee_id": str(c["employee_id"]) if c.get("employee_id") else None,
        }
        for c in citas
    ]


def mensaje(e: CitasAfectadas) -> str:
    n = len(e.citas)
    cuantas = "1 cita" if n == 1 else f"{n} citas"
    if e.habia_vistas:
        return (
            f"Mientras lo revisabas han cambiado las citas afectadas: ahora son {cuantas}. "
            "Revísalas antes de seguir."
        )
    if e.motivo == SIN_PROFESIONAL:
        verbo = "se queda" if n == 1 else "se quedan"
        return (
            f"Si la das de baja, {cuantas} {verbo} sin profesional. No se borran ni se mueven: "
            "revísalas y confirma para darla de baja."
        )
    return (
        f"Este cambio deja {cuantas} fuera de horario. No se borran ni se mueven: "
        "revísalas y confirma para guardar."
    )


def a_http(e: CitasAfectadas):
    """409 con la lista. Import tardío para que la regla no dependa de FastAPI."""
    from fastapi import HTTPException, status

    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"codigo": CODIGO, "motivo": e.motivo, "mensaje": mensaje(e), "citas": e.citas},
    )
