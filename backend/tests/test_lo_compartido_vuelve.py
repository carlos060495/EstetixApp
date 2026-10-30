"""El guarda de `tests/lo_compartido.py`, con sus controles: lo que un test deja
cambiado se ve y se devuelve. Se prueban `_foto` y `_devolver` directamente: la
fixture que las usa alrededor de cada test es solo el envoltorio."""

from __future__ import annotations

import os
import sys
import types

from app.core.security import get_current_user
from app.main import app
from app.routers import auth
from tests.lo_compartido import YA_SE_SABE, _devolver, _foto


def test_CONTROL_un_cliente_sustituido_se_ve_y_se_devuelve():
    original = auth._auth_admin
    antes = _foto()
    auth._auth_admin = lambda settings: "falso"
    assert _devolver(antes) == ["app.routers.auth._auth_admin"]
    assert auth._auth_admin is original


def test_CONTROL_una_variable_de_entorno_se_ve_y_se_devuelve():
    antes = _foto()
    os.environ["ZZ_LO_COMPARTIDO"] = "1"
    assert _devolver(antes) == ["os.environ['ZZ_LO_COMPARTIDO']"]
    assert "ZZ_LO_COMPARTIDO" not in os.environ


def test_CONTROL_un_override_de_la_app_global_se_ve_y_se_devuelve():
    antes = _foto()
    app.dependency_overrides[get_current_user] = lambda: None
    assert _devolver(antes) == ["app.dependency_overrides ['get_current_user']"]
    assert get_current_user not in app.dependency_overrides


def test_CONTROL_un_atributo_nuevo_en_un_modulo_se_ve_y_se_quita():
    antes = _foto()
    auth.zz_nuevo = 1
    assert _devolver(antes) == ["app.routers.auth.zz_nuevo (nuevo)"]
    assert not hasattr(auth, "zz_nuevo")


def test_un_submodulo_importado_por_primera_vez_no_cuenta():
    """Python cuelga del paquete el submódulo que se importa: no es un cambio
    del test, y quitarlo rompería el siguiente import."""
    antes = _foto()
    paquete = sys.modules["app.core"]
    falso = types.ModuleType("app.core.zz_submodulo")
    paquete.zz_submodulo = falso
    try:
        assert _devolver(antes) == []
    finally:
        del paquete.zz_submodulo


def test_lo_que_pytest_escribe_el_mismo_no_cuenta():
    antes = _foto()
    os.environ["PYTEST_CURRENT_TEST"] = "otra cosa"
    assert _devolver(antes) == []


def test_la_lista_solo_tiene_ficheros_que_existen():
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assert all(os.path.exists(os.path.join(raiz, f)) for f in YA_SE_SABE), YA_SE_SABE


def test_CONTROL_un_test_nuevo_que_no_devuelve_se_pone_rojo_y_el_siguiente_no_lo_paga():
    """De verdad, con pytest: un fichero de tests nuevo (fuera de la lista)
    cambia `auth._auth_admin` sin devolverlo. Ese test sale ROJO, y el que va
    detrás encuentra el cliente de siempre."""
    import subprocess

    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    fichero = os.path.join(raiz, "tests", "test_zz_control_lo_compartido.py")
    with open(fichero, "w", encoding="utf-8") as f:
        f.write(
            "from app.routers import auth\n"
            "ORIGINAL = auth._auth_admin\n"
            "def test_a_lo_cambia():\n"
            "    auth._auth_admin = lambda settings: 'falso'\n"
            "def test_b_lo_encuentra_como_estaba():\n"
            "    assert auth._auth_admin is ORIGINAL\n"
        )
    try:
        # El hijo escribe en UTF-8 pase lo que pase en la consola de quien lo lance
        # (en Windows sería cp1252, y leerlo como UTF-8 reventaría aquí).
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", fichero],
                           cwd=raiz, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    finally:
        os.remove(fichero)
    salida = r.stdout + r.stderr
    # Los dos tests pasan su cuerpo; el primero sale con ERROR al desmontarse
    # (es lo que dejó cambiado), y el segundo encontró el cliente de siempre.
    assert "2 passed, 1 error" in salida, salida
    assert "test_b_lo_encuentra_como_estaba" not in salida.split("short test summary")[-1], salida
    assert "ERROR tests/test_zz_control_lo_compartido.py::test_a_lo_cambia" in salida, salida
    assert "app.routers.auth._auth_admin" in salida, salida
