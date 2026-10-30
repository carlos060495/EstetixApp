"""LAS COOKIES DE SESIÓN QUE PONE EL BACKEND, todas con los mismos atributos y
en un solo sitio (05-10-2026).

  · `__Host-sa-access-token` — el panel de plataforma (`admin_auth.py`).
  · `__Host-sa-impersonation` — el modo soporte (`impersonation.py`).
  · `__Host-ref-sesion` — el portal del referido (`portal_referido.py`).
  · `__Host-demo` — de qué navegador es un salón de muestra (`demo.py`, 0080).
  · `__Host-mostrador` — el dispositivo del mostrador (`mostrador.py`, 0090).

MISMO SITIO, DISTINTO ORIGEN. El frontend está en `www.estetixapp.es` y el
backend en `api.estetixapp.es`: el mismo sitio (`estetixapp.es`). Antes se
pensaba que el backend vivía en otro (el nombre de Railway) y las cookies iban
con `SameSite=None` para sobrevivir a ser de tercero. No lo son, así que van
todo lo estrictas que pueden:

  · `SameSite=Strict` — una petición de `www` a `api` es del mismo sitio y la
    lleva; una que nazca en cualquier otro sitio, nunca. Es la defensa de CSRF
    del navegador; la del origen (`admin_auth_guard`, `exigir_origen_permitido`)
    se queda además, porque otro subdominio de `estetixapp.es` también sería
    «mismo sitio».
  · `HttpOnly` — ningún JavaScript la lee, ni el nuestro.
  · `Secure` — solo por https.
  · Sin `Domain` — solo `api.estetixapp.es`, ni `www` ni ningún otro.
  · `Path=/`.
  · El prefijo `__Host-` — el navegador SOLO la acepta si es `Secure`, sin
    `Domain` y con `Path=/`, y ningún otro subdominio de `estetixapp.es` puede
    plantar una cookie con ese nombre para suplantarla.

EN LOCAL: `SameSite=Strict` exige que frontend y backend sean del mismo sitio:
`localhost:3000` con `localhost:8100` sí; con `127.0.0.1:8100`, no (son sitios
distintos). Las dos por `localhost`.

Borrarla exige los MISMOS atributos con los que se puso: por eso hay una sola
función para cada cosa. Lo vigila `tests/test_cookies_de_sesion.py`.
"""

from __future__ import annotations

from fastapi import Response

COOKIE_PANEL = "__Host-sa-access-token"
COOKIE_SOPORTE = "__Host-sa-impersonation"
COOKIE_REFERIDO = "__Host-ref-sesion"
# El navegador que abrió un salón de muestra (0080): volver es volver a la suya.
COOKIE_DEMO = "__Host-demo"
# El dispositivo del mostrador (0090): su valor es un secreto, no un token de la base.
COOKIE_MOSTRADOR = "__Host-mostrador"

_ATRIBUTOS = {"httponly": True, "secure": True, "samesite": "strict", "path": "/"}


def poner_cookie_de_sesion(response: Response, nombre: str, valor: str, segundos: int) -> None:
    response.set_cookie(key=nombre, value=valor, max_age=segundos, **_ATRIBUTOS)


def borrar_cookie_de_sesion(response: Response, nombre: str) -> None:
    response.delete_cookie(key=nombre, **_ATRIBUTOS)
