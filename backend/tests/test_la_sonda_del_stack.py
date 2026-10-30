"""LA SONDA DEL STACK, probada con el stack caído — que es lo que importa.

Un guarda que solo se prueba con el stack vivo no prueba nada: la sonda vieja
también decía «sí» cuando el stack estaba. Lo que se rompía era el «no», y era
un «no» que hacía DESAPARECER tests en vez de hacerlos fallar.

Así que aquí el caso principal es el stack caído, de dos maneras:

  · desde fuera, con pytest en un subproceso sobre una suite de juguete y la
    sonda apuntando a un puerto donde no hay nadie — lo que ve quien lanza la
    tirada: el código de salida y lo que se lee en pantalla;
  · desde dentro, contando las peticiones, para fijar lo que no se ve: que se
    reintenta, que se pregunta UNA sola vez, y que si no corre ningún test de
    base real no se pregunta nada.

Y con sus controles. Sin ellos, una sonda que dijera «no está» SIEMPRE pasaría
todos los casos de arriba.
"""

from __future__ import annotations

import http.server
import os
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from tests import stack_local

BACKEND = Path(__file__).resolve().parents[1]

# Un puerto donde no hay nadie escuchando. La conexión se rechaza: «no está».
NADIE = "http://127.0.0.1:1"

SUITE_CON_BASE_REAL = """
import pytest

def test_normal():
    assert True

@pytest.mark.base_real
def test_de_base_real():
    assert True
"""

SUITE_SIN_BASE_REAL = """
def test_normal():
    assert True
"""


def _pytest(tmp_path: Path, suite: str, *, estricto: bool, url: str) -> subprocess.CompletedProcess:
    (tmp_path / "test_juguete.py").write_text(suite, encoding="utf-8")
    # El plazo se acorta A PROPÓSITO: lo que se prueba es que EXISTE y que se
    # reintenta dentro de él, no cuántos segundos dura. Los segundos de verdad
    # —120 en modo estricto, 5 sin él— están en `stack_local.py`.
    entorno = {**os.environ, "PYTHONPATH": str(BACKEND),
               "ESTETIX_STACK_URL": url, "ESTETIX_STACK_PLAZO": "2",
               # EL HIJO ESCRIBE EN UTF-8, DICHO AQUÍ. Sin esto, en Windows su
               # salida usa el códec del sistema (cp1252) y el aviso —«EL STACK
               # LOCAL NO ESTÁ»— revienta al imprimirse: el proceso muere sin
               # decir nada y este test falla por un acento, no por el guarda.
               # En Linux (UTF-8) no pasa, que es la peor clase de test
               # frágil: el que solo se rompe en algunas máquinas.
               "PYTHONIOENCODING": "utf-8"}
    entorno.pop("ESTETIX_SIN_SALTOS", None)
    if estricto:
        entorno["ESTETIX_SIN_SALTOS"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "tests.stack_local", "-p", "no:cacheprovider",
         "-p", "no:randomly", "--rootdir", str(tmp_path), str(tmp_path / "test_juguete.py")],
        cwd=tmp_path, env=entorno, capture_output=True, text=True, timeout=300,
        # El códec, DICHO: sin esto se decodifica con el del sistema —cp1252 en
        # Windows— y un acento en la salida del hijo revienta la lectura.
        encoding="utf-8", errors="replace",
    )


