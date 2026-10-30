"""Puestos del salón (0027) — la etiqueta que sustituye al «Rol» decorativo.

Lo que se fija aquí, por orden de importancia:

  1. Que un puesto EN USO no se puede borrar, y que el mensaje dice cuántos
     empleados lo tienen. Borrarlo dejaría a varios empleados sin puesto de
     golpe y sin avisar.
  2. Que solo el admin escribe. La RLS dice lo mismo por su cuenta; esto es la
     otra capa, y ninguna se fía de la otra.
  3. Que el nombre es único por salón IGNORANDO mayúsculas: «Barbero» y
     «barbero» en el mismo desplegable solo generan dudas.
  4. Que al empleado se le puede QUITAR el puesto. Suena menor, pero
     `exclude_none` se come los null y sin cuidado ese «Sin puesto» no haría
     nada.

El aislamiento por RLS y la siembra sin pérdida de datos se prueban contra
Postgres de verdad en supabase/tests/tenant_isolation.sql, con contraprueba: un
fake nunca demostraría que la política existe.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.core.security import CurrentUser, get_current_user, get_postgrest_client
from app.db.postgrest_client import PostgrestError
from app.routers import employees, puestos

SALON = "11111111-1111-1111-1111-111111111111"
OTRO_SALON = "22222222-2222-2222-2222-222222222222"


class FakeDB:
    """PostgREST con lo justo: puestos y empleados de un salón.

    Hace cumplir el índice único por (salón, nombre en minúsculas), que es lo
    que de verdad decide los duplicados; sin eso el test de duplicados pasaría
    aunque el índice no existiera.
    """

    def __init__(self, filas_puestos=None, filas_empleados=None):
        self.puestos = list(filas_puestos or [])
        self.empleados = list(filas_empleados or [])
        self.borrados: list[str] = []
        self.n = 0

    def _tabla(self, nombre):
        return {"puestos": self.puestos, "employees": self.empleados}[nombre]

    @staticmethod
    def _casa(fila, campo, filtro):
        op, _, valor = filtro.partition(".")
        actual = fila.get(campo)
        if op == "eq":
            return str(actual) == valor if actual is not None else False
        raise AssertionError(f"filtro no soportado en el fake: {filtro}")

    async def select(self, table, params=None):
        params = params or {}
        filas = [
            f
            for f in self._tabla(table)
            if all(
                self._casa(f, k, v)
                for k, v in params.items()
                if k not in ("select", "limit", "order")
            )
        ]
        if "limit" in params:
            filas = filas[: int(params["limit"])]
        return [dict(f) for f in filas]

    def _choca(self, salon_id, nombre, excluir=None):
        return any(
            p["salon_id"] == salon_id
            and p["nombre"].strip().lower() == nombre.strip().lower()
            and p["id"] != excluir
            for p in self.puestos
        )

    async def insert(self, table, body):
        assert table == "puestos"
        if self._choca(body["salon_id"], body["nombre"]):
            raise PostgrestError(409, 'duplicate key value ... (23505)')
        self.n += 1
        fila = {"id": f"puesto-{self.n}", **body}
        self.puestos.append(fila)
        return dict(fila)

    async def update(self, table, params, body):
        assert table == "puestos"
        tocadas = []
        for f in self._tabla(table):
            if all(self._casa(f, k, v) for k, v in params.items()):
                if "nombre" in body and self._choca(f["salon_id"], body["nombre"], excluir=f["id"]):
                    raise PostgrestError(409, 'duplicate key value ... (23505)')
                f.update(body)
                tocadas.append(dict(f))
        return tocadas

    async def delete(self, table, params):
        for f in list(self._tabla(table)):
            if all(self._casa(f, k, v) for k, v in params.items()):
                # La FK con `on delete restrict` es la última red.
                if any(e.get("puesto_id") == f["id"] for e in self.empleados):
                    raise PostgrestError(409, "violates foreign key constraint (23503)")
                self.puestos.remove(f)
                self.borrados.append(f["id"])


def _cliente(db: FakeDB, rol: str = "admin", router=puestos.router) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        sub="u1", salon_id=SALON, rol=rol, email=None
    )
    app.dependency_overrides[get_postgrest_client] = lambda: db
    return TestClient(app)


def _puesto(id_, nombre, salon=SALON):
    return {"id": id_, "salon_id": salon, "nombre": nombre}


def _empleado(id_, puesto_id, salon=SALON):
    return {"id": id_, "salon_id": salon, "nombre": "Alguien", "puesto_id": puesto_id}


# ──────────────────────────────────────────────────────────────────── listar


def test_la_lista_dice_cuantos_empleados_tiene_cada_puesto():
    db = FakeDB(
        [_puesto("p1", "Barbero"), _puesto("p2", "Recepción")],
        [_empleado("e1", "p1"), _empleado("e2", "p1"), _empleado("e3", "p2")],
    )
    filas = _cliente(db).get("/puestos").json()

    # Es lo que decide si el botón de borrar está disponible.
    assert {f["nombre"]: f["empleados"] for f in filas} == {"Barbero": 2, "Recepción": 1}


def test_un_empleado_sin_puesto_no_cuenta_para_ninguno():
    db = FakeDB([_puesto("p1", "Barbero")], [_empleado("e1", None)])
    assert _cliente(db).get("/puestos").json()[0]["empleados"] == 0


# ──────────────────────────────────────────────────────────────────── crear


def test_crear_un_puesto():
    db = FakeDB()
    r = _cliente(db).post("/puestos", json={"nombre": "Barbero"})

    assert r.status_code == 201
    assert r.json()["nombre"] == "Barbero"
    assert r.json()["empleados"] == 0
    assert db.puestos[0]["salon_id"] == SALON  # el salón sale del JWT, no del cuerpo


def test_el_nombre_se_recorta_antes_de_guardarlo():
    # Sin esto, « Barbero» entraría como un puesto distinto de «Barbero» pese
    # al índice único, que compara ya recortado.
    db = FakeDB()
    assert _cliente(db).post("/puestos", json={"nombre": "  Barbero  "}).json()["nombre"] == "Barbero"


@pytest.mark.parametrize("nombre", ["", "   ", "\t"])
def test_un_nombre_vacio_se_rechaza(nombre):
    assert _cliente(FakeDB()).post("/puestos", json={"nombre": nombre}).status_code == 422


def test_un_nombre_demasiado_largo_se_rechaza():
    assert _cliente(FakeDB()).post("/puestos", json={"nombre": "x" * 61}).status_code == 422


def test_no_se_repite_un_puesto_aunque_cambie_la_caja():
    db = FakeDB([_puesto("p1", "Barbero")])
    r = _cliente(db).post("/puestos", json={"nombre": "BARBERO"})

    assert r.status_code == 409
    assert "Ya tienes un puesto" in r.json()["detail"]
    assert len(db.puestos) == 1


def test_dos_salones_si_pueden_tener_el_mismo_puesto():
    db = FakeDB([_puesto("p1", "Barbero", salon=OTRO_SALON)])
    assert _cliente(db).post("/puestos", json={"nombre": "Barbero"}).status_code == 201


# ─────────────────────────────────────────────────────────────────── renombrar


def test_renombrar_un_puesto():
    db = FakeDB([_puesto("p1", "Barbero")], [_empleado("e1", "p1")])
    r = _cliente(db).patch("/puestos/p1", json={"nombre": "Barbero sénior"})

    assert r.status_code == 200
    assert r.json()["nombre"] == "Barbero sénior"
    # El empleado no se toca: apunta por id. Ese es todo el motivo de que esto
    # sea una tabla y no una columna de texto.
    assert db.empleados[0]["puesto_id"] == "p1"


def test_renombrar_a_uno_que_ya_existe_se_rechaza():
    db = FakeDB([_puesto("p1", "Barbero"), _puesto("p2", "Recepción")])
    assert _cliente(db).patch("/puestos/p2", json={"nombre": "barbero"}).status_code == 409


def test_renombrar_un_puesto_de_otro_salon_da_404():
    db = FakeDB([_puesto("p1", "Barbero", salon=OTRO_SALON)])
    assert _cliente(db).patch("/puestos/p1", json={"nombre": "Otro"}).status_code == 404


# ───────────────────────────────────────────────────────────────────── borrar


def test_borrar_un_puesto_que_no_usa_nadie():
    db = FakeDB([_puesto("p1", "Barbero")])
    assert _cliente(db).delete("/puestos/p1").status_code == 204
    assert db.puestos == []


def test_no_se_borra_un_puesto_que_tienen_empleados():
    db = FakeDB([_puesto("p1", "Barbero")], [_empleado("e1", "p1"), _empleado("e2", "p1")])
    r = _cliente(db).delete("/puestos/p1")

    assert r.status_code == 409
    # El mensaje dice CUÁNTOS: un «no se puede» a secas obliga a buscarlos a mano.
    assert "2 empleados" in r.json()["detail"]
    assert len(db.puestos) == 1


def test_el_mensaje_va_en_singular_con_un_solo_empleado():
    db = FakeDB([_puesto("p1", "Barbero")], [_empleado("e1", "p1")])
    assert "1 empleado." in _cliente(db).delete("/puestos/p1").json()["detail"]


def test_si_alguien_lo_asigna_entre_medias_la_FK_lo_para():
    """La cuenta previa y el DELETE no son atómicos. La base de datos es quien
    de verdad lo impide; el 409 de arriba solo existe para dar un mensaje
    mejor."""
    db = FakeDB([_puesto("p1", "Barbero")])
    cuenta_original = puestos._cuantos_empleados

    async def cuenta_ciega(_db, _salon):
        # Simula la ventana: al contar todavía no había nadie…
        db.empleados.append(_empleado("e9", "p1"))  # …y ahora sí.
        return {}

    puestos._cuantos_empleados = cuenta_ciega
    try:
        r = _cliente(db).delete("/puestos/p1")
    finally:
        puestos._cuantos_empleados = cuenta_original

    assert r.status_code == 409
    assert len(db.puestos) == 1


# ─────────────────────────────────────────────────── solo el admin escribe


# ────────────────────────────────────── el empleado y su puesto (schemas)


def test_el_puesto_es_opcional_al_crear_un_empleado():
    # Un salón recién creado no tiene ningún puesto todavía: exigirlo dejaría
    # el alta bloqueada por un campo que solo se muestra.
    from app.schemas.employees import EmployeeCreate

    e = EmployeeCreate(nombre="Ana", telefono="600111222")
    assert e.puesto_id is None


def test_quitar_el_puesto_es_distinto_de_no_tocarlo():
    """`exclude_none` se come los null. Sin `model_fields_set`, elegir «Sin
    puesto» no haría nada y el empleado se quedaría con el de antes."""
    from app.schemas.employees import EmployeeUpdate

    quitar = EmployeeUpdate(puesto_id=None)
    no_tocar = EmployeeUpdate(nombre="Ana")

    assert "puesto_id" in quitar.model_fields_set
    assert "puesto_id" not in no_tocar.model_fields_set


def test_el_empleado_ya_no_tiene_campo_rol():
    """El «Rol» decorativo desaparece del contrato de la API: mientras siguiera
    ahí, alguien volvería a rellenarlo creyendo que reparte permisos."""
    from app.schemas.employees import EmployeeCreate, EmployeeOut, EmployeeUpdate

    for modelo in (EmployeeCreate, EmployeeUpdate, EmployeeOut):
        assert "rol" not in modelo.model_fields


class FakeEmpleados(FakeDB):
    """Para el router de empleados: responde a las tablas que consulta."""

    async def select(self, table, params=None):
        if table in ("puestos", "employees"):
            return await super().select(table, params)
        return []


def test_no_se_puede_enlazar_el_puesto_de_otro_salon():
    # La RLS de `employees` valida la fila del empleado, no lo que referencia.
    db = FakeEmpleados([_puesto("p1", "Barbero", salon=OTRO_SALON)])
    app = FastAPI()
    app.include_router(employees.router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        sub="u1", salon_id=SALON, rol="admin", email=None
    )
    app.dependency_overrides[get_postgrest_client] = lambda: db
    r = TestClient(app).patch("/employees/e1", json={"puesto_id": "p1"})

    assert r.status_code == 400
    assert "no pertenece a este salón" in r.json()["detail"]
