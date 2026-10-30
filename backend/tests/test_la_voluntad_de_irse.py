"""UNA CANCELACIÓN NO SE BORRA SOLA (0053), y con ella pendiente no se cambia de plan.

Tres guardas, los tres recorriendo el código y no una lista:

  1. CON UNA BAJA PENDIENTE NINGÚN CAMBIO DE PLAN SE ACEPTA, ni subir ni bajar.
     Se descubren por el árbol sintáctico todas las rutas cuyo endpoint pasa
     por `_resolver_cambio` —hoy la vista previa y el cambio— y cada una se
     prueba de verdad en los dos sentidos y con los dos tipos de «pendiente»
     (la marca en Stripe, o solo nuestra voluntad). 409 y ni una escritura en
     Stripe.
  2. SOLO REANUDAR Y EL FIN REAL BORRAN LA VOLUNTAD. Se busca en todo `app/`
     cualquier sitio que ponga `baja_solicitada_at` a nulo; solo valen el
     endpoint `reanudar` y la rama de `_campos_de_la_suscripcion` que se ejecuta
     cuando la suscripción ha terminado de verdad.
  3. TODA ESCRITURA NUESTRA EN STRIPE VA FIRMADA, que es lo que deja a la
     alarma decir «esto lo hizo nuestro propio código».
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from app.routers import billing
from tests.test_baja_suscripcion import (
    PRECIOS,
    Base,
    StripeDoble,
    _cliente,
    _con_stripe,
    _fila,
)

APP = Path(__file__).resolve().parents[1] / "app"


# ═══ 1. Con una baja pendiente no se cambia de plan ═══════════════════════


def rutas_de_cambio_de_plan(fuente: str) -> set[str]:
    """`POST /ruta` de cada endpoint cuyo cuerpo llama a `_resolver_cambio`."""
    out: set[str] = set()
    for f in ast.walk(ast.parse(fuente)):
        if not isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        llama = any(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_resolver_cambio"
            for n in ast.walk(f)
        )
        if not llama:
            continue
        for d in f.decorator_list:
            if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr in ("post", "put", "patch"):
                out.add(f"{d.func.attr.upper()} {d.args[0].value}")
    return out


RUTAS = sorted(rutas_de_cambio_de_plan((APP / "routers" / "billing.py").read_text(encoding="utf-8")))

# (plan que tiene, plan al que va): una bajada y una subida.
SENTIDOS = {"bajar": ("elite", "pro"), "subir": ("pro", "elite")}
ESCRITURAS = ("modify", "sched_create", "sched_modify", "sched_release")


def _intento(monkeypatch, ruta, sentido, pendiente):
    actual, nuevo = SENTIDOS[sentido]
    fila = _fila(plan=actual)
    doble = StripeDoble(plan=actual)
    if pendiente in ("marca", "las_dos"):
        doble.sub["cancel_at_period_end"] = True
        fila["cancel_at_period_end"] = True
    if pendiente in ("voluntad", "las_dos"):
        fila["baja_solicitada_at"] = "2026-09-28T10:00:00+00:00"
    base = Base(fila)
    _con_stripe(monkeypatch, doble, base)
    monkeypatch.setattr(
        billing.stripe.Invoice, "create_preview_async",
        lambda **kw: _preview(), raising=False,
    )
    r = _cliente(base).post(ruta.split(" ", 1)[1], json={"plan": nuevo, "intervalo": "mensual"})
    return r, doble


async def _preview():
    return {"currency": "eur", "lines": {"data": [{"amount": 5900, "parent": {}}]}}


def test_el_descubridor_encuentra_la_vista_previa_y_el_cambio():
    assert RUTAS == ["POST /billing/cambiar-plan", "POST /billing/cambiar-plan/preview"]


@pytest.mark.parametrize("ruta", RUTAS)
@pytest.mark.parametrize("sentido", ["bajar", "subir"])
@pytest.mark.parametrize("pendiente", ["marca", "voluntad", "las_dos"])
def test_con_una_baja_pendiente_no_se_cambia_de_plan(monkeypatch, ruta, sentido, pendiente):
    r, doble = _intento(monkeypatch, ruta, sentido, pendiente)

    assert r.status_code == 409, r.text
    assert r.json()["detail"]["codigo"] == "baja_pendiente"
    assert [n for n, _ in doble.llamadas if n in ESCRITURAS] == []


@pytest.mark.parametrize("ruta", RUTAS)
@pytest.mark.parametrize("sentido", ["bajar", "subir"])
def test_EL_CONTROL_sin_baja_pendiente_si_se_cambia(monkeypatch, ruta, sentido):
    """Sin esto, el 409 de arriba podría venir de cualquier otro fallo."""
    r, _ = _intento(monkeypatch, ruta, sentido, pendiente="ninguna")

    assert r.status_code == 200, r.text


def test_EL_CONTROL_una_ruta_nueva_que_cambie_de_plan_se_descubre():
    fuente = '''
@router.post("/billing/cambiar-plan/express")
async def express(payload):
    actual = await _resolver_cambio(payload, db, user, settings)
'''
    assert rutas_de_cambio_de_plan(fuente) == {"POST /billing/cambiar-plan/express"}


# ═══ 2. Solo reanudar y el fin real borran la voluntad ════════════════════

CAMPO = "baja_solicitada_at"


def _es_none(n) -> bool:
    return isinstance(n, ast.Constant) and n.value is None


def _dentro_de_termina_de_verdad(padres: list) -> bool:
    return any(
        isinstance(p, ast.If) and "_TERMINA_DE_VERDAD" in ast.unparse(p.test) for p in padres
    )


def borrados_de_la_voluntad(fuente: str, fichero: str) -> list[str]:
    """Cada sitio que pone la voluntad a nulo, con el permiso que tiene."""
    out: list[str] = []

    def ir(n, funcion, padres):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcion = n.name
        borra = False
        if isinstance(n, ast.Dict):
            borra = any(
                isinstance(k, ast.Constant) and k.value == CAMPO and _es_none(v)
                for k, v in zip(n.keys, n.values)
            )
        elif isinstance(n, ast.Assign) and _es_none(n.value):
            borra = any(
                isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant) and t.slice.value == CAMPO
                for t in n.targets
            )
        elif isinstance(n, ast.keyword) and n.arg == CAMPO and _es_none(n.value):
            borra = True
        if borra:
            permitido = (fichero.endswith("routers/baja.py") and funcion == "reanudar") or (
                fichero.endswith("routers/billing.py")
                and funcion == "_campos_de_la_suscripcion"
                and _dentro_de_termina_de_verdad(padres)
            )
            out.append(f"{fichero}:{n.lineno} {funcion} {'PERMITIDO' if permitido else 'PROHIBIDO'}")
        for h in ast.iter_child_nodes(n):
            ir(h, funcion, padres + [n])

    ir(ast.parse(fuente), "<módulo>", [])
    return out


def _todos() -> list[str]:
    out = []
    for f in sorted(APP.rglob("*.py")):
        out += borrados_de_la_voluntad(f.read_text(encoding="utf-8"), f.relative_to(APP.parent).as_posix())
    return out


def test_solo_reanudar_y_el_fin_real_borran_la_voluntad():
    prohibidos = [x for x in _todos() if x.endswith("PROHIBIDO")]
    assert prohibidos == []


def test_EL_CONTROL_encuentra_los_dos_sitios_permitidos():
    """Antivacío: si el descubridor no viera nada, el verde de arriba no valdría."""
    permitidos = [x for x in _todos() if x.endswith("PERMITIDO")]
    assert any("routers/baja.py" in x and " reanudar " in x for x in permitidos)
    assert any("routers/billing.py" in x and "_campos_de_la_suscripcion" in x for x in permitidos)


@pytest.mark.parametrize(
    "codigo",
    [
        # Un cambio de plan que la limpiara «de paso».
        'async def cambiar_plan():\n    await guardar({"baja_solicitada_at": None})\n',
        # Por asignación.
        'def f(fields):\n    fields["baja_solicitada_at"] = None\n',
        # Por argumento con nombre.
        "def f():\n    return dict(baja_solicitada_at=None)\n",
        # En el sitio permitido, pero fuera de la rama del fin real.
        'def _campos_de_la_suscripcion(estado, fields):\n    fields["baja_solicitada_at"] = None\n',
    ],
)
def test_EL_CONTROL_cualquier_otro_borrado_se_detecta(codigo):
    fichero = "app/routers/billing.py"
    assert [x for x in borrados_de_la_voluntad(codigo, fichero) if x.endswith("PROHIBIDO")]


def test_EL_CONTROL_la_rama_del_fin_real_si_vale():
    codigo = (
        "def _campos_de_la_suscripcion(estado, fields):\n"
        "    if estado in _TERMINA_DE_VERDAD:\n"
        '        fields["baja_solicitada_at"] = None\n'
    )
    assert borrados_de_la_voluntad(codigo, "app/routers/billing.py")[0].endswith("PERMITIDO")


# ═══ 3. Toda escritura nuestra en Stripe va firmada ═══════════════════════

_ESCRIBEN = {("Subscription", m) for m in ("modify", "modify_async", "cancel", "cancel_async", "delete", "delete_async")} | {
    ("SubscriptionSchedule", m)
    for m in ("create", "create_async", "modify", "modify_async", "release", "release_async", "cancel", "cancel_async")
}


def escrituras_sin_firma(fuente: str, fichero: str) -> list[str]:
    out = []
    for n in ast.walk(ast.parse(fuente)):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)):
            continue
        objeto = n.func.value
        if not (isinstance(objeto, ast.Attribute) and (objeto.attr, n.func.attr) in _ESCRIBEN):
            continue
        clave = next((k.value for k in n.keywords if k.arg == "idempotency_key"), None)
        firmada = isinstance(clave, ast.Call) and getattr(clave.func, "id", "") == "clave_nuestra"
        if not firmada:
            out.append(f"{fichero}:{n.lineno} {objeto.attr}.{n.func.attr}")
    return out


def test_toda_escritura_nuestra_en_stripe_va_firmada():
    sin = []
    for f in sorted(APP.rglob("*.py")):
        sin += escrituras_sin_firma(f.read_text(encoding="utf-8"), f.relative_to(APP.parent).as_posix())
    assert sin == []


def test_EL_CONTROL_una_escritura_sin_firma_se_detecta():
    codigo = (
        "async def f():\n"
        "    await stripe.Subscription.modify_async(sid, cancel_at_period_end=False)\n"
        "    await stripe.SubscriptionSchedule.create_async(from_subscription=sid, idempotency_key='x')\n"
        "    await stripe.Subscription.modify_async(sid, idempotency_key=clave_nuestra('ok'))\n"
    )
    assert len(escrituras_sin_firma(codigo, "x.py")) == 2
