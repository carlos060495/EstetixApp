"""La página pública de reservas. Sin login, y por eso con estos tests.

LO QUE SE FIJA AQUÍ, por orden de gravedad si se rompiera:

  1. QUE NO SE FILTRE NADA. El widget entra con el token del inquilino, que lee
     el CRM entero y la agenda con notas. Lo único que lo contiene es la
     proyección campo a campo. Hay un test que mira la respuesta cruda y falla
     si aparece cualquier campo interno.
  2. QUE NO SEA UN BUSCADOR DE TELÉFONOS. Reservar con el móvil de una clienta
     ya fichada enlaza su ficha —eso es correcto— pero la confirmación debe
     devolver el nombre TECLEADO, no el guardado. Si devolviera el guardado,
     bastaría con ir probando números para cosechar la clientela del salón.
  3. QUE LAS PUERTAS CERRADAS CONTESTEN IGUAL. Slug inexistente, salón
     suspendido, widget apagado y cuenta cancelada dan el mismo 404 con el
     mismo texto.
  4. QUE EL CUERPO NO MANDE. `salon_id`, `estado` y `canal` los pone el
     servidor; mandarlos desde fuera no cambia nada.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.core.config import Settings, get_settings
from app.core.security import get_service_postgrest_client
from app.db.postgrest_client import PostgrestError
from app.routers import publico

SALON = "11111111-1111-1111-1111-111111111111"
SLUG = "salon-demo"
SERVICIO = "22222222-2222-2222-2222-222222222222"
EMP_A = "33333333-3333-3333-3333-333333333333"
EMP_B = "44444444-4444-4444-4444-444444444444"
MANANA = (datetime.now(timezone.utc) + timedelta(days=1)).replace(
    hour=10, minute=0, second=0, microsecond=0
)


@pytest.fixture(autouse=True)
def _la_hora_de_este_test() -> None:
    """LA HORA SE TOMA AL EMPEZAR CADA TEST, no al importar el fichero. Tomada al
    importar, una tirada que cruzara la medianoche dejaba el día de la cita un día
    más cerca de lo que el test cree (15-10-2026)."""
    global MANANA
    MANANA = (datetime.now(timezone.utc) + timedelta(days=1)).replace(
        hour=10, minute=0, second=0, microsecond=0
    )


def _settings() -> Settings:
    return Settings(
        supabase_url="http://local",
        supabase_secret_key="k",
        supabase_jwks_url="http://local/jwks",
        database_url="postgresql://x",
    )


@dataclass
class EmpFalso:
    id: str
    nombre: str


@dataclass
class HuecoFalso:
    inicio: datetime
    fin: datetime
    empleados: tuple


class FakeDB:
    """Doble de PostgREST. Hace de cliente de servicio Y de inquilino: lo que
    importa aquí no es cuál entra, sino qué sale por el cable."""

    def __init__(self, salon: dict | None = None, **kw):
        self.salon = salon
        self.servicios = kw.get("servicios") or [
            {"id": SERVICIO, "nombre": "Corte + barba", "precio": 28.0, "duracion_min": 45,
             # Campos internos: si alguno se cuela en la respuesta, el test lo caza.
             "visible_online": True, "category_id": "cat-1", "salon_id": SALON},
        ]
        self.clientes = kw.get("clientes") or []
        self.citas_recientes = kw.get("citas_recientes") or []
        self.citas_del_cliente = kw.get("citas_del_cliente") or []
        self.empleados = kw.get("empleados") or [
            {"id": EMP_A, "nombre": "Lucho"}, {"id": EMP_B, "nombre": "Ana"},
        ]
        self.insertado: list[tuple[str, dict]] = []
        self.actualizaciones: list[tuple[str, dict, dict]] = []
        self.revienta_insert = None

    async def select(self, table, params=None):
        p = params or {}
        if table == "salons":
            return [self.salon] if self.salon else []
        if table == "services":
            return self.servicios
        if table == "clients":
            return self.clientes
        if table == "appointments":
            # Distingue los dos contadores del anti-spam por su filtro.
            if "client_id" in p:
                return self.citas_del_cliente
            return self.citas_recientes
        if table == "employees":
            return [dict(e, salon_id=SALON, estado="activo") for e in self.empleados]
        if table == "employee_services":
            return [{"employee_id": e["id"]} for e in self.empleados]
        return []

    async def update(self, table, params, body):
        self.actualizaciones.append((table, params, body))
        return [body]

    async def insert(self, table, body):
        self.insertado.append((table, body))
        if table == "appointments" and self.revienta_insert:
            raise self.revienta_insert
        if table == "clients":
            return {"id": "cli-nuevo", **body}
        return {"id": "cita-nueva", **body}


def _salon(**kw) -> dict:
    base = {
        "zona_horaria": "Europe/Madrid",
        "id": SALON, "nombre_comercial": "Salón Demo", "logo_url": None,
        "accent_color": "#C9A84C", "direccion": "C/ Mayor 14", "telefono": "944123456",
        "suspendido": False, "reservas_online_activo": True,
        # UNA CUENTA QUE PAGA (0086): sin suscripción viva la página no reserva.
        "subscriptions": [{"estado": "active"}],
    }
    base.update(kw)
    return base


def _hueco(*emps) -> HuecoFalso:
    return HuecoFalso(
        inicio=MANANA, fin=MANANA + timedelta(minutes=45),
        empleados=tuple(EmpFalso(i, n) for i, n in (emps or ((EMP_A, "Lucho"), (EMP_B, "Ana")))),
    )


@pytest.fixture()
def motor(monkeypatch):
    """El motor de disponibilidad, simulado. Su lógica ya tiene sus propios
    tests; aquí interesa lo que el router hace con lo que devuelve."""
    estado = {"hueco": _hueco(), "dia": None}

    async def _hueco_concreto(db, salon_id, **kw):
        if isinstance(estado["hueco"], Exception):
            raise estado["hueco"]
        return estado["hueco"]

    async def _huecos_del_dia(db, salon_id, **kw):
        if isinstance(estado["dia"], Exception):
            raise estado["dia"]
        return estado["dia"] or {
            "fecha": MANANA.date().isoformat(), "cerrado": False, "motivo": None,
            "servicio": {"id": SERVICIO, "nombre": "Corte + barba", "precio": 28.0,
                         "duracion_min": 45},
            "huecos": [{
                "hora": "10:00", "inicio": MANANA.isoformat(),
                "fin": (MANANA + timedelta(minutes=45)).isoformat(),
                "empleados": [{"id": EMP_A, "nombre": "Lucho"}, {"id": EMP_B, "nombre": "Ana"}],
            }],
        }

    monkeypatch.setattr(publico, "hueco_concreto", _hueco_concreto)
    monkeypatch.setattr(publico, "huecos_del_dia", _huecos_del_dia)
    return estado


def _cliente(db: FakeDB, monkeypatch) -> TestClient:
    async def _tenant(settings, servicio, salon_id):
        return db

    monkeypatch.setattr(publico, "cliente_del_salon", _tenant)
    app = FastAPI()
    app.include_router(publico.router)
    app.dependency_overrides[get_service_postgrest_client] = lambda: db
    app.dependency_overrides[get_settings] = _settings
    return TestClient(app)


# ═══════════════════════════════════════════ las puertas cerradas


@pytest.mark.parametrize(
    "salon,caso",
    [
        (None, "el slug no existe"),
        (_salon(suspendido=True), "el salón está suspendido"),
        (_salon(reservas_online_activo=False), "el widget está apagado"),
    ],
)
def test_las_tres_puertas_cerradas_contestan_LO_MISMO(monkeypatch, salon, caso):
    """Distinguirlas ya sería contar algo. Que un desconocido pueda averiguar
    que un salón existe pero está suspendido no le sirve a él y sí a quien esté
    mirando la competencia."""
    r = _cliente(FakeDB(salon), monkeypatch).get(f"/publico/{SLUG}")

    assert r.status_code == 404, caso
    assert r.json()["detail"] == "Esta página de reservas no está disponible."


# NUNCA ACTIVADA TAMBIÉN (0086): `incomplete` y la cuenta sin suscripción ya
# no «se dejan como están»: no hay una suscripción viva.
@pytest.mark.parametrize("estado", ["canceled", "unpaid", "incomplete_expired", "incomplete", None])
def test_una_cuenta_cancelada_es_una_puerta_cerrada_mas(monkeypatch, estado):
    """Medido el 16-09-2026: una cuenta cancelada seguía admitiendo reservas
    por la web, gratis y sin fecha de fin. Contesta como las demás puertas
    cerradas, con el mismo texto: a quien reserva no le importa por qué."""
    db = FakeDB(_salon(subscriptions=[{"estado": estado}] if estado else []))
    r = _cliente(db, monkeypatch).get(f"/publico/{SLUG}")

    assert r.status_code == 404
    assert r.json()["detail"] == "Esta página de reservas no está disponible."


@pytest.mark.parametrize("estado", ["canceled", "unpaid", "incomplete_expired"])
def test_y_reservar_a_ciegas_en_una_cuenta_cancelada_tampoco_cuela(monkeypatch, motor, estado):
    db = FakeDB(_salon(subscriptions=[{"estado": estado}]))
    r = _cliente(db, monkeypatch).post(
        f"/publico/{SLUG}/reservar",
        json={"service_id": SERVICIO, "inicio": MANANA.isoformat(),
              "nombre": "María", "telefono": "600112233"},
    )
    assert r.status_code == 404
    assert db.insertado == []


@pytest.mark.parametrize(
    "subs,caso",
    [
        ([{"estado": "past_due"}], "tarjeta caducada: Stripe está reintentando"),
        ([{"estado": "canceled"}, {"estado": "active"}], "una cancelada vieja no apaga a quien paga"),
    ],
)
def test_lo_que_NO_es_una_cuenta_cancelada_sigue_reservando(monkeypatch, subs, caso):
    r = _cliente(FakeDB(_salon(subscriptions=subs)), monkeypatch).get(f"/publico/{SLUG}")
    assert r.status_code == 200, caso


def test_la_pagina_pide_el_estado_de_la_suscripcion_a_la_cuenta(monkeypatch):
    """El doble devuelve lo que le pongan, así que se comprueba también que la
    consulta PIDE la suscripción: sin esto, quitarla del `select` dejaría el
    guard mirando una clave que nunca llega, y todo pasaría."""
    pedido = {}
    db = FakeDB(_salon())
    original = db.select

    async def _select(table, params=None):
        if table == "salons":
            pedido.update(params or {})
        return await original(table, params)

    db.select = _select
    _cliente(db, monkeypatch).get(f"/publico/{SLUG}")
    assert "subscriptions(estado)" in pedido.get("select", "")


def test_reservar_en_un_salon_apagado_tampoco_cuela(monkeypatch, motor):
    """El guard va en el contexto, no solo en la pantalla: si solo estuviera al
    leer, se podría reservar a ciegas contra un salón apagado."""
    db = FakeDB(_salon(reservas_online_activo=False))
    r = _cliente(db, monkeypatch).post(
        f"/publico/{SLUG}/reservar",
        json={"service_id": SERVICIO, "inicio": MANANA.isoformat(),
              "nombre": "María", "telefono": "600112233"},
    )
    assert r.status_code == 404
    assert db.insertado == []


# ═══════════════════════════════════════════ que no se filtre nada


def test_la_pagina_NO_devuelve_ni_un_campo_interno(monkeypatch):
    """LA AFIRMACIÓN DE SEGURIDAD. Se mira el JSON crudo, no el modelo: si
    alguien devolviera la fila de PostgREST tal cual, esto lo caza."""
    cuerpo = _cliente(FakeDB(_salon()), monkeypatch).get(f"/publico/{SLUG}").json()

    crudo = str(cuerpo)
    for prohibido in ("visible_online", "category_id", "salon_id", "suspendido",
                      "reservas_online_activo", "cif", "plan", "stripe"):
        assert prohibido not in crudo, f"se ha filtrado «{prohibido}»"

    assert cuerpo["salon"]["nombre"] == "Salón Demo"
    assert set(cuerpo["servicios"][0]) == {"id", "nombre", "precio", "duracion_min"}


def test_solo_se_ofrecen_los_servicios_visibles_online(monkeypatch):
    """Contrapeso del anterior: que no se filtre `visible_online` en la salida no
    puede significar que se haya dejado de usar como FILTRO."""
    db = FakeDB(_salon())
    _cliente(db, monkeypatch).get(f"/publico/{SLUG}")
    # El router pide services con ese filtro; se comprueba interceptándolo.
    pedido = {}

    async def _select(table, params=None):
        if table == "services":
            pedido.update(params or {})
            return db.servicios
        if table == "salons":
            return [db.salon]
        return []

    db.select = _select
    _cliente(db, monkeypatch).get(f"/publico/{SLUG}")
    assert pedido.get("visible_online") == "eq.true"


def test_la_pagina_de_un_centro_NO_ofrece_los_servicios_de_su_hermano(monkeypatch):
    """LA FUGA MEDIDA. El token del widget es de la cuenta y ve los dos centros:
    la página de Salon Centro enseñaba 10 servicios, 9 de Salon Norte. Aquí el
    doble devuelve los de la cuenta entera salvo que se le pida un centro, que
    es exactamente lo que hace la base con ese token."""
    otro = "otro-centro-de-la-misma-cuenta"
    db = FakeDB(
        _salon(),
        servicios=[
            {"id": SERVICIO, "nombre": "Corte + barba", "precio": 28.0, "duracion_min": 45,
             "visible_online": True, "salon_id": SALON},
            {"id": "srv-hermano", "nombre": "Tinte del otro centro", "precio": 40.0,
             "duracion_min": 60, "visible_online": True, "salon_id": otro},
        ],
    )
    todos = db.servicios

    async def _select(table, params=None):
        p = params or {}
        if table == "services":
            centro = p.get("salon_id", "").removeprefix("eq.")
            return [s for s in todos if not centro or s["salon_id"] == centro]
        if table == "salons":
            return [db.salon]
        return []

    db.select = _select
    cuerpo = _cliente(db, monkeypatch).get(f"/publico/{SLUG}").json()

    assert [s["nombre"] for s in cuerpo["servicios"]] == ["Corte + barba"]


def test_los_profesionales_solo_traen_id_y_nombre(monkeypatch, motor):
    cuerpo = _cliente(FakeDB(_salon()), monkeypatch).get(
        f"/publico/{SLUG}/profesionales?service_id={SERVICIO}"
    ).json()

    assert cuerpo
    for p in cuerpo:
        assert set(p) == {"id", "nombre"}


# ═══════════════════════════════════════════ el selector no depende de hoy


class _PlantillaReal(FakeDB):
    """Una plantilla con de todo: quien hace el servicio, quien no, y quien está
    en pausa. Este doble SÍ aplica los filtros de estado y de servicio, que son
    justo lo que se prueba."""

    PLANTILLA = [
        {"id": EMP_A, "nombre": "Lucho", "estado": "activo", "hace": True},
        {"id": EMP_B, "nombre": "Ana", "estado": "activo", "hace": True},
        {"id": "55555555-5555-5555-5555-555555555555", "nombre": "Bea", "estado": "activo", "hace": False},
        {"id": "66666666-6666-6666-6666-666666666666", "nombre": "Carla", "estado": "pausa", "hace": True},
    ]

    async def select(self, table, params=None):
        p = params or {}
        if table == "employees":
            filas = [e for e in self.PLANTILLA if p.get("estado") in (None, f"eq.{e['estado']}")]
            if p.get("order") == "nombre.asc":
                filas = sorted(filas, key=lambda e: e["nombre"])
            return [{"id": e["id"], "nombre": e["nombre"], "persona_id": None} for e in filas]
        if table == "employee_services":
            pedidos = p.get("employee_id", "")
            return [{"employee_id": e["id"]} for e in self.PLANTILLA if e["hace"] and e["id"] in pedidos]
        return await super().select(table, params)


def test_quien_esta_LLENA_HOY_sigue_en_el_selector(monkeypatch, motor):
    """EL FALLO VIVO DESDE EL 2 DE SEPTIEMBRE. Hoy solo le queda hueco a Lucho:
    Ana tiene el día lleno. La lista salía de los huecos de hoy y el widget la
    pide UNA vez para todas las fechas, así que Ana desaparecía también para
    mañana y para dentro de un mes, con la agenda vacía."""
    llamadas = []

    async def _solo_lucho_tiene_hueco_hoy(db, salon_id, **kw):
        llamadas.append(kw)
        return {"fecha": MANANA.date().isoformat(), "cerrado": False, "motivo": None, "huecos": [
            {"hora": "18:00", "inicio": MANANA.isoformat(), "fin": MANANA.isoformat(),
             "empleados": [{"id": EMP_A, "nombre": "Lucho"}]},
        ]}

    monkeypatch.setattr(publico, "huecos_del_dia", _solo_lucho_tiene_hueco_hoy)
    r = _cliente(_PlantillaReal(_salon()), monkeypatch).get(
        f"/publico/{SLUG}/profesionales?service_id={SERVICIO}"
    )

    assert r.status_code == 200, r.text
    assert {p["nombre"] for p in r.json()} == {"Lucho", "Ana"}
    assert llamadas == [], "la lista del selector no puede preguntar por los huecos de ningún día"


def test_el_selector_aplica_el_filtro_del_motor(monkeypatch, motor):
    """Ni quien no hace el servicio (Bea) ni quien está en pausa (Carla): son las
    dos que el motor descarta, y la lista no puede enseñar a nadie que luego no
    va a tener horas. En orden alfabético, que es el del motor."""
    r = _cliente(_PlantillaReal(_salon()), monkeypatch).get(
        f"/publico/{SLUG}/profesionales?service_id={SERVICIO}"
    )

    assert [p["nombre"] for p in r.json()] == ["Ana", "Lucho"]


def test_el_selector_de_un_servicio_ajeno_contesta_como_una_puerta_cerrada(monkeypatch, motor):
    """Un servicio que no es de este salón da el mismo 404 que un salón que no
    existe. Antes lo decía el motor a través de los huecos de hoy; ahora lo dice
    la misma carga del servicio, sin pasar por ningún día."""
    db = _PlantillaReal(_salon())
    db.servicios = []  # por constructor no: `servicios=[]` cae en el valor por defecto
    r = _cliente(db, monkeypatch).get(
        f"/publico/{SLUG}/profesionales?service_id={SERVICIO}"
    )

    assert r.status_code == 404
    assert r.json()["detail"] == "Esta página de reservas no está disponible."


def test_la_disponibilidad_no_dice_de_quien_es_cada_cita(monkeypatch, motor):
    """Un hueco dice cuándo hay sitio. No puede decir quién ocupa el resto."""
    cuerpo = _cliente(FakeDB(_salon()), monkeypatch).get(
        f"/publico/{SLUG}/disponibilidad?service_id={SERVICIO}&fecha={MANANA.date()}"
    ).json()

    assert set(cuerpo) == {"fecha", "cerrado", "motivo", "huecos"}
    assert set(cuerpo["huecos"][0]) == {"inicio", "fin", "hora", "empleados"}


# ═══════════════════════════════════════════ el oráculo de teléfonos


def test_la_confirmacion_devuelve_el_nombre_TECLEADO_y_no_el_del_CRM(monkeypatch, motor):
    """EL TEST QUE IMPIDE COSECHAR LA CLIENTELA.

    El teléfono ya está fichado como «María Fernández Ruiz». La reserva enlaza
    ESA ficha —correcto, no se duplica el CRM— pero la respuesta debe repetir lo
    que la persona escribió. Si devolviera el nombre guardado, cualquiera podría
    ir probando números y sacar la lista de clientes del salón.
    """
    db = FakeDB(_salon(), clientes=[{"id": "cli-1", "nombre": "María Fernández Ruiz"}])
    r = _cliente(db, monkeypatch).post(
        f"/publico/{SLUG}/reservar",
        json={"service_id": SERVICIO, "inicio": MANANA.isoformat(),
              "nombre": "Pepe", "telefono": "600112233"},
    )

    assert r.status_code == 201
    assert r.json()["nombre"] == "Pepe"
    assert "Fernández" not in str(r.json())
    # Y sin embargo la cita SÍ se enlaza a la ficha existente.
    tabla, cuerpo = db.insertado[0]
    assert tabla == "appointments" and cuerpo["client_id"] == "cli-1"


def test_la_cita_guarda_el_nombre_de_QUIEN_RESERVA_para_que_el_salon_lo_vea(monkeypatch, motor):
    """EL BUG DE PRODUCTO: una madre reserva para su hija desde su propio móvil.

    El teléfono está fichado como «Carlos». La cita se enlaza a esa ficha —bien,
    es su teléfono y su historial— pero el salón acababa viendo «Carlos» en la
    agenda y recibiendo a Marisol. Quien reservó escribió un nombre y ese nombre
    no llegaba a ninguna parte.

    Las TRES cosas a la vez, que es lo que hace que el arreglo no rompa nada:
    """
    db = FakeDB(_salon(), clientes=[{"id": "cli-1", "nombre": "Carlos"}])
    r = _cliente(db, monkeypatch).post(
        f"/publico/{SLUG}/reservar",
        json={"service_id": SERVICIO, "inicio": MANANA.isoformat(),
              "nombre": "Marisol Luna", "telefono": "600112233"},
    )

    assert r.status_code == 201
    # 1 · La confirmación PÚBLICA sigue diciendo solo lo que escribió. Ni rastro
    #     del nombre de la ficha: eso sería el oráculo de siempre.
    assert r.json()["nombre"] == "Marisol Luna"
    assert "Carlos" not in str(r.json())

    _tabla, cuerpo = db.insertado[-1]
    # 2 · La cita queda enlazada a la ficha del teléfono: continuidad de CRM.
    assert cuerpo["client_id"] == "cli-1"
    # 3 · Y el nombre dado viaja en la CITA, que es lo que el panel va a pintar.
    assert cuerpo["nombre_reserva"] == "Marisol Luna"


def test_reservar_NO_renombra_la_ficha_del_cliente(monkeypatch, motor):
    """La otra mitad, y la que protege al salón: si el nombre dado sobrescribiera
    el de la ficha, cualquiera que conozca un teléfono podría renombrar a un
    cliente desde una página pública."""
    db = FakeDB(_salon(), clientes=[{"id": "cli-1", "nombre": "Carlos"}])
    _cliente(db, monkeypatch).post(
        f"/publico/{SLUG}/reservar",
        json={"service_id": SERVICIO, "inicio": MANANA.isoformat(),
              "nombre": "Marisol Luna", "telefono": "600112233"},
    )

    # Ni un UPDATE ni un INSERT sobre `clients`: la ficha existente se reutiliza
    # tal cual.
    assert [t for t, _ in db.insertado] == ["appointments"]


def test_con_ficha_nueva_el_nombre_se_guarda_en_los_dos_sitios(monkeypatch, motor):
    """Contrapeso: cuando NO hay ficha previa, el nombre dado es el de la ficha
    Y el de la reserva. No son datos en conflicto, solo dos usos del mismo."""
    db = FakeDB(_salon(), clientes=[])
    _cliente(db, monkeypatch).post(
        f"/publico/{SLUG}/reservar",
        json={"service_id": SERVICIO, "inicio": MANANA.isoformat(),
              "nombre": "Marisol Luna", "telefono": "600112233"},
    )

    escrituras = dict(db.insertado)
    assert escrituras["clients"]["nombre"] == "Marisol Luna"
    assert escrituras["appointments"]["nombre_reserva"] == "Marisol Luna"


# ═══════════════════════════════════════════ la escritura


def test_la_cita_se_crea_confirmada_y_por_el_canal_online(monkeypatch, motor):
    db = FakeDB(_salon())
    r = _cliente(db, monkeypatch).post(
        f"/publico/{SLUG}/reservar",
        json={"service_id": SERVICIO, "inicio": MANANA.isoformat(),
              "nombre": "María", "telefono": "600112233"},
    )

    assert r.status_code == 201
    _tabla, cuerpo = db.insertado[-1]
    assert cuerpo["estado"] == "confirmada"
    assert cuerpo["canal"] == "online"
    assert cuerpo["salon_id"] == SALON


def test_el_cuerpo_NO_puede_imponer_salon_estado_ni_canal(monkeypatch, motor):
    """Que esos tres los ponga el servidor es lo que impide escribir en el salón
    de al lado o colar una cita marcada como ya pagada."""
    db = FakeDB(_salon())
    r = _cliente(db, monkeypatch).post(
        f"/publico/{SLUG}/reservar",
        json={"service_id": SERVICIO, "inicio": MANANA.isoformat(),
              "nombre": "María", "telefono": "600112233",
              "salon_id": "99999999-9999-9999-9999-999999999999",
              "estado": "cancelada", "canal": "manual", "notas": "inyectado"},
    )

    assert r.status_code == 201
    _tabla, cuerpo = db.insertado[-1]
    assert cuerpo["salon_id"] == SALON
    assert cuerpo["estado"] == "confirmada"
    assert cuerpo["canal"] == "online"
    assert cuerpo["notas"] is None


def test_sin_profesional_se_asigna_el_primero_del_hueco(monkeypatch, motor):
    """«Cualquiera disponible». El motor devuelve los empleados ordenados por
    carga del día, así que el primero es el que menos citas tiene."""
    motor["hueco"] = _hueco((EMP_B, "Ana"), (EMP_A, "Lucho"))
    db = FakeDB(_salon())
    r = _cliente(db, monkeypatch).post(
        f"/publico/{SLUG}/reservar",
        json={"service_id": SERVICIO, "inicio": MANANA.isoformat(),
              "nombre": "María", "telefono": "600112233"},
    )

    assert r.json()["profesional"] == "Ana"
    assert db.insertado[-1][1]["employee_id"] == EMP_B


def test_con_los_recordatorios_APAGADOS_el_email_se_ignora(monkeypatch, motor):
    """El interruptor del salón manda. Si no ha pedido recordatorios, no se le
    guarda a nadie un correo que no va a usar."""
    db = FakeDB(_salon(), clientes=[{"id": "cli-1", "nombre": "María"}])
    r = _cliente(db, monkeypatch).post(
        f"/publico/{SLUG}/reservar",
        json={"service_id": SERVICIO, "inicio": MANANA.isoformat(),
              "nombre": "María", "telefono": "600112233", "email": "maria@correo.es"},
    )

    assert r.status_code == 201
    assert "maria@correo.es" not in str(db.actualizaciones)


def test_con_los_recordatorios_ENCENDIDOS_el_email_se_guarda(monkeypatch, motor):
    """La captación: quien no está en el CRM deja su correo al reservar."""
    db = FakeDB(
        _salon(recordatorios_email_activo=True),
        clientes=[{"id": "cli-1", "nombre": "María"}],
    )
    r = _cliente(db, monkeypatch).post(
        f"/publico/{SLUG}/reservar",
        json={"service_id": SERVICIO, "inicio": MANANA.isoformat(),
              "nombre": "María", "telefono": "600112233", "email": "  Maria@Correo.ES  "},
    )

    assert r.status_code == 201
    tabla, params, body = db.actualizaciones[-1]
    assert tabla == "clients"
    # NORMALIZADO POR EL MISMO SITIO QUE EL DEL BOT: fuera los espacios que
    # arrastra un teclado de móvil y el dominio en minúsculas. La parte de
    # delante de la arroba se respeta tal cual: quien decide si distingue
    # mayúsculas es el servidor de correo, no nosotros.
    assert body == {"email": "Maria@correo.es"}
    # NUNCA ENCIMA DE UNO QUE YA HAYA: el del CRM lo puso el salón, y quien
    # reserva puede teclear mal. La condición va en el propio UPDATE.
    assert params["email"] == "is.null"


def test_un_email_que_no_es_un_email_NO_llega_al_CRM(monkeypatch, motor):
    """Y LA RESERVA SE HACE IGUAL, que es la mitad de la afirmación.

    Antes el widget guardaba cualquier cadena de 120 caracteres: el
    `type="email"` del navegador era toda la defensa. Una dirección inventada no
    da un error visible —da un recordatorio que sale, rebota y queda anotado
    semanas después—, así que se valida en el servidor. Pero perder la cita por
    una errata en un campo opcional sería mucho peor que perder el correo.
    """
    db = FakeDB(
        _salon(recordatorios_email_activo=True),
        clientes=[{"id": "cli-1", "nombre": "María"}],
    )
    r = _cliente(db, monkeypatch).post(
        f"/publico/{SLUG}/reservar",
        json={"service_id": SERVICIO, "inicio": MANANA.isoformat(),
              "nombre": "María", "telefono": "600112233", "email": "maria@correo"},
    )

    assert r.status_code == 201
    assert db.insertado[-1][0] == "appointments"
    assert "maria@correo" not in str(db.actualizaciones)


def test_un_telefono_ilegible_se_rechaza_antes_de_tocar_la_agenda(monkeypatch, motor):
    db = FakeDB(_salon())
    r = _cliente(db, monkeypatch).post(
        f"/publico/{SLUG}/reservar",
        json={"service_id": SERVICIO, "inicio": MANANA.isoformat(),
              "nombre": "María", "telefono": "no-es-un-telefono"},
    )
    assert r.status_code == 400
    assert db.insertado == []


# ═══════════════════════════════════════════ la carrera por el hueco


def test_si_el_hueco_ya_no_esta_se_dice_sin_crear_nada(monkeypatch, motor):
    motor["hueco"] = None
    db = FakeDB(_salon())
    r = _cliente(db, monkeypatch).post(
        f"/publico/{SLUG}/reservar",
        json={"service_id": SERVICIO, "inicio": MANANA.isoformat(),
              "nombre": "María", "telefono": "600112233"},
    )

    assert r.status_code == 409
    assert db.insertado == []


def test_si_pierde_la_carrera_contra_el_candado_tambien_es_409(monkeypatch, motor):
    """El caso que la comprobación previa NO puede cubrir: entre mirar e
    insertar cabe otra petición entera. Lo para el EXCLUDE de la 0030."""
    db = FakeDB(_salon())
    db.revienta_insert = PostgrestError(
        409, 'conflicting key value violates exclusion constraint "appointments_sin_solape"'
    )
    r = _cliente(db, monkeypatch).post(
        f"/publico/{SLUG}/reservar",
        json={"service_id": SERVICIO, "inicio": MANANA.isoformat(),
              "nombre": "María", "telefono": "600112233"},
    )

    assert r.status_code == 409
    assert "acaba de ocuparse" in r.json()["detail"]


# ═══════════════════════════════════════════ anti-spam


def test_un_telefono_que_ya_reservo_de_mas_recibe_429(monkeypatch, motor):
    db = FakeDB(
        _salon(),
        clientes=[{"id": "cli-1", "nombre": "María"}],
        citas_del_cliente=[{"id": f"c{i}"} for i in range(publico.MAX_POR_TELEFONO + 1)],
    )
    r = _cliente(db, monkeypatch).post(
        f"/publico/{SLUG}/reservar",
        json={"service_id": SERVICIO, "inicio": MANANA.isoformat(),
              "nombre": "María", "telefono": "600112233"},
    )

    assert r.status_code == 429
    assert db.insertado == []


def test_una_rafaga_en_el_salon_tambien_frena_aunque_cambien_el_telefono(monkeypatch, motor):
    """El contador por teléfono no ve un script que va cambiando el número. Este
    sí, porque cuenta las reservas online recientes del salón vengan de donde
    vengan."""
    db = FakeDB(
        _salon(),
        citas_recientes=[{"id": f"c{i}"} for i in range(publico.MAX_POR_SALON + 1)],
    )
    r = _cliente(db, monkeypatch).post(
        f"/publico/{SLUG}/reservar",
        json={"service_id": SERVICIO, "inicio": MANANA.isoformat(),
              "nombre": "Otra", "telefono": "600999888"},
    )

    assert r.status_code == 429
    assert db.insertado == []


def test_por_debajo_del_tope_se_reserva_con_normalidad(monkeypatch, motor):
    """Contrapeso: el freno no puede estar siempre puesto."""
    db = FakeDB(
        _salon(),
        clientes=[{"id": "cli-1", "nombre": "María"}],
        citas_del_cliente=[{"id": "c1"}],
        citas_recientes=[{"id": "c1"}, {"id": "c2"}],
    )
    r = _cliente(db, monkeypatch).post(
        f"/publico/{SLUG}/reservar",
        json={"service_id": SERVICIO, "inicio": MANANA.isoformat(),
              "nombre": "María", "telefono": "600112233"},
    )

    assert r.status_code == 201
