"""LA FRONTERA DEL PORTAL DEL REFERIDO, leída del código (0071, 05-10-2026).

El referido no lleva ningún token que llegue a Postgres: la frontera la pone el
backend, y en un solo sitio (`core/sesion_referido.sesion_de_referido`). Aquí se
recorre el árbol de sintaxis, no una lista:

  1. TODA consulta de una ruta del portal sale del `referido.id` que da la
     sesión, y ninguna ruta del portal recibe un identificador del cliente
     (ni en la ruta, ni en la consulta, ni en el cuerpo). Un cuerpo solo se
     admite si su modelo no tiene ningún campo con pinta de identificador (el
     país de `conectar`, 05-10-2026).
     UNA EXCEPCIÓN, ACOTADA (0077): el trabajador de una agencia. La agencia
     elige a cuál dar de baja, y ese id llega del cliente. Se admite solo si
     CADA vez que se usa va en la misma llamada que `referido.id`: quien
     decide si es suyo es la base (`baja_de_trabajador` y `poner_seguimiento`
     filtran por agencia), y eso lo prueba `test_agencias_base_real.py`.
  2. LA CONTRASEÑA DEL REFERIDO no acaba en Supabase Auth ni en `admin_users`
     (ni en `users`): ningún módulo del referido los toca, y `password_hash`
     solo se escribe a través de `referido_usar_enlace`, en la base.

Que una sesión de referido no alcance ninguna ruta de salón ni del panel lo
prueba `test_portal_referido_base_real.py`, de verdad; que cada ruta del portal
declare REFERIDO, `test_publico_declarado.py`. Cada guarda con su control.
"""

from __future__ import annotations

import ast
import pathlib
import re

from fastapi.routing import APIRoute

from app.core.impersonation_policy import iterar_rutas
from app.core.publico import Publico, publicos_de

APP = pathlib.Path(__file__).resolve().parents[1] / "app"
PORTAL = APP / "routers" / "portal_referido.py"
DEL_REFERIDO = [APP / "core" / "sesion_referido.py", APP / "core" / "acceso_referido.py", PORTAL,
                APP / "routers" / "referidos.py"]
_CONSULTAS = {"select", "rpc", "insert", "update", "delete", "count", "upsert_many", "insert_many"}
# Lo que una ruta del portal puede recibir: la sesión, los ajustes y la petición.
_PARAMETROS_PERMITIDOS = {"referido", "settings", "request"}
# Un campo de un cuerpo con pinta de identificador.
_IDENTIFICADOR = re.compile(r"(^id$|_id$|referido|cuenta|acct|token)")


# ═══ 1. Todo sale del referido de la sesión ══════════════════════════════


def _rutas_del_portal(arbol: ast.Module) -> list[ast.AsyncFunctionDef | ast.FunctionDef]:
    """Las funciones decoradas con `@router_portal.…`."""
    fuera = []
    for n in ast.walk(arbol):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for d in n.decorator_list:
                if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and \
                        isinstance(d.func.value, ast.Name) and d.func.value.id == "router_portal":
                    fuera.append(n)
    return fuera


def _nombra_al_referido(nodo: ast.AST) -> bool:
    return any(isinstance(n, ast.Attribute) and n.attr == "id" and isinstance(n.value, ast.Name)
               and n.value.id == "referido" for n in ast.walk(nodo))


def _cuerpos_sin_identificador(arbol: ast.Module, f) -> set[str]:
    """Los parámetros de `f` cuyo modelo (del mismo módulo) no tiene ningún
    campo con pinta de identificador."""
    modelos = {n.name: n for n in ast.walk(arbol) if isinstance(n, ast.ClassDef)}
    buenos = set()
    for a in f.args.args + f.args.kwonlyargs:
        nombres = {n.id for n in ast.walk(a.annotation) if isinstance(n, ast.Name)} if a.annotation else set()
        clases = [modelos[n] for n in nombres if n in modelos]
        if not clases:
            continue
        campos = [c.target.id for m in clases for c in m.body
                  if isinstance(c, ast.AnnAssign) and isinstance(c.target, ast.Name)]
        if campos and not any(_IDENTIFICADOR.search(x) for x in campos):
            buenos.add(a.arg)
    return buenos


