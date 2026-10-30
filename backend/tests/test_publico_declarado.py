"""CADA RUTA DICE PARA QUIÉN ES, Y LA QUE NO LO DICE DENIEGA (05-10-2026).
Y LO QUE QUEDA DE LOS ROLES MUERTOS NO CRECE.

  1. LAS RUTAS. Recorre todas las de la app de verdad y se pone rojo si una no
     declara su público (`core/publico.py`) o declara dos, o si lo que declara no
     cuadra con lo que exige: ADMINISTRADORA sin sesión de salón, CUALQUIERA con
     ella, PLATAFORMA sin la cookie del panel…
  2. LA PUERTA, de verdad: en una app con la misma dependencia global, una ruta
     sin declarar contesta 403 y su código NO llega a correr.
  3. LOS ROLES MUERTOS (estilista, recepcionista). Solo la administradora tiene
     cuenta, y la 0069 los borró. Rojo si vuelve algo: otra comprobación de rol, otro `require_roles` con otro rol, o algo
     que escriba `users.pin_fichaje_hash` o un rol que no sea «admin» —en Python,
     en una migración nueva, en los seeds o en una función de la base—.

Cada regla, con su control.
"""

from __future__ import annotations

import pathlib

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.core import publico
from app.core.admin_deps import get_admin_claims
from app.core.impersonation_policy import iterar_rutas
from app.core.publico import Publico, exigir_publico_declarado, publicos_de
from app.core.security import dispositivo_del_mostrador, get_current_user, get_current_user_aunque_suspendida
from app.core.sesion_referido import sesion_de_referido
from tests.roles_muertos import hallazgos_backend, hallazgos_python, hallazgos_sql, hallazgos_supabase

RAIZ = pathlib.Path(__file__).resolve().parents[1]


def _app_real() -> FastAPI:
    from app.main import app
    return app


# ═══ 1. Las rutas ════════════════════════════════════════════════════════


def _llega(dependant, funcion) -> bool:
    return any(d.call is funcion or _llega(d, funcion) for d in dependant.dependencies)


def problemas_de(rutas) -> list[str]:
    out = []
    for r in iterar_rutas(rutas):
        if not isinstance(r, APIRoute):
            continue
        nombre = f"{'/'.join(sorted(r.methods))} {r.path}"
        ps = publicos_de(r.dependant)
        if len(ps) != 1:
            out.append(f"{nombre}: declara {sorted(p.value for p in ps) or 'nada'}")
            continue
        p = next(iter(ps))
        sesion = _llega(r.dependant, get_current_user)
        puerta = _llega(r.dependant, get_current_user_aunque_suspendida)
        panel = _llega(r.dependant, get_admin_claims)
        # 0071: la sesión del referido. Una ruta que la alcance y no declare
        # REFERIDO —o que la declare y alcance otra sesión— sale aquí.
        referido = _llega(r.dependant, sesion_de_referido)
        # 0090: las puertas del mostrador las autoriza la cookie del dispositivo.
        disp = _llega(r.dependant, dispositivo_del_mostrador)
        esperado = {
            Publico.ADMINISTRADORA: (True, False, False, False, False),
            Publico.QUIOSCO: (True, False, False, False, False),
            Publico.ADMINISTRADORA_AUNQUE_SUSPENDIDA: (False, True, False, False, False),
            Publico.PLATAFORMA: (False, False, True, False, False),
            Publico.REFERIDO: (False, False, False, True, False),
            Publico.CUALQUIERA: (False, False, False, False, False),
            Publico.CON_ENLACE: (False, False, False, False, False),
            Publico.WEBHOOK: (False, False, False, False, False),
            Publico.SEGUIMIENTO: (False, False, False, False, False),
            Publico.MOSTRADOR: (False, False, False, False, True),
        }[p]
        if (sesion, puerta, panel, referido, disp) != esperado:
            out.append(f"{nombre}: declara {p.value} y exige sesión={sesion}, puerta={puerta}, "
                       f"panel={panel}, referido={referido}, mostrador={disp}")
    return out


