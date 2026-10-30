"""DÓNDE HAY HUECO POR LLENAR (`/stats/equipo`), contra la base de verdad.

  · EL ESPEJO: las horas con cita de cada persona, más las citas sin
    profesional, suman EXACTAMENTE los minutos de cita con los que `/stats`
    calcula la ocupación. Si un día dejaran de cuadrar, habría dos verdades.
  · Las horas fichadas son las del historial de cada persona (con la misma
    regla: lo cerrado suma, lo abierto no) y los días sin cerrar se dicen.
  · El orden es alfabético —sin que las tildes manden a nadie al final—, no por
    horas.
  · Una ficha en pausa sale si en el periodo tiene algo; si no, no.
"""

from __future__ import annotations

import pytest

from app.db.postgrest_client import PostgrestClient
from tests.test_alta_completa_e2e import SECRET_KEY, SUPABASE_URL
from tests.test_camino_humano_dos_centros import (  # noqa: F401  (fixtures)
    _cabeceras,
    anyio_backend,
    dos_centros,
)

pytestmark = [pytest.mark.base_real, pytest.mark.anyio]

H = _cabeceras()
# Septiembre de 2026 en Madrid: ya pasado, para que nada salga «en curso».
PERIODO = {"desde": "2026-08-31T22:00:00+00:00", "hasta": "2026-09-30T22:00:00+00:00",
           "fecha_desde": "2026-09-01", "fecha_hasta": "2026-09-30"}


def _db() -> PostgrestClient:
    return PostgrestClient(SUPABASE_URL, SECRET_KEY, timeout=30.0)


@pytest.fixture()
async def equipo(dos_centros):  # noqa: F811
    cliente, salon = dos_centros["cliente"], dos_centros["salon_id"]
    ids = {}
    for nombre in ("Álvaro Ruiz", "Zoe Martín", "beatriz Gil", "Carmen Sin Nada", "Pausada Con Fichajes",
                   "Pausada Sin Nada"):
        r = cliente.post("/employees", json={"nombre": nombre, "telefono": "600111222"}, headers=H)
        assert r.status_code == 201, r.text
        ids[nombre] = r.json()["id"]
    db = _db()
    servicio = (await db.select("services", {"select": "id", "salon_id": f"eq.{salon}", "limit": "1"}))
    servicio_id = servicio[0]["id"] if servicio else None

    async def fichar(nombre, entrada, salida=None):
        await db.insert("time_entries", {"salon_id": salon, "employee_id": ids[nombre], "tipo": "entrada", "ts": entrada})
        if salida:
            await db.insert("time_entries", {"salon_id": salon, "employee_id": ids[nombre], "tipo": "salida", "ts": salida})

    async def cita(nombre, inicio, fin, estado="completada"):
        await db.insert("appointments", {"salon_id": salon, "employee_id": ids.get(nombre), "service_id": servicio_id,
                                         "inicio": inicio, "fin": fin, "estado": estado, "notas": "equipo"})

    # Álvaro: 8 h fichadas el día 5, 4 h con cita (una de ellas, confirmada).
    await fichar("Álvaro Ruiz", "2026-09-05T07:00:00+00:00", "2026-09-05T15:00:00+00:00")
    await cita("Álvaro Ruiz", "2026-09-05T08:00:00+00:00", "2026-09-05T10:00:00+00:00")
    await cita("Álvaro Ruiz", "2026-09-05T12:00:00+00:00", "2026-09-05T13:00:00+00:00")
    # Y una CONFIRMADA (no completada): ocupa hueco igual. Sin ella, contar solo
    # las completadas daría el mismo total y el espejo no lo vería.
    await cita("Álvaro Ruiz", "2026-09-05T13:00:00+00:00", "2026-09-05T14:00:00+00:00", estado="confirmada")
    # Zoe: 4 h el día 6 y un día 7 SIN CERRAR; 1 h con cita y una cancelada que no cuenta.
    await fichar("Zoe Martín", "2026-09-06T07:00:00+00:00", "2026-09-06T11:00:00+00:00")
    await fichar("Zoe Martín", "2026-09-07T07:00:00+00:00")
    await cita("Zoe Martín", "2026-09-06T08:00:00+00:00", "2026-09-06T09:00:00+00:00")
    await cita("Zoe Martín", "2026-09-06T09:00:00+00:00", "2026-09-06T10:00:00+00:00", estado="cancelada")
    # Y DESPUÉS DEL CORTE (el día 10 a mediodía, en las pruebas del periodo en
    # curso): 4 h fichadas y 2 h de cita el día 20.
    await fichar("Zoe Martín", "2026-09-20T07:00:00+00:00", "2026-09-20T11:00:00+00:00")
    # Y una sesión que empieza JUSTO DESPUÉS del corte (el 10, de 13:00 a 15:00
    # UTC): se lee con el margen del historial, y tiene que no contar.
    await fichar("Zoe Martín", "2026-09-10T13:00:00+00:00", "2026-09-10T15:00:00+00:00")
    await cita("Zoe Martín", "2026-09-20T08:00:00+00:00", "2026-09-20T10:00:00+00:00")
    # beatriz: 2 h de cita y NI UNA HORA FICHADA.
    await cita("beatriz Gil", "2026-09-08T08:00:00+00:00", "2026-09-08T10:00:00+00:00")
    # Una cita sin profesional: 30 min.
    await cita(None, "2026-09-09T08:00:00+00:00", "2026-09-09T08:30:00+00:00")
    # Pausada con fichajes: trabajó el día 2 y se puso en pausa después.
    await fichar("Pausada Con Fichajes", "2026-09-02T07:00:00+00:00", "2026-09-02T13:00:00+00:00")
    for nombre in ("Pausada Con Fichajes", "Pausada Sin Nada"):
        await db.update("employees", {"id": f"eq.{ids[nombre]}"}, {"estado": "pausa"})
    yield {**dos_centros, "ids": ids}
    await db.delete("appointments", {"salon_id": f"eq.{salon}", "notas": "eq.equipo"})


