"""Los huecos REALES de la agenda. Punto único de esa cuenta.

El panel no lo necesita: no pregunta «dame
los huecos libres», pinta una rejilla con las citas encima y el hueco lo ve el
ojo de la recepcionista. El bot no tiene ojos, así que hay que calcularlo — y se
calcula UNA vez, aquí, para que el panel, el bot y el futuro widget de reserva
online no puedan discrepar sobre si algo está libre.

QUÉ RESPETA UN HUECO, en este orden:

  1. Las EXCEPCIONES por fecha (festivos, cierres) y, si no hay, el horario
     semanal. Eso no se decide aquí: se pregunta a `core/horario_activo.py`,
     que ya era el punto único del horario.
  2. Solo empleados en activo (`employees.estado`): quien está en pausa no
     recibe citas.
  2b. El HORARIO PROPIO de cada ficha (0045), siempre dentro del del salón. Una
     ficha sin horario propio no pasa por este filtro: se comporta exactamente
     como antes. Se pregunta a `core/horario_empleada.py`.
  3. Solo quien SABE hacer el servicio (`employee_services`).
  4. La duración del servicio: la cita tiene que caber ENTERA dentro de un
     tramo, sin atravesar la pausa del mediodía.
  5. Las citas que ya tiene cada empleado.
  6. La política de antelación (ver `PoliticaReserva`).

ZONA HORARIA: el horario se guarda en hora local del salón y las citas como
instantes. Se calcula en local y se devuelve en UTC, con la zona DEL CENTRO
(`salons.zona_horaria`, 0060), que viaja en el contexto y en cada hueco. La
conversión es la de `core/zona.py` y ninguna otra.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.core import zona as zonas
from app.core.citas import ESTADOS_QUE_NO_OCUPAN, CitaNoValida
from app.core.horario_activo import (
    cabe_en_un_tramo,
    cargar_excepciones,
    cargar_horario,
    describe_horario,
    tramos_activos,
)
from app.core.horario_empleada import (
    SIN_HORARIO_PROPIO,
    HorarioPropio,
    cargar_horarios_propios,
    tramos_de_la_ficha,
)
from app.db.postgrest_client import PostgrestClient
from app.schemas.excepciones import ExcepcionOut
from app.schemas.horario import HorarioSemana

# Cada cuánto se ofrece un hueco. 15 minutos es el múltiplo al que ya obliga el
# `check` de `services.duracion_min`, así que cualquier servicio encaja en la
# rejilla sin dejar restos.
PASO_MIN = 15

# Cuántos días se recorren buscando alternativas cuando el día pedido no tiene
# nada. Una semana es lo que un cliente acepta como «lo más cercano»; más allá
# es mejor que elija él otra fecha.
DIAS_DE_BUSQUEDA = 7

# Cuánto se separan entre sí las alternativas que se le OFRECEN a un cliente.
#
# Una hora, y no el paso de la rejilla (15 min), porque son dos cosas distintas:
# la rejilla es la resolución con la que se puede agendar, y esto es la
# distancia a la que dos opciones se leen como opciones. «Las 10:00, las 10:15 o
# las 10:30» no es elegir, es la misma hora dicha tres veces.
#
# No es un mínimo duro: si el día no da para tanto, `repartidos` rellena con lo
# que haya antes que esconder huecos reales.
SEPARACION_ALTERNATIVAS = timedelta(hours=1)

# Hasta dónde llega «cerca» de la hora que ha pedido el cliente.
#
# Es la frontera del sesgo hacia delante (ver `mas_cercanos`): DENTRO de esta
# ventana se prefiere lo posterior a lo anterior —quien pide la una acepta mejor
# la 13:15 que las 12:45—, y FUERA de ella manda la distancia a secas, porque un
# salto grande hacia delante no es mejor que uno igual hacia atrás. Sin esta
# frontera, un salón con la tarde llena le ofrecería las 17:00 a quien pidió la
# una en vez de las 12:45, que es lo que de verdad le vale.
#
# Una hora es lo que una persona acepta como «casi a esa hora». Coincide en
# valor con `SEPARACION_ALTERNATIVAS` por casualidad, no por relación: son dos
# cosas distintas y se cambian por separado.
CERCANIA_ANCLA = timedelta(hours=1)


@dataclass(frozen=True)
class PoliticaReserva:
    """Los límites de CUÁNDO se puede reservar. No los de si hay hueco.

    Existen dos porque el panel y el bot no juegan al mismo juego, y confundirlos
    rompería uno de los dos.
    """

    antelacion_minima: timedelta
    horizonte: timedelta


# La recepcionista tiene que poder agendar a la clienta que tiene DELANTE, ahora
# mismo, y también la boda del año que viene. Aplicarle los límites del bot
# convertiría una herramienta de trabajo en un estorbo.
POLITICA_PANEL = PoliticaReserva(antelacion_minima=timedelta(0), horizonte=timedelta(days=365))

# El bot sí lleva límites, y por motivos distintos:
#   · Una hora de antelación para que al salón no le entre por WhatsApp una cita
#     que empieza en diez minutos y nadie ha visto todavía.
#   · Tres semanas de horizonte porque más allá el salón todavía no sabe ni qué
#     turnos tendrá, y una cita a tres meses vista por WhatsApp se olvida.
POLITICA_BOT = PoliticaReserva(antelacion_minima=timedelta(hours=1), horizonte=timedelta(days=21))


def politica_del_bot(salon: dict | None = None) -> PoliticaReserva:
    """La política que se le aplica al bot de este salón.

    Hoy devuelve siempre la misma, y por eso acepta un `salon` que no usa: es el
    punto único preparado para que mañana estos dos números sean columnas del
    salón, configurables desde Ajustes. Cuando llegue ese día se cambia esta
    función y ni el motor ni las herramientas se enteran.
    """
    return POLITICA_BOT


@dataclass(frozen=True)
class EmpleadoLibre:
    id: str
    nombre: str


@dataclass(frozen=True)
class Hueco:
    """Un hueco concreto y quién puede atenderlo.

    `empleados` viene ORDENADO por carga del día (el que menos citas tiene,
    primero) y, a igualdad, por id. Así, cuando el cliente no elige profesional,
    quien decide es este orden y no el azar del listado: reparte el trabajo en
    vez de saturar siempre al primero de la lista, y es determinista, que es lo
    que permite testearlo.
    """

    inicio: datetime
    fin: datetime
    empleados: tuple[EmpleadoLibre, ...]
    # La zona del centro del hueco: la hora que se le dice a alguien es la de
    # ESE centro, no la del servidor ni la de quien pregunta.
    zona: ZoneInfo = field(compare=False, repr=False)

    @property
    def hora_local(self) -> str:
        return zonas.hhmm(self.inicio, self.zona)

    @property
    def fecha_local(self) -> date:
        return zonas.fecha_local(self.inicio, self.zona)


@dataclass(frozen=True)
class _Contexto:
    """Lo que no cambia de un día a otro, cargado una sola vez."""

    servicio: dict
    empleados: list[dict]
    semana: HorarioSemana
    excepciones: list[ExcepcionOut]
    # El horario propio de cada ficha candidata. Una ficha que no aparece, o
    # que aparece vacía, no tiene horario propio: como antes de la 0045.
    propios: dict[str, HorarioPropio]
    # La zona del centro (0060): la del horario y la de las horas que se dicen.
    zona: ZoneInfo

    @property
    def duracion(self) -> timedelta:
        return timedelta(minutes=int(self.servicio["duracion_min"]))


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def _local(fecha: date, hora: time, z: ZoneInfo) -> datetime:
    """Una hora de pared del salón, con su zona puesta."""
    return zonas.instante_local(fecha, hora, z)


def parsear_fecha(fecha: str | date) -> date:
    """«2026-08-26» -> date. Lanza `CitaNoValida` si no lo es.

    Se valida aquí y no en el borde porque el bot recibe la fecha de un modelo
    de lenguaje: viene de fuera igual que un parámetro de query, y tratarla como
    dato de confianza sería el mismo error en los dos casos.
    """
    if isinstance(fecha, date):
        return fecha
    try:
        return date.fromisoformat(str(fecha).strip())
    except ValueError:
        raise CitaNoValida(
            "fecha_invalida", f"«{fecha}» no es una fecha válida (formato AAAA-MM-DD).", status=400
        )


async def _cargar_servicio(db: PostgrestClient, salon_id: str, service_id: str) -> dict:
    servicios = await db.select(
        "services",
        {
            "select": "id,nombre,precio,duracion_min",
            "id": f"eq.{service_id}",
            "salon_id": f"eq.{salon_id}",
            "limit": "1",
        },
    )
    if not servicios:
        raise CitaNoValida(
            "servicio_desconocido", "Servicio no encontrado en este salón", status=400
        )
    return servicios[0]


async def _cargar_contexto(
    db: PostgrestClient, salon_id: str, service_id: str, employee_id: str | None
) -> _Contexto:
    servicio = await _cargar_servicio(db, salon_id, service_id)

    empleados = await _empleados_que_lo_hacen(db, salon_id, service_id, employee_id)

    return _Contexto(
        servicio=servicio,
        empleados=empleados,
        semana=await cargar_horario(db, salon_id),
        excepciones=await cargar_excepciones(db, salon_id),
        # Dos consultas para todas las candidatas, no una por día ni por ficha:
        # buscar alternativas en siete días no puede multiplicar los viajes.
        propios=await cargar_horarios_propios(db, [e["id"] for e in empleados]),
        zona=await zonas.zona_del_centro(db, salon_id),
    )


async def profesionales_del_servicio(
    db: PostgrestClient, salon_id: str, service_id: str
) -> list[EmpleadoLibre]:
    """Quién puede hacer ESTE servicio en este salón, sin mirar ningún día.

    Es la lista del selector de profesionales del widget, y NO puede salir de
    los huecos de un día concreto. Salía de los de HOY, y eso escondía a una
    profesional para TODAS las fechas en cuanto se quedaba sin hueco hoy —con
    el día lleno, o con su última hora ya pasada— mientras otra sí lo tenía.
    Solo se consultaba el respaldo cuando hoy no le quedaba hueco a nadie.
    Vivo desde el 2 de septiembre (8617929) hasta este arreglo.

    El filtro es el del motor, el mismo `_empleados_que_lo_hacen` que decide a
    quién se le buscan huecos: en activo, de este salón, y que realice el
    servicio. Así la lista no puede enseñar a nadie que el motor descarte, que
    era lo que se buscaba al sacarla de los huecos, sin atarla a un día.

    Lo que eso deja fuera a propósito: quien está de vacaciones o no trabaja en
    todo el horizonte sigue saliendo, y al elegirla verá los días sin horas. Es
    lo mismo que ya pasa con quien tiene la agenda llena, y averiguarlo exigiría
    recorrer tres semanas de huecos por cada visita a la página.
    """
    await _cargar_servicio(db, salon_id, service_id)
    empleados = await _empleados_que_lo_hacen(db, salon_id, service_id, None)
    return [EmpleadoLibre(id=e["id"], nombre=e["nombre"]) for e in empleados]


async def _empleados_que_lo_hacen(
    db: PostgrestClient, salon_id: str, service_id: str, employee_id: str | None
) -> list[dict]:
    """Quién puede atender este servicio, en activo y de este salón.

    El filtro por salón va en `employees` y NO en `employee_services`: esa tabla
    no tiene columna `salon_id` (su RLS va por el empleado), así que se consulta
    acotada a los ids que ya sabemos que son nuestros. Mismo cuidado que en
    `citas.asegurar_empleado_realiza_servicio`.
    """
    params = {
        # `persona_id` viaja con la ficha porque quien ocupa la hora es LA
        # PERSONA: si Eva tiene ficha en dos centros, lo que la deja libre o no
        # son sus citas en los dos, no las de esta ficha.
        "select": "id,nombre,persona_id",
        "salon_id": f"eq.{salon_id}",
        "estado": "eq.activo",
        "order": "nombre.asc",
    }
    if employee_id:
        params["id"] = f"eq.{employee_id}"
    del_salon = await db.select("employees", params)

    if employee_id and not del_salon:
        # Puede ser de otro salón, o estar en pausa. No se distingue en el
        # mensaje a propósito: decirle a un desconocido «esa persona existe pero
        # está de baja» es contar algo que no le corresponde.
        raise CitaNoValida(
            "profesional_desconocido",
            # «Ese profesional» daba por hecho el masculino para alguien de
            # quien no se sabe nada. Se dice sin género, igual que arriba.
            "Esa persona no está disponible en este salón",
            status=400,
        )
    if not del_salon:
        return []

    ids = ",".join(e["id"] for e in del_salon)
    enlaces = await db.select(
        "employee_services",
        {"select": "employee_id", "service_id": f"eq.{service_id}", "employee_id": f"in.({ids})"},
    )
    saben = {e["employee_id"] for e in enlaces}
    return [e for e in del_salon if e["id"] in saben]


def persona_de(empleado: dict) -> str:
    """Quién es, no qué ficha es.

    Para la inmensa mayoría de la plantilla `persona_id` es NULL y esto devuelve
    su propio id, o sea que todo lo que hay debajo se comporta exactamente igual
    que antes. Solo cambia para quien trabaja en más de un centro.
    """
    return empleado.get("persona_id") or empleado["id"]


async def _citas_del_rango(
    db: PostgrestClient,
    salon_id: str,
    empleados: list[dict],
    desde: datetime,
    hasta: datetime,
    excluir: str | None = None,
) -> list[dict]:
    """Las citas que OCUPAN a esos empleados en el rango.

    Canceladas y no_show quedan fuera: no ocupan hueco. Es el mismo criterio que
    `citas.asegurar_sin_solape` y que el candado de la migración 0030 —los tres
    leen `ESTADOS_QUE_NO_OCUPAN`—, porque si discreparan, cancelar una cita
    liberaría la hora en un sitio y no en otro.

    `excluir` deja fuera una cita concreta, y hace falta para MOVERLA: al buscar
    hueco para la nueva hora, la propia cita que se está moviendo no puede
    contarse como ocupada. Sin esto, correr una cita quince minutos sería
    imposible —se chocaría consigo misma—. Es el mismo `exclude_id` que ya tenía
    `citas.asegurar_sin_solape` para el panel, y por el mismo motivo.
    """
    if not empleados:
        return []
    personas = ",".join(sorted({persona_de(e) for e in empleados}))
    params = {
        "select": "id,employee_id,persona_id,inicio,fin",
        # SIN FILTRO DE SALÓN, y es el cambio entero. Antes esto preguntaba «qué
        # citas tiene esta ficha EN ESTE CENTRO», así que el motor era ciego al
        # otro y ofrecía una hora que la base iba a rechazar después. Ofrecer
        # algo que va a fallar al pulsarlo es peor que no ofrecerlo.
        #
        # No abre nada: `persona_id` identifica a UNA persona, que solo existe
        # dentro de su cuenta, así que la consulta no puede alcanzar filas de
        # otra ni con service_role —que es con lo que entra el widget público—.
        # Con token de inquilino, además, la RLS acota antes de llegar aquí.
        "persona_id": f"in.({personas})",
        "estado": f"not.in.({','.join(ESTADOS_QUE_NO_OCUPAN)})",
        "inicio": f"lt.{hasta.isoformat()}",
        "fin": f"gt.{desde.isoformat()}",
        "order": "inicio.asc,id.asc",
    }
    if excluir:
        params["id"] = f"neq.{excluir}"
    # TODAS: siete días de un equipo grande pasan de mil citas, y con `select`
    # las que sobraran se quedaban fuera en silencio: el motor daba por libre
    # una hora ocupada.
    return await db.select_todo("appointments", params)


def _clave(cita: dict) -> str | None:
    """La clave de ocupación de una cita: su persona, y si no la tuviera, su
    ficha.

    ES EL MISMO `coalesce(persona_id, employee_id)` QUE LA RESTRICCIÓN DE LA
    BASE, a propósito. `persona_id` lo rellenan un trigger y el backfill de la
    0044, así que en la práctica siempre está; pero si alguna fila se quedara
    sin sellar, con este `coalesce` sigue ocupando su hora en su centro —que es
    lo que hacía antes— en vez de desaparecer del cálculo. Nunca se protege
    menos que antes.
    """
    return cita.get("persona_id") or cita.get("employee_id")


def _ocupacion(citas: list[dict]) -> dict[str, list[tuple[datetime, datetime]]]:
    """Las horas ocupadas POR PERSONA. La clave dejó de ser la ficha con la
    0044: dos fichas de la misma señora comparten ocupación."""
    ocupado: dict[str, list[tuple[datetime, datetime]]] = {}
    for c in citas:
        emp = _clave(c)
        if not emp:
            continue
        ocupado.setdefault(emp, []).append(
            (datetime.fromisoformat(c["inicio"]), datetime.fromisoformat(c["fin"]))
        )
    return ocupado


def _carga_por_dia(citas: list[dict], z: ZoneInfo) -> dict[tuple[str, date], int]:
    """Cuántas citas tiene cada PERSONA cada día. Para repartir el trabajo.

    Contar también las del otro centro es lo correcto: quien lleva cinco citas
    allí no está más libre que quien lleva cinco aquí.
    """
    carga: dict[tuple[str, date], int] = {}
    for c in citas:
        emp = _clave(c)
        if not emp:
            continue
        dia = zonas.fecha_local(c["inicio"], z)
        carga[(emp, dia)] = carga.get((emp, dia), 0) + 1
    return carga


async def _huecos_en_rango(
    db: PostgrestClient,
    salon_id: str,
    ctx: _Contexto,
    desde: date,
    hasta: date,
    politica: PoliticaReserva,
    ahora: datetime,
    excluir: str | None = None,
) -> list[Hueco]:
    """El motor. Todos los huecos entre dos fechas, ambas incluidas.

    Las citas del rango entero se piden en UNA consulta, no una por día: buscar
    alternativas en los próximos siete días no puede costar siete viajes a la
    base de datos por cada mensaje de WhatsApp.

    Devuelve TODOS los huecos del rango, sin recortar. Antes admitía un `tope`
    para cortar en los N primeros, y eso obligaba a que quien eligiera se
    quedara con huecos consecutivos. Quien quiera pocos, que elija después
    (`repartidos`): el recorte de aquí decidía por él y decidía mal. El rango lo
    acota quien llama, que es el único límite que hace falta.
    """
    if not ctx.empleados:
        return []

    z = ctx.zona
    ini_rango = zonas.a_utc(_local(desde, time(0, 0), z))
    fin_rango = zonas.a_utc(_local(hasta + timedelta(days=1), time(0, 0), z))
    citas = await _citas_del_rango(db, salon_id, ctx.empleados, ini_rango, fin_rango, excluir)
    ocupado = _ocupacion(citas)
    carga = _carga_por_dia(citas, z)

    no_antes_de = ahora + politica.antelacion_minima
    no_despues_de = ahora + politica.horizonte
    duracion = ctx.duracion

    fuera: list[Hueco] = []
    dia = desde
    while dia <= hasta:
        # Cuándo atiende cada candidata ESE día: su horario recortado al del
        # salón (`tramos_de_la_ficha`). Es LA definición de «disponible», y la
        # ocupación de Estadísticas suma estos mismos tramos: si una cambiara y
        # la otra no, la ocupación mediría contra horas que la agenda no ofrece.
        # Una ficha sin horario propio recibe los del salón tal cual.
        atiende_del_dia = {
            e["id"]: tramos_de_la_ficha(
                dia, ctx.semana, ctx.excepciones, ctx.propios.get(e["id"], SIN_HORARIO_PROPIO)
            )
            for e in ctx.empleados
        }
        # Los huecos se siguen generando sobre los tramos DEL SALÓN, que son el
        # techo: el horario de una ficha solo puede quitar, nunca añadir.
        for tramo in tramos_activos(dia, ctx.semana, ctx.excepciones):
            cursor = _local(dia, tramo.inicio, z)
            # El fin del tramo también en local, para poder compararlo con el
            # final de la cita sin que el cambio de hora descoloque la cuenta.
            fin_tramo = _local(dia, tramo.fin, z)
            while cursor + duracion <= fin_tramo:
                inicio = zonas.a_utc(cursor)
                fin = zonas.a_utc(cursor + duracion)
                cursor += timedelta(minutes=PASO_MIN)

                if inicio < no_antes_de or inicio > no_despues_de:
                    continue

                libres = [
                    e
                    for e in ctx.empleados
                    if cabe_en_un_tramo(inicio, fin, atiende_del_dia[e["id"]], z=z)
                    and not any(
                        ini < fin and f > inicio for ini, f in ocupado.get(persona_de(e), ())
                    )
                ]
                if not libres:
                    continue

                # El menos cargado ese día primero; a igualdad, por id, para que
                # dos ejecuciones den siempre el mismo resultado.
                libres.sort(key=lambda e: (carga.get((persona_de(e), dia), 0), e["id"]))
                fuera.append(
                    Hueco(
                        inicio=inicio,
                        fin=fin,
                        empleados=tuple(EmpleadoLibre(id=e["id"], nombre=e["nombre"]) for e in libres),
                        zona=z,
                    )
                )
        dia += timedelta(days=1)

    return fuera


async def huecos_del_dia(
    db: PostgrestClient,
    salon_id: str,
    *,
    service_id: str,
    fecha: str | date,
    employee_id: str | None = None,
    politica: PoliticaReserva = POLITICA_PANEL,
    ahora: datetime | None = None,
) -> dict:
    """Los huecos de UN día, en la forma que devuelve la API.

    Si el día está cerrado se dice con `cerrado` y `motivo` en vez de devolver
    una lista vacía sin explicación: «no hay huecos» y «ese día es festivo» son
    cosas distintas para quien pregunta, y el bot necesita distinguirlas para
    contestar algo útil.
    """
    ahora = ahora or _ahora()
    dia = parsear_fecha(fecha)
    ctx = await _cargar_contexto(db, salon_id, service_id, employee_id)

    tramos = tramos_activos(dia, ctx.semana, ctx.excepciones)
    huecos = (
        []
        if not tramos
        else await _huecos_en_rango(db, salon_id, ctx, dia, dia, politica, ahora)
    )

    return {
        "fecha": dia.isoformat(),
        "servicio": {
            "id": ctx.servicio["id"],
            "nombre": ctx.servicio["nombre"],
            "precio": ctx.servicio.get("precio"),
            "duracion_min": ctx.servicio["duracion_min"],
        },
        "cerrado": not tramos,
        "motivo": describe_horario(dia, ctx.semana, ctx.excepciones) if not tramos else None,
        "huecos": [
            {
                "hora": h.hora_local,
                "inicio": h.inicio.isoformat(),
                "fin": h.fin.isoformat(),
                "empleados": [{"id": e.id, "nombre": e.nombre} for e in h.empleados],
            }
            for h in huecos
        ],
    }


def _inicio_de(h) -> datetime:
    return h.inicio


def mas_cercanos(
    huecos: list,
    limite: int,
    referencia: datetime,
    clave: Callable[[Any], datetime] = _inicio_de,
) -> list:
    """Los `limite` huecos que mejor le sirven a quien pidió una hora concreta.

    Es la selección para cuando el cliente ha dicho una hora —«sobre la una», o
    la hora que pidió y resultó estar ocupada—. Ahí repartir por el día es justo
    lo contrario de lo que quiere: si pide la una, lo que le sirve es la 13:15,
    no las 15:00. Y además esos tramos de en medio, que son los que peor se
    llenan, dejan de ofrecerse nunca.

    ══ EL ORDEN, que es toda la función ══

    Tres grupos, y dentro de cada uno por cercanía:

      1. Los que van DESPUÉS de la hora pedida (o justo en ella) y están CERCA.
      2. Los que van ANTES y están cerca.
      3. Todo lo demás, mire hacia donde mire.

    SESGO HACIA DELANTE: a quien pide la una se le ofrece antes la 13:15 que las
    12:45 aunque estén a la misma distancia. Puede no estar libre todavía —ha
    dicho «la una» porque antes no puede—, pero esperar un cuarto de hora
    siempre puede. Adelantarle la cita es pedirle algo; retrasársela, no.

    PERO NO A CUALQUIER PRECIO, y por eso existe el grupo 2 antes que el 3: si
    pide la una y está todo cogido hasta las tres, lo que le sirve son las 12:45,
    no las 15:00. `CERCANIA_ANCLA` es la frontera de lo que cuenta como «cerca»:
    dentro de esa ventana el sesgo hacia delante decide, y fuera de ella manda
    la distancia a secas, porque un salto grande hacia delante ya no es mejor
    que un salto igual de grande hacia atrás.

    SE DEVUELVE EN ESE ORDEN Y NO EN ORDEN DE RELOJ, deliberadamente: quien lo
    lea —el modelo— ofrece los primeros, así que los primeros tienen que ser los
    que mejor le sirven al cliente.

    `clave` permite usar esto sobre cualquier cosa que tenga un instante dentro
    —un `Hueco` o el dict que devuelve `huecos_del_dia`—, para no tener dos
    implementaciones de lo mismo.
    """
    if limite <= 0 or not huecos:
        return []

    def distancia(h) -> timedelta:
        return abs(clave(h) - referencia)

    despues_cerca, antes_cerca, lejos = [], [], []
    for h in huecos:
        if distancia(h) > CERCANIA_ANCLA:
            lejos.append(h)
        elif clave(h) >= referencia:
            despues_cerca.append(h)
        else:
            antes_cerca.append(h)

    for grupo in (despues_cerca, antes_cerca, lejos):
        grupo.sort(key=distancia)
    return (despues_cerca + antes_cerca + lejos)[:limite]


def repartidos(
    huecos: list,
    limite: int,
    separacion: timedelta,
    clave: Callable[[Any], datetime] = _inicio_de,
) -> list:
    """`limite` huecos separados entre sí, para poder OFRECERLOS.

    Es la selección para una petición ABIERTA, sin hora: «¿qué tenéis el
    sábado?». Ahí lo útil es una oferta variada por el día. Cuando el cliente
    SÍ ha dicho una hora, la selección buena es `mas_cercanos`, no esta.

    EL PROBLEMA QUE RESUELVE: coger los tres primeros de la lista da tres cuartos
    de hora seguidos —«te ofrezco las 10:00, las 10:15 o las 10:30»—, que como
    oferta a una persona suena a máquina y además no es una elección de verdad:
    las tres son casi la misma hora. Separadas («las 10:00, las 11:00 o las
    12:00») el cliente elige de verdad.

    CÓMO: se recorre en orden y se coge el primero, luego el primero que esté al
    menos `separacion` después del anterior, y así. Empezar por el más temprano
    no es arbitrario: el cliente pidió una hora que no había, y lo más cercano a
    lo que pidió es lo primero que quede.

    SI NO LLEGAN, SE RELLENA. Un día casi lleno puede tener solo tres huecos
    sueltos y pegados: ahí devolver uno solo «porque no guardan la distancia»
    sería esconder huecos reales. La separación es para que la oferta se lea
    bien, no una regla de negocio.

    Y el relleno coge el hueco MÁS LEJANO de los ya elegidos, no el siguiente de
    la lista: si no se puede separar una hora, al menos que se separe lo que se
    pueda. Con huecos en 10:00, 10:15, 10:30 y 11:45, rellenar por orden daría
    «10:00, 10:15 y 11:45» —las dos primeras otra vez pegadas— y así da «10:00,
    10:30 y 11:45», que es lo mejor que permite ese día.

    Función PURA: no toca la base de datos. Así se puede probar el reparto sin
    montar un salón entero.
    """
    if limite <= 0 or not huecos:
        return []

    elegidos: list = []
    for h in huecos:
        if len(elegidos) >= limite:
            break
        if not elegidos or clave(h) - clave(elegidos[-1]) >= separacion:
            elegidos.append(h)

    if len(elegidos) < limite:
        _rellenar_lo_mas_separado(huecos, elegidos, limite, clave)
        elegidos.sort(key=clave)

    return elegidos


def _rellenar_lo_mas_separado(
    huecos: list, elegidos: list, limite: int, clave: Callable[[Any], datetime]
) -> None:
    """Completa `elegidos` hasta `limite` con los huecos más alejados. In situ."""
    escogidos = {clave(h) for h in elegidos}
    resto = [h for h in huecos if clave(h) not in escogidos]

    while resto and len(elegidos) < limite:
        # Más lejos del más cercano ya elegido; a igualdad, el más temprano
        # (`-timestamp` invierte el orden dentro del `max`), para que dos
        # ejecuciones den siempre lo mismo.
        mejor = max(
            resto,
            key=lambda h: (
                min(abs(clave(h) - clave(e)) for e in elegidos),
                -clave(h).timestamp(),
            ),
        )
        elegidos.append(mejor)
        resto.remove(mejor)


async def primeros_huecos(
    db: PostgrestClient,
    salon_id: str,
    *,
    service_id: str,
    desde: str | date,
    employee_id: str | None = None,
    politica: PoliticaReserva = POLITICA_BOT,
    ahora: datetime | None = None,
    limite: int = 3,
    dias: int = DIAS_DE_BUSQUEDA,
    separacion: timedelta | None = None,
    ancla: datetime | None = None,
    excluir: str | None = None,
) -> list[Hueco]:
    """Los huecos a partir de una fecha, mirando varios días.

    Es lo que se ofrece cuando el día pedido no tiene nada: «el sábado no me
    queda hueco, pero el lunes tengo a las 10:00». Sin esto, el bot solo sabe
    decir que no.

    CÓMO SE ELIGEN, que depende de lo que haya dicho el cliente:

      · `ancla` — ha pedido una hora (o la que pidió estaba ocupada): los más
        CERCANOS a esa hora. Manda sobre `separacion`, porque cuando alguien
        pide la una, repartirle la tarde no le sirve de nada.
      · `separacion` — petición abierta, sin hora: repartidos por el rango, para
        que la oferta sea variada.
      · Ninguna de las dos: los `limite` primeros, tal cual.

    Las dos van como parámetros y no metidas dentro a la fuerza porque esa
    elección es de quien va a OFRECERLOS —sabe lo que ha dicho el cliente—, no
    del motor. El motor solo sabe qué está libre.
    """
    ahora = ahora or _ahora()
    primero = parsear_fecha(desde)
    ctx = await _cargar_contexto(db, salon_id, service_id, employee_id)
    todos = await _huecos_en_rango(
        db,
        salon_id,
        ctx,
        primero,
        primero + timedelta(days=max(0, dias - 1)),
        politica,
        ahora,
        excluir,
    )
    if ancla is not None:
        return mas_cercanos(todos, limite, ancla)
    if separacion is not None:
        return repartidos(todos, limite, separacion)
    return todos[:limite]


async def hueco_concreto(
    db: PostgrestClient,
    salon_id: str,
    *,
    service_id: str,
    inicio: datetime,
    employee_id: str | None = None,
    politica: PoliticaReserva = POLITICA_BOT,
    ahora: datetime | None = None,
    excluir: str | None = None,
) -> Hueco | None:
    """¿Está libre ESTE hueco exacto? Devuelve quién puede atenderlo.

    Es la comprobación que se hace antes de proponer una cita y OTRA VEZ antes
    de crearla: entre el resumen y el «sí» del cliente pueden pasar minutos, y
    en ese rato el hueco puede haberse ocupado o haber quedado dentro de la hora
    de antelación mínima. Devolver `None` significa «ya no», no «nunca».
    """
    ahora = ahora or _ahora()
    ctx = await _cargar_contexto(db, salon_id, service_id, employee_id)
    dia = zonas.fecha_local(inicio, ctx.zona)
    for h in await _huecos_en_rango(db, salon_id, ctx, dia, dia, politica, ahora, excluir):
        if h.inicio == inicio:
            return h
    return None
