"""EL ENLACE DE SEGUIMIENTO NO LLEVA DINERO, Y ES PÚBLICO A PROPÓSITO (0077,
06-10-2026.

Se recorre la app, no una lista:

  1. SIN DINERO. Cada ruta que declara `publico.SEGUIMIENTO`: su modelo de
     respuesta, campo a campo y hacia dentro (listas y modelos anidados), no
     tiene ningún campo de dinero ni de porcentaje. Y de verdad: la ruta,
     llamada con una base que le devuelve DE MÁS —el precio del plan, el
     porcentaje, la comisión—, no deja salir nada de eso, venga de donde
     venga. El NOMBRE del plan es texto y pasa; su precio, no. Las columnas de
     la función de la base, en `test_agencias_base_real.py`.
  2. PÚBLICO A PROPÓSITO. Toda ruta que cuelga de `/seguimiento` declara
     `SEGUIMIENTO` —ni CUALQUIERA, ni ninguna sesión—, solo lee (GET), y no
     se indexa. Que toda ruta declare su público lo vigila además
     `test_publico_declarado.py`.

Cada guarda con su control, que lo pone en rojo.
"""

from __future__ import annotations

import re
import typing

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.core import agencias, publico
from app.core.impersonation_policy import iterar_rutas
from app.core.publico import Publico, publicos_de
from app.main import app
from app.routers import seguimiento as ruta_seguimiento
from tests.conftest import FakePostgrest

# Cualquier nombre con pinta de dinero o de porcentaje.
DINERO = re.compile(r"importe|precio|price|amount|centimo|cents|pct|porcentaje|percent|comision|commission|"
                    r"devengad|pagad|cobrad|debe|saldo|moneda|currency|eur|usd|tarifa|cuota|fee|iva|tax|neto|"
                    r"total|coste|cost", re.I)
# Un valor con pinta de dinero: «49,00 €», «29.00», «15 %».
VALOR_DE_DINERO = re.compile(r"\d+[.,]\d{2}\b|€|\$|\d\s?%")


def rutas_de_seguimiento(rutas) -> list[APIRoute]:
    return [r for r in iterar_rutas(rutas) if isinstance(r, APIRoute) and Publico.SEGUIMIENTO in publicos_de(r.dependant)]


def campos_de_dinero(modelo, camino: str = "") -> list[str]:
    """Los campos de dinero de un modelo, recorriéndolo hacia dentro."""
    fuera = []
    for nombre, campo in modelo.model_fields.items():
        aqui = f"{camino}{nombre}"
        if DINERO.search(nombre):
            fuera.append(aqui)
        for tipo in _tipos(campo.annotation):
            if isinstance(tipo, type) and issubclass(tipo, BaseModel):
                fuera += campos_de_dinero(tipo, aqui + ".")
    return fuera


def _tipos(anotacion) -> list:
    hijos = typing.get_args(anotacion)
    return [anotacion] + [t for h in hijos for t in _tipos(h)]


def claves_de_dinero(datos, camino: str = "") -> list[str]:
    """Las claves de dinero de una respuesta JSON, y los valores que lo parecen."""
    fuera = []
    if isinstance(datos, dict):
        for k, v in datos.items():
            if DINERO.search(k):
                fuera.append(camino + k)
            fuera += claves_de_dinero(v, f"{camino}{k}.")
    elif isinstance(datos, list):
        for i, v in enumerate(datos):
            fuera += claves_de_dinero(v, f"{camino}{i}.")
    elif isinstance(datos, (int, float)) and not isinstance(datos, bool):
        fuera.append(f"{camino.rstrip('.')}={datos}")  # aquí no hay ni un número
    elif isinstance(datos, str) and VALOR_DE_DINERO.search(datos):
        fuera.append(f"{camino.rstrip('.')}={datos}")
    return fuera


# ═══ 1. Sin dinero ═══════════════════════════════════════════════════════


def test_ninguna_ruta_de_seguimiento_tiene_un_campo_de_dinero():
    rutas = rutas_de_seguimiento(app.routes)
    assert rutas  # antivacío: el recorrido ve la página del enlace
    for r in rutas:
        assert r.response_model is not None, r.path
        assert campos_de_dinero(r.response_model) == [], r.path


def test_CONTROL_el_precio_del_plan_en_la_respuesta_se_pone_rojo():
    class SalonConPrecio(BaseModel):
        salon: str
        plan: str
        plan_precio_centimos: int

    class Respuesta(BaseModel):
        trabajador: str
        salones: list[SalonConPrecio]

    assert campos_de_dinero(Respuesta) == ["salones.plan_precio_centimos"]


TOKEN = "t" * 43
TRAMPA = [
    # Lo que la base le daría DE MÁS a la ruta: no tiene que salir nada de esto.
    {"trabajador": "Ana", "agencia": "Agencia Norte", "salon": "Peluquería Sol", "plan": "pro", "plan_al_llegar": "elite",
     "desde": "2026-09-01T10:00:00+00:00", "estado": "paga",
     "precio_centimos": 4900, "pct": "15.00", "comision_centimos": 735, "moneda": "eur", "telefono": "600112233"},
]