def _siempre_con_el_referido(f, nombre: str) -> bool:
    """Cada uso de `nombre` en el cuerpo de `f` es un argumento de una llamada
    que lleva también `referido.id`. Sin usos, no: no hay nada que lo acote."""
    llamadas = [n for n in ast.walk(f) if isinstance(n, ast.Call)]
    usos = [n for n in ast.walk(f) if isinstance(n, ast.Name) and n.id == nombre]
    if not usos:
        return False
    for uso in usos:
        acotado = False
        for c in llamadas:
            args = list(c.args) + [k.value for k in c.keywords]
            if any(uso is x or any(uso is y for y in ast.walk(x)) for x in args) and \
                    any(_nombra_al_referido(x) for x in args):
                acotado = True
                break
        if not acotado:
            return False
    return True


def fugas(codigo: str) -> list[str]:
    fuera = []
    arbol = ast.parse(codigo)
    for f in _rutas_del_portal(arbol):
        nombres = {a.arg for a in f.args.args + f.args.kwonlyargs}
        de_mas = nombres - _PARAMETROS_PERMITIDOS - _cuerpos_sin_identificador(arbol, f)
        # El trabajador de una agencia (0077): solo si viaja siempre con referido.id.
        de_mas = {x for x in de_mas if not (x.startswith("trabajador") and _siempre_con_el_referido(f, x))}
        if de_mas:
            fuera.append(f"{f.name}: recibe {sorted(de_mas)} del cliente")
        if "referido" not in nombres:
            fuera.append(f"{f.name}: no recibe la sesión del referido")
        for n in ast.walk(f):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in _CONSULTAS:
                if not any(_nombra_al_referido(a) for a in list(n.args) + [k.value for k in n.keywords]):
                    fuera.append(f"{f.name}:{n.lineno}: {n.func.attr} sin referido.id")
    return fuera


def test_cada_consulta_del_portal_sale_del_referido_de_la_sesion():
    codigo = PORTAL.read_text(encoding="utf-8")
    assert len(_rutas_del_portal(ast.parse(codigo))) >= 2  # antivacío: portal y salir
    assert fugas(codigo) == []


def test_las_rutas_del_portal_son_las_que_declaran_referido():
    """Y al revés: lo que declara REFERIDO es justo lo del `router_portal`."""
    from app.main import app

    declaran = {(sorted(r.methods)[0], r.path) for r in iterar_rutas(app.routes)
                if isinstance(r, APIRoute) and publicos_de(r.dependant) == {Publico.REFERIDO}}
    assert declaran == {("GET", "/referido/portal"), ("POST", "/referido/salir"),
                        ("POST", "/referido/cobros/conectar"), ("POST", "/referido/cobros/sincronizar"),
                        # Los trabajadores de una agencia (0077).
                        ("POST", "/referido/trabajadores"),
                        ("POST", "/referido/trabajadores/{trabajador_id}/baja"),
                        ("POST", "/referido/trabajadores/{trabajador_id}/seguimiento"),
                        ("POST", "/referido/trabajadores/{trabajador_id}/seguimiento/revocar")}


_TRABAJADOR_SUELTO = '''
@router_portal.post("/trabajadores/{trabajador_id}/baja")
async def baja(trabajador_id: UUID, referido: ReferidoActual = Depends(sesion_de_referido), settings=None):
    await agencias.es_agencia(db, referido.id)
    await db.rpc("baja_de_trabajador", {"p_trabajador": str(trabajador_id)})
'''

_TRABAJADOR_ACOTADO = '''
@router_portal.post("/trabajadores/{trabajador_id}/baja")
async def baja(trabajador_id: UUID, referido: ReferidoActual = Depends(sesion_de_referido), settings=None):
    await agencias.dar_de_baja(db, referido.id, str(trabajador_id))
'''


def test_CONTROL_el_id_de_un_trabajador_sin_la_agencia_al_lado_se_pone_rojo():
    """El id de un trabajador que llega del cliente y se usa SIN `referido.id`
    en la misma llamada: cualquiera daría de baja al trabajador de otra agencia."""
    assert any("recibe ['trabajador_id'] del cliente" in h for h in fugas(_TRABAJADOR_SUELTO))
    assert fugas(_TRABAJADOR_ACOTADO) == []


