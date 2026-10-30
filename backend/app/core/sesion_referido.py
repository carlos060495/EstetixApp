"""LA PUERTA DEL REFERIDO (0071): quién es, en UN solo sitio.

Un referido no entra por Supabase Auth (la 0063 no le daría token, y así se
queda) ni por `admin_users` (ese panel ve todas las cuentas). Tiene su propia
contraseña, su propia sesión y su propio ámbito. De la puerta del panel de
plataforma se copia la FORMA —cookie propia, con los atributos de
`core/cookies_de_sesion.py`, y el origen comprobado en lo que cambia algo—,
no la tabla.

EL REFERIDO NO RECIBE NINGÚN TOKEN QUE LLEGUE A POSTGRES, así que la frontera la
pone el backend, y la pone AQUÍ: `sesion_de_referido` resuelve quién es a partir
de la cookie —nunca de un identificador que mande el cliente— y todas las
consultas del portal salen del `id` que devuelve. Lo vigila
`tests/test_portal_referido.py`.

EL ESTADO SE MIRA EN CADA PETICIÓN, no al entrar: `referido_de_la_sesion` solo
devuelve la sesión de un referido APROBADO y con contraseña. Rechazarlo corta
su sesión en la siguiente petición.

La sesión es una cadena aleatoria opaca, y en la base solo está su hash: sin
secreto de firma que custodiar ni que rotar, y cerrarla es borrar una fila.
"""

from __future__ import annotations

import hashlib
import secrets
from functools import cache
from urllib.parse import urlparse

import anyio
from fastapi import Depends, HTTPException, Request, status
from passlib.hash import argon2
from pydantic import BaseModel

from app.core.config import Settings, get_settings
from app.core.cookies_de_sesion import COOKIE_REFERIDO
from app.db.postgrest_client import PostgrestClient, PostgrestError

# Nombre y atributos en `core/cookies_de_sesion.py`.
COOKIE_SESION_REFERIDO = COOKIE_REFERIDO
HORAS_SESION = 12
_SEGURAS = {"GET", "HEAD", "OPTIONS"}

_argon2id = argon2.using(type="ID")


@cache
def _hash_de_nadie() -> str:
    """Para que «este correo no existe» tarde lo mismo que «contraseña mala»: se
    comprueba igual, contra un hash que no es de nadie. Al primer uso y no al
    importar: importar el módulo no debe poner argon2 a trabajar."""
    return _argon2id.hash(secrets.token_urlsafe(24))


class ReferidoActual(BaseModel):
    id: str
    nombre: str
    email: str
    codigo: str | None = None


def hash_de_token(token: str) -> str:
    """Los tokens (sesión y enlaces) son 32 bytes aleatorios: no hace falta un
    hash lento, solo que la base no guarde el token en claro."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_nuevo() -> str:
    return secrets.token_urlsafe(32)


async def hashear_contrasena(clave: str) -> str:
    return await anyio.to_thread.run_sync(_argon2id.hash, clave)


async def verificar_contrasena(clave: str, guardado: str | None) -> bool:
    """Siempre cuesta lo mismo, haya hash o no."""
    def _verificar() -> bool:
        try:
            return _argon2id.verify(clave, guardado or _hash_de_nadie()) and guardado is not None
        except (TypeError, ValueError):
            return False
    return await anyio.to_thread.run_sync(_verificar)


def servicio(settings: Settings) -> PostgrestClient:
    """Con la clave de servicio: las tablas del referido no aceptan otra."""
    return PostgrestClient(settings.supabase_url, settings.supabase_secret_key)


def _origen(request: Request) -> str | None:
    origen = request.headers.get("origin")
    if not origen:
        referer = request.headers.get("referer")
        if referer:
            p = urlparse(referer)
            origen = f"{p.scheme}://{p.netloc}" if p.scheme and p.netloc else None
    return origen.rstrip("/") if origen else None


def exigir_origen_permitido(request: Request, settings: Settings = Depends(get_settings)) -> None:
    """`SameSite=None` obliga a defenderse del CSRF en lo que cambia algo, igual
    que el panel (`admin_auth_guard`): solo desde el frontend."""
    if request.method in _SEGURAS:
        return
    if _origen(request) not in set(settings.allowed_origins_list):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Origen no permitido")


async def sesion_de_referido(
    request: Request,
    settings: Settings = Depends(get_settings),
    _origen_ok: None = Depends(exigir_origen_permitido),
) -> ReferidoActual:
    """QUIÉN ES. Lo único que lo sabe. Ver la cabecera."""
    token = request.cookies.get(COOKIE_SESION_REFERIDO)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No has entrado.")
    try:
        filas = await servicio(settings).rpc("referido_de_la_sesion", {"p_token_hash": hash_de_token(token)})
    except PostgrestError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudo comprobar la sesión.") from e
    if not filas or not filas[0]:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Tu sesión ya no vale. Vuelve a entrar.")
    return ReferidoActual(**{**filas[0], "id": str(filas[0]["id"])})
