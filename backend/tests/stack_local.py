"""¿ESTÁ EL STACK LOCAL DE SUPABASE? Se pregunta UNA VEZ, y esperando.

POR QUÉ EXISTE. Antes cada fichero de base real preguntaba por su cuenta, con
un único intento y dos segundos de paciencia:

    def _stack_vivo() -> bool:
        try:
            r = httpx.get(f"{SUPABASE_URL}/rest/v1/", ..., timeout=2.0)

Doce ficheros, doce disparos, doce oportunidades distintas de que uno pille al
stack ocupado: no fallaba nada, pero entre 8 y 30 tests DESAPARECÍAN, y cada
vez eran otros, con la suite en verde por debajo.

EL ERROR DE FONDO no era el número. Era decidir POR TIEMPO DE ESPERA si un test
EXISTE. Un stack lento tiene que hacer los tests lentos, no hacerlos
desaparecer.

CÓMO SE DECIDE AHORA:

  · UNA SOLA SONDA POR TIRADA. El resultado se guarda; los doce ficheros no
    preguntan nada, solo se marcan `base_real`. Y si en esta tirada no corre
    ningún test de base real —una tirada con `-m 'not base_real'`— no se
    pregunta NADA: ni una petición, ni un segundo de espera.
  · SE ESPERA DE VERDAD, reintentando hasta agotar el plazo, en vez de disparar
    una vez. Un stack recién arrancado tarda: que tarde.
  · EN MODO ESTRICTO, SI AL FINAL NO ESTÁ, ES UN ERROR. Ahí el stack SIEMPRE
    tiene que estar; si no está, la tirada no vale y no se disfraza de
    «saltados».
  · SIN MODO ESTRICTO SE SIGUE SALTANDO, que es lo razonable en un portátil sin
    Docker, pero se dice en pantalla y con el motivo delante.

QUIÉN ACTIVA EL MODO ESTRICTO: `ESTETIX_SIN_SALTOS=1`, la misma variable que
hace fallar los tests saltados. Ver `tests/sin_saltos.py`.

Se registra desde `conftest.py` importando el gancho; el guarda que lo prueba
desde fuera lo carga con `-p tests.stack_local`.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import httpx
import pytest
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

# La misma dirección y la misma clave que usan los tests de base real; hay un
# control que lo comprueba, porque una sonda que mire a otro sitio que los tests
# es exactamente la avería que esto tiene que detectar.
URL = os.environ.get("ESTETIX_STACK_URL", "http://127.0.0.1:54321")
CLAVE = os.environ.get("ESTETIX_STACK_CLAVE", "")

VARIABLE_ESTRICTA = "ESTETIX_SIN_SALTOS"

# CUÁNTO SE ESPERA. En modo estricto el stack puede acabar de arrancar en frío:
# dos minutos de espera no arruinan nada y evitan perder la tirada. Sin él, quien
# no lo tiene levantado no merece esperar tanto para que le digan lo que ya sabe.
PLAZO_ESTRICTO = 120.0
PLAZO_NORMAL = 5.0
ENTRE_INTENTOS = 1.0

# NUNCA UN SOLO DISPARO, pase lo que pase con el reloj. Si una petición tarda
# ella sola más que el plazo —en Windows, una conexión rechazada tarda tres
# segundos— el plazo se agotaría en el primer intento y volveríamos a tener
# exactamente la sonda vieja.
INTENTOS_MINIMOS = 2

MOTIVO_DEL_SALTO = (
    "El stack local de Supabase no responde. `npx supabase start` para que corra."
)

# Lo que contestó cada intento, y durante cuánto se insistió: para poder
# ENSEÑARLO en vez de contarlo.
INTENTOS: list[str] = []
PLAZO_USADO = 0.0

_respuesta: bool | None = None


def estricto() -> bool:
    return os.environ.get(VARIABLE_ESTRICTA) == "1"


def plazo_de_este_sitio() -> float:
    """Cuánto se espera aquí. `ESTETIX_STACK_PLAZO` lo acorta: lo usa el guarda
    de esta sonda, que tiene que probar que el plazo existe sin tardar dos
    minutos en cada caso."""
    forzado = os.environ.get("ESTETIX_STACK_PLAZO")
    if forzado:
        return float(forzado)
    return PLAZO_ESTRICTO if estricto() else PLAZO_NORMAL


def _un_toque() -> tuple[bool, str]:
    """Una petición. Devuelve si valió y QUÉ contestó, para poder enseñarlo."""
    try:
        r = httpx.get(f"{URL}/rest/v1/", headers={"apikey": CLAVE}, timeout=5.0)
        return r.status_code < 500, f"HTTP {r.status_code}"
    except Exception as e:  # noqa: BLE001 — cualquier fallo de red es «no está»
        return False, f"{type(e).__name__}: {e}"


def hay_stack(*, plazo: float | None = None, de_nuevo: bool = False) -> bool:
    """¿Está? Se pregunta una vez por tirada; las demás llamadas son gratis.

    `de_nuevo` es para los tests de esta sonda, que necesitan preguntarlo varias
    veces con distintas condiciones.
    """
    global _respuesta, PLAZO_USADO
    if _respuesta is not None and not de_nuevo:
        return _respuesta

    if plazo is None:
        plazo = plazo_de_este_sitio()
    PLAZO_USADO = plazo
    INTENTOS.clear()
    empezado = time.monotonic()
    while True:
        vale, que_dijo = _un_toque()
        INTENTOS.append(f"+{time.monotonic() - empezado:5.1f}s  {que_dijo}")
        if vale:
            break
        if len(INTENTOS) >= INTENTOS_MINIMOS and time.monotonic() - empezado >= plazo:
            break
        time.sleep(ENTRE_INTENTOS)

    _respuesta = vale
    return vale


def _gritar(config, lineas: list[str]) -> None:
    """A pantalla, donde se vea. Sin reporter —capturado, `-s`— por stderr."""
    reporter = config.pluginmanager.getplugin("terminalreporter")
    if reporter is None:
        print("\n".join(lineas))
        return
    reporter.write_sep("!", lineas[0], red=True, bold=True)
    for linea in lineas[1:]:
        reporter.line(linea)


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config, items):
    """La única sonda de la tirada, y lo que se hace con su respuesta.

    `trylast`: así corre DESPUÉS del filtrado por `-m`, y `items` ya son los
    tests que de verdad van a correr. Por eso una tirada que los descarta con
    `-m 'not base_real'` no llega ni a preguntar.
    """
    de_base_real = [i for i in items if i.get_closest_marker("base_real")]
    if not de_base_real:
        return

    if hay_stack():
        return

    detalle = [f"  intento {n}: {q}" for n, q in enumerate(INTENTOS, 1)]

    if estricto():
        # UN ERROR, NUNCA UN SALTO. Aquí el stack siempre tiene que estar; si no
        # está, la tirada no vale, y decirlo como «saltados» fue justo lo que
        # nos escondió el problema durante cinco tiradas.
        raise pytest.UsageError(
            "\n".join(
                [
                    f"EL STACK LOCAL NO ESTÁ y {len(de_base_real)} tests de base real "
                    "lo necesitan. Esta tirada NO VALE.",
                    f"Se preguntó a {URL} durante {PLAZO_USADO:.0f}s, "
                    f"{len(INTENTOS)} veces:",
                    *detalle,
                    "Mira el paso «Levantar Supabase local»: o no arrancó, o "
                    "escucha en otro sitio.",
                ]
            )
        )

    marca = pytest.mark.skip(reason=MOTIVO_DEL_SALTO)
    for item in de_base_real:
        item.add_marker(marca)
    _gritar(
        config,
        [
            f"SE SALTAN {len(de_base_real)} TESTS DE BASE REAL: el stack local no responde",
            f"  se preguntó a {URL} durante {PLAZO_USADO:.0f}s, "
            f"{len(INTENTOS)} veces:",
            *detalle,
            "  `npx supabase start` y vuelve a lanzarlos.",
        ],
    )
