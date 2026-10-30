"""Visitas, total gastado y «dormidas», calculados de lo que pasó.

ANTES: `clients.visitas` y `clients.total_gastado` eran columnas que la app
leía y no escribía nadie, así que la ficha decía 0 visitas a una clienta de
toda la vida, y «Dormidos» («5 visitas o menos») marcaba a todo el mundo.

AHORA se leen de la vista `clientas_con_actividad` (0058), y el doble la calcula
igual que Postgres (tests/conftest.py): estos tests siembran CITAS, no números.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.core import clientas
from app.core import zona as zonas
from app.core.clientas import (
    MESES_SIN_VENIR_PARA_DORMIDA,
    corte_de_dormida,
    restar_meses,
)
from tests.zona_de_prueba import ZONA_SALON
from app.core.security import CurrentUser, get_current_user, get_postgrest_client
from app.routers import clients
from tests.conftest import FakePostgrest

SALON = "11111111-1111-1111-1111-111111111111"
OTRO = "22222222-2222-2222-2222-222222222222"
AHORA = datetime.now(timezone.utc)


@pytest.fixture(autouse=True)
def _la_hora_de_este_test() -> None:
    """LA HORA SE TOMA AL EMPEZAR CADA TEST, no al importar el fichero. Tomada al
    importar, envejecía con la tirada: pasados 25 minutos la sesión de soporte del
    doble ya había caducado y la suite se colgaba (15-10-2026)."""
    global AHORA
    AHORA = datetime.now(timezone.utc)


def _clienta(id_: str, nombre: str, salon: str = SALON) -> dict:
    return {"id": id_, "salon_id": salon, "nombre": nombre, "telefono": f"+3460000{id_[-4:]}",
            "email": None, "cumpleanos": None, "alergias": None, "notas": None, "vip": False}


def _cita(id_: str, clienta: str, hace_dias: int, estado: str = "completada", servicio: str = "sv-corte") -> dict:
    inicio = AHORA - timedelta(days=hace_dias)
    return {"id": id_, "salon_id": SALON, "client_id": clienta, "service_id": servicio, "estado": estado,
            "inicio": inicio.isoformat(), "fin": (inicio + timedelta(minutes=45)).isoformat()}


def _db() -> FakePostgrest:
    return FakePostgrest({
        "salons": [{"id": SALON}, {"id": OTRO}],
        "clients": [
            _clienta("cl-0001", "Ana Habitual"),
            _clienta("cl-0002", "Bea Dormida"),
            _clienta("cl-0003", "Carla Nueva"),
            _clienta("cl-0004", "Ana Dormida"),
            _clienta("cl-0005", "Dora Anulada"),
            _clienta("cl-0009", "Eva de otro salón", salon=OTRO),
        ],
        "services": [
            {"id": "sv-corte", "salon_id": SALON, "precio": 25},
            {"id": "sv-tinte", "salon_id": SALON, "precio": 60.5},
        ],
        "appointments": [
            # Ana Habitual: tres atendidas, la última hace 10 días.
            _cita("ap-1", "cl-0001", 10),
            _cita("ap-2", "cl-0001", 40, servicio="sv-tinte"),
            _cita("ap-3", "cl-0001", 200),
            # Una cancelada y un no_show NO son visitas.
            _cita("ap-4", "cl-0001", 5, estado="cancelada"),
            _cita("ap-5", "cl-0001", 6, estado="no_show"),
            # Bea: vino mucho, pero hace más de tres meses.
            *[_cita(f"ap-b{i}", "cl-0002", 120 + i) for i in range(8)],
            # Ana Dormida: una sola visita, hace más de un año.
            _cita("ap-6", "cl-0004", 400),
            # Dora: solo tiene una cita reciente CANCELADA y una confirmada futura.
            _cita("ap-7", "cl-0005", 3, estado="cancelada"),
            _cita("ap-8", "cl-0005", -7, estado="confirmada"),
        ],
    })


@pytest.fixture()
def db() -> FakePostgrest:
    return _db()


def _cliente(db: FakePostgrest) -> TestClient:
    app = FastAPI()
    app.include_router(clients.router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        sub="u1", salon_id=SALON, rol="admin", email="duena@salon.test"
    )
    app.dependency_overrides[get_postgrest_client] = lambda: db
    return TestClient(app)


def _por_nombre(filas: list[dict]) -> dict[str, dict]:
    return {f["nombre"]: f for f in filas}


# ═══════════════════════════════════════════════ visitas y total gastado


def test_las_visitas_son_las_citas_atendidas(db):
    filas = _por_nombre(_cliente(db).get("/clients").json())
    assert filas["Ana Habitual"]["visitas"] == 3
    assert filas["Bea Dormida"]["visitas"] == 8
    assert filas["Carla Nueva"]["visitas"] == 0
    assert filas["Dora Anulada"]["visitas"] == 0


def test_el_total_gastado_suma_el_precio_de_lo_atendido(db):
    filas = _por_nombre(_cliente(db).get("/clients").json())
    assert filas["Ana Habitual"]["total_gastado"] == 25 + 60.5 + 25
    assert filas["Bea Dormida"]["total_gastado"] == 8 * 25
    assert filas["Carla Nueva"]["total_gastado"] == 0


def test_la_ficha_tambien_sale_de_lo_que_paso(db):
    ficha = _cliente(db).get("/clients/cl-0001").json()
    assert (ficha["visitas"], ficha["total_gastado"]) == (3, 110.5)
    assert ficha["ultima_visita"] is not None


def test_una_cita_que_se_completa_se_ve_en_la_siguiente_lectura(db):
    """Sin trigger ni copia: no hay nada que mantener al día."""
    c = _cliente(db)
    assert c.get("/clients/cl-0003").json()["visitas"] == 0
    db.tables["appointments"].append(_cita("ap-nueva", "cl-0003", 0))
    assert c.get("/clients/cl-0003").json()["visitas"] == 1


def test_editar_una_clienta_no_le_borra_las_visitas(db):
    """La fila de `clients` no trae lo calculado: el PATCH relee de la vista."""
    r = _cliente(db).patch("/clients/cl-0001", json={"notas": "Prefiere la mañana"})
    assert r.status_code == 200, r.text
    assert (r.json()["visitas"], r.json()["notas"]) == (3, "Prefiere la mañana")


def test_la_lista_NO_lee_la_tabla_clients(db):
    """CONTROL: si alguien vuelve a leer `clients`, este test lo dice."""
    leidas: list[str] = []
    original = db.select

    async def espia(tabla, params=None):
        leidas.append(tabla)
        return await original(tabla, params)

    db.select = espia
    c = _cliente(db)
    c.get("/clients")
    c.get("/clients/cl-0001")
    assert leidas and set(leidas) == {"clientas_con_actividad"}


# ═══════════════════════════════════════════════ dormidas


def test_dormida_es_sin_cita_atendida_en_tres_meses(db):
    nombres = {f["nombre"] for f in _cliente(db).get("/clients", params={"filtro": "dormidas"}).json()}
    # Bea vino 8 veces, pero hace más de tres meses: dormida aunque «muchas visitas».
    # Carla nunca vino. Dora solo tiene una cancelada y una futura: no cuentan.
    assert nombres == {"Bea Dormida", "Carla Nueva", "Ana Dormida", "Dora Anulada"}


def test_el_filtro_mira_TODAS_las_clientas_y_no_la_pagina(db):
    """Antes filtraba la página de 50 que la pantalla tenía cargada."""
    pagina = _cliente(db).get("/clients", params={"filtro": "dormidas", "limit": 2}).json()
    assert [f["nombre"] for f in pagina] == ["Ana Dormida", "Bea Dormida"]


def test_dormidas_y_busqueda_a_la_vez(db):
    filas = _cliente(db).get("/clients", params={"filtro": "dormidas", "q": "ana"}).json()
    assert [f["nombre"] for f in filas] == ["Ana Dormida"]


def test_el_filtro_no_cruza_de_salon(db):
    nombres = {f["nombre"] for f in _cliente(db).get("/clients", params={"filtro": "dormidas"}).json()}
    assert "Eva de otro salón" not in nombres


def test_un_filtro_desconocido_se_rechaza(db):
    assert _cliente(db).get("/clients", params={"filtro": "inventado"}).status_code == 422


def test_el_corte_esta_justo_en_la_frontera(db):
    """Una visita JUSTO después del corte despierta; una justo antes, no."""
    # El «hoy» del SALÓN, como la app: entre las 00:00 y las 02:00 de Madrid la
    # fecha UTC todavía es la de ayer.
    hoy = zonas.hoy(ZONA_SALON)
    corte = datetime.combine(corte_de_dormida(hoy), datetime.min.time(), ZONA_SALON)
    db.tables["appointments"] += [
        {**_cita("ap-x", "cl-0003", 0), "inicio": (corte + timedelta(minutes=1)).isoformat()},
        {**_cita("ap-y", "cl-0004", 0), "inicio": (corte - timedelta(minutes=1)).isoformat()},
    ]
    nombres = {f["nombre"] for f in _cliente(db).get("/clients", params={"filtro": "dormidas"}).json()}
    assert "Carla Nueva" not in nombres
    assert "Ana Dormida" in nombres



# ═══════════════════════════════════════════════ el número, en un solo sitio


def test_el_numero_es_tres_meses():
    assert MESES_SIN_VENIR_PARA_DORMIDA == 3
    assert corte_de_dormida(date(2026, 10, 1)) == date(2026, 7, 1)


@pytest.mark.parametrize("hoy, esperado", [
    (date(2026, 5, 31), date(2026, 2, 28)),   # febrero no tiene 31
    (date(2028, 5, 31), date(2028, 2, 29)),   # bisiesto
    (date(2026, 2, 15), date(2025, 11, 15)),  # cruza de año
    (date(2026, 1, 31), date(2025, 10, 31)),
])
def test_restar_meses_en_los_bordes(hoy, esperado):
    assert restar_meses(hoy, 3) == esperado


def test_el_router_usa_el_numero_con_nombre(db, monkeypatch):
    """CONTROL: cambiar la constante cambia el filtro. Si el router llevara un
    3 suelto, esto seguiría dando lo mismo."""
    monkeypatch.setattr(clientas, "MESES_SIN_VENIR_PARA_DORMIDA", 12)
    nombres = {f["nombre"] for f in _cliente(db).get("/clients", params={"filtro": "dormidas"}).json()}
    # Con 12 meses, Bea (hace ~4 meses) ya no está dormida; Ana Dormida (hace 400 días) sí.
    assert "Bea Dormida" not in nombres
    assert "Ana Dormida" in nombres


# ═══════════════════════════════════════════════ VIP y búsqueda, en el servidor


def _muchas(db):
    """60 clientas más, todas por delante en el orden alfabético: Ana, Bea y las
    demás quedan fuera de la primera página de 50."""
    db.tables["clients"] += [_clienta(f"cl-9{i:03d}", f"Aaa Relleno {i:02d}") for i in range(60)]
    db.tables["clients"][3]["vip"] = True  # Ana Dormida
    db.tables["clients"][2]["email"] = "carla@correo.test"  # Carla Nueva
    return db


def test_vip_mira_todas_y_no_la_pagina(db):
    filas = _cliente(_muchas(db)).get("/clients", params={"filtro": "vip", "limit": 50}).json()
    assert [f["nombre"] for f in filas] == ["Ana Dormida"]


def test_la_busqueda_encuentra_a_quien_no_esta_en_la_primera_pagina(db):
    c = _cliente(_muchas(db))
    primera = {f["nombre"] for f in c.get("/clients", params={"limit": 50}).json()}
    assert "Dora Anulada" not in primera  # está, pero en la página 2
    assert [f["nombre"] for f in c.get("/clients", params={"q": "dora", "limit": 50}).json()] == ["Dora Anulada"]


def test_la_busqueda_tambien_mira_el_email(db):
    filas = _cliente(_muchas(db)).get("/clients", params={"q": "carla@correo"}).json()
    assert [f["nombre"] for f in filas] == ["Carla Nueva"]


def test_vip_y_busqueda_a_la_vez(db):
    c = _cliente(_muchas(db))
    assert [f["nombre"] for f in c.get("/clients", params={"filtro": "vip", "q": "ana"}).json()] == ["Ana Dormida"]
    assert c.get("/clients", params={"filtro": "vip", "q": "bea"}).json() == []