@pytest.fixture()
def un_stack_que_contesta():
    """Un servidor de verdad en un puerto libre, para el control del «sí»."""
    class Callado(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        puerto = s.getsockname()[1]
    servidor = http.server.HTTPServer(("127.0.0.1", puerto), Callado)
    hilo = threading.Thread(target=servidor.serve_forever, daemon=True)
    hilo.start()
    try:
        yield f"http://127.0.0.1:{puerto}"
    finally:
        servidor.shutdown()


# ── EL GUARDA: con el stack caído ──────────────────────────────────────────

def test_en_modo_estricto_un_stack_caido_es_un_ERROR_y_NUNCA_un_salto(tmp_path):
    """Sin esto, hasta 30 tests se esfumaban y nadie se enteraba."""
    r = _pytest(tmp_path, SUITE_CON_BASE_REAL, estricto=True, url=NADIE)

    assert r.returncode != 0, r.stdout[-1500:]
    salida = r.stdout + r.stderr
    assert "EL STACK LOCAL NO ESTÁ" in salida, salida[-1500:]
    assert "Esta tirada NO VALE" in salida
    # Y lo enseña: a quién preguntó y qué le contestaron.
    assert NADIE in salida
    assert "intento 1:" in salida
    # LO QUE NO PUEDE PASAR: que se disfrace de «saltados».
    assert "skipped" not in salida.lower()


def test_en_local_se_sigue_saltando_PERO_se_dice(tmp_path):
    """En un portátil sin Docker saltar es lo razonable. Callarlo, no."""
    r = _pytest(tmp_path, SUITE_CON_BASE_REAL, estricto=False, url=NADIE)

    assert r.returncode == 0, r.stdout[-1500:]
    assert "1 passed" in r.stdout and "1 skipped" in r.stdout
    assert "SE SALTAN 1 TESTS DE BASE REAL" in r.stdout, r.stdout[-1500:]
    assert "npx supabase start" in r.stdout


# ── LOS CONTROLES: si fallan, el guarda no vale ────────────────────────────

def test_EL_CONTROL_con_el_stack_vivo_el_test_de_base_real_CORRE(
    tmp_path, un_stack_que_contesta
):
    """Sin esto, una sonda que dijera «no está» siempre pasaría los de arriba."""
    r = _pytest(tmp_path, SUITE_CON_BASE_REAL, estricto=True, url=un_stack_que_contesta)

    assert r.returncode == 0, r.stdout[-1500:]
    assert "2 passed" in r.stdout
    assert "skipped" not in r.stdout.lower()


def test_EL_CONTROL_sin_tests_de_base_real_el_stack_caido_da_igual(tmp_path):
    """Una tirada con `-m 'not base_real'` y sin stack: no es asunto suyo."""
    r = _pytest(tmp_path, SUITE_SIN_BASE_REAL, estricto=True, url=NADIE)

    assert r.returncode == 0, r.stdout[-1500:]
    assert "1 passed" in r.stdout
    assert "EL STACK LOCAL NO ESTÁ" not in r.stdout


# ── LO QUE NO SE VE DESDE FUERA: cuántas veces se pregunta ─────────────────

@pytest.fixture()
def sonda_limpia(monkeypatch):
    """La sonda sin memoria y sin esperas, para poder contarle las peticiones."""
    monkeypatch.setattr(stack_local, "_respuesta", None)
    monkeypatch.setattr(stack_local, "ENTRE_INTENTOS", 0.0)
    return stack_local


def _contador(respuestas: list[bool], sonda, monkeypatch) -> list[int]:
    veces = [0]

    def falso():
        veces[0] += 1
        vale = respuestas[min(veces[0] - 1, len(respuestas) - 1)]
        return vale, "HTTP 200" if vale else "ConnectError: nadie escucha"

    monkeypatch.setattr(sonda, "_un_toque", falso)
    return veces


def test_espera_de_verdad_reintentando_en_vez_de_disparar_una_vez(sonda_limpia, monkeypatch):
    """Un stack que tarda en arrancar tiene que encontrarse, no perderse."""
    veces = _contador([False, False, True], sonda_limpia, monkeypatch)

    assert sonda_limpia.hay_stack(plazo=10.0, de_nuevo=True) is True
    assert veces[0] == 3, "no reintentó: se rindió al primer «no»"
    assert len(sonda_limpia.INTENTOS) == 3


def test_si_de_verdad_no_esta_lo_dice_despues_de_insistir(sonda_limpia, monkeypatch):
    """Y no se queda insistiendo para siempre: hay un plazo."""
    veces = _contador([False], sonda_limpia, monkeypatch)

    assert sonda_limpia.hay_stack(plazo=0.3, de_nuevo=True) is False
    assert veces[0] >= 2, "disparó una sola vez, como la sonda vieja"
    assert any("nadie escucha" in i for i in sonda_limpia.INTENTOS)


def test_ni_con_el_plazo_a_cero_dispara_una_sola_vez(sonda_limpia, monkeypatch):
    """El agujero por el que volvería la sonda vieja: una petición que tarda
    ella sola más que el plazo. En Windows, una conexión rechazada tarda tres
    segundos; con el plazo en local, eso daba UN intento."""
    veces = _contador([False], sonda_limpia, monkeypatch)

    assert sonda_limpia.hay_stack(plazo=0.0, de_nuevo=True) is False
    assert veces[0] >= sonda_limpia.INTENTOS_MINIMOS


def test_UNA_sola_sonda_por_tirada(sonda_limpia, monkeypatch):
    """Doce ficheros eran doce oportunidades de que se cayera uno distinto."""
    veces = _contador([True], sonda_limpia, monkeypatch)

    assert sonda_limpia.hay_stack() is True
    for _ in range(11):
        assert sonda_limpia.hay_stack() is True

    assert veces[0] == 1, f"preguntó {veces[0]} veces"


def test_sin_tests_de_base_real_no_se_pregunta_NADA(sonda_limpia, monkeypatch):
    """Ni una petición ni un segundo de espera: esa tirada no los corre."""
    veces = _contador([False], sonda_limpia, monkeypatch)

    class ItemNormal:
        def get_closest_marker(self, nombre):
            return None

    # `config` a None a propósito: si llegara a usarlo, reventaría, y eso es
    # justo lo que se quiere saber.
    sonda_limpia.pytest_collection_modifyitems(None, [ItemNormal(), ItemNormal()])

    assert veces[0] == 0


def test_la_sonda_mira_donde_miran_los_tests(sonda_limpia):
    """Una sonda que pregunta a otra dirección que los tests es la avería."""
    from tests.test_alta_completa_e2e import SECRET_KEY, SUPABASE_URL

    assert stack_local.URL == os.environ.get("ESTETIX_STACK_URL", SUPABASE_URL)
    assert stack_local.CLAVE == SECRET_KEY
