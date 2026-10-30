"""El camino que recorre un navegador de verdad, que no lo probaba nadie.

QUÉ PASÓ. Al desplegar el selector de centro, la aplicación entera dejó de
funcionar en producción:

    "OPTIONS /auth/me HTTP/1.1"        400 Bad Request
    "OPTIONS /services HTTP/1.1"       400 Bad Request
    "OPTIONS /salon/centros HTTP/1.1"  400 Bad Request

El frontend empezó a mandar `X-Centro-Activo` en TODAS las peticiones y esa
cabecera no estaba en `allow_headers`. Un navegador pregunta antes de cada
llamada con un `OPTIONS` (el preflight), y `CORSMiddleware` responde
«400 Disallowed CORS headers» a cualquier preflight que pida una cabecera no
declarada. Como la mandaba en todas, cayó todo — incluidas rutas que no se
habían tocado, que es lo que despistaba.

POR QUÉ NO LO VIO NADIE, con 1420 tests en verde. Porque ninguno hacía un
`OPTIONS`. Y el matiz importa: NO es que `TestClient` no pueda —puede, y este
fichero lo hace—; es que nadie lo había escrito. La herramienta estaba; la
prueba no.

Este fichero cubre las dos mitades:

  1. Que el preflight con la cabecera que el frontend manda de verdad
     conteste 200.
  2. Que NINGUNA cabecera que el frontend mande se quede sin declarar, leyendo
     el frontend de verdad. Misma idea que `test_limites_espejo.py`: si mañana
     alguien la renombra en un lado, esto se pone rojo en vez de tirar la
     aplicación.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from app.core.centro_activo import CABECERA_CENTRO
from app.main import app

# Un origen que la configuración de PRUEBAS permite (el regex de localhost).
# Lo que se aísla aquí es la lista de CABECERAS: usar el dominio de
# producción haría fallar por «Disallowed CORS origin» y el test estaría
# midiendo otra cosa.
ORIGEN = "http://localhost:3000"
FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"


def _permitidas() -> set[str]:
    """Las cabeceras que el middleware de CORS acepta, en minúsculas.

    Se leen del middleware ya montado y no de una copia: una copia aquí tendría
    el mismo problema que la del frontend.
    """
    for m in app.user_middleware:
        opciones = getattr(m, "kwargs", None) or {}
        if "allow_headers" in opciones:
            return {h.lower() for h in opciones["allow_headers"]}
    raise AssertionError("no se encontró el CORSMiddleware en la aplicación")


def _preflight(cliente: TestClient, ruta: str, cabeceras: str):
    return cliente.options(
        ruta,
        headers={
            "Origin": ORIGEN,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": cabeceras,
        },
    )


# ═══════════════════════════ 1. el preflight, tal cual lo hace el navegador

@pytest.mark.parametrize(
    "ruta", ["/auth/me", "/services", "/salon/centros", "/salon/schedule"]
)
def test_el_preflight_con_la_cabecera_del_centro_pasa(ruta):
    """Las cuatro rutas del incidente, con la cabecera que lo provocó."""
    cliente = TestClient(app)
    r = _preflight(cliente, ruta, f"authorization,content-type,{CABECERA_CENTRO.lower()}")

    assert r.status_code == 200, (
        f"{ruta} responde {r.status_code} al preflight: {r.text[:80]}. "
        "El navegador ni llegaría a hacer la petición de verdad."
    )


def test_una_cabecera_que_NO_esta_declarada_sigue_rechazandose():
    """El control. Sin esto, el test de arriba pasaría igual si `allow_headers`
    aceptara cualquier cosa — y entonces no estaría comprobando nada."""
    cliente = TestClient(app)
    r = _preflight(cliente, "/auth/me", "authorization,x-cabecera-inventada")

    assert r.status_code == 400


def test_el_preflight_devuelve_la_cabecera_en_la_respuesta():
    """No basta con el 200: el navegador solo manda la petición de verdad si el
    servidor le confirma la cabecera en `access-control-allow-headers`."""
    cliente = TestClient(app)
    r = _preflight(cliente, "/auth/me", CABECERA_CENTRO.lower())

    permitidas = (r.headers.get("access-control-allow-headers") or "").lower()
    assert CABECERA_CENTRO.lower() in permitidas


# ═══════════════════════════ 2. el espejo con el frontend

def cabeceras_que_manda_el_frontend() -> set[str]:
    """Toda cabecera que el frontend pone en una petición, sacada del código.

    Se buscan las claves con pinta de cabecera dentro de los objetos `headers`
    del cliente de API. No es un analizador de TypeScript: reconoce la forma
    que usa este repositorio y nada más — igual que el lector de migraciones de
    `tests/esquema.py`, y por el mismo motivo.
    """
    encontradas: set[str] = set()
    for fichero in (FRONTEND / "lib" / "api").glob("*.ts"):
        texto = fichero.read_text(encoding="utf-8")
        for bloque in re.findall(r"headers:\s*\{(.*?)\n\s*\}", texto, re.S):
            for nombre in re.findall(r"[\"']([A-Za-z][A-Za-z0-9-]{2,})[\"']\s*:", bloque):
                encontradas.add(nombre.lower())
            # `Authorization: \`Bearer …\`` va sin comillas en la clave.
            for nombre in re.findall(r"\b(Authorization)\s*:", bloque):
                encontradas.add(nombre.lower())
    return encontradas


def test_el_lector_del_frontend_encuentra_algo():
    """El control del control.

    Si la expresión dejara de encajar —un cambio de formato en `client.ts`—
    el test de abajo compararía un conjunto vacío contra otro y pasaría sin
    mirar nada. Estas tres tienen que estar sí o sí.
    """
    encontradas = cabeceras_que_manda_el_frontend()
    assert "content-type" in encontradas
    assert "authorization" in encontradas
    assert CABECERA_CENTRO.lower() in encontradas


def test_toda_cabecera_que_manda_el_frontend_esta_permitida():
    """EL TEST QUE HABRÍA EVITADO LA CAÍDA.

    El nombre de la cabecera del centro vive en dos sitios: el frontend que la
    manda y el backend que la permite. Renombrarla en uno solo tira producción
    entera, y hasta hoy nada lo vigilaba.
    """
    sin_declarar = sorted(cabeceras_que_manda_el_frontend() - _permitidas())

    assert not sin_declarar, (
        f"El frontend manda cabeceras que el CORS no permite: {sin_declarar}. "
        "Un navegador recibirá 400 en el preflight de TODAS las peticiones, no "
        "solo de las que usen esa cabecera. Añádelas a `allow_headers` en "
        "app/main.py."
    )
