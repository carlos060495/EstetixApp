"""En un centro en pausa no se escribe. Y no por vigilancia: por construcción.

CÓMO SE SOSTIENE. Todo lo que el panel escribe lo escribe en `user.salon_id`
—son 188 sitios—. `get_current_user` garantiza que ese valor nunca es un centro
en pausa, así que ninguno de los 188 puede escribir en uno, y no hay que
acordarse de nada en ninguno de ellos. Poner un `if` en cada endpoint es
exactamente la forma de que un día falte en uno.

LO QUE SÍ HAY QUE VIGILAR, y es lo que hace este fichero: los pocos sitios donde
un centro llega DESDE FUERA de la sesión —por la ruta, por el query o dentro del
cuerpo—. Ahí el valor no ha pasado por esa garantía, así que cada uno se
comprueba por separado o se apunta con su motivo. Mismo patrón que
`test_consultas_publicas_filtran_por_centro.py`, y por el mismo motivo: un
camino nuevo no puede entrar sin que nadie se entere.

NO SE MIRAN LAS FUNCIONES INTERNAS. Casi todas llevan un `salon_id` en la firma
y lo reciben de quien las llama, que lo sacó de la sesión: listarlas aquí sería
cincuenta entradas que nadie lee y que esconderían las tres que importan.
"""

from __future__ import annotations

import ast
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
BACKEND = RAIZ / "backend" / "app"

# Cómo llama este repositorio a un centro cuando viaja como dato.
NOMBRES_DE_CENTRO = ("salon_id", "centro_id", "copiar_servicios_de", "centro_que_sigue")

_METODOS = {"get", "post", "put", "patch", "delete"}

# Los cuerpos de petición terminan en `In` por convención de la casa; lo que
# termina en `Out` sale, no entra.
_SUFIJO_DE_ENTRADA = "In"


# Cada entrada: por qué ese sitio puede recibir un centro de fuera sin que sea
# un agujero. El motivo tiene que explicarlo, no etiquetarlo.
ACEPTADOS: dict[str, str] = {
    "routers/centros.py · CrearCentroIn.copiar_servicios_de": (
        "Es el centro del que se COPIA el catálogo al abrir uno nuevo, y solo se "
        "lee de él. Abrir un centro solo se puede cuando el plan admite más de "
        "los que hay, y en esa cuenta no hay ninguno en pausa; además "
        "`_copiar_servicios` comprueba que el origen es de la misma cuenta."
    ),
    "routers/billing.py · CambioPlanIn.centro_que_sigue": (
        "Es la elección de la titular al PROGRAMAR una bajada, o sea antes de "
        "que exista ninguna pausa. Se escribe en `salons.sigue_al_bajar`, que no "
        "es un dato de trabajo del centro sino su decisión, y el índice único "
        "parcial de la 0049 impide que queden dos marcados."
    ),
    "routers/centros.py · ElegirCentroIn.centro_que_sigue": (
        "Es la titular cambiando QUÉ centro se queda activo (0088): no escribe "
        "ningún dato de trabajo en él, solo `salons.sigue_al_bajar`, y "
        "`elegir_el_que_sigue` comprueba que el centro es de su cuenta, que el "
        "plan no los cubre todos y que no ha cambiado en los últimos 30 días."
    ),
    "routers/admin_plataforma.py · logs(salon_id)": (
        "Es el panel de PLATAFORMA, que no pasa por la frontera del inquilino ni "
        "por el plan: es soporte mirando los registros de un salón, igual que "
        "entra en una cuenta suspendida a propósito. Filtra, no escribe."
    ),
}


def _ruta(fichero: Path) -> str:
    return str(fichero.relative_to(BACKEND.parent)).replace("\\", "/").replace("app/", "")


def _es_handler_de_ruta(nodo: ast.AST) -> bool:
    for dec in getattr(nodo, "decorator_list", []):
        f = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(f, ast.Attribute) and f.attr in _METODOS:
            return True
    return False