def test_cada_ruta_declara_un_publico_y_es_el_que_exige():
    app = _app_real()
    rutas = [r for r in iterar_rutas(app.routes) if isinstance(r, APIRoute)]
    assert len(rutas) > 120  # antivacío: el recorrido ve las rutas de los routers incluidos
    assert problemas_de(app.routes) == []


def _app_con(*rutas_a_mano) -> FastAPI:
    app = FastAPI(dependencies=[Depends(exigir_publico_declarado)])
    r = APIRouter()
    for metodo, ruta, deps, fn in rutas_a_mano:
        r.add_api_route(ruta, fn, methods=[metodo], dependencies=deps)
    app.include_router(r)
    return app


async def _nada():
    return {"ok": True}


@pytest.mark.parametrize("deps, motivo", [
    ([], "declara nada"),
    ([publico.CUALQUIERA, publico.WEBHOOK], "declara ['cualquiera', 'webhook']"),
    ([publico.CUALQUIERA, Depends(get_current_user)], "exige sesión=True"),
    ([publico.ADMINISTRADORA_AUNQUE_SUSPENDIDA, Depends(get_current_user)], "exige sesión=True"),
    ([publico.PLATAFORMA, Depends(get_current_user)], "exige sesión=True"),
    ([publico.REFERIDO, Depends(get_current_user)], "exige sesión=True"),
    ([publico.ADMINISTRADORA, Depends(sesion_de_referido)], "referido=True"),
    ([publico.CUALQUIERA, Depends(sesion_de_referido)], "referido=True"),
], ids=["sin_declarar", "dos_publicos", "cualquiera_con_sesion", "puerta_por_el_muro", "panel_con_sesion",
        "referido_con_sesion_de_salon", "salon_con_sesion_de_referido", "portal_sin_declararlo"])
def test_CONTROL_una_ruta_mal_declarada_se_pone_roja(deps, motivo):
    app = _app_con(("GET", "/zz", deps, _nada))
    problemas = problemas_de(app.routes)
    assert problemas and motivo in problemas[0], problemas


def test_CONTROL_administradora_sin_sesion_se_pone_roja():
    """No se puede fabricar con `Depends`: la declaración YA trae la sesión. Se
    comprueba la regla con una declaración falsificada."""
    async def falsa():
        return None
    falsa.__publico__ = Publico.ADMINISTRADORA
    app = _app_con(("GET", "/zz", [Depends(falsa)], _nada))
    assert "exige sesión=False" in problemas_de(app.routes)[0]


# ═══ 2. La puerta, de verdad ═════════════════════════════════════════════


def test_la_app_de_verdad_deniega_lo_no_declarado_lo_primero():
    deps = [d.dependency for d in _app_real().router.dependencies]
    assert deps[0] is exigir_publico_declarado, deps


def test_una_ruta_sin_declarar_contesta_403_y_su_codigo_no_corre():
    corrio = []

    async def sin_declarar():
        corrio.append(1)
        return {"ok": True}

    app = _app_con(("GET", "/sin", [], sin_declarar), ("GET", "/con", [publico.CUALQUIERA], _nada),
                   ("GET", "/dos", [publico.CUALQUIERA, publico.WEBHOOK], sin_declarar))
    c = TestClient(app)
    r = c.get("/sin")
    assert r.status_code == 403 and "no declara quién puede usarla" in r.json()["detail"]
    assert c.get("/dos").status_code == 403
    assert corrio == []
    assert c.get("/con").status_code == 200


def test_CONTROL_sin_la_dependencia_global_la_ruta_sin_declarar_SI_correria():
    """Lo que pasaba antes: sin `exigir_publico_declarado`, nace abierta."""
    app = FastAPI()
    corrio = []

    @app.get("/sin")
    async def sin_declarar():
        corrio.append(1)
        return {"ok": True}

    assert TestClient(app).get("/sin").status_code == 200 and corrio == [1]


# ═══ 3. Los roles muertos ════════════════════════════════════════════════

