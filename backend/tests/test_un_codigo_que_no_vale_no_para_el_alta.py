"""UN CÓDIGO QUE NO VALE NO PARA EL ALTA, Y DEJA RASTRO (0077, 06-10-2026).

«Un alta perdida cuesta más que una atribución perdida: esa es la dirección en
la que debe fallar.» El código del formulario puede no existir, ser de un
trabajador dado de baja o venir mal escrito: el salón entra igual, sin
atribución, y el código queda en `codigos_que_no_valieron` con la cuenta a la
que llegó, para repescarlo a mano.

Se recorre el código, no una lista:

  1. Toda función de `backend/app` que llama a `atribuir_alta` no hace nada
     con lo que contesta que pueda parar el alta: ni un `raise` que dependa de
     ello, ni una condición que corte el camino.
  2. El campo del alta (`SignupIn.codigo_referido`) no tiene ninguna regla que
     devuelva un 422: ni largo máximo, ni patrón. Un código larguísimo se corta.
  3. De verdad, por `/auth/signup` y contra la base: un código que no existe,
     uno de un trabajador de baja y uno de 200 caracteres entran con 202, sin
     atribución y con su rastro.

Cada guarda con su control.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from app.schemas.auth import SignupIn

RAIZ = pathlib.Path(__file__).resolve().parents[1] / "app"


def _llama_a_atribuir(n: ast.AST) -> bool:
    return isinstance(n, ast.Call) and (
        (isinstance(n.func, ast.Name) and n.func.id == "atribuir_alta")
        or (isinstance(n.func, ast.Attribute) and n.func.attr == "atribuir_alta"))


def bloqueos(codigo: str, nombre: str) -> tuple[list[str], list[str]]:
    """(las funciones que atribuyen, las que pueden parar el alta por lo que contesta)."""
    atribuyen, bloquean = [], []
    for f in ast.walk(ast.parse(codigo)):
        if not isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)) or f.name == "atribuir_alta":
            continue
        if not any(_llama_a_atribuir(n) for n in ast.walk(f)):
            continue
        atribuyen.append(f"{nombre}::{f.name}")
        # Las variables que reciben lo que contesta.
        vars_ = {t.id for n in ast.walk(f) if isinstance(n, (ast.Assign, ast.AnnAssign))
                 and any(_llama_a_atribuir(x) for x in ast.walk(n.value or ast.Pass()))
                 for t in (n.targets if isinstance(n, ast.Assign) else [n.target]) if isinstance(t, ast.Name)}
        for n in ast.walk(f):
            # Una condición sobre lo que contesta que corta: raise o return.
            if isinstance(n, (ast.If, ast.IfExp, ast.Assert, ast.While)):
                prueba = n.test
                mira = any((isinstance(x, ast.Name) and x.id in vars_) or _llama_a_atribuir(x) for x in ast.walk(prueba))
                corta = isinstance(n, ast.Assert) or any(isinstance(x, (ast.Raise, ast.Return))
                                                         for x in ast.walk(n) if x is not prueba)
                if mira and corta:
                    bloquean.append(f"{nombre}::{f.name}:{n.lineno}")
            # O que se le pase a un error.
            if isinstance(n, ast.Raise) and n.exc is not None and \
                    any(isinstance(x, ast.Name) and x.id in vars_ for x in ast.walk(n.exc)):
                bloquean.append(f"{nombre}::{f.name}:{n.lineno}")
    return atribuyen, bloquean


def _todo_el_backend() -> tuple[list[str], list[str]]:
    atribuyen, bloquean = [], []
    for ruta in sorted(RAIZ.rglob("*.py")):
        a, b = bloqueos(ruta.read_text(encoding="utf-8"), ruta.relative_to(RAIZ.parent).as_posix())
        atribuyen += a
        bloquean += b
    return atribuyen, bloquean


def test_lo_que_contesta_atribuir_alta_nunca_para_el_alta():
    atribuyen, bloquean = _todo_el_backend()
    assert "app/routers/auth.py::signup" in atribuyen  # antivacío
    assert bloquean == []


_BLOQUEA_CON_RAISE = '''
async def signup(db, payload):
    resultado = await atribuir_alta(db, "c", payload.codigo_referido)
    if resultado != "atribuida":
        raise HTTPException(status_code=422, detail="Ese código no existe.")
'''

_BLOQUEA_CON_ASSERT = '''
async def signup(db, payload):
    assert await atribuir_alta(db, "c", payload.codigo_referido) == "atribuida"
'''

_SOLO_LO_APUNTA = '''
async def signup(db, payload):
    resultado = await atribuir_alta(db, "c", payload.codigo_referido)
    logger.info("Signup: código de referido → %s", resultado)
'''


@pytest.mark.parametrize("codigo", [_BLOQUEA_CON_RAISE, _BLOQUEA_CON_ASSERT], ids=["raise", "assert"])
def test_CONTROL_un_alta_que_se_para_por_el_codigo_se_pone_roja(codigo):
    assert bloqueos(codigo, "app/zz.py")[1] != []


def test_CONTROL_apuntar_lo_que_contesta_no_es_parar():
    assert bloqueos(_SOLO_LO_APUNTA, "app/zz.py") == (["app/zz.py::signup"], [])


# ═══ 2. El campo no devuelve un 422 ══════════════════════════════════════

_REGLAS_QUE_RECHAZAN = {"maxLength", "minLength", "pattern", "enum", "const", "format"}


def reglas_que_rechazan(modelo) -> list[str]:
    propiedad = modelo.model_json_schema()["properties"]["codigo_referido"]
    variantes = [propiedad] + propiedad.get("anyOf", [])
    return sorted({k for v in variantes for k in v if k in _REGLAS_QUE_RECHAZAN})


def test_el_campo_del_codigo_no_tiene_reglas_que_rechacen_el_alta():
    assert reglas_que_rechazan(SignupIn) == []
    # Y uno larguísimo se corta a lo que guarda la base, no se rechaza.
    datos = {"email": "a@b.es", "password": "UnaClaveBastanteLarga123!", "salon_nombre": "Salón Sol",
             "direccion": "Calle 1", "telefono": "600112233", "admin_nombre": "Ana", "plan": "pro",
             "cif": "B12345674", "codigo_referido": "Z" * 500}
    assert SignupIn(**datos).codigo_referido == "Z" * 64


def test_CONTROL_un_largo_maximo_en_el_campo_se_pone_rojo():
    from pydantic import BaseModel, Field

    class ConLargo(BaseModel):
        codigo_referido: str | None = Field(default=None, max_length=64)

    class ConPatron(BaseModel):
        codigo_referido: str | None = Field(default=None, pattern=r"^[A-Z0-9]{6}$")

    assert reglas_que_rechazan(ConLargo) == ["maxLength"]
    assert reglas_que_rechazan(ConPatron) == ["pattern"]


# ═══ 3. De verdad, por el alta ═══════════════════════════════════════════

from tests.test_la_atribucion_base_real import alta, anyio_backend  # noqa: E402,F401 — fixtures
from tests.test_un_salon_no_puede_cambiarse_el_plan import sql  # noqa: E402


@pytest.mark.base_real
@pytest.mark.anyio
async def test_un_codigo_que_no_existe_de_baja_o_larguisimo_entra_igual_y_deja_rastro(alta):
    # Una agencia aprobada con un trabajador dado de baja.
    sql("""
