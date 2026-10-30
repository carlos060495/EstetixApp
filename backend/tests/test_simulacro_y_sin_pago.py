"""El simulacro (lo que haría el borrado, contra lo decidido) y el reloj de quien
nunca pagó. Con dobles; contra la base de verdad, `test_simulacro_base_real.py`."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.core import plazo_de_datos as plazos
from app.core.config import Settings
from app.core.simulacro import SE_QUEDA, clasificar
from app.tareas import plazo_de_datos as tarea
from tests.conftest import FakePostgrest

AJUSTES = Settings(supabase_url="http://x", supabase_secret_key="k", supabase_jwks_url="http://x/j",
                   database_url="postgresql://x", frontend_base_url="https://www.estetixapp.es")
AHORA = datetime(2026, 10, 4, 9, tzinfo=timezone.utc)


def _r(paso, tabla, filas, accion, detalle="x"):
    return {"paso": paso, "tabla": tabla, "filas": filas, "accion": accion, "detalle": detalle}


# ═══ 1. El simulacro, contra lo decidido ═══════════════════════════════════


def test_lo_decidido_que_se_queda_es_lo_que_dijo_carlos():
    assert set(SE_QUEDA) == {
        # La cuenta y la suscripción ya NO (0068): son la ficha del cliente.
        "public.decisiones_de_dinero", "public.descargas_de_datos",
        "public.impersonation_sessions", "public.support_access_codes", "public.admin_logs",
        # 0068: la constancia del borrado. Es nuestra y no dice de quién era.
        "public.cuentas_borradas",
        # 0070: quién trajo cada cuenta (comisiones a perpetuidad), los códigos
        # que no valieron, y los referidos de los que cuelgan.
        "public.altas_atribuidas", "public.codigos_que_no_valieron", "public.referidos",
        # 0075: lo que se debe a los referidos y el registro de los cambios de
        # su % (06-10: una comisión devengada no se va con el salón).
        "public.comisiones", "public.cambios_de_porcentaje",
        # 0076: los pagos a los referidos y lo que pasó con cada uno.
        "public.pagos_a_referidos", "public.pagos_a_referidos_resultados", "public.cambios_de_minimo",
    }


def test_borrar_algo_que_se_queda_es_un_problema():
    sim = clasificar([_r(1, "public.decisiones_de_dinero", 1, "borra", "en cascada desde public.salons"),
                      _r(1, "public.clients", 12, "borra")])
    problemas = {f.tabla: f.problema for f in sim.problemas}
    assert "public.decisiones_de_dinero" in problemas and "se queda" in problemas["public.decisiones_de_dinero"]
    assert "public.clients" not in problemas


def test_aunque_hoy_no_tenga_filas_el_camino_ya_es_un_problema():
    sim = clasificar([_r(1, "public.support_access_codes", 0, "borra", "en cascada desde public.salons")])
    assert sim.problemas and "en cuanto las tuviera" in sim.problemas[0].problema


def test_quedarse_con_algo_que_debe_irse_es_un_problema_y_al_reves_no():
    sim = clasificar([_r(3, "public.correcciones_de_fichaje", 3, "queda"),
                      _r(3, "public.admin_logs", 2, "queda"), _r(3, "public.accounts", 1, "queda")])
    # La cuenta que se quedara también sería un problema desde la 0068.
    assert [f.tabla for f in sim.problemas] == ["public.accounts", "public.correcciones_de_fichaje"]
    assert "registro de jornada" in sim.problemas[1].problema


def test_lo_que_se_queda_sin_dueno_o_en_auth_es_un_problema():
    """0083: `auth.scim_users` salía como «queda_sin_referencia» y nadie lo veía
    en rojo. Sin dueño solo está bien para lo nuestro."""
    sim = clasificar([
        _r(2, "auth.scim_users", 1, "queda_sin_referencia", "scim_users_user_id_fkey → auth.users (SET NULL)"),
        _r(2, "public.admin_logs", 3, "queda_sin_referencia", "admin_logs → public.salons (SET NULL)"),
        _r(6, "auth.flow_state", 2, "queda_en_auth", "nombra a sus accesos"),
        # 0084: una tabla que no se ha podido mirar no es «nada».
        _r(3, "public.zz_bloqueada", None, "no_comprobada", "no se ha podido mirar: lock timeout"),
    ])
    # Por paso: el 2 (sin dueño), el 3 (no comprobada) y el 6 (en Auth).
    assert [f.tabla for f in sim.problemas] == ["auth.scim_users", "public.zz_bloqueada", "auth.flow_state"]
    assert "sin dueño" in sim.problemas[0].problema
    assert "No se ha podido comprobar" in sim.problemas[1].problema
    assert "se negaría" in sim.problemas[2].problema


def test_un_trigger_que_no_deja_borrar_solo_estorba_si_hay_algo_que_deberia_irse():
    sim = clasificar([
        _r(3, "public.correcciones_de_fichaje", 3, "queda"),
        _r(4, "public.correcciones_de_fichaje", None, "trigger_impide_borrar", "el trigger … rechaza"),
        _r(4, "public.decisiones_de_dinero", None, "trigger_impide_borrar", "el trigger … rechaza"),
    ])
    tablas = [(f.tabla, f.que_haria) for f in sim.problemas]
    assert ("public.correcciones_de_fichaje", "trigger_impide_borrar") in tablas
    assert all(t != "public.decisiones_de_dinero" for t, _ in tablas)


def test_cuenta_las_filas_de_la_base_auth_incluida_y_no_lo_de_fuera():
    sim = clasificar([_r(1, "public.clients", 12, "borra"), _r(1, "public.salons", 2, "borra"),
                      _r(1, "auth.users", 1, "borra"), _r(5, "storage.objects (salon-assets)", 1, "borra"),
                      _r(5, "stripe (clientes)", 1, "borra")])
    assert sim.filas_a_borrar == 15


# ═══ 2. El reloj de quien nunca pagó ════════════════════════════════════════


def _cuentas(*cuentas, subs=()):
    return FakePostgrest({
        "accounts": [{"id": c, "nombre": c, "created_at": alta.isoformat(), "aviso_plazo_terminada_at": None,
                      "aviso_plazo_30_at": None, "aviso_plazo_7_at": None} for c, alta in cuentas],
        "subscriptions": [{"id": f"s-{a}", "account_id": a, "estado": e, "terminada_at": None,
                           "aviso_terminada_at": None, "aviso_30_at": None, "aviso_7_at": None} for a, e in subs],
        "salons": [{"id": f"c-{c}", "account_id": c, "nombre_comercial": c, "created_at": alta.isoformat(),
                    "sigue_al_bajar": False, "zona_horaria": "Europe/Madrid"} for c, alta in cuentas],
        "users": [{"id": f"u-{c}", "account_id": c, "email": f"{c}@x.es", "rol": "admin"} for c, _ in cuentas],
        "tareas_latido": [],
    })


# Quién entra en el reloj sin pago lo decide la base (`relojes_del_plazo`,
# 0068) y se prueba contra ella: `test_el_borrado_a_90_dias_base_real.py`.


def _reloj_sin_pago(db: FakePostgrest, cuenta: str):
    def reloj(body):
        c = next(f for f in db.tables["accounts"] if f["id"] == cuenta)
        return [{"account_id": cuenta, "origen": "sin_pago", "terminada_at": "2026-09-28T09:00:00+00:00",
                 "fila_id": cuenta, "aviso_terminada": bool(c["aviso_plazo_terminada_at"]),
                 "aviso_30": bool(c["aviso_plazo_30_at"]), "aviso_7": bool(c["aviso_plazo_7_at"]),
                 "zona_horaria": "Europe/Madrid", "terminada_el": "2026-09-28", "se_borran_el": "2026-12-27",
                 "dias_restantes": 84}]
    db.rpcs["relojes_del_plazo"] = reloj


@pytest.mark.anyio
async def test_quien_nunca_pago_recibe_su_aviso_y_se_sella_en_la_cuenta(monkeypatch):
    enviados = []

    async def enviar(settings, destino, asunto, cuerpo, clave=None):
        enviados.append((destino, asunto, cuerpo))

    monkeypatch.setattr(tarea, "enviar_email_branded", enviar)
    monkeypatch.setattr(tarea, "_ahora", lambda: AHORA)
    db = _cuentas(("vieja", AHORA - timedelta(days=20)))
    _reloj_sin_pago(db, "vieja")
    await tarea.ejecutar(AJUSTES, db)
    [(destino, asunto, cuerpo)] = enviados
    assert destino == "vieja@x.es" and "No llegaste a activar" in asunto
    assert db.tables["accounts"][0]["aviso_plazo_terminada_at"]
    await tarea.ejecutar(AJUSTES, db)
    assert len(enviados) == 1


# ═══ 3. Los textos dicen lo del registro de jornada ═══════════════════════


@pytest.mark.parametrize("aviso,origen", [("terminada", "suscripcion"), ("terminada", "sin_pago"),
                                          ("30", "suscripcion"), ("7", "suscripcion")])
def test_cada_correo_dice_que_la_descarga_lleva_el_registro_de_jornada_y_que_lo_guarde_lo_que_diga_su_ley(aviso, origen):
    p = plazos.Plazo(date(2026, 10, 5), date(2027, 1, 3), 91)
    _, cuerpo = tarea.cuerpo_del_aviso(AJUSTES, aviso, p, "Salón Lúa", origen)
    assert "La descarga incluye el registro de jornada de tu equipo" in cuerpo
    assert "el tiempo que exija la ley de tu país" in cuerpo
    assert "años" not in cuerpo  # sin número: cada país tiene el suyo
