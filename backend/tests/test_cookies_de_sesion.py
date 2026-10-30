"""LAS COOKIES DE SESIÓN, ESTRICTAS Y EN UN SOLO SITIO (05-10-2026).

Frontend (`www.estetixapp.es`) y backend (`api.estetixapp.es`) son el MISMO
SITIO, así que las cookies de sesión del panel, del modo soporte y del portal
del referido van todo lo estrictas que pueden (`core/cookies_de_sesion.py`).

  1. Recorre `backend/app` (árbol de sintaxis): rojo si algo llama a
     `set_cookie` o `delete_cookie` fuera de `core/cookies_de_sesion.py`.
  2. Lo que esa función escribe de verdad: `__Host-`, `Secure`, `HttpOnly`,
     `SameSite=Strict`, `Path=/` y SIN `Domain`; y el borrado, igual.

Cada guarda con su control.
"""

from __future__ import annotations

import ast
import pathlib

import pytest
from fastapi import Response

from app.core import cookies_de_sesion as c

APP = pathlib.Path(__file__).resolve().parents[1] / "app"
_SOLO_AQUI = "core/cookies_de_sesion.py"


def fuera_de_su_sitio(codigo: str, nombre: str) -> list[str]:
    return [
        f"{nombre}:{n.lineno}: {n.func.attr}"
        for n in ast.walk(ast.parse(codigo))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr in ("set_cookie", "delete_cookie")
    ]


def test_ninguna_cookie_se_pone_fuera_de_su_sitio():
    hallado = []
    for ruta in sorted(APP.rglob("*.py")):
        relativa = ruta.relative_to(APP).as_posix()
        if relativa == _SOLO_AQUI:
            continue
        hallado += fuera_de_su_sitio(ruta.read_text(encoding="utf-8"), relativa)
    assert hallado == []
    # Antivacío: en su sitio sí las ve.
    assert len(fuera_de_su_sitio((APP / _SOLO_AQUI).read_text(encoding="utf-8"), _SOLO_AQUI)) == 2


def test_CONTROL_una_cookie_puesta_a_mano_se_pone_roja():
    a_mano = 'def f(r, t):\n    r.set_cookie(key="sa-access-token", value=t, samesite="none")\n'
    assert fuera_de_su_sitio(a_mano, "zz.py") == ["zz.py:2: set_cookie"]


def _cabecera(poner: bool, nombre: str) -> str:
    r = Response()
    if poner:
        c.poner_cookie_de_sesion(r, nombre, "valor", 60)
    else:
        c.borrar_cookie_de_sesion(r, nombre)
    return r.headers["set-cookie"]


def estricta(cabecera: str) -> list[str]:
    """Lo que le falta (o le sobra) a una cabecera Set-Cookie de sesión."""
    partes = [p.strip().lower() for p in cabecera.split(";")]
    nombre = cabecera.split("=", 1)[0]
    fallos = []
    if not nombre.startswith("__Host-"):
        fallos.append("sin el prefijo __Host-")
    for exigido in ("secure", "httponly", "samesite=strict", "path=/"):
        if exigido not in partes:
            fallos.append(f"sin {exigido}")
    if any(p.startswith("domain=") for p in partes):
        fallos.append("con domain")
    return fallos


@pytest.mark.parametrize("nombre", [c.COOKIE_PANEL, c.COOKIE_SOPORTE, c.COOKIE_REFERIDO])
@pytest.mark.parametrize("poner", [True, False], ids=["al_ponerla", "al_borrarla"])
def test_las_tres_son_estrictas(nombre, poner):
    assert estricta(_cabecera(poner, nombre)) == []


def test_las_que_usan_las_puertas_son_estas():
    from app.core.impersonation import COOKIE_IMPERSONACION
    from app.core.sesion_referido import COOKIE_SESION_REFERIDO

    assert COOKIE_IMPERSONACION == c.COOKIE_SOPORTE
    assert COOKIE_SESION_REFERIDO == c.COOKIE_REFERIDO
    assert "COOKIE_PANEL" in (APP / "main.py").read_text(encoding="utf-8")


@pytest.mark.parametrize("cabecera, falta", [
    ("sa-access-token=x; HttpOnly; Path=/; SameSite=strict; Secure", "sin el prefijo __Host-"),
    ("__Host-x=x; HttpOnly; Path=/; SameSite=none; Secure", "sin samesite=strict"),
    ("__Host-x=x; HttpOnly; Path=/; SameSite=strict", "sin secure"),
    ("__Host-x=x; Domain=estetixapp.es; HttpOnly; Path=/; SameSite=strict; Secure", "con domain"),
], ids=["sin_prefijo", "samesite_none", "sin_secure", "con_domain"])
def test_CONTROL_una_cookie_menos_estricta_se_pone_roja(cabecera, falta):
    assert falta in estricta(cabecera)
