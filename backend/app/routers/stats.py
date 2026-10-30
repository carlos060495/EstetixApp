import unicodedata
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Query

from app.core import publico
from app.core import zona as zonas
from app.core.horario_activo import (
    Tramo,
    cargar_excepciones,
    cargar_horario,
)
from app.core.horario_empleada import (
    SIN_HORARIO_PROPIO,
    HorarioPropio,
    cargar_horarios_propios,
    tramos_de_la_ficha,
)
from app.core.security import (
    get_current_user,
    CurrentUser,
    get_postgrest_client,
    get_service_postgrest_client,
)
from app.db.postgrest_client import PostgrestClient
from app.routers import employees as empleados
from app.routers.employees import _marcas_corregidas, _resumir_corregidas
from app.schemas.excepciones import ExcepcionOut
from app.schemas.finances import EquipoOut, StatsOut
from app.schemas.horario import HorarioSemana

# Estadísticas es SOLO ADMIN (DIRECTRICES §4.4: finanzas ocultas al resto).
router = APIRouter(prefix="/stats", tags=["stats"], dependencies=[publico.ADMINISTRADORA])



def _minutos(inicio_iso: str, fin_iso: str) -> int:
    inicio = datetime.fromisoformat(inicio_iso)
    fin = datetime.fromisoformat(fin_iso)
    return max(0, int((fin - inicio).total_seconds() // 60))


def _minutos_de_tramos(tramos: list[Tramo]) -> int:
    return sum(zonas.minutos_entre(t.inicio, t.fin) for t in tramos)


async def _citas_que_ocupan(db: PostgrestClient, salon_id: str, desde_iso: str, hasta_iso: str) -> list[dict]:
    """Las citas que bloquean hueco en el periodo (ni canceladas ni no-show).

    ES LA ÚNICA LECTURA de minutos de cita de Estadísticas: de aquí salen la
    ocupación del centro (`/stats`) y las horas con cita de cada persona
    (`/stats/equipo`). Dos lecturas podrían acabar contando cosas distintas.
    """
    return await db.select_todo(
        "appointments",
        {
            "select": "inicio,fin,employee_id",
            "salon_id": f"eq.{salon_id}",
            "estado": "not.in.(cancelada,no_show)",
            "inicio": f"lt.{hasta_iso}",
            "fin": f"gt.{desde_iso}",
            "order": "inicio.asc,id.asc",
        },
    )


def minutos_de_cita_por_ficha(ocupadas: list[dict]) -> dict[str | None, int]:
    """Los minutos de cita de cada ficha; `None`, los de las citas sin
    profesional. La ocupación del centro es LA SUMA DE ESTO: el total y el
    reparto por persona salen del mismo bucle y no pueden dejar de cuadrar."""
    por_ficha: dict[str | None, int] = {}
    for c in ocupadas:
        ficha = c.get("employee_id")
        por_ficha[ficha] = por_ficha.get(ficha, 0) + _minutos(c["inicio"], c["fin"])
    return por_ficha


def minutos_disponibles_de(
    desde: date,
    hasta: date,
    semana: HorarioSemana | None,
    excepciones: list[ExcepcionOut],
    propios: dict[str, HorarioPropio],
    fichas: list[str],
) -> int:
    """Minutos en que se podía dar cita en [desde, hasta], ambos incluidos.

    Por cada ficha activa y cada día, los tramos de `tramos_de_la_ficha`: su
    horario propio (vacaciones y días libres incluidos) recortado al del centro
    (pausa, festivos y cierres incluidos). Es la MISMA función con la que el
    motor de disponibilidad decide a quién ofrecer cada hueco, así que «100 %»
    quiere decir «la agenda no tenía nada más que ofrecer». Una ficha sin
    horario propio cuenta el horario del centro entero.

    Antes se suponían 8 h de lunes a sábado por empleada.
    """
    total = 0
    d = desde
    while d <= hasta:
        for ficha in fichas:
            total += _minutos_de_tramos(
                tramos_de_la_ficha(d, semana, excepciones, propios.get(ficha, SIN_HORARIO_PROPIO))
            )
        d += timedelta(days=1)
    return total


@router.get("", response_model=StatsOut)
async def get_stats(
    desde: datetime = Query(..., description="Inicio del período (citas), ISO"),
    hasta: datetime = Query(..., description="Fin EXCLUSIVO del período (citas), ISO"),
    fecha_desde: date = Query(..., description="Inicio del período (gastos/ingresos manuales)"),
    fecha_hasta: date = Query(..., description="Fin INCLUSIVE (gastos/ingresos manuales)"),
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> dict:
    """Resumen del período. Fórmulas:

    - ingresos_servicios: Σ precio del servicio de las citas 'completada' del
      período (los servicios no se registran a mano; derivan de la agenda).
    - ingresos_productos / ingresos_otros: Σ incomes del período por tipo.
    - gastos_fijos / gastos_variables: Σ expenses del período por tipo
      (pagados Y pendientes: son costes devengados del período).
    - beneficio_neto = ingresos_totales − gastos_totales.
    - ocupacion_pct = minutos de cita (no canceladas/no-show) / minutos
      disponibles × 100, con disponibles = Σ por empleada activa y día de su
      horario recortado al del centro (`minutos_disponibles_de`), lo mismo que
      la agenda ofrece.
    - punto_equilibrio_pct = cobertura de costes: ingresos_totales /
      gastos_totales × 100. ≥100 → costes cubiertos (equilibrio alcanzado);
      sin gastos en el período se devuelve 100.
    """
    desde_iso, hasta_iso = desde.isoformat(), hasta.isoformat()

    # Citas completadas del período, con nombre y precio de su servicio. De
    # aquí sale TANTO el total de ingresos por servicio COMO su detalle por
    # cita (misma derivación → coherencia garantizada, sin doble conteo). Solo
    # 'completada': las pendientes/confirmadas no cuentan ni se listan.
    #
    # `select_todo` y no `select` en las cuatro lecturas que se SUMAN: `select`
    # se queda en mil filas sin avisar, y Estadísticas dejaba de contar a
    # partir de la cita o el movimiento mil con un total de aspecto exacto.
    completadas = await db.select_todo(
        "appointments",
        {
            "select": "id,inicio,fin,services(nombre,precio)",
            "salon_id": f"eq.{user.salon_id}",
            "estado": "eq.completada",
            "inicio": f"lt.{hasta_iso}",
            "fin": f"gt.{desde_iso}",
            "order": "inicio.asc,id.asc",
        },
    )
    # El día de cada cita, el del CENTRO: era el de UTC, y una cita a las 00:30
    # de Madrid salía el día anterior.
    z = await zonas.zona_del_centro(db, user.salon_id)
    servicios = [
        {
            "id": c["id"],
            "fecha": zonas.fecha_local(c["inicio"], z).isoformat(),
            "concepto": (c.get("services") or {}).get("nombre") or "Servicio",
            "importe": (c.get("services") or {}).get("precio") or 0,
        }
        for c in completadas
    ]
    ingresos_servicios = sum(s["importe"] for s in servicios)

    # Minutos ocupados: toda cita que bloquea hueco (no cancelada/no-show). El
    # total es la suma del reparto por persona (ver `/stats/equipo`).
    ocupadas = await _citas_que_ocupan(db, user.salon_id, desde_iso, hasta_iso)
    minutos_citas = sum(minutos_de_cita_por_ficha(ocupadas).values())

    activos = await db.select(
        "employees",
        {"select": "id", "salon_id": f"eq.{user.salon_id}", "estado": "eq.activo"},
    )
    fichas = [e["id"] for e in activos]
    minutos_disponibles = minutos_disponibles_de(
        fecha_desde,
        fecha_hasta,
        await cargar_horario(db, user.salon_id),
        await cargar_excepciones(db, user.salon_id),
        await cargar_horarios_propios(db, fichas),
        fichas,
    )
    ocupacion_pct = (
        round(minutos_citas / minutos_disponibles * 100, 1) if minutos_disponibles else 0.0
    )

    rango_fecha = f"(fecha.gte.{fecha_desde.isoformat()},fecha.lte.{fecha_hasta.isoformat()})"
    ingresos = await db.select_todo(
        "incomes",
        {"select": "tipo,importe", "salon_id": f"eq.{user.salon_id}", "and": rango_fecha,
         # Lo ANULADO no suma (0092): la fila se queda en Movimientos, tachada.
         "anulado_at": "is.null", "order": "fecha.asc,id.asc"},
    )
    ingresos_productos = sum(i["importe"] for i in ingresos if i["tipo"] == "producto")
    ingresos_otros = sum(i["importe"] for i in ingresos if i["tipo"] == "otro")

    gastos = await db.select_todo(
        "expenses",
        {"select": "tipo,importe", "salon_id": f"eq.{user.salon_id}", "and": rango_fecha,
         # Lo ANULADO no suma (0092): la fila se queda en Movimientos, tachada.
         "anulado_at": "is.null", "order": "fecha.asc,id.asc"},
    )
    gastos_fijos = sum(g["importe"] for g in gastos if g["tipo"] == "fijo")
    gastos_variables = sum(g["importe"] for g in gastos if g["tipo"] == "variable")
    gastos_totales = gastos_fijos + gastos_variables

    ingresos_totales = ingresos_servicios + ingresos_productos + ingresos_otros
    punto_equilibrio_pct = (
        round(ingresos_totales / gastos_totales * 100, 1) if gastos_totales else 100.0
    )

    return {
        "ingresos_servicios": round(ingresos_servicios, 2),
        "ingresos_productos": round(ingresos_productos, 2),
        "ingresos_otros": round(ingresos_otros, 2),
        "ingresos_totales": round(ingresos_totales, 2),
        "gastos_fijos": round(gastos_fijos, 2),
        "gastos_variables": round(gastos_variables, 2),
        "gastos_totales": round(gastos_totales, 2),
        "beneficio_neto": round(ingresos_totales - gastos_totales, 2),
        "citas_completadas": len(completadas),
        "minutos_citas": minutos_citas,
        "minutos_disponibles": minutos_disponibles,
        "ocupacion_pct": ocupacion_pct,
        "punto_equilibrio_pct": punto_equilibrio_pct,
        "servicios": servicios,
    }


def _orden_por_nombre(nombre: str) -> str:
    """Alfabético de verdad: sin que las tildes manden a «Álvaro» al final."""
    sin_tildes = unicodedata.normalize("NFD", nombre or "")
    return "".join(c for c in sin_tildes if unicodedata.category(c) != "Mn").casefold()


@router.get("/equipo", response_model=EquipoOut)
async def get_equipo(
    desde: datetime = Query(..., description="Inicio del período, ISO (el mismo que /stats)"),
    hasta: datetime = Query(..., description="Fin EXCLUSIVO del período, ISO"),
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
    service: PostgrestClient = Depends(get_service_postgrest_client),
) -> dict:
    """DÓNDE HAY HUECO POR LLENAR: por persona, las horas fichadas y, dentro,
    las horas con cita, en el mismo periodo que el resto de Estadísticas.

    · Horas FICHADAS: las del historial de fichajes, con sus correcciones y con
      la misma regla (`_resumir_corregidas`): una sesión cuenta en el día de su
      entrada, lo cerrado suma y lo abierto no. Son las mismas horas que se ven
      en la ficha de cada persona.
    · Horas CON CITA: las de la ocupación del centro (`_citas_que_ocupan` y
      `minutos_de_cita_por_ficha`): la ocupación es la suma de esto más las
      citas sin profesional, que van aparte.
    · LO QUE NO SE PUEDE CONTAR se dice: los días con una sesión sin cerrar
      (incompleto o en curso) de cada persona, para que una barra corta no se
      lea como «trabajó menos».

    El orden es ALFABÉTICO, no por horas: esto no es una clasificación.
    Salen las fichas activas, y las que están en pausa si en el periodo tienen
    algo (fichajes, citas o días sin cerrar).

    UN PERIODO SIN TERMINAR SE CORTA AHORA, LAS DOS BARRAS (02-10-2026).
    El gráfico contesta qué se hizo con el tiempo que ya pasó; la ocupación,
    cuán llena está la agenda, incluido lo que viene. Con las fichadas hasta hoy
    y las citas hasta el día 31, cada persona parecía más ocupada de lo que
    estaba y el hueco —todo el mensaje— se encogía. El corte usa la MISMA regla
    que la ocupación en sus bordes: una cita que cruza el borde cuenta entera.
    Por eso el espejo se sostiene igual: este gráfico, en un periodo en curso,
    es la ocupación pedida con el mismo inicio y con fin AHORA.
    """
    z = await zonas.zona_del_centro(db, user.salon_id)
    corte = max(desde, min(hasta, empleados._utc_now()))
    en_curso = corte < hasta
    fichas = await db.select_todo(
        "employees", {"select": "id,nombre,estado", "salon_id": f"eq.{user.salon_id}", "order": "nombre.asc,id.asc"}
    )
    cita = minutos_de_cita_por_ficha(
        await _citas_que_ocupan(db, user.salon_id, desde.isoformat(), corte.isoformat())
    )
    personas = []
    for f in fichas:
        marcas = await _marcas_corregidas(db, service, user.salon_id, f["id"], desde, corte)
        dias, fichados = _resumir_corregidas(marcas, desde, corte, z)
        sin_contar = [{"fecha": d.fecha, "estado": d.estado} for d in sorted(dias, key=lambda d: d.fecha)
                      if d.estado != "completo"]
        minutos_cita = cita.get(f["id"], 0)
        if f["estado"] != "activo" and not (fichados or minutos_cita or sin_contar):
            continue
        personas.append({
            "employee_id": f["id"],
            "nombre": f["nombre"],
            "estado": f["estado"],
            "minutos_fichados": fichados,
            "minutos_cita": minutos_cita,
            "dias_sin_contar": sin_contar,
        })
    personas.sort(key=lambda p: (_orden_por_nombre(p["nombre"]), p["employee_id"]))
    return {
        "personas": personas,
        "minutos_cita_sin_profesional": cita.get(None, 0),
        # La ocupación del centro DEL MISMO TRAMO (hasta `hasta_efectivo`), para
        # comprobar que cuadra: Σ personas + sin profesional.
        "minutos_citas_total": sum(cita.values()),
        # Hasta dónde llegan las barras: el fin del periodo, o ahora si no ha terminado.
        "hasta_efectivo": corte,
        "en_curso": en_curso,
    }
