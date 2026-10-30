"""LAS CORRECCIONES DE FICHAJE, SIN BASE (0061): aplicarlas encima de lo que
marcó el reloj y decir cuáles son imposibles.

Lo que no se puede probar sin la base —los triggers, los permisos, quién puede
corregir— está en `test_correcciones_fichaje_base_real.py`.
"""

from __future__ import annotations

from app.core import correcciones_fichaje as corr
from app.core.impersonation_policy import BLOQUEADOS
from app.routers import employees
from tests.zona_de_prueba import ZONA_SALON


def _f(id_, tipo, cuando):
    """Una marca del reloj. `cuando` en UTC: «07-13T16:16» (Madrid +2)."""
    return {"id": id_, "tipo": tipo, "ts": f"2026-{cuando}:00+00:00"}


def _c(n, accion, marca_id, tipo, antes=None, despues=None):
    return {"id": n, "ocurrido_at": f"2026-10-02T10:00:0{n}+00:00", "accion": accion, "marca_id": marca_id,
            "tipo": tipo, "ts_antes": antes and f"2026-{antes}:00+00:00",
            "ts_despues": despues and f"2026-{despues}:00+00:00", "motivo": "Se olvidó de fichar la salida"}


# El caso de producción del 13 de julio (Luis Ramírez, Salón Demo): entrada,
# salida un minuto después y una entrada que nunca se cerró.
LUIS = [_f("e1", "entrada", "07-13T16:16"), _f("s1", "salida", "07-13T16:17"), _f("e2", "entrada", "07-13T17:01")]


def test_el_reloj_no_cambia_y_la_correccion_va_encima():
    marcas = corr.aplicar(LUIS, [_c(1, "nueva", "n1", "salida", despues="07-13T19:00")])
    por_id = {m.id: m for m in marcas}
    assert [m.id for m in marcas] == ["e1", "s1", "e2", "n1"]
    assert por_id["n1"].nueva and por_id["n1"].corregida
    # Las del reloj, con su hora del reloj y sin corregir.
    assert all(por_id[i].ts == por_id[i].ts_reloj and not por_id[i].corregida for i in ("e1", "s1", "e2"))
    assert corr.nuevos_problemas(corr.aplicar(LUIS, []), marcas, ZONA_SALON) == []


def test_cambiar_una_hora_guarda_la_del_reloj():
    m = {x.id: x for x in corr.aplicar(LUIS, [_c(1, "cambia", "e2", "entrada", "07-13T17:01", "07-13T16:30")])}["e2"]
    assert m.ts.isoformat() == "2026-07-13T16:30:00+00:00"
    assert m.ts_reloj.isoformat() == "2026-07-13T17:01:00+00:00"


def test_anular_no_borra_la_marca_pero_no_cuenta():
    marcas = corr.aplicar(LUIS, [_c(1, "anula", "e2", "entrada", "07-13T17:01")])
    assert {m.id for m in marcas} == {"e1", "s1", "e2"}
    assert [f["id"] for f in corr.como_filas(marcas)] == ["e1", "s1"]


def test_las_correcciones_se_aplican_en_el_orden_en_que_se_hicieron():
    """Una salida nueva, cambiada después: cuenta la última hora."""
    marcas = corr.aplicar(LUIS, [_c(2, "cambia", "n1", "salida", "07-13T19:00", "07-13T18:30"),
                                 _c(1, "nueva", "n1", "salida", despues="07-13T19:00")])
    n1 = {m.id: m for m in marcas}["n1"]
    assert n1.ts.isoformat() == "2026-07-13T18:30:00+00:00" and len(n1.correcciones) == 2


# ═══ LAS IMPOSIBLES ═══


def _nuevos(correcciones):
    return corr.nuevos_problemas(corr.aplicar(LUIS, []), corr.aplicar(LUIS, correcciones), ZONA_SALON)


def test_una_salida_anterior_a_la_entrada():
    """Poner la salida a las 18:30 de Madrid a una entrada de las 19:01."""
    (p,) = _nuevos([_c(1, "nueva", "n1", "salida", despues="07-13T16:30")])
    assert p.clave == "salida_antes_de_entrada"
    assert p.mensaje == "La salida de las 18:30 es anterior a la entrada de las 19:01."


def test_una_jornada_de_30_horas():
    (p,) = _nuevos([_c(1, "nueva", "n1", "salida", despues="07-14T23:01")])
    assert p.clave == "demasiado_larga"
    assert p.mensaje.startswith("Una sesión de 30 horas (entrada 19:01, salida 01:01 del 15/07)")


def test_dos_sesiones_que_se_solapan():
    """Una jornada entera de 18:00 a 20:00 encima de la sesión abierta de las 19:01."""
    problemas = _nuevos([_c(1, "nueva", "n1", "entrada", despues="07-13T16:30"),
                         _c(2, "nueva", "n2", "salida", despues="07-13T18:00")])
    assert "solape" in {p.clave for p in problemas}


def test_mover_la_entrada_despues_de_su_salida():
    """La salida de las 18:17 se queda antes de la entrada movida a las 18:40; y,
    de rebote, la entrada de las 19:01 cae dentro de esa sesión sin cerrar."""
    problemas = _nuevos([_c(1, "cambia", "e1", "entrada", "07-13T16:16", "07-13T16:40")])
    assert [p.clave for p in problemas] == ["salida_antes_de_entrada", "solape"]
    assert problemas[0].mensaje == "La salida de las 18:17 es anterior a la entrada de las 18:40."


def test_lo_que_ya_estaba_mal_no_impide_arreglar_otra_cosa():
    """Una salida suelta del día 12 sigue ahí; poner la salida del 13 es posible."""
    con_suelta = [_f("x", "salida", "07-12T10:00"), *LUIS]
    antes = corr.aplicar(con_suelta, [])
    despues = corr.aplicar(con_suelta, [_c(1, "nueva", "n1", "salida", despues="07-13T19:00")])
    assert [p.clave for p in corr.problemas(despues, ZONA_SALON)] == ["marca_suelta"]
    assert corr.nuevos_problemas(antes, despues, ZONA_SALON) == []


def test_la_sesion_maxima_es_la_del_historial():
    assert corr.SESION_MAXIMA == employees.SESION_MAXIMA


def test_soporte_no_puede_corregir():
    """Guarda 1: la ruta está bloqueada para las sesiones de soporte. (La 2, la
    comprobación dentro del endpoint, se prueba contra la base.)"""
    assert "POST /employees/{employee_id}/fichajes/correcciones" in BLOQUEADOS


def test_una_entrada_al_dia_siguiente_no_solapa_con_una_sesion_sin_cerrar():
    """La de las 19:01 del 13 nunca se cerró; una jornada el 14 por la mañana no
    se solapa con ella: como en el historial, se dejó sin cerrar."""
    assert _nuevos([_c(1, "nueva", "n1", "entrada", despues="07-14T08:00"),
                    _c(2, "nueva", "n2", "salida", despues="07-14T12:00")]) == []
