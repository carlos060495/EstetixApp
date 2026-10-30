"""El estado del asistente: la pantalla no puede decir «próximamente» de algo
que ya funciona, ni «conecta tu WhatsApp» a quien ya lo conectó.

EL BUG QUE ESTO CIERRA: la página «Asistente IA» enseñaba un «Próximamente» con
el bot construido y atendiendo clientes. No era un texto desactualizado sino un
agujero real: no había forma de preguntar desde el frontend si el salón tenía su
número conectado, porque `salon_whatsapp_config` solo la leían por dentro el
webhook y la bandeja.

TRES ESTADOS Y NO DOS, que es lo que se fija aquí:

  · sin filas          → nunca conectado. Vía manual.
  · filas apagadas     → dado de alta pero inactivo. NO es lo mismo, y tratarlo
                         como «no conectado» mandaría a repetir un trámite que
                         hace soporte a mano.
  · alguna activa      → el bot está atendiendo.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.core.security import CurrentUser, get_current_user, get_postgrest_client
from app.db.postgrest_client import PostgrestError
from app.routers import bot_asistente

SALON = "11111111-1111-1111-1111-111111111111"


class FakeDB:
    def __init__(self, filas: list[dict] | None = None, plan: str = "pro", revienta=False):
        self.filas = filas if filas is not None else []
        self.plan = plan
        self.revienta = revienta
        self.params: dict = {}

    async def select(self, table, params=None):
        if table == "salons":
            # El plan viene por el embed de la cuenta desde la 0040.
            return [{"account_id": "cuenta-1", "accounts": {"plan": self.plan}}]
        if table == "salon_whatsapp_config":
            if self.revienta:
                raise PostgrestError(500, "PostgREST caído")
            self.params = params or {}
            return self.filas
        return []


def _cliente(db: FakeDB, rol: str = "admin") -> TestClient:
    app = FastAPI()
    app.include_router(bot_asistente.router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        sub="u1", salon_id=SALON, rol=rol, email="duena@salon.test"
    )
    app.dependency_overrides[get_postgrest_client] = lambda: db
    return TestClient(app)


def _fila(numero="+34 600 11 22 33", activo=True, **mas) -> dict:
    return {"id": f"cfg-{numero}", "phone_number_id": "1", "display_phone_number": numero, "is_active": activo, **mas}


def _num(n: dict) -> dict:
    return {"numero": n["numero"], "activo": n["activo"]}


# ═══════════════════════════════════════════════ los tres estados


def test_sin_numero_no_esta_conectado():
    r = _cliente(FakeDB([])).get("/bot/estado")

    assert r.status_code == 200
    assert {k: r.json()[k] for k in ("estado", "conectado", "activo", "numeros")} == {
        "estado": "sin_conectar", "conectado": False, "activo": False, "numeros": []}


def test_con_numero_activo_el_asistente_esta_funcionando():
    r = _cliente(FakeDB([_fila()])).get("/bot/estado")

    cuerpo = r.json()
    assert cuerpo["conectado"] is True
    assert cuerpo["activo"] is True
    assert [_num(n) for n in cuerpo["numeros"]] == [{"numero": "+34 600 11 22 33", "activo": True}]
    assert cuerpo["estado"] == "atendiendo"


def test_un_numero_APAGADO_sigue_estando_conectado():
    """LA DISTINCIÓN QUE IMPORTA. Si esto devolviera `conectado: False`, la
    pantalla le diría «conecta tu WhatsApp» a un salón que ya lo tiene dado de
    alta, y lo mandaría a pedirle a soporte algo que ya hizo."""
    cuerpo = _cliente(FakeDB([_fila(activo=False)])).get("/bot/estado").json()

    assert cuerpo["conectado"] is True
    assert cuerpo["activo"] is False
    assert cuerpo["estado"] == "apagado"


def test_con_problema_manda_y_dice_que_pasa_desde_cuando_y_que_hacer():
    """0085: el estado que no existía. Meta rechaza los envíos y el salón lo ve."""
    fila = _fila(problema_desde="2026-10-12T10:00:00+00:00", problema_codigo="190",
                 problema_detalle="Error validating access token")
    cuerpo = _cliente(FakeDB([fila])).get("/bot/estado").json()

    assert cuerpo["estado"] == "con_problema"
    p = cuerpo["numeros"][0]["problema"]
    assert p["desde"].startswith("2026-10-12") and p["codigo"] == "190"
    assert "caducado" in p["que_pasa"] and "vuelve a conectarlo" in p["que_hacer"]


# ═══════════════════════════════════════════════ dos sedes


def test_con_dos_sedes_se_devuelven_LAS_DOS():
    """La 0024 permite varias filas por salón a propósito (dos sedes, dos
    números). Enseñar solo la primera le escondería al salón que su segunda sede
    está sin conectar."""
    db = FakeDB([_fila("+34 600 11 22 33", True), _fila("+34 610 99 88 77", False)])
    cuerpo = _cliente(db).get("/bot/estado").json()

    assert len(cuerpo["numeros"]) == 2
    # `activo` es «hay ALGUNA funcionando», no «están todas».
    assert cuerpo["activo"] is True
    assert [n["activo"] for n in cuerpo["numeros"]] == [True, False]


def test_un_numero_sin_display_no_se_inventa():
    """Meta no siempre manda `display_phone_number` y la 0024 lo permite nulo.
    La pantalla enseña el hueco; fabricar un número sería peor que no tenerlo."""
    cuerpo = _cliente(FakeDB([_fila(None)])).get("/bot/estado").json()

    assert [_num(n) for n in cuerpo["numeros"]] == [{"numero": None, "activo": True}]
    assert cuerpo["conectado"] is True


# ═══════════════════════════════════════════════ quién puede mirar


def test_un_plan_que_no_llega_recibe_402():
    """El muro del frontend es solo UX; quien de verdad lo impide es esto."""
    r = _cliente(FakeDB([_fila()], plan="basico")).get("/bot/estado")

    assert r.status_code == 402


def test_el_plan_superior_tambien_entra():
    assert _cliente(FakeDB([_fila()], plan="elite")).get("/bot/estado").status_code == 200


# ═══════════════════════════════════════════════ lo que se le pide a la BD


def test_se_filtra_por_salon_y_no_por_otra_cosa():
    """Contrapeso: la RLS ya filtraría, pero pedir sin filtro dependería solo de
    ella. Defensa en profundidad, y además evita traerse filas de más."""
    db = FakeDB([_fila()])
    _cliente(db).get("/bot/estado")

    assert db.params["salon_id"] == f"eq.{SALON}"
    assert "is_active" in db.params["select"]


def test_si_la_bd_falla_es_502_y_no_un_estado_inventado():
    """Callarse aquí y devolver «no conectado» sería peor que el error: la
    pantalla le diría a un salón con el bot funcionando que no lo tiene."""
    r = _cliente(FakeDB(revienta=True)).get("/bot/estado")

    assert r.status_code == 502
