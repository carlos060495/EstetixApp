"""Abrir el segundo centro: solo Elite, solo dos, y solo un administrador.

Y el listado, que es donde vive la propiedad importante: el selector no filtra
nada: lo filtra la RLS. Este fichero comprueba que el endpoint no añade ningún
filtro propio —porque el día que alguien lo «arregle» quitándolo, no habría
nada que quitar— y que el límite se cuenta sobre la CUENTA.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.core.security import (
    CurrentUser,
    get_current_user,
    get_postgrest_client,
    get_service_postgrest_client,
)
from app.routers import centros

CUENTA = "cuenta-a"
CENTRO_1 = "00000000-0000-0000-0000-000000000001"
CENTRO_2 = "0b0b0b0b-0000-0000-0000-00000000000b"


class FakeDB:
    """El cliente de INQUILINO ve solo lo que la RLS deja ver; el de servicio, todo."""

    def __init__(self, *, plan="elite", de_la_cuenta=1, visibles=None):
        self.plan = plan
        self.de_la_cuenta = de_la_cuenta
        self.visibles = visibles if visibles is not None else [
            {"zona_horaria": "Europe/Madrid", "id": CENTRO_1, "nombre_comercial": "Salón Demo", "slug": "salon-demo"}
        ]
        self.consultas: list[tuple] = []
        self.escrituras: list[tuple] = []
        self.servicios = [
            {"nombre": "Corte", "precio": 25, "duracion_min": 30,
             "visible_online": True, "category_id": "cat-1"},
        ]

    async def select(self, table, params=None):
        self.consultas.append((table, dict(params or {})))
        params = params or {}
        if table == "salons" and "account_id" in params and "id" not in params:
            return [{"id": f"c{i}"} for i in range(self.de_la_cuenta)]
        if table == "salons" and "id" in params and "account_id" in params:
            return [{"id": CENTRO_1}]
        if table == "salons":
            # Sin filtros: es el listado del selector, y lo acota la RLS.
            return list(self.visibles)
        if table == "service_categories":
            return [{"id": "cat-1", "nombre": "Peluquería", "tipo": "servicio"}]
        if table == "services":
            return list(self.servicios)
        return []

    async def insert(self, table, body):
        self.escrituras.append((table, dict(body)))
        if table == "salons":
            return {"id": "centro-nuevo", **body}
        return {"id": f"{table}-nuevo", **body}

    async def insert_many(self, table, rows):
        self.escrituras.append((table, list(rows)))

    async def update(self, table, params, body):
        self.escrituras.append((table, dict(body)))
        return [body]


def _alta(nombre: str, **extra) -> dict:
    """El cuerpo mínimo para abrir un centro.

    DIRECCIÓN Y TELÉFONO SON OBLIGATORIOS desde el aterrizaje del 20-09-2026:
    San Sebastián se creó solo con nombre y su página pública no sabía decirle a
    una clienta ni adónde ir ni por dónde llamar. Va en un ayudante para que
    añadir un campo obligatorio mañana no sea reescribir siete llamadas.
    """
    return {
        "nombre_comercial": nombre,
        "direccion": "Calle Mayor 1, Donostia",
        "telefono": "600 11 22 33",
        **extra,
    }


def _cliente(db: FakeDB, rol="admin") -> TestClient:
    app = FastAPI()
    app.include_router(centros.router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        sub="u1", salon_id=CENTRO_1, rol=rol, email=None
    )
    app.dependency_overrides[get_postgrest_client] = lambda: db
    app.dependency_overrides[get_service_postgrest_client] = lambda: db
    return TestClient(app)


def _con_plan(db: FakeDB):
    """`leer_cuenta` va por el embed de `salons`; se le da lo que pide."""
    original = db.select

    async def select(table, params=None):
        if table == "salons" and (params or {}).get("select", "").startswith("account_id,accounts"):
            return [{"account_id": CUENTA, "accounts": {"plan": db.plan}}]
        return await original(table, params)

    db.select = select
    return db


# ══════════════════════════════ el listado

def test_el_listado_no_lleva_ningun_filtro_mio():
    """LA PROPIEDAD: el selector solo puede enseñar los centros de esa
    persona, y no porque el backend lo filtre — porque la RLS ya lo hace.

    Si este endpoint añadiera un `account_id` o un `user_id`, sería un filtro
    que alguien puede quitar sin querer. No hay ninguno: la consulta va sin
    `where`, y lo que vuelve es lo que `salones_visibles()` deja pasar.
    """
    db = FakeDB()
    _cliente(db).get("/salon/centros")

    tabla, params = db.consultas[0]
    assert tabla == "salons"
    assert set(params) <= {"select", "order"}, f"el listado lleva filtros propios: {params}"


def test_el_activo_lo_dice_el_backend():
    db = FakeDB()
    r = _cliente(db).get("/salon/centros").json()
    assert r["activo"] == CENTRO_1


# ══════════════════════════════ abrir el segundo

@pytest.mark.parametrize("plan", ["basico", "pro"])
def test_sin_elite_no_se_abre_un_segundo_centro(plan):
    db = _con_plan(FakeDB(plan=plan, de_la_cuenta=1))
    r = _cliente(db).post("/salon/centros", json=_alta("Centro Norte"))

    assert r.status_code == 409
    assert "Elite" in r.json()["detail"]
    assert not [t for t, _ in db.escrituras if t == "salons"]


def test_elite_abre_el_segundo():
    db = _con_plan(FakeDB(plan="elite", de_la_cuenta=1))
    r = _cliente(db).post("/salon/centros", json=_alta("Centro Norte"))

    assert r.status_code == 201, r.text
    creado = [b for t, b in db.escrituras if t == "salons"][0]
    assert creado["account_id"] == CUENTA
    assert creado["slug"] == "centro-norte"


def test_elite_NO_abre_el_tercero():
    """Dos es dos. El control del test de arriba."""
    db = _con_plan(FakeDB(plan="elite", de_la_cuenta=2))
    r = _cliente(db).post("/salon/centros", json=_alta("Centro Sur"))

    assert r.status_code == 409
    assert not [t for t, _ in db.escrituras if t == "salons"]


def test_el_limite_se_cuenta_sobre_la_CUENTA_no_sobre_lo_que_uno_ve():
    """Se cuentan los centros de la CUENTA, no los que devuelve la consulta de
    quien pregunta: lo segundo sería contar permisos, no centros."""
    db = _con_plan(FakeDB(plan="elite", de_la_cuenta=2))
    _cliente(db).post("/salon/centros", json=_alta("Centro Sur"))

    cuenta = [p for t, p in db.consultas if t == "salons" and "account_id" in p and "id" not in p]
    assert cuenta, "no se contó por cuenta"


# ══════════════════════════════ copiar el catálogo

def test_copiar_servicios_los_copia_UNA_vez():
    """Copiar, no sincronizar: a partir de aquí cada catálogo va por su lado."""
    db = _con_plan(FakeDB(plan="elite", de_la_cuenta=1))
    r = _cliente(db).post(
        "/salon/centros",
        json=_alta("Centro Norte", copiar_servicios_de=CENTRO_1),
    )

    assert r.status_code == 201, r.text
    copiados = [b for t, b in db.escrituras if t == "services"][0]
    assert copiados[0]["salon_id"] == "centro-nuevo"
    assert copiados[0]["precio"] == 25
    # La categoría se recrea en el centro nuevo: copiar los servicios sin ellas
    # dejaría el catálogo sin ordenar.
    assert copiados[0]["category_id"] == "service_categories-nuevo"


def test_sin_pedir_copia_el_catalogo_nace_vacio():
    db = _con_plan(FakeDB(plan="elite", de_la_cuenta=1))
    _cliente(db).post("/salon/centros", json=_alta("Centro Norte"))

    assert not [t for t, _ in db.escrituras if t == "services"]


# ══════════════════════════ lo que un centro necesita para atender a alguien
#
# EL CASO REAL, del aterrizaje del 20-09-2026: San Sebastián se creó con un
# nombre y nada más. Su página pública le decía a una clienta «escríbenos por
# otro medio o pásate por el salón» sin decir por qué medio ni adónde, y el bot
# tampoco tenía nada que darle cuando no podía ayudar.


@pytest.mark.parametrize("falta", ["direccion", "telefono"])
def test_sin_direccion_o_sin_telefono_NO_se_abre_un_centro(falta):
    db = _con_plan(FakeDB(plan="elite", de_la_cuenta=1))
    cuerpo = _alta("Centro Norte")
    cuerpo.pop(falta)

    r = _cliente(db).post("/salon/centros", json=cuerpo)

    assert r.status_code == 422, r.text
    assert not [t for t, _ in db.escrituras if t == "salons"], "se creó a medias"


@pytest.mark.parametrize("vacio", ["", "   ", "C/"])
def test_una_direccion_que_no_lleva_a_ninguna_parte_tampoco_vale(vacio):
    """Ni vacía ni de tres letras: el caso de uso es una clienta que no ha
    pisado nunca el local y llega por un QR de Instagram."""
    db = _con_plan(FakeDB(plan="elite", de_la_cuenta=1))

    r = _cliente(db).post("/salon/centros", json=_alta("Centro Norte", direccion=vacio))

    assert r.status_code == 422


def test_un_telefono_QUE_NO_SE_PUEDE_MARCAR_se_rechaza_con_su_motivo():
    """La longitud la mira el esquema; que sea marcable de verdad lo decide
    `core/telefonos`, como en el perfil del salón. Y el motivo viaja con CÓDIGO
    para que el formulario lo ponga debajo del campo."""
    db = _con_plan(FakeDB(plan="elite", de_la_cuenta=1))

    r = _cliente(db).post("/salon/centros", json=_alta("Centro Norte", telefono="600 11"))

    assert r.status_code == 422, r.text
    assert r.json()["detail"]["codigo"] == "telefono_no_valido"
    assert r.json()["detail"]["mensaje"]


def test_el_telefono_se_guarda_en_E164_y_la_direccion_limpia():
    """Igual que en el perfil: se guarda canónico para que el `tel:` del correo
    y el del widget marquen, escriba la gente como escriba."""
    db = _con_plan(FakeDB(plan="elite", de_la_cuenta=1))

    r = _cliente(db).post(
        "/salon/centros",
        json=_alta("Centro Norte", telefono="600 11 22 33", direccion="  Calle Mayor 1  "),
    )

    assert r.status_code == 201, r.text
    (_, fila), = [(t, f) for t, f in db.escrituras if t == "salons"]
    assert fila["telefono"] == "+34600112233"
    assert fila["direccion"] == "Calle Mayor 1"


def test_el_listado_dice_A_QUE_CENTRO_le_falta_el_contacto():
    """Para los que YA existen sin esos datos. Es un AVISO, no un bloqueo: un
    salón que no puede entrar a media mañana porque le exigimos un dato nuestro
    es peor problema que el que arregla."""
    db = FakeDB(
        visibles=[
            {"zona_horaria": "Europe/Madrid", "id": CENTRO_1, "nombre_comercial": "Completo", "slug": "a",
             "direccion": "Calle Mayor 1", "telefono": "+34600112233"},
            {"zona_horaria": "Europe/Madrid", "id": CENTRO_2, "nombre_comercial": "San Sebastian", "slug": "b",
             "direccion": None, "telefono": None},
        ]
    )

    centros = _cliente(db).get("/salon/centros").json()["centros"]
    por_id = {c["id"]: c for c in centros}

    assert por_id[CENTRO_1]["falta_contacto"] is False
    assert por_id[CENTRO_2]["falta_contacto"] is True
    # Y NO se devuelven los datos en sí: la barra solo necesita saber si faltan.
    assert "telefono" not in por_id[CENTRO_2]