_CON_ID_DEL_CLIENTE = '''
@router_portal.get("/altas/{referido_id}")
async def altas(referido_id: str, referido: ReferidoActual = Depends(sesion_de_referido)):
    return await db.rpc("portal_del_referido", {"p_referido": referido_id})
'''

_SIN_FILTRO = '''
@router_portal.get("/todo")
async def todo(referido: ReferidoActual = Depends(sesion_de_referido), settings=None):
    return await servicio(settings).select("altas_atribuidas", {"select": "*"})
'''


def test_CONTROL_un_id_que_llega_del_cliente_se_pone_rojo():
    hallado = fugas(_CON_ID_DEL_CLIENTE)
    assert any("recibe ['referido_id'] del cliente" in h for h in hallado), hallado
    assert any("rpc sin referido.id" in h for h in hallado), hallado


_CUERPO_CON_ID = '''
class CambiarIn(BaseModel):
    pais: str | None = None
    referido_id: str

@router_portal.post("/cambiar")
async def cambiar(payload: CambiarIn, referido: ReferidoActual = Depends(sesion_de_referido)):
    return await db.rpc("x", {"p_referido": referido.id})
'''


def test_CONTROL_un_cuerpo_con_un_identificador_se_pone_rojo():
    assert any("recibe ['payload'] del cliente" in h for h in fugas(_CUERPO_CON_ID))
    # Y el mismo cuerpo sin el identificador, no.
    assert fugas(_CUERPO_CON_ID.replace("    referido_id: str\n", "")) == []


def test_CONTROL_una_consulta_sin_el_referido_se_pone_roja():
    assert any("select sin referido.id" in h for h in fugas(_SIN_FILTRO))


# ═══ 2. La contraseña no va a ninguna otra puerta ════════════════════════

_OTRAS_PUERTAS = ("SupabaseAuthAdminClient", "auth_admin", "_auth_admin", "generate_signup_link",
                  "generate_recovery_link", "create_unconfirmed_user", "update_user_by_id")
_TABLAS_DE_OTRAS_PUERTAS = {"admin_users", "users"}


def otra_puerta(codigo: str, nombre: str) -> list[str]:
    fuera = []
    for n in ast.walk(ast.parse(codigo)):
        if isinstance(n, (ast.Name, ast.Attribute)):
            texto = n.id if isinstance(n, ast.Name) else n.attr
            if texto in _OTRAS_PUERTAS:
                fuera.append(f"{nombre}:{n.lineno}: usa {texto}")
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in _CONSULTAS \
                and n.args and isinstance(n.args[0], ast.Constant) and n.args[0].value in _TABLAS_DE_OTRAS_PUERTAS:
            fuera.append(f"{nombre}:{n.lineno}: toca {n.args[0].value}")
        # `password_hash` como clave de un diccionario que se escribe: solo la
        # base lo escribe (referido_usar_enlace).
        if isinstance(n, ast.Dict) and any(isinstance(k, ast.Constant) and k.value == "password_hash" for k in n.keys):
            fuera.append(f"{nombre}:{n.lineno}: escribe password_hash")
    return fuera


def test_la_contrasena_del_referido_no_va_a_auth_ni_al_panel():
    hallado = []
    for ruta in DEL_REFERIDO:
        hallado += otra_puerta(ruta.read_text(encoding="utf-8"), ruta.name)
    assert hallado == []


def test_y_la_escribe_solo_la_base():
    codigo = PORTAL.read_text(encoding="utf-8")
    assert '"referido_usar_enlace"' in codigo and '"p_password_hash": nuevo' in codigo


def test_CONTROL_mandarla_a_auth_o_al_panel_se_pone_rojo():
    a_auth = "async def f(auth_admin, e, p):\n    await auth_admin.create_unconfirmed_user(e, p)\n"
    al_panel = 'async def f(db, h):\n    await db.insert("admin_users", {"email": "x", "password_hash": h})\n'
    a_mano = 'async def f(db, h):\n    await db.update("referidos", {"id": "eq.1"}, {"password_hash": h})\n'
    assert any("create_unconfirmed_user" in h for h in otra_puerta(a_auth, "zz.py"))
    assert any("toca admin_users" in h for h in otra_puerta(al_panel, "zz.py"))
    assert any("escribe password_hash" in h for h in otra_puerta(a_mano, "zz.py"))
