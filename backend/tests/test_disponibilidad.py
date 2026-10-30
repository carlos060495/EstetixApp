"""El motor de huecos: `app/core/disponibilidad.py`.

Es la pieza de la que depende que el bot no ofrezca una hora que no existe, así
que se prueba caso por caso: horario, festivos, pausa, duración, empleados que
no hacen el servicio, empleados en pausa, citas ya puestas, la política de
antelación y el reparto de carga.

FECHAS: todo se ancla a un lunes concreto (2026-09-07) y se le pasa `ahora`
explícito. Nada aquí depende de qué día se ejecuten los tests.

ZONA: el salón trabaja en Europe/Madrid; en septiembre eso es UTC+2. Las horas
UTC de los asertos llevan esa resta hecha a mano a propósito — si alguien rompe
la conversión, el número no cuadra y el test lo dice.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.citas import CitaNoValida
from app.core.disponibilidad import (
    POLITICA_BOT,
    POLITICA_PANEL,
    Hueco,
    hueco_concreto,
    huecos_del_dia,
    mas_cercanos,
    primeros_huecos,
    repartidos,
)
from tests.zona_de_prueba import ZONA_SALON

SALON = "11111111-1111-1111-1111-111111111111"
OTRO_SALON = "99999999-9999-9999-9999-999999999999"

CORTE = "c0000000-0000-0000-0000-000000000001"  # 30 min
COLOR = "c0000000-0000-0000-0000-000000000002"  # 90 min

ANA = "e0000000-0000-0000-0000-00000000000a"
BRUNO = "e0000000-0000-0000-0000-00000000000b"
CARLA = "e0000000-0000-0000-0000-00000000000c"

LUNES = "2026-09-07"
SABADO = "2026-09-12"
DOMINGO = "2026-09-13"

# Las 08:00 UTC del lunes = las 10:00 en Madrid, justo la apertura.
APERTURA_LUNES = datetime(2026, 9, 7, 8, 0, tzinfo=timezone.utc)
# Un «ahora» muy anterior: así ninguna política recorta nada salvo cuando el
# test lo busca a propósito.
AMANECE = datetime(2026, 9, 7, 4, 0, tzinfo=timezone.utc)


# ------------------------------------------------------------------- fake


class FakePostgrest:
    """Lo justo de PostgREST para este motor: eq, in, not.in, lt, gt, limit.

    Se implementan los filtros DE VERDAD (y no un `return todo`) porque media
    prueba de este módulo consiste precisamente en que las consultas acotan lo
    que dicen acotar: el salón, el servicio, los estados que no ocupan hueco.
    """

    def __init__(self, tablas: dict[str, list[dict]]):
        self.tablas = tablas
        self.consultas: list[tuple[str, dict]] = []

    async def select_todo(self, table, params=None):
        """Como el real: exige un orden estable y trae todas; el doble no pagina."""
        assert "order" in (params or {}), f"select_todo({table!r}) sin order"
        return await self.select(table, params)

    async def select(self, tabla: str, params: dict | None = None) -> list[dict]:
        params = params or {}
        self.consultas.append((tabla, params))
        filas = [dict(f) for f in self.tablas.get(tabla, [])]
        for campo, filtro in params.items():
            if campo in ("select", "order", "limit"):
                continue
            filas = [f for f in filas if _pasa(f.get(campo), filtro)]
        if "limit" in params:
            filas = filas[: int(params["limit"])]
        return filas


def _pasa(valor, filtro: str) -> bool:
    op, _, esperado = filtro.partition(".")
    if op == "eq":
        return str(valor) == esperado
    if op == "in":
        return str(valor) in esperado.strip("()").split(",")
    if op == "not":
        sub_op, _, resto = esperado.partition(".")
        if sub_op == "in":
            return str(valor) not in resto.strip("()").split(",")
        raise AssertionError(f"filtro no soportado: {filtro}")
    if op == "lt":
        return datetime.fromisoformat(valor) < datetime.fromisoformat(esperado)
    if op == "gt":
        return datetime.fromisoformat(valor) > datetime.fromisoformat(esperado)
    raise AssertionError(f"filtro no soportado: {filtro}")


def _db(**cambios) -> FakePostgrest:
    """Un salón con dos servicios y tres empleados.

    Ana y Bruno cortan; Carla solo colorea. Ana además está en activo, y es la
    que se pone en pausa en el test que lo necesita.
    """
    tablas = {
        # El centro existe: su zona horaria (0060) la pone la base por defecto.
        "salons": [{"id": SALON, "zona_horaria": "Europe/Madrid"}],
        "services": [
            {"id": CORTE, "salon_id": SALON, "nombre": "Corte de caballero", "precio": 25, "duracion_min": 30},
            {"id": COLOR, "salon_id": SALON, "nombre": "Coloración", "precio": 60, "duracion_min": 90},
        ],
        "employees": [
            {"id": ANA, "salon_id": SALON, "nombre": "Ana", "estado": "activo"},
            {"id": BRUNO, "salon_id": SALON, "nombre": "Bruno", "estado": "activo"},
            {"id": CARLA, "salon_id": SALON, "nombre": "Carla", "estado": "activo"},
        ],
        "employee_services": [
            {"employee_id": ANA, "service_id": CORTE},
            {"employee_id": BRUNO, "service_id": CORTE},
            {"employee_id": CARLA, "service_id": COLOR},
        ],
        "appointments": [],
        "salon_schedule": [],
        "salon_schedule_exceptions": [],
    }
    tablas.update(cambios)
    return FakePostgrest(tablas)


def _cita(
    employee_id: str,
    inicio_utc: str,
    minutos: int,
    estado: str = "confirmada",
    salon_id: str = SALON,
    persona_id: str | None = None,
) -> dict:
    """Una cita como la deja LA BASE, no como la manda el backend.

    `persona_id` lo rellena el trigger `cita_apunta_a_la_persona` (0044) con la
    persona de la ficha, y sin enlace esa persona es la propia ficha. El doble
    tiene que hacer lo mismo: el motor de huecos calcula la ocupación POR
    PERSONA, y una fila sin sellar simplemente no ocuparía nada.
    """
    ini = datetime.fromisoformat(inicio_utc)
    return {
        "id": f"cita-{employee_id[-1]}-{inicio_utc}",
        "salon_id": salon_id,
        "employee_id": employee_id,
        "persona_id": persona_id or employee_id,
        "inicio": ini.isoformat(),
        "fin": (ini + timedelta(minutes=minutos)).isoformat(),
        "estado": estado,
    }


async def _horas(db, **kwargs) -> list[str]:
    kwargs.setdefault("service_id", CORTE)
    kwargs.setdefault("fecha", LUNES)
    kwargs.setdefault("politica", POLITICA_BOT)
    kwargs.setdefault("ahora", AMANECE)
    datos = await huecos_del_dia(db, SALON, **kwargs)
    return [h["hora"] for h in datos["huecos"]]


# ------------------------------------------------------------- lo básico


@pytest.mark.anyio
async def test_un_lunes_normal_ofrece_toda_la_jornada_en_pasos_de_15():
    horas = await _horas(_db())
    # Horario por defecto: 10:00–20:00. Un corte dura 30 min, así que el último
    # hueco que CABE entero empieza a las 19:30.
    assert horas[0] == "10:00"
    assert horas[1] == "10:15"
    assert horas[-1] == "19:30"
    assert len(horas) == 39


@pytest.mark.anyio
async def test_la_cita_tiene_que_caber_entera_antes_de_cerrar():
    # Coloración dura 90 min: con cierre a las 20:00, el último hueco es 18:30.
    horas = await _horas(_db(), service_id=COLOR)
    assert horas[-1] == "18:30"


@pytest.mark.anyio
async def test_el_sabado_cierra_antes():
    horas = await _horas(_db(), fecha=SABADO)
    assert horas[0] == "10:00"
    assert horas[-1] == "13:30"  # sábado 10:00–14:00


@pytest.mark.anyio
async def test_el_domingo_esta_cerrado_y_se_dice_por_que():
    datos = await huecos_del_dia(
        _db(), SALON, service_id=CORTE, fecha=DOMINGO, ahora=AMANECE
    )
    assert datos["cerrado"] is True
    assert datos["huecos"] == []
    assert "cerrado" in datos["motivo"]


@pytest.mark.anyio
async def test_un_festivo_gana_al_horario_semanal_y_dice_el_motivo():
    db = _db(
        salon_schedule_exceptions=[
            {
                "id": "exc-1",
                "salon_id": SALON,
                "fecha_inicio": LUNES,
                "fecha_fin": LUNES,
                "cerrado": True,
                "apertura": None,
                "cierre": None,
                "tiene_pausa": False,
                "pausa_inicio": None,
                "pausa_fin": None,
                "motivo": "Fiesta local",
            }
        ]
    )
    datos = await huecos_del_dia(db, SALON, service_id=CORTE, fecha=LUNES, ahora=AMANECE)
    assert datos["cerrado"] is True
    assert "Fiesta local" in datos["motivo"]


@pytest.mark.anyio
async def test_la_cita_no_puede_atravesar_la_pausa():
    db = _db(
        salon_schedule=[
            {
                "salon_id": SALON,
                "dia_semana": d,
                "cerrado": False,
                "apertura": "10:00:00",
                "cierre": "20:00:00",
                "tiene_pausa": True,
                "pausa_inicio": "14:00:00",
                "pausa_fin": "16:00:00",
            }
            for d in range(1, 8)
        ]
    )
    horas = await _horas(db)
    # Último antes de la pausa: 13:30 (acaba a las 14:00). Nada entre medias.
    assert "13:30" in horas
    assert "13:45" not in horas
    assert "14:00" not in horas
    assert "15:45" not in horas
    assert "16:00" in horas


# ------------------------------------------------------------- empleados


@pytest.mark.anyio
async def test_solo_ofrecen_hueco_quienes_saben_hacer_el_servicio():
    datos = await huecos_del_dia(
        _db(), SALON, service_id=CORTE, fecha=LUNES, ahora=AMANECE
    )
    nombres = {e["nombre"] for e in datos["huecos"][0]["empleados"]}
    assert nombres == {"Ana", "Bruno"}  # Carla solo colorea


@pytest.mark.anyio
async def test_un_empleado_en_pausa_no_recibe_citas():
    db = _db(
        employees=[
            {"id": ANA, "salon_id": SALON, "nombre": "Ana", "estado": "pausa"},
            {"id": BRUNO, "salon_id": SALON, "nombre": "Bruno", "estado": "activo"},
        ]
    )
    datos = await huecos_del_dia(db, SALON, service_id=CORTE, fecha=LUNES, ahora=AMANECE)
    assert [e["nombre"] for e in datos["huecos"][0]["empleados"]] == ["Bruno"]


@pytest.mark.anyio
async def test_si_nadie_hace_el_servicio_no_hay_huecos_pero_el_dia_esta_abierto():
    db = _db(employee_services=[])
    datos = await huecos_del_dia(db, SALON, service_id=CORTE, fecha=LUNES, ahora=AMANECE)
    assert datos["cerrado"] is False
    assert datos["huecos"] == []


@pytest.mark.anyio
async def test_pedir_un_profesional_concreto_limita_la_busqueda_a_el():
    datos = await huecos_del_dia(
        _db(), SALON, service_id=CORTE, fecha=LUNES, employee_id=BRUNO, ahora=AMANECE
    )
    assert all(
        [e["nombre"] for e in h["empleados"]] == ["Bruno"] for h in datos["huecos"]
    )


@pytest.mark.anyio
async def test_un_profesional_que_no_hace_el_servicio_deja_el_dia_sin_huecos():
    # Carla existe y está activa, pero no corta. No es un error: es un día sin
    # huecos PARA ELLA, y quien pregunte tiene que poder ofrecer a otra persona.
    datos = await huecos_del_dia(
        _db(), SALON, service_id=CORTE, fecha=LUNES, employee_id=CARLA, ahora=AMANECE
    )
    assert datos["huecos"] == []


@pytest.mark.anyio
async def test_un_profesional_de_otro_salon_es_un_error_claro():
    with pytest.raises(CitaNoValida) as e:
        await huecos_del_dia(
            _db(),
            SALON,
            service_id=CORTE,
            fecha=LUNES,
            employee_id="e0000000-0000-0000-0000-0000000000ff",
            ahora=AMANECE,
        )
    assert e.value.codigo == "profesional_desconocido"


@pytest.mark.anyio
async def test_un_servicio_de_otro_salon_es_un_error_claro():
    with pytest.raises(CitaNoValida) as e:
        await huecos_del_dia(
            _db(), OTRO_SALON, service_id=CORTE, fecha=LUNES, ahora=AMANECE
        )
    assert e.value.codigo == "servicio_desconocido"


# --------------------------------------------------------- citas puestas


@pytest.mark.anyio
async def test_una_cita_ocupa_a_su_empleado_pero_no_a_los_demas():
    # Bruno ocupado de 12:00 a 12:30 locales (10:00 UTC).
    db = _db(appointments=[_cita(BRUNO, "2026-09-07T10:00:00+00:00", 30)])
    datos = await huecos_del_dia(db, SALON, service_id=CORTE, fecha=LUNES, ahora=AMANECE)
    doce = next(h for h in datos["huecos"] if h["hora"] == "12:00")
    assert [e["nombre"] for e in doce["empleados"]] == ["Ana"]


@pytest.mark.anyio
async def test_con_todos_ocupados_ese_hueco_desaparece():
    db = _db(
        appointments=[
            _cita(ANA, "2026-09-07T10:00:00+00:00", 30),
            _cita(BRUNO, "2026-09-07T10:00:00+00:00", 30),
        ]
    )
    horas = await _horas(db)
    assert "12:00" not in horas
    assert "11:45" not in horas  # un corte a las 11:45 acabaría a las 12:15
    assert "12:30" in horas


@pytest.mark.anyio
async def test_una_cita_cancelada_libera_el_hueco():
    db = _db(
        appointments=[
            _cita(ANA, "2026-09-07T10:00:00+00:00", 30, estado="cancelada"),
            _cita(BRUNO, "2026-09-07T10:00:00+00:00", 30, estado="no_show"),
        ]
    )
    horas = await _horas(db)
    assert "12:00" in horas


@pytest.mark.anyio
async def test_el_menos_cargado_del_dia_va_primero():
    # Ana tiene dos citas ese día, Bruno ninguna: a las 17:00 debe ofrecerse
    # Bruno el primero aunque Ana vaya antes por orden alfabético.
    db = _db(
        appointments=[
            _cita(ANA, "2026-09-07T08:00:00+00:00", 30),
            _cita(ANA, "2026-09-07T09:00:00+00:00", 30),
        ]
    )
    datos = await huecos_del_dia(db, SALON, service_id=CORTE, fecha=LUNES, ahora=AMANECE)
    tarde = next(h for h in datos["huecos"] if h["hora"] == "17:00")
    assert [e["nombre"] for e in tarde["empleados"]] == ["Bruno", "Ana"]


# ------------------------------------------------------------- políticas


@pytest.mark.anyio
async def test_no_se_ofrece_nada_en_el_pasado():
    # Son las 15:00 locales (13:00 UTC) del propio lunes.
    ahora = datetime(2026, 9, 7, 13, 0, tzinfo=timezone.utc)
    horas = await _horas(_db(), ahora=ahora, politica=POLITICA_PANEL)
    assert "10:00" not in horas
    assert horas[0] == "15:00"


@pytest.mark.anyio
async def test_el_bot_exige_una_hora_de_antelacion():
    ahora = datetime(2026, 9, 7, 13, 0, tzinfo=timezone.utc)  # 15:00 locales
    horas = await _horas(_db(), ahora=ahora, politica=POLITICA_BOT)
    assert "15:00" not in horas
    assert "15:45" not in horas
    assert horas[0] == "16:00"


@pytest.mark.anyio
async def test_el_panel_no_tiene_antelacion_minima():
    # La recepcionista tiene que poder agendar a la clienta que tiene delante.
    ahora = datetime(2026, 9, 7, 13, 0, tzinfo=timezone.utc)
    horas = await _horas(_db(), ahora=ahora, politica=POLITICA_PANEL)
    assert horas[0] == "15:00"


@pytest.mark.anyio
async def test_el_bot_no_agenda_mas_alla_de_tres_semanas():
    lejos = (datetime.fromisoformat(LUNES + "T00:00:00+00:00") + timedelta(days=25)).date()
    horas = await _horas(_db(), fecha=lejos.isoformat(), ahora=APERTURA_LUNES)
    assert horas == []


@pytest.mark.anyio
async def test_dentro_del_horizonte_si_hay_huecos():
    cerca = (datetime.fromisoformat(LUNES + "T00:00:00+00:00") + timedelta(days=14)).date()
    horas = await _horas(_db(), fecha=cerca.isoformat(), ahora=APERTURA_LUNES)
    assert horas


@pytest.mark.anyio
async def test_una_fecha_que_no_es_una_fecha_da_error_claro():
    with pytest.raises(CitaNoValida) as e:
        await huecos_del_dia(_db(), SALON, service_id=CORTE, fecha="el sábado", ahora=AMANECE)
    assert e.value.codigo == "fecha_invalida"


# ---------------------------------------------------------- alternativas


@pytest.mark.anyio
async def test_las_alternativas_saltan_al_dia_siguiente_si_hoy_no_queda_nada():
    # Domingo cerrado: pedir alternativas desde el domingo debe dar el lunes.
    huecos = await primeros_huecos(
        _db(),
        SALON,
        service_id=CORTE,
        desde=DOMINGO,
        ahora=datetime(2026, 9, 13, 4, 0, tzinfo=timezone.utc),
        limite=2,
    )
    assert len(huecos) == 2
    assert all(h.fecha_local.isoformat() == "2026-09-14" for h in huecos)
    assert huecos[0].hora_local == "10:00"


# ═══════════════════════════════════ reparto de las alternativas
#
# Coger los tres primeros de la lista daba tres cuartos de hora seguidos
# («las 10:00, las 10:15 o las 10:30»), que a una persona le suena a máquina y
# además no es una elección: son casi la misma hora. `repartidos` es una función
# PURA, así que su parte se prueba sin tocar la base de datos.


def _hueco(hhmm: str) -> Hueco:
    h, m = (int(x) for x in hhmm.split(":"))
    ini = datetime(2026, 9, 7, h, m, tzinfo=timezone.utc)
    return Hueco(zona=ZONA_SALON, inicio=ini, fin=ini + timedelta(minutes=30), empleados=())


def _horas_de(huecos: list[Hueco]) -> list[str]:
    """Las horas de una lista de huecos. Ojo: `_horas` (arriba) es otra cosa —
    consulta un día entero contra el fake—; este no toca la base de datos."""
    return [h.inicio.strftime("%H:%M") for h in huecos]


UNA_HORA = timedelta(hours=1)


def test_las_alternativas_salen_separadas_una_hora():
    todos = [_hueco(f"{h:02d}:{m:02d}") for h in range(10, 20) for m in (0, 15, 30, 45)]
    assert _horas_de(repartidos(todos, 3, UNA_HORA)) == ["10:00", "11:00", "12:00"]


def test_el_reparto_empieza_por_lo_mas_temprano():
    # El cliente pidió una hora que no había: lo más parecido a lo que quería es
    # lo primero que quede, no un hueco de media tarde.
    todos = [_hueco(h) for h in ["10:00", "10:30", "14:00", "18:00"]]
    assert _horas_de(repartidos(todos, 3, UNA_HORA))[0] == "10:00"


def test_si_el_hueco_siguiente_no_llega_a_la_hora_se_salta():
    todos = [_hueco(h) for h in ["10:00", "10:15", "10:30", "11:45", "12:00"]]
    # 10:15 y 10:30 quedan a menos de una hora de las 10:00, así que la primera
    # que vale es 11:45. Falta una tercera y ninguna guarda la distancia: entra
    # la MÁS SEPARADA de las ya elegidas (10:30), no la siguiente de la lista
    # (10:15), que volvería a quedar pegada a las 10:00.
    assert _horas_de(repartidos(todos, 3, UNA_HORA)) == ["10:00", "10:30", "11:45"]


def test_con_pocos_huecos_se_devuelven_los_que_haya_sin_forzar_la_separacion():
    # Un día casi lleno: tres huecos sueltos y pegados. Devolver uno solo
    # «porque no guardan la distancia» sería esconder huecos reales.
    todos = [_hueco(h) for h in ["16:00", "16:15", "16:30"]]
    assert _horas_de(repartidos(todos, 3, UNA_HORA)) == ["16:00", "16:15", "16:30"]


def test_el_relleno_no_repite_ni_desordena():
    todos = [_hueco(h) for h in ["10:00", "10:15", "11:30"]]
    elegidos = _horas_de(repartidos(todos, 3, UNA_HORA))
    assert elegidos == sorted(elegidos)          # en orden, siempre
    assert len(elegidos) == len(set(elegidos))   # sin repetir
    assert elegidos == ["10:00", "10:15", "11:30"]


def test_no_se_devuelven_mas_de_los_pedidos():
    todos = [_hueco(f"{h:02d}:00") for h in range(10, 20)]
    assert len(repartidos(todos, 2, UNA_HORA)) == 2


@pytest.mark.parametrize("limite", [0, -1])
def test_pedir_cero_alternativas_no_devuelve_ninguna(limite):
    assert repartidos([_hueco("10:00")], limite, UNA_HORA) == []


def test_sin_huecos_no_hay_nada_que_repartir():
    assert repartidos([], 3, UNA_HORA) == []


# ─────────────────────────────── selección anclada a una hora
#
# Cuando el cliente HA DICHO una hora, repartir por el día es lo contrario de lo
# que le sirve: si pide la una, lo que quiere es la 13:15, no las 15:00. Y esos
# cuartos de hora de en medio son justo los que peor se llenan.


def test_se_devuelven_los_de_la_hora_pedida_en_adelante():
    # SESGO HACIA DELANTE: quien pide la una y la tiene libre quiere la una, y
    # cuarto y y media. Intercalarle las 12:45 sería adelantarle la cita sin que
    # lo haya pedido.
    todos = [_hueco(f"{h:02d}:{m:02d}") for h in range(10, 20) for m in (0, 15, 30, 45)]
    la_una = datetime(2026, 9, 7, 13, 0, tzinfo=timezone.utc)
    assert _horas_de(mas_cercanos(todos, 3, la_una)) == ["13:00", "13:15", "13:30"]


def test_se_tira_de_horas_anteriores_cuando_no_hay_posteriores_cerca():
    # Pide la una y está todo cogido hasta las tres. Lo que le sirve son las
    # 12:45, no las 15:00: hacia delante solo se prefiere lo que sigue estando
    # CERCA de lo que pidió.
    todos = [_hueco(h) for h in ["12:15", "12:30", "12:45", "15:00", "15:15"]]
    la_una = datetime(2026, 9, 7, 13, 0, tzinfo=timezone.utc)
    assert _horas_de(mas_cercanos(todos, 3, la_una)) == ["12:45", "12:30", "12:15"]


def test_se_completan_con_anteriores_si_las_posteriores_no_llegan():
    # Dos posteriores cerca y falta una tercera: se completa hacia atrás, no
    # saltando lejos hacia delante.
    todos = [_hueco(h) for h in ["12:30", "13:45", "14:00", "18:00"]]
    la_una = datetime(2026, 9, 7, 13, 0, tzinfo=timezone.utc)
    assert _horas_de(mas_cercanos(todos, 3, la_una)) == ["13:45", "14:00", "12:30"]


def test_fuera_de_la_ventana_de_cercania_manda_la_distancia_y_no_el_lado():
    # A dos y tres horas de lo que pidió, ir hacia delante ya no es mejor que ir
    # hacia atrás: gana lo que esté más cerca, mire hacia donde mire.
    todos = [_hueco(h) for h in ["11:00", "16:00"]]
    la_una = datetime(2026, 9, 7, 13, 0, tzinfo=timezone.utc)
    assert _horas_de(mas_cercanos(todos, 2, la_una)) == ["11:00", "16:00"]


def test_los_cercanos_salen_ordenados_por_cercania_y_no_por_hora():
    # El orden IMPORTA: quien lo lee ofrece los primeros, así que los primeros
    # tienen que ser los que más se parecen a lo que pidió el cliente.
    todos = [_hueco(h) for h in ["11:00", "12:45", "13:15", "16:00"]]
    la_una = datetime(2026, 9, 7, 13, 0, tzinfo=timezone.utc)
    elegidos = _horas_de(mas_cercanos(todos, 4, la_una))
    assert elegidos == ["13:15", "12:45", "11:00", "16:00"]
    assert elegidos != sorted(elegidos)


def test_lo_posterior_gana_a_lo_anterior_aunque_este_mas_lejos():
    # Dentro de la ventana de cercanía el lado manda sobre la distancia: quien
    # pide la una acepta mejor las 13:45 que las 12:45 aunque estén a 45 y a 15
    # minutos. Adelantarle la cita es pedirle algo; retrasársela, no.
    todos = [_hueco("12:45"), _hueco("13:45")]
    la_una = datetime(2026, 9, 7, 13, 0, tzinfo=timezone.utc)
    assert _horas_de(mas_cercanos(todos, 2, la_una)) == ["13:45", "12:45"]


def test_los_cercanos_valen_aunque_esten_todos_antes_o_todos_despues():
    todos = [_hueco(h) for h in ["10:00", "10:15", "10:30"]]
    tarde = datetime(2026, 9, 7, 19, 0, tzinfo=timezone.utc)
    assert _horas_de(mas_cercanos(todos, 2, tarde)) == ["10:30", "10:15"]


def test_pedir_cercanos_de_una_lista_vacia_no_revienta():
    ahora = datetime(2026, 9, 7, 13, 0, tzinfo=timezone.utc)
    assert mas_cercanos([], 3, ahora) == []
    assert mas_cercanos([_hueco("10:00")], 0, ahora) == []


@pytest.mark.anyio
async def test_el_ancla_manda_sobre_el_reparto():
    # Si se pasan las dos cosas, gana la cercanía: el reparto es para cuando NO
    # se sabe qué hora quiere el cliente.
    huecos = await primeros_huecos(
        _db(),
        SALON,
        service_id=CORTE,
        desde=LUNES,
        ahora=AMANECE,
        limite=3,
        separacion=UNA_HORA,
        ancla=datetime(2026, 9, 7, 15, 0, tzinfo=timezone.utc),  # 17:00 locales
    )
    assert [h.hora_local for h in huecos] == ["17:00", "17:15", "17:30"]


@pytest.mark.anyio
async def test_los_cercanos_tambien_son_huecos_realmente_libres():
    # 17:00–18:00 locales ocupadas para los dos: la cercanía no puede colar una
    # hora que no está libre.
    db = _db(
        appointments=[_cita(e, "2026-09-07T15:00:00+00:00", 60) for e in (ANA, BRUNO)]
    )
    huecos = await primeros_huecos(
        db,
        SALON,
        service_id=CORTE,
        desde=LUNES,
        ahora=AMANECE,
        limite=3,
        ancla=datetime(2026, 9, 7, 15, 0, tzinfo=timezone.utc),
    )
    horas = [h.hora_local for h in huecos]
    # De las 17:00 no hay nada libre, y las 16:45 tampoco valen: un corte de 30
    # min empezado ahí acabaría a las 17:15, dentro de la cita. La primera
    # posterior que está libre y cerca son las 18:00, y va delante de las 16:30
    # por el sesgo hacia delante aunque quede más lejos.
    assert horas == ["18:00", "16:30", "16:15"]
    assert not any(h.startswith("17:") for h in horas)


@pytest.mark.anyio
async def test_primeros_huecos_reparte_cuando_se_le_pide():
    huecos = await primeros_huecos(
        _db(), SALON, service_id=CORTE, desde=LUNES, ahora=AMANECE, limite=3, separacion=UNA_HORA
    )
    assert [h.hora_local for h in huecos] == ["10:00", "11:00", "12:00"]


@pytest.mark.anyio
async def test_sin_separacion_siguen_saliendo_los_primeros_seguidos():
    # El nombre de la función promete «los primeros»: quien no pida reparto los
    # sigue teniendo. La separación es cosa de quien va a OFRECERLOS.
    huecos = await primeros_huecos(
        _db(), SALON, service_id=CORTE, desde=LUNES, ahora=AMANECE, limite=3
    )
    assert [h.hora_local for h in huecos] == ["10:00", "10:15", "10:30"]


@pytest.mark.anyio
async def test_el_reparto_solo_ofrece_huecos_realmente_libres():
    # Se llena de 11:00 a 12:00 para los dos empleados: el reparto tiene que
    # saltárselo, no «separar» a ciegas cada hora.
    db = _db(
        appointments=[
            _cita(e, "2026-09-07T09:00:00+00:00", 60) for e in (ANA, BRUNO)
        ]
    )
    huecos = await primeros_huecos(
        db, SALON, service_id=CORTE, desde=LUNES, ahora=AMANECE, limite=3, separacion=UNA_HORA
    )
    horas = [h.hora_local for h in huecos]
    assert "11:00" not in horas and "11:15" not in horas
    assert horas == ["10:00", "12:00", "13:00"]


@pytest.mark.anyio
async def test_el_reparto_cruza_al_dia_siguiente_si_hace_falta():
    # Sábado: solo abre de 10:00 a 14:00, así que las tres alternativas
    # separadas caben ese mismo día. Con un día cerrado detrás, el reparto sigue
    # contando desde el hueco anterior aunque cambie la fecha.
    huecos = await primeros_huecos(
        _db(),
        SALON,
        service_id=CORTE,
        desde=SABADO,
        ahora=datetime(2026, 9, 12, 4, 0, tzinfo=timezone.utc),
        limite=3,
        separacion=UNA_HORA,
    )
    assert [h.hora_local for h in huecos] == ["10:00", "11:00", "12:00"]
    assert all(h.fecha_local.isoformat() == SABADO for h in huecos)


@pytest.mark.anyio
async def test_las_alternativas_respetan_al_profesional_pedido():
    db = _db(appointments=[_cita(BRUNO, "2026-09-07T08:00:00+00:00", 30)])
    huecos = await primeros_huecos(
        db, SALON, service_id=CORTE, desde=LUNES, employee_id=BRUNO, ahora=AMANECE, limite=1
    )
    assert huecos[0].hora_local == "10:30"


# ------------------------------------------------------- hueco concreto


@pytest.mark.anyio
async def test_hueco_concreto_dice_quien_puede_atenderlo():
    h = await hueco_concreto(
        _db(), SALON, service_id=CORTE, inicio=APERTURA_LUNES, ahora=AMANECE
    )
    assert h is not None
    assert [e.nombre for e in h.empleados] == ["Ana", "Bruno"]


@pytest.mark.anyio
async def test_hueco_concreto_devuelve_none_si_ya_no_esta_libre():
    db = _db(
        appointments=[
            _cita(ANA, "2026-09-07T08:00:00+00:00", 30),
            _cita(BRUNO, "2026-09-07T08:00:00+00:00", 30),
        ]
    )
    assert (
        await hueco_concreto(db, SALON, service_id=CORTE, inicio=APERTURA_LUNES, ahora=AMANECE)
        is None
    )


@pytest.mark.anyio
async def test_hueco_concreto_devuelve_none_a_una_hora_que_no_esta_en_la_rejilla():
    # Las 10:07 no son un hueco: la rejilla va de 15 en 15.
    raro = APERTURA_LUNES + timedelta(minutes=7)
    assert (
        await hueco_concreto(_db(), SALON, service_id=CORTE, inicio=raro, ahora=AMANECE) is None
    )