def puertas_por_las_que_entra_un_centro() -> dict[str, str]:
    """Cada sitio donde un centro puede entrar desde la petición.

    Dos formas, y solo dos: el parámetro de un handler de ruta (ruta o query) y
    el campo de un cuerpo de petición.
    """
    fuera: dict[str, str] = {}

    for fichero in sorted((BACKEND / "routers").rglob("*.py")):
        if "__pycache__" in fichero.parts:
            continue
        arbol = ast.parse(fichero.read_text(encoding="utf-8"), filename=str(fichero))
        for nodo in ast.walk(arbol):
            if not isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not _es_handler_de_ruta(nodo):
                continue
            a = nodo.args
            for arg in (*a.posonlyargs, *a.args, *a.kwonlyargs):
                if arg.arg in NOMBRES_DE_CENTRO:
                    clave = f"{_ruta(fichero)} · {nodo.name}({arg.arg})"
                    fuera[clave] = f"parámetro de {nodo.name}"

    for fichero in sorted((BACKEND / "schemas").rglob("*.py")):
        if "__pycache__" in fichero.parts:
            continue
        arbol = ast.parse(fichero.read_text(encoding="utf-8"), filename=str(fichero))
        for nodo in ast.walk(arbol):
            if not isinstance(nodo, ast.ClassDef) or not nodo.name.endswith(_SUFIJO_DE_ENTRADA):
                continue
            for campo in nodo.body:
                if (
                    isinstance(campo, ast.AnnAssign)
                    and isinstance(campo.target, ast.Name)
                    and campo.target.id in NOMBRES_DE_CENTRO
                ):
                    clave = f"routers/{fichero.stem}.py · {nodo.name}.{campo.target.id}"
                    fuera[clave] = f"campo de {nodo.name}"
    return fuera


# ═══════════════════════════════════════════════════════ el registro


def test_ningun_camino_nuevo_recibe_un_centro_sin_que_nadie_lo_haya_decidido():
    encontradas = puertas_por_las_que_entra_un_centro()
    nuevas = sorted(set(encontradas) - set(ACEPTADOS))

    assert not nuevas, (
        "Por aquí entra un centro desde fuera de la sesión:\n"
        + "\n".join(f"  · {n}   ({encontradas[n]})" for n in nuevas)
        + "\n\n`user.salon_id` ya viene garantizado como un centro ACTIVO; un "
        "centro que entra por la petición, no. Compruébalo contra el reparto, o "
        "apúntalo en ACEPTADOS con el motivo (lee la cabecera antes)."
    )


def test_no_sobran_entradas_de_caminos_que_ya_no_existen():
    """Al revés: una entrada que sobra suele ser una función renombrada, y su
    permiso seguiría ahí esperando a que otra ocupe el nombre."""
    sobran = sorted(set(ACEPTADOS) - set(puertas_por_las_que_entra_un_centro()))

    assert not sobran, f"Entradas de ACEPTADOS que ya no hacen falta: {sobran}"


def test_cada_entrada_tiene_un_motivo_de_verdad():
    for clave, motivo in ACEPTADOS.items():
        assert len(motivo.split()) >= 12, f"«{clave}» necesita un motivo, no una etiqueta"


# ═══════════════════════════════════ el control: que no pase en vacío


def test_CONTROL_el_lector_encuentra_las_puertas_que_SI_existen():
    """Si el recorrido dejara de reconocer las firmas o la convención de los
    esquemas de entrada, no habría puertas, no habría nuevas, y los dos tests de
    arriba pasarían sin mirar nada."""
    encontradas = puertas_por_las_que_entra_un_centro()

    assert len(encontradas) == 4, sorted(encontradas)
    assert "routers/centros.py · CrearCentroIn.copiar_servicios_de" in encontradas


def test_CONTROL_un_endpoint_inventado_que_acepte_un_centro_se_caza(tmp_path):
    """EL CONTROL QUE IMPORTA. No basta con no encontrar caminos nuevos: hay que
    demostrar que los encontraría. Se le da un handler con el centro en la ruta,
    que es la forma más fácil de colar uno sin darse cuenta."""
    fichero = tmp_path / "inventado.py"
    fichero.write_text(
        "@router.post('/citas/{salon_id}')\n"
        "async def mover_cita_a(salon_id: str, cita: str):\n"
        "    return await db.update('appointments', {'id': f'eq.{cita}'}, "
        "{'salon_id': salon_id})\n",
        encoding="utf-8",
    )

    arbol = ast.parse(fichero.read_text(encoding="utf-8"))
    cazadas = [
        n.name
        for n in ast.walk(arbol)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and _es_handler_de_ruta(n)
        and any(
            a.arg in NOMBRES_DE_CENTRO
            for a in (*n.args.posonlyargs, *n.args.args, *n.args.kwonlyargs)
        )
    ]

    assert cazadas == ["mover_cita_a"], "el lector no reconocería un camino nuevo"


def test_CONTROL_una_funcion_interna_NO_cuenta_como_puerta(tmp_path):
    """El contrapeso: sin decorador de ruta, ese `salon_id` viene de quien la
    llama, que lo sacó de la sesión. Si contara, este registro tendría cincuenta
    entradas y las tres que importan no se verían."""
    fichero = tmp_path / "interna.py"
    fichero.write_text(
        "async def _leer_agenda(db, salon_id: str):\n    return []\n", encoding="utf-8"
    )

    arbol = ast.parse(fichero.read_text(encoding="utf-8"))
    handlers = [
        n
        for n in ast.walk(arbol)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and _es_handler_de_ruta(n)
    ]

    assert handlers == []