# EL INVENTARIO DEL 05-10-2026 se borró en la 0069. Desde entonces, vacío: lo
# que aparezca nombrando o comprobando un rol muerto, o escribiendo el PIN de
# `users`, rojo.
YA_HABIA_EN_PYTHON: set[tuple[str, str, str]] = set()
YA_HABIA_EN_SQL: set[tuple[str, str, str]] = set()

# A partir de qué migración se mira: las anteriores son historia.
DESDE_MIGRACION = 69


def test_lo_que_queda_de_los_roles_muertos_no_crece_en_python():
    hallado = set(hallazgos_backend(RAIZ))
    assert hallado - YA_HABIA_EN_PYTHON == set()
    # Y la lista no guarda lo que ya se borró: quitarlo de la lista es parte de borrarlo.
    assert YA_HABIA_EN_PYTHON - hallado == set()


def test_lo_que_queda_de_los_roles_muertos_no_crece_en_sql():
    hallado = set(hallazgos_supabase(RAIZ, DESDE_MIGRACION))
    assert hallado - YA_HABIA_EN_SQL == set()
    assert YA_HABIA_EN_SQL - hallado == set()


@pytest.mark.parametrize("codigo, que", [
    ('def f(user):\n    if user.rol == "recepcionista":\n        return 1\n', "nombra el rol 'recepcionista'"),
    ('def f(user):\n    if user.rol != "admin":\n        return 1\n', "compara un rol con «admin»"),
    ('def f(ficha):\n    return (ficha.get("rol") or "") == "admin"\n', "compara un rol con «admin»"),
    ('ruta = Depends(require_roles("admin", "estilista"))\n', "require_roles con otro rol"),
    ('async def f(db):\n    await db.update("users", {"id": "eq.1"}, {"pin_fichaje_hash": h})\n', "escribe users.pin_fichaje_hash"),
    ('async def f(db, r):\n    await db.insert("users", {"nombre": "x", "rol": r})\n', "escribe users.rol = r"),
], ids=["nombra_un_rol", "compara_con_admin", "compara_por_get", "require_roles", "escribe_el_pin", "escribe_un_rol"])
def test_CONTROL_lo_nuevo_en_python_se_pone_rojo(codigo, que):
    hallado = hallazgos_python(codigo, "app/zz.py")
    assert any(que in h[2] for h in hallado), hallado


def test_lo_que_no_es_un_rol_muerto_no_cuenta():
    """Las roles del PANEL (owner, support, viewer) y escribir «admin» no son esto,
    ni la recepcionista de carne y hueso de los comentarios y docstrings."""
    codigo = (
        '"""La recepcionista agenda a quien tiene delante."""\n'
        "def f(claims, roles):\n"
        "    # la recepcionista\n"
        "    if claims.rol not in roles:\n"
        "        return 1\n"
        'async def g(db):\n    await db.insert("users", {"nombre": "x", "rol": "admin"})\n'
    )
    assert hallazgos_python(codigo, "app/zz.py") == []


@pytest.mark.parametrize("sql, que", [
    ("insert into public.users (salon_id, nombre, email, rol) values ('s', 'x', 'x@x', 'estilista');", "escribe un rol muerto"),
    ("update public.users set pin_fichaje_hash = 'h' where id = 'x';", "escribe users.pin_fichaje_hash"),
], ids=["inserta_estilista", "pone_el_pin"])
def test_CONTROL_lo_nuevo_en_sql_se_pone_rojo(sql, que):
    assert any(que == h[2] for h in hallazgos_sql(sql, "supabase/migrations/0099_zz.sql"))


def test_en_sql_no_cuenta_lo_que_no_escribe_ni_el_pin_de_employees():
    sql = (
        "-- una estilista de antes\n"
        "update public.employees set pin_fichaje_hash = 'h' where id = 'x';\n"
        "delete from public.users where rol = 'estilista';\n"
    )
    assert hallazgos_sql(sql, "supabase/migrations/0099_zz.sql") == []
