"""El plazo de los datos (0064): el reloj, los avisos y el webhook. NO HAY BORRADO.

Con dobles. La prueba contra la base de verdad —«vuelve el día 80 y no pasa
nada»— está en `test_plazo_de_datos_base_real.py`.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.core import plazo_de_datos as plazos
from app.core.config import Settings
from app.core.emails import EnvioFallido
from app.routers.billing import _campos_de_la_suscripcion
from app.tareas import plazo_de_datos as tarea
from tests.conftest import FakePostgrest



def _utc(*a) -> datetime:
    return datetime(*a, tzinfo=timezone.utc)


# ═══ 1. Las fechas ═════════════════════════════════════════════════════════
# Cuándo se borra cada cuenta lo calcula la base (`relojes_del_plazo`, 0068), y
# se prueba contra ella: `test_el_borrado_a_90_dias_base_real.py`.


def test_la_fecha_del_correo_se_escribe_entera():
    assert plazos.fecha_larga(date(2027, 1, 3)) == "3 de enero de 2027"


# ═══ 2. Qué aviso toca ═════════════════════════════════════════════════════

NADA = {"terminada": False, "30": False, "7": False}


def _p(dias: int) -> plazos.Plazo:
    return plazos.Plazo(date(2026, 1, 1), date(2026, 4, 1), dias)


@pytest.mark.parametrize("dias,toca", [(90, "terminada"), (31, "terminada"), (30, "30"), (8, "30"),
                                      (7, "7"), (0, "7"), (-1, None)])
def test_cada_franja_tiene_su_aviso(dias, toca):
    assert plazos.aviso_que_toca(_p(dias), NADA) == toca


def test_un_aviso_mandado_no_se_repite():
    assert plazos.aviso_que_toca(_p(60), {**NADA, "terminada": True}) is None
    assert plazos.aviso_que_toca(_p(20), {**NADA, "30": True}) is None
    assert plazos.aviso_que_toca(_p(3), {**NADA, "7": True}) is None


def test_un_cron_caido_retrasa_pero_no_manda_dos_de_golpe():
    # Caído del día 70 al 85: al volver quedan 5 días y toca el de 7, no el de 30.
    assert plazos.aviso_que_toca(_p(5), NADA) == "7"


# ═══ 3. El webhook pone y quita el reloj ════════════════════════════════════

AJUSTES = Settings(supabase_url="http://x", supabase_secret_key="k", supabase_jwks_url="http://x/j",
                   database_url="postgresql://x")


def test_al_terminar_de_verdad_empieza_el_reloj_con_la_fecha_de_stripe():
    campos, _, _ = _campos_de_la_suscripcion(
        {"id": "sub_1", "status": "canceled", "ended_at": 1_790_000_000, "items": {"data": []}}, AJUSTES
    )
    assert campos["terminada_at"] == datetime.fromtimestamp(1_790_000_000, tz=timezone.utc).isoformat()


def test_pedir_la_baja_no_empieza_el_reloj():
    # Activa con la baja programada: sigue pagada y trabajando.
    campos, _, _ = _campos_de_la_suscripcion(
        {"id": "sub_1", "status": "active", "cancel_at_period_end": True, "items": {"data": []}}, AJUSTES
    )
    assert campos["terminada_at"] is None


@pytest.mark.parametrize("estado", ["active", "trialing"])
def test_si_vuelve_el_reloj_y_los_avisos_se_borran(estado):
    campos, _, _ = _campos_de_la_suscripcion({"id": "sub_2", "status": estado, "items": {"data": []}}, AJUSTES)
    assert {k: campos[k] for k in ("terminada_at", *plazos.SELLO.values())} == {
        "terminada_at": None, "aviso_terminada_at": None, "aviso_30_at": None, "aviso_7_at": None}


def test_impagada_no_es_terminada_y_no_toca_el_reloj():
    campos, _, _ = _campos_de_la_suscripcion({"id": "sub_1", "status": "unpaid", "items": {"data": []}}, AJUSTES)
    assert "terminada_at" not in campos


# ═══ 4. La tarea ═══════════════════════════════════════════════════════════

CUENTA = "acc-1"


def _base(terminada_at: str, estado: str = "canceled", **sellos) -> FakePostgrest:
    db = _tablas(terminada_at, estado, **sellos)

    def reloj(body):
        # Lo que contesta `relojes_del_plazo` para esta cuenta el 25-12-2026:
        # terminó el 5-10, se borra el 3-1-2027, quedan 9 días. Una suscripción
        # viva no tiene reloj. Los sellos, los de la tabla en ese momento.
        sub = db.tables["subscriptions"][0]
        if sub["estado"] != "canceled":
            return []
        return [{"account_id": CUENTA, "origen": "suscripcion", "terminada_at": sub["terminada_at"],
                 "fila_id": sub["id"], "aviso_terminada": bool(sub["aviso_terminada_at"]),
                 "aviso_30": bool(sub["aviso_30_at"]), "aviso_7": bool(sub["aviso_7_at"]),
                 "zona_horaria": "Europe/Madrid", "terminada_el": "2026-10-05", "se_borran_el": "2027-01-03",
                 "dias_restantes": 9}]

    db.rpcs["relojes_del_plazo"] = reloj
    return db


def _tablas(terminada_at: str, estado: str, **sellos) -> FakePostgrest:
    return FakePostgrest({
        "subscriptions": [{"id": "s1", "account_id": CUENTA, "estado": estado, "terminada_at": terminada_at,
                           "aviso_terminada_at": None, "aviso_30_at": None, "aviso_7_at": None, **sellos}],
        "accounts": [{"id": CUENTA, "nombre": "Salón Lúa"}],
        "salons": [{"id": "c1", "account_id": CUENTA, "nombre_comercial": "Salón Lúa",
                    "created_at": "2026-01-01T00:00:00+00:00", "sigue_al_bajar": False,
                    "zona_horaria": "Europe/Madrid"}],
        "users": [{"id": "u1", "account_id": CUENTA, "email": "duena@lua.es", "rol": "admin"}],
        "tareas_latido": [],
    })


@pytest.fixture()
def correos(monkeypatch):
    enviados: list[tuple[str, str, str]] = []

    async def enviar(settings, destino, asunto, cuerpo, clave=None):
        enviados.append((destino, asunto, cuerpo))

    monkeypatch.setattr(tarea, "enviar_email_branded", enviar)
    monkeypatch.setattr(tarea, "_ahora", lambda: _utc(2026, 12, 25, 9))
    return enviados


@pytest.mark.anyio
async def test_a_diez_dias_manda_el_de_30_con_la_fecha_exacta_y_solo_a_la_administradora(correos):
    db = _base("2026-10-05T10:00:00+00:00")  # se borra el 3-1-2027; el 25-12 quedan 9
    resumen = await tarea.ejecutar(AJUSTES, db)
    assert resumen["enviados"] == 1
    [(destino, asunto, cuerpo)] = correos
    assert destino == "duena@lua.es"
    assert "3 de enero de 2027" in asunto and "Quedan 30 días" in asunto
    assert "/ajustes" in cuerpo and "Descargar mis datos" in cuerpo
    assert db.tables["subscriptions"][0]["aviso_30_at"]
    assert db.tables["tareas_latido"][0]["tarea"] == "plazo_de_datos"


@pytest.mark.anyio
async def test_dos_pasadas_no_mandan_dos_correos(correos):
    db = _base("2026-10-05T10:00:00+00:00")
    await tarea.ejecutar(AJUSTES, db)
    await tarea.ejecutar(AJUSTES, db)
    assert len(correos) == 1


@pytest.mark.anyio
async def test_si_ha_vuelto_no_se_le_escribe(correos):
    # Estado vivo, aunque la fecha se hubiera quedado: no está en plazo.
    db = _base("2026-10-05T10:00:00+00:00", estado="active")
    resumen = await tarea.ejecutar(AJUSTES, db)
    assert resumen["en_plazo"] == 0 and correos == []


@pytest.mark.anyio
async def test_un_fallo_seguro_suelta_el_sello_para_reintentar(monkeypatch, correos):
    async def falla(*a, **k):
        raise EnvioFallido("caído", seguro=True)

    monkeypatch.setattr(tarea, "enviar_email_branded", falla)
    db = _base("2026-10-05T10:00:00+00:00")
    resumen = await tarea.ejecutar(AJUSTES, db)
    assert resumen["fallidos"] == 1
    assert db.tables["subscriptions"][0]["aviso_30_at"] is None


@pytest.mark.anyio
async def test_un_fallo_ambiguo_deja_el_sello(monkeypatch, correos):
    async def falla(*a, **k):
        raise EnvioFallido("timeout", seguro=False)

    monkeypatch.setattr(tarea, "enviar_email_branded", falla)
    db = _base("2026-10-05T10:00:00+00:00")
    await tarea.ejecutar(AJUSTES, db)
    assert db.tables["subscriptions"][0]["aviso_30_at"]


def test_la_tarea_no_sabe_borrar():
    """LA TAREA NO BORRA, y esto lo vigila: ninguna llamada a
    `delete` en la tarea ni en el cálculo del plazo."""
    import inspect

    for modulo in (tarea, plazos):
        assert ".delete(" not in inspect.getsource(modulo), modulo.__name__