async def test_el_espejo_las_horas_con_cita_de_cada_persona_son_la_ocupacion(equipo):
    cliente = equipo["cliente"]
    stats = cliente.get("/stats", params=PERIODO, headers=H).json()
    e = cliente.get("/stats/equipo", params={"desde": PERIODO["desde"], "hasta": PERIODO["hasta"]}, headers=H).json()
    assert sum(p["minutos_cita"] for p in e["personas"]) + e["minutos_cita_sin_profesional"] == stats["minutos_citas"]
    assert e["minutos_citas_total"] == stats["minutos_citas"] == 4 * 60 + 3 * 60 + 2 * 60 + 30
    assert e["en_curso"] is False


async def test_cada_persona_y_los_dias_sin_contar(equipo):
    e = equipo["cliente"].get("/stats/equipo", params={"desde": PERIODO["desde"], "hasta": PERIODO["hasta"]},
                              headers=H).json()
    por = {p["nombre"]: p for p in e["personas"]}
    assert (por["Álvaro Ruiz"]["minutos_fichados"], por["Álvaro Ruiz"]["minutos_cita"]) == (480, 240)
    assert (por["Zoe Martín"]["minutos_fichados"], por["Zoe Martín"]["minutos_cita"]) == (600, 180)
    assert por["Zoe Martín"]["dias_sin_contar"] == [{"fecha": "2026-09-07", "estado": "incompleto"}]
    assert (por["beatriz Gil"]["minutos_fichados"], por["beatriz Gil"]["minutos_cita"]) == (0, 120)
    assert (por["Carmen Sin Nada"]["minutos_fichados"], por["Carmen Sin Nada"]["minutos_cita"]) == (0, 0)
    assert por["Pausada Con Fichajes"]["estado"] == "pausa" and por["Pausada Con Fichajes"]["minutos_fichados"] == 360
    assert "Pausada Sin Nada" not in por
    assert e["minutos_cita_sin_profesional"] == 30


async def test_el_orden_es_alfabetico_y_no_por_horas(equipo):
    e = equipo["cliente"].get("/stats/equipo", params={"desde": PERIODO["desde"], "hasta": PERIODO["hasta"]},
                              headers=H).json()
    nombres = [p["nombre"] for p in e["personas"] if p["nombre"] in equipo["ids"]]
    assert nombres == ["Álvaro Ruiz", "beatriz Gil", "Carmen Sin Nada", "Pausada Con Fichajes", "Zoe Martín"]


async def test_un_periodo_sin_nada(equipo):
    e = equipo["cliente"].get("/stats/equipo", params={"desde": "2025-01-01T00:00:00+01:00",
                                                       "hasta": "2025-01-02T00:00:00+01:00"}, headers=H).json()
    assert all(p["minutos_fichados"] == 0 and p["minutos_cita"] == 0 for p in e["personas"])
    assert e["minutos_citas_total"] == 0


# ═══ EL PERIODO EN CURSO: LAS DOS BARRAS SE CORTAN AHORA ═══════════════════

AHORA = "2026-09-10T12:00:00+00:00"  # septiembre, a medio hacer


def _ahora_el_10(monkeypatch):
    from datetime import datetime

    monkeypatch.setattr("app.routers.employees._utc_now", lambda: datetime.fromisoformat(AHORA))


async def test_en_curso_las_dos_barras_llegan_hasta_ahora(equipo, monkeypatch):
    _ahora_el_10(monkeypatch)
    e = equipo["cliente"].get("/stats/equipo", params={"desde": PERIODO["desde"], "hasta": PERIODO["hasta"]},
                              headers=H).json()
    assert e["en_curso"] is True and e["hasta_efectivo"].startswith("2026-09-10T12:00:00")
    zoe = next(p for p in e["personas"] if p["nombre"] == "Zoe Martín")
    # Lo del día 20 todavía no ha pasado (ni sus 4 h fichadas ni sus 2 h de
    # cita), y la sesión del 10 que empieza después del corte, tampoco.
    assert (zoe["minutos_fichados"], zoe["minutos_cita"]) == (240, 60)


async def test_el_espejo_en_curso_es_la_ocupacion_hasta_ahora(equipo, monkeypatch):
    """El espejo con el corte puesto: el gráfico de un periodo en curso suma lo
    mismo que la ocupación pedida con el mismo inicio y con fin AHORA."""
    _ahora_el_10(monkeypatch)
    cliente = equipo["cliente"]
    e = cliente.get("/stats/equipo", params={"desde": PERIODO["desde"], "hasta": PERIODO["hasta"]}, headers=H).json()
    hasta_ahora = cliente.get("/stats", params={**PERIODO, "hasta": AHORA}, headers=H).json()
    completo = cliente.get("/stats", params=PERIODO, headers=H).json()
    assert sum(p["minutos_cita"] for p in e["personas"]) + e["minutos_cita_sin_profesional"] == hasta_ahora["minutos_citas"]
    assert e["minutos_citas_total"] == hasta_ahora["minutos_citas"] == 4 * 60 + 60 + 2 * 60 + 30
    # Y la ocupación del centro sigue contando lo que viene: son preguntas distintas.
    assert completo["minutos_citas"] - hasta_ahora["minutos_citas"] == 2 * 60