@pytest.fixture()
def con_trampa(monkeypatch):
    db = FakePostgrest({})
    db.rpcs["seguimiento_de_un_trabajador"] = lambda body: [dict(f) for f in TRAMPA]
    monkeypatch.setattr(ruta_seguimiento, "servicio", lambda settings: db)
    ruta_seguimiento._freno._by_ip.clear()
    return db


def test_la_respuesta_no_deja_salir_dinero_aunque_la_base_lo_traiga(con_trampa):
    r = TestClient(app).get(f"/seguimiento/{TOKEN}")
    assert r.status_code == 200, r.text
    datos = r.json()
    # Antivacío: el salón y el NOMBRE del plan sí están.
    assert datos["salones"][0]["salon"] == "Peluquería Sol" and datos["salones"][0]["plan"] == "Pro"
    assert datos["salones"][0]["plan_al_llegar"] == "Elite"  # el NOMBRE del plan del alta (cierre)
    assert claves_de_dinero(datos) == []
    assert "600112233" not in r.text  # ni datos de contacto
    assert "noindex" in r.headers["x-robots-tag"] and r.headers["cache-control"] == "no-store"


def test_CONTROL_una_respuesta_con_dinero_se_pone_roja():
    """Lo que dejaría salir la ruta si devolviera las filas de la base tal cual
    (la mutación «la ruta devuelve lo que le da la base» lo hace de verdad)."""
    assert {"salones.0.precio_centimos", "salones.0.pct", "salones.0.comision_centimos",
            "salones.0.moneda"} <= set(claves_de_dinero({"salones": TRAMPA}))
    # Y aunque el campo no lo parezca: un valor de dinero, o un número suelto.
    assert claves_de_dinero({"salones": [{"plan": "Pro · 49,00 €"}]}) == ["salones.0.plan=Pro · 49,00 €"]
    assert claves_de_dinero({"salones": [{"cuanto": 4900}]}) == ["salones.0.cuanto=4900"]


def test_un_enlace_que_no_abre_da_404_sin_decir_nada_mas(monkeypatch):
    db = FakePostgrest({})
    db.rpcs["seguimiento_de_un_trabajador"] = lambda body: []
    monkeypatch.setattr(ruta_seguimiento, "servicio", lambda settings: db)
    r = TestClient(app).get(f"/seguimiento/{TOKEN}")
    assert r.status_code == 404 and "noindex" in r.headers["x-robots-tag"]
    # Un token corto ni se busca.
    assert TestClient(app).get("/seguimiento/corto").status_code == 404


def test_el_token_es_largo_aleatorio_y_solo_se_guarda_su_hash():
    vistos = {agencias.token_de_seguimiento() for _ in range(200)}
    assert len(vistos) == 200 and all(len(t) >= 43 for t in vistos)
    t = next(iter(vistos))
    assert agencias.hash_de_seguimiento(t) != t and len(agencias.hash_de_seguimiento(t)) == 64


# ═══ 2. Público a propósito ══════════════════════════════════════════════


def problemas_de_publico(rutas) -> list[str]:
    fuera = []
    for r in iterar_rutas(rutas):
        if not isinstance(r, APIRoute):
            continue
        declara = publicos_de(r.dependant)
        if r.path.startswith("/seguimiento") and declara != {Publico.SEGUIMIENTO}:
            fuera.append(f"{r.path} declara {sorted(p.value for p in declara)}, no SEGUIMIENTO")
        if Publico.SEGUIMIENTO in declara and set(r.methods or ()) != {"GET"}:
            fuera.append(f"{r.path}: el enlace de seguimiento solo lee, y esta ruta acepta {sorted(r.methods)}")
    return fuera


def test_el_enlace_de_seguimiento_declara_que_es_publico_a_proposito():
    assert rutas_de_seguimiento(app.routes)
    assert problemas_de_publico(app.routes) == []


def _app_con(declaracion, metodo: str = "get") -> FastAPI:
    otra = FastAPI()
    r = APIRouter(prefix="/seguimiento", dependencies=[declaracion])
    getattr(r, metodo)("/{token}")(lambda token: {})
    otra.include_router(r)
    return otra


@pytest.mark.parametrize("declaracion,metodo", [
    (publico.CUALQUIERA, "get"),      # público, pero sin decir que es a propósito
    (publico.REFERIDO, "get"),        # con la sesión de alguien
    (publico.SEGUIMIENTO, "post"),    # que escribe
], ids=["cualquiera", "con_sesion", "escribe"])
def test_CONTROL_un_seguimiento_mal_declarado_se_pone_rojo(declaracion, metodo):
    assert problemas_de_publico(_app_con(declaracion, metodo).routes) != []
