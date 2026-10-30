"""El horario propio de una ficha: su semana y sus ausencias o cambios (0045).

LOS USA LA PESTAÑA «JORNADA» DE LA FICHA.

QUIÉN: leer, cualquiera del centro (es lo que hace la RLS de la tabla, y la
empleada va a ver la agenda completa); escribir, solo admin.

DOS REGLAS AL ESCRIBIR, las dos antes de tocar la base:

  1. QUE NO SE CRUCE CON SU OTRA FICHA. Una persona con ficha en dos centros no
     puede trabajar a la vez en los dos. Se compara con lo que cada centro deja
     de verdad: su horario propio dentro del horario de ESE salón.
  2. QUE NO DEJE CITAS FUERA SIN HABERLAS VISTO (`core/impacto.py`).

LO QUE LA REGLA 1 NO MIRA, dicho para que no se suponga: la semana nueva de una
ficha contra las ausencias o cambios FUTUROS de la otra. Se compara semana con
semana, y cada ausencia o cambio nuevo con lo que la otra ficha tiene esas
fechas. Y si la otra ficha no tiene horario propio no hay cruce que detectar:
allí se la sigue ofreciendo todo el día (ver `core/horario_empleada.py`, «la
equivalencia a medias»).
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Body, Depends, HTTPException, status

from app.core import publico
from app.core import zona as zonas
from app.core.errores_visibles import visible
from app.core.horario_activo import (
    Tramo,
    _tramos_de_horas,
    cargar_excepciones,
    cargar_horario,
    interseccion,
    tramos_activos,
)
from app.core.horario_empleada import (
    TABLA_EXCEPCIONES,
    TABLA_SEMANA,
    HorarioPropio,
    cargar_horarios_propios,
    tramos_propios,
)
from app.core.impacto import (
    CitasAfectadas,
    a_http,
    cargar_marco,
    citas_por_venir,
    exigir_que_se_hayan_visto,
    que_quedan_fuera,
)
from app.core.security import CurrentUser, get_current_user, get_postgrest_client
from app.db.postgrest_client import PostgrestClient, PostgrestError
from app.schemas.excepciones import ExcepcionOut
from app.schemas.horario import DiaHorario, HorarioSemana
from app.schemas.impacto import (
    ConCitasVistas,
    ExcepcionCreateIn,
    HorarioEmpleadaIn,
    HorarioEmpleadaOut,
    OtraFichaOut,
    TramoHoras,
)

router = APIRouter(prefix="/employees/{employee_id}/horario", tags=["horario"], dependencies=[publico.ADMINISTRADORA])

_CODIGO_SOLAPE = "23P01"
_NOMBRE_DIA = {1: "lunes", 2: "martes", 3: "miércoles", 4: "jueves", 5: "viernes", 6: "sábado", 7: "domingo"}
# Hasta dónde se comparan día a día unas vacaciones con la otra ficha. Un año
# sobra para cualquier ausencia real y acota el trabajo de una fecha mal puesta.
_MAX_DIAS_COMPARADOS = 400


# ─────────────────────────────────────────────────────────────── ayudas


async def _ficha(db: PostgrestClient, salon_id: str, employee_id: str) -> dict:
    """La ficha, SOLO si es de este centro. El filtro de salón va además de la
    RLS: dos cerraduras, ninguna confiando en la otra."""
    filas = await db.select(
        "employees",
        {
            "select": "id,nombre,salon_id,persona_id,estado",
            "id": f"eq.{employee_id}",
            "salon_id": f"eq.{salon_id}",
            "limit": "1",
        },
    )
    if not filas:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Empleado no encontrado")
    return filas[0]


def _tramos_del_dia_semana(dia: DiaHorario | None) -> list[Tramo] | None:
    if dia is None:
        return None
    if dia.cerrado:
        return []
    return _tramos_de_horas(dia.apertura, dia.cierre, dia.tiene_pausa, dia.pausa_inicio, dia.pausa_fin)


def _dia_de(semana: HorarioSemana, n: int) -> DiaHorario | None:
    return next((d for d in semana.dias if d.dia_semana == n), None)


def _horas(tramos: list[Tramo]) -> str:
    return " y ".join(f"{zonas.reloj(t.inicio)} a {zonas.reloj(t.fin)}" for t in tramos)


async def _otras_fichas(db: PostgrestClient, ficha: dict) -> list[dict]:
    """Las otras fichas de la misma persona, con el nombre de su centro."""
    persona = ficha.get("persona_id") or ficha["id"]
    filas = await db.select(
        "employees",
        {"select": "id,nombre,salon_id,persona_id", "or": f"(id.eq.{persona},persona_id.eq.{persona})"},
    )
    otras = [f for f in filas if f["id"] != ficha["id"]]
    if not otras:
        return []
    salones = await db.select(
        "salons",
        {
            "select": "id,nombre_comercial",
            "id": f"in.({','.join(sorted({o['salon_id'] for o in otras}))})",
        },
    )
    nombres = {s["id"]: s.get("nombre_comercial") or "otro centro" for s in salones}
    return [{**o, "centro": nombres.get(o["salon_id"], "otro centro")} for o in otras]


def _cruce(no_puede: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"codigo": "se_cruza_con_otro_centro", "mensaje": no_puede},
    )


# EN EL INSTANTE REAL, NO EN LA HORA DE PARED (19-10-2026). Dos centros de la
# misma persona pueden estar en zonas distintas (Madrid y Canarias): las 10:00
# de uno son las 09:00 del otro. Cada tramo se pasa a instante con la zona de SU
# centro y se comparan instantes; y como la hora de diferencia puede mover un
# tramo al día de al lado, cada día se compara con el anterior, el mismo y el
# siguiente del otro centro. El cambio de hora lo resuelve la zona de cada uno.
# La semana se recorre en fechas de verdad durante un año: así cae dentro
# cualquier cambio de hora de cualquiera de los dos países.
_DIAS_DE_LA_SEMANA_COMPARADOS = 371


def _instantes(dia, tramos: list[Tramo], z) -> list[tuple]:
    return [(zonas.a_utc(zonas.instante_local(dia, t.inicio, z)), zonas.a_utc(zonas.instante_local(dia, t.fin, z)))
            for t in tramos]


def _se_cruzan(a: list[tuple], b: list[tuple]) -> bool:
    return any(x0 < y1 and y0 < x1 for x0, x1 in a for y0, y1 in b)


def _cruce_con(mias: list[tuple], dia, suyas_del, z_otra) -> list[Tramo] | None:
    """Los tramos del otro centro que se cruzan con `mias` (instantes del día `dia`)."""
    for d in (dia - timedelta(days=1), dia, dia + timedelta(days=1)):
        suyas = suyas_del(d)
        if suyas and _se_cruzan(mias, _instantes(d, suyas, z_otra)):
            return suyas
    return None


def _de_alli(z_mia, z_otra, otra: dict) -> str:
    return "" if z_mia.key == z_otra.key else f" (hora de {otra['centro']})"


async def _sin_cruce_semanal(db: PostgrestClient, ficha: dict, semana: HorarioSemana) -> None:
    """La semana nueva contra la semana de su otra ficha, dentro de cada salón."""
    otras = await _otras_fichas(db, ficha)
    if not otras:
        return
    salon_propio = await cargar_horario(db, ficha["salon_id"])
    z_mia = await zonas.zona_del_centro(db, ficha["salon_id"])
    propios = await cargar_horarios_propios(db, [o["id"] for o in otras])
    hoy = zonas.hoy(z_mia)
    for otra in otras:
        propio = propios[otra["id"]]
        if not propio.tiene_semana:
            continue  # sin horario propio allí: nada que comparar (equivalencia)
        salon_otra = await cargar_horario(db, otra["salon_id"])
        z_otra = await zonas.zona_del_centro(db, otra["salon_id"])

        def suyas_del(d, propio=propio, salon_otra=salon_otra):
            suyas = _tramos_del_dia_semana(propio.dias.get(d.isoweekday()))
            return interseccion(suyas, _tramos_del_dia_semana(_dia_de(salon_otra, d.isoweekday())) or []) if suyas else []

        for k in range(_DIAS_DE_LA_SEMANA_COMPARADOS):
            dia = hoy + timedelta(days=k)
            n = dia.isoweekday()
            mias = _tramos_del_dia_semana(_dia_de(semana, n)) or []
            mias = interseccion(mias, _tramos_del_dia_semana(_dia_de(salon_propio, n)) or [])
            if mias and (suyas := _cruce_con(_instantes(dia, mias, z_mia), dia, suyas_del, z_otra)):
                raise _cruce(
                    f"Los {_NOMBRE_DIA[n]} {ficha['nombre']} ya trabaja en {otra['centro']} "
                    f"de {_horas(suyas)}{_de_alli(z_mia, z_otra, otra)}. "
                    "Una persona no puede estar en dos centros a la vez."
                )


async def _sin_cruce_en_fechas(db: PostgrestClient, ficha: dict, excepcion: ExcepcionCreateIn) -> None:
    """Un cambio puntual con horas, contra lo que la otra ficha tiene esas fechas."""
    if excepcion.cerrado:
        return  # librar no se cruza con nada
    otras = await _otras_fichas(db, ficha)
    if not otras:
        return
    mias_base = _tramos_de_horas(
        excepcion.apertura, excepcion.cierre, excepcion.tiene_pausa, excepcion.pausa_inicio, excepcion.pausa_fin
    )
    semana_propia = await cargar_horario(db, ficha["salon_id"])
    exc_propias = await cargar_excepciones(db, ficha["salon_id"])
    z_mia = await zonas.zona_del_centro(db, ficha["salon_id"])
    propios = await cargar_horarios_propios(db, [o["id"] for o in otras])
    for otra in otras:
        propio = propios[otra["id"]]
        if not propio.tiene_semana and not propio.excepciones:
            continue
        semana_otra = await cargar_horario(db, otra["salon_id"])
        exc_otra = await cargar_excepciones(db, otra["salon_id"])
        z_otra = await zonas.zona_del_centro(db, otra["salon_id"])

        def suyas_del(d, propio=propio, semana_otra=semana_otra, exc_otra=exc_otra):
            # Ese día la otra ficha no tiene horario propio: no hay cruce que
            # detectar (la equivalencia a medias). Sin este salto, sus tramos
            # serían TODO el horario de su salón y cualquier cambio puntual aquí
            # «se cruzaría» con un horario que nadie definió.
            propias_de_la_otra = tramos_propios(d, propio)
            if propias_de_la_otra is None:
                return []
            return interseccion(propias_de_la_otra, tramos_activos(d, semana_otra, exc_otra))

        dia = excepcion.fecha_inicio
        ultimo = min(excepcion.fecha_fin, excepcion.fecha_inicio + timedelta(days=_MAX_DIAS_COMPARADOS))
        while dia <= ultimo:
            mias = interseccion(mias_base, tramos_activos(dia, semana_propia, exc_propias))
            if mias and (suyas := _cruce_con(_instantes(dia, mias, z_mia), dia, suyas_del, z_otra)):
                raise _cruce(
                    f"El {zonas.dia_dd_mm_aaaa(dia)} {ficha['nombre']} ya trabaja en {otra['centro']} "
                    f"de {_horas(suyas)}{_de_alli(z_mia, z_otra, otra)}. "
                    "Una persona no puede estar en dos centros a la vez."
                )
            dia += timedelta(days=1)


async def _exigir_impacto(
    db: PostgrestClient, salon_id: str, ficha_id: str, nuevo: HorarioPropio, vistas: list[str]
) -> None:
    antes = await cargar_marco(db, salon_id, [ficha_id])
    despues = antes.con_horario_propio(ficha_id, nuevo)
    citas = await citas_por_venir(db, salon_id, employee_id=ficha_id)
    try:
        await exigir_que_se_hayan_visto(db, que_quedan_fuera(citas, antes, despues), vistas)
    except CitasAfectadas as e:
        raise a_http(e) from e


async def _lo_de_sus_otras_fichas(db: PostgrestClient, ficha: dict) -> list[OtraFichaOut]:
    """Para LA EQUIVALENCIA A MEDIAS, que la pantalla tiene que decir.

    Cada otra ficha de la persona, con lo que trabaja allí cada día de la semana
    dentro del horario de su salón. Con eso la pantalla dice «en Norte no tiene
    horario propio» o «en Centro trabaja lun, mar…», y desactiva los días en los
    que copiar una franja se cruzaría con el otro centro.

    Si quien mira no puede ver el otro centro, la RLS no lo devuelve y aquí no
    se enseña nada: no es un error, es la frontera de siempre.
    """
    try:
        otras = await _otras_fichas(db, ficha)
        if not otras:
            return []
        propios = await cargar_horarios_propios(db, [o["id"] for o in otras])
        salida = []
        for otra in otras:
            propio = propios[otra["id"]]
            semana = None
            if propio.tiene_semana:
                salon_otra = await cargar_horario(db, otra["salon_id"])
                semana = []
                for n in range(1, 8):
                    suyas = _tramos_del_dia_semana(propio.dias.get(n)) or []
                    suyas = interseccion(suyas, _tramos_del_dia_semana(_dia_de(salon_otra, n)) or [])
                    semana.append(
                        [TramoHoras(inicio=zonas.reloj(t.inicio), fin=zonas.reloj(t.fin)) for t in suyas]
                    )
            salida.append(
                OtraFichaOut(
                    employee_id=otra["id"], centro=otra["centro"], tiene_horario=propio.tiene_semana, semana=semana
                )
            )
        return salida
    except PostgrestError:
        return []


async def _leer(db: PostgrestClient, ficha: dict) -> HorarioEmpleadaOut:
    propio = (await cargar_horarios_propios(db, [ficha["id"]]))[ficha["id"]]
    return HorarioEmpleadaOut(
        semana=propio.semana(),
        excepciones=list(propio.excepciones),
        otras_fichas=await _lo_de_sus_otras_fichas(db, ficha),
    )


# ──────────────────────────────────────────────────────────── endpoints


@router.get(
    "",
    response_model=HorarioEmpleadaOut,
    summary="Horario propio de una ficha",
    description=(
        "`semana = null` significa SIN HORARIO PROPIO: se la ofrece en todo el "
        "horario del salón, como antes de la 0045. Las ausencias y cambios "
        "pueden existir sin semana."
    ),
)
async def leer_horario(
    employee_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> HorarioEmpleadaOut:
    ficha = await _ficha(db, user.salon_id, employee_id)
    try:
        return await _leer(db, ficha)
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudo leer el horario"
        ) from e


@router.put(
    "",
    response_model=HorarioEmpleadaOut,
    summary="Guarda la semana de una ficha",
    description=(
        "Los siete días de una vez. Siempre dentro del horario del salón. 409 "
        "`se_cruza_con_otro_centro` si choca con su otra ficha; 409 "
        "`citas_afectadas` si deja citas fuera que no están en `citas_vistas`."
    ),
)
async def guardar_semana(
    employee_id: str,
    payload: HorarioEmpleadaIn,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> HorarioEmpleadaOut:
    ficha = await _ficha(db, user.salon_id, employee_id)
    semana = HorarioSemana(dias=payload.dias)
    await _sin_cruce_semanal(db, ficha, semana)

    actual = (await cargar_horarios_propios(db, [employee_id]))[employee_id]
    nuevo = HorarioPropio(dias={d.dia_semana: d for d in semana.dias}, excepciones=actual.excepciones)
    await _exigir_impacto(db, user.salon_id, employee_id, nuevo, payload.citas_vistas)

    filas = [
        {
            "employee_id": employee_id,
            "dia_semana": d.dia_semana,
            "cerrado": d.cerrado,
            "apertura": d.apertura.isoformat(),
            "cierre": d.cierre.isoformat(),
            "tiene_pausa": d.tiene_pausa,
            "pausa_inicio": d.pausa_inicio.isoformat() if d.pausa_inicio else None,
            "pausa_fin": d.pausa_fin.isoformat() if d.pausa_fin else None,
        }
        for d in semana.dias
    ]
    try:
        await db.upsert_many(TABLA_SEMANA, filas, on_conflict="employee_id,dia_semana")
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=visible("guardar el horario de la empleada"),
        ) from e
    return await _leer(db, ficha)


@router.delete(
    "",
    response_model=HorarioEmpleadaOut,
    summary="Vuelve a «sin horario propio»",
    description=(
        "Borra la semana. La ficha vuelve a estar disponible en todo el horario "
        "del salón. Sus ausencias y cambios se conservan."
    ),
)
async def quitar_semana(
    employee_id: str,
    payload: ConCitasVistas | None = Body(default=None),
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> HorarioEmpleadaOut:
    ficha = await _ficha(db, user.salon_id, employee_id)
    actual = (await cargar_horarios_propios(db, [employee_id]))[employee_id]
    # Quitar la semana solo ensancha, así que no debería dejar nada fuera; pasa
    # igualmente por el mismo cálculo para que ninguna puerta se lo salte.
    nuevo = HorarioPropio(dias=None, excepciones=actual.excepciones)
    await _exigir_impacto(db, user.salon_id, employee_id, nuevo, (payload or ConCitasVistas()).citas_vistas)
    await db.delete(TABLA_SEMANA, {"employee_id": f"eq.{employee_id}"})
    return await _leer(db, ficha)


@router.post(
    "/excepciones",
    response_model=ExcepcionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Añade una ausencia o un cambio puntual",
    description=(
        "cerrado=true: libra esas fechas (vacaciones, un día libre). "
        "cerrado=false: ese horario en lugar del de su semana. Dos de la misma "
        "ficha no pueden cubrir la misma fecha."
    ),
)
async def anadir_excepcion(
    employee_id: str,
    payload: ExcepcionCreateIn,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> ExcepcionOut:
    ficha = await _ficha(db, user.salon_id, employee_id)
    actual = (await cargar_horarios_propios(db, [employee_id]))[employee_id]

    # Se adelanta el solape para decirlo con palabras ANTES de enseñar citas
    # afectadas que se calcularían con dos excepciones a la vez. La garantía
    # sigue siendo el EXCLUDE de la tabla.
    if any(
        e.fecha_inicio <= payload.fecha_fin and payload.fecha_inicio <= e.fecha_fin
        for e in actual.excepciones
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ya hay una ausencia o un cambio en esas fechas. Quita el existente o elige otras.",
        )

    await _sin_cruce_en_fechas(db, ficha, payload)

    datos = payload.model_dump(mode="json", exclude={"citas_vistas"})
    provisional = ExcepcionOut(id="nueva", **payload.model_dump(exclude={"citas_vistas"}))
    nuevo = HorarioPropio(dias=actual.dias, excepciones=(*actual.excepciones, provisional))
    await _exigir_impacto(db, user.salon_id, employee_id, nuevo, payload.citas_vistas)

    try:
        fila = await db.insert(TABLA_EXCEPCIONES, {**datos, "employee_id": employee_id})
    except PostgrestError as e:
        if _CODIGO_SOLAPE in e.detail:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Ya hay una ausencia o un cambio en esas fechas. Quita el existente o elige otras.",
            ) from e
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=visible("guardar la excepción de la empleada"),
        ) from e
    return ExcepcionOut(**{k: v for k, v in fila.items() if k != "employee_id"})


@router.delete(
    "/excepciones/{excepcion_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Quita una ausencia o un cambio puntual",
    description=(
        "Quitar unas vacaciones solo ensancha; quitar un cambio que ampliaba su "
        "horario puede dejar citas fuera, y entonces pasa por `citas_vistas`."
    ),
)
async def quitar_excepcion(
    employee_id: str,
    excepcion_id: str,
    payload: ConCitasVistas | None = Body(default=None),
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> None:
    await _ficha(db, user.salon_id, employee_id)
    actual = (await cargar_horarios_propios(db, [employee_id]))[employee_id]
    restantes = tuple(e for e in actual.excepciones if e.id != excepcion_id)
    if len(restantes) != len(actual.excepciones):
        nuevo = HorarioPropio(dias=actual.dias, excepciones=restantes)
        await _exigir_impacto(
            db, user.salon_id, employee_id, nuevo, (payload or ConCitasVistas()).citas_vistas
        )
    await db.delete(
        TABLA_EXCEPCIONES, {"id": f"eq.{excepcion_id}", "employee_id": f"eq.{employee_id}"}
    )
