"""Lecturas de la agenda que dependen del horario de cada ficha.

Tres, y las tres son de LECTURA para cualquiera del centro, como la agenda:

  · /agenda/horarios — cuándo trabaja cada ficha un día: las bandas rayadas y
    la marca de «fuera de su horario».
  · /agenda/por-resolver — las citas por venir que se han quedado sin nadie:
    sin profesional, con ella en pausa, o fuera de horario. Es el «dónde» de
    «guardar y ocuparme de ellas»: sin esto, una cita sin profesional no se
    veía en ninguna parte (la agenda no le pinta columna).
  · /agenda/alternativas — las dos salidas de Nueva cita cuando la persona
    elegida no trabaja a esa hora (`core/alternativas.py`).
"""

from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core import publico
from app.core import zona as zonas
from app.core.alternativas import Salida, alternativas_para, describir_su_dia
from app.core.citas import CitaNoValida
from app.core.horario_empleada import SIN_HORARIO_PROPIO, cargar_horarios_propios, tramos_propios
from app.core.impacto import cargar_marco, citas_por_venir, describir, motivo_para_resolver
from app.core.security import admite_cuenta_cortada, CurrentUser, get_current_user, get_postgrest_client
from app.db.postgrest_client import PostgrestClient, PostgrestError
from app.schemas.agenda import (
    AlternativasOut,
    CitaPorResolver,
    HorarioDelDiaFicha,
    HorariosDelDiaOut,
    PorResolverOut,
    SalidaOut,
    TramoOut,
)

router = APIRouter(prefix="/agenda", tags=["agenda"], dependencies=[publico.ADMINISTRADORA])

# La lista de por resolver se acota: si un salón llega a tener cientos, lo que
# necesita es saber cuántas son y empezar por las más cercanas.
_MAX_POR_RESOLVER = 200


def _502(que: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"No se pudo leer {que}.")


@router.get("/horarios", response_model=HorariosDelDiaOut, summary="Cuándo trabaja cada ficha un día")
@admite_cuenta_cortada("agenda")
async def horarios_del_dia(
    fecha: date = Query(..., description="AAAA-MM-DD"),
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> HorariosDelDiaOut:
    try:
        fichas = await db.select("employees", {"select": "id,estado", "salon_id": f"eq.{user.salon_id}"})
        propios = await cargar_horarios_propios(db, [f["id"] for f in fichas])
    except PostgrestError as e:
        raise _502("el horario del día") from e

    salida = []
    for f in fichas:
        propio = propios.get(f["id"], SIN_HORARIO_PROPIO)
        tramos = tramos_propios(fecha, propio)
        salida.append(
            HorarioDelDiaFicha(
                employee_id=f["id"],
                estado=f.get("estado") or "activo",
                tramos=None
                if tramos is None
                else [TramoOut(inicio=zonas.reloj(t.inicio), fin=zonas.reloj(t.fin)) for t in tramos],
                descripcion=describir_su_dia(fecha, propio),
            )
        )
    return HorariosDelDiaOut(fecha=fecha, fichas=salida)


@router.get("/por-resolver", response_model=PorResolverOut, summary="Citas por venir que se han quedado sin nadie")
@admite_cuenta_cortada("agenda")
async def por_resolver(
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> PorResolverOut:
    try:
        marco = await cargar_marco(db, user.salon_id)
        pendientes = []
        for cita in await citas_por_venir(db, user.salon_id):
            motivo = motivo_para_resolver(cita, marco)
            if motivo:
                pendientes.append((cita, motivo))
        acotadas = pendientes[:_MAX_POR_RESOLVER]
        descritas = await describir(db, [c for c, _ in acotadas])
    except PostgrestError as e:
        raise _502("las citas por resolver") from e

    return PorResolverOut(
        total=len(pendientes),
        citas=[CitaPorResolver(**d, motivo=m) for d, (_, m) in zip(descritas, acotadas, strict=True)],
    )


def _salida(s: Salida | None) -> SalidaOut | None:
    if s is None:
        return None
    return SalidaOut(inicio=s.inicio, fecha=s.fecha, hora=s.hora, employee_id=s.employee_id, nombre=s.nombre)


@router.get(
    "/alternativas",
    response_model=AlternativasOut,
    summary="Las dos salidas cuando la profesional no trabaja a esa hora",
)
async def alternativas(
    service_id: str = Query(...),
    employee_id: str = Query(...),
    inicio: datetime = Query(..., description="ISO con zona"),
    excluir: str | None = Query(default=None, description="La cita que se está editando"),
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> AlternativasOut:
    if inicio.tzinfo is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="`inicio` tiene que llevar zona horaria")
    try:
        servicios = await db.select(
            "services",
            {"select": "id,duracion_min", "id": f"eq.{service_id}", "salon_id": f"eq.{user.salon_id}", "limit": "1"},
        )
        fichas = await db.select(
            "employees",
            {"select": "id", "id": f"eq.{employee_id}", "salon_id": f"eq.{user.salon_id}", "limit": "1"},
        )
    except PostgrestError as e:
        raise _502("la agenda") from e
    if not servicios:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Servicio no encontrado")
    if not fichas:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Empleado no encontrado")

    try:
        alt = await alternativas_para(
            db,
            user.salon_id,
            service_id=service_id,
            employee_id=employee_id,
            inicio=inicio,
            duracion_min=int(servicios[0]["duracion_min"]),
            excluir=excluir,
        )
    except CitaNoValida as e:
        raise HTTPException(status_code=e.status, detail=e.mensaje) from e
    except PostgrestError as e:
        raise _502("la agenda") from e

    return AlternativasOut(
        fuera_de_su_horario=alt.fuera_de_su_horario,
        descripcion=alt.descripcion,
        misma_profesional=_salida(alt.misma_profesional),
        otro_dia=alt.otro_dia,
        otra_profesional=_salida(alt.otra_profesional),
        nadie_mas_lo_hace=alt.nadie_mas_lo_hace,
        dias_buscados=alt.dias_buscados,
    )