do $$ declare ag uuid; begin
  insert into public.referidos (nombre, email, codigo, estado, tipo, aprobado_at)
    values ('zz Agencia del alta', 'zz.alta@agencias.test', 'ZZAA01', 'aprobado', 'agencia', now()) returning id into ag;
  perform public.alta_de_trabajador(ag, 'Bea Se Fue', 'ZZBA01');
  update public.trabajadores_de_agencia set de_baja_at = now() where codigo = 'ZZBA01';
end $$;
""")
    try:
        for llegado, motivo in (("ZZNO01", "no_existe"), (" zzba01 ", "trabajador_de_baja"),
                                ("Q" * 200, "no_existe")):
            cuenta = await alta["dar_de_alta"](llegado)  # 202: el alta sigue
            assert sql(f"select count(*) from public.altas_atribuidas where cuenta_id = '{cuenta['account_id']}';") == "0"
            rastro = sql("select motivo || '|' || cuenta_nombre || '|' || char_length(codigo_llegado) "
                         f"from public.codigos_que_no_valieron where cuenta_id = '{cuenta['account_id']}';")
            assert rastro == f"{motivo}|{cuenta['cuenta_nombre']}|{min(len(llegado), 64)}"
    finally:
        sql("""
set session_replication_role = replica;
delete from public.codigos_que_no_valieron where codigo_llegado in ('ZZNO01', ' zzba01 ') or codigo_llegado like 'QQQQ%';
set session_replication_role = origin;
delete from public.trabajadores_de_agencia where codigo = 'ZZBA01';
delete from public.referidos where codigo = 'ZZAA01';
""")
