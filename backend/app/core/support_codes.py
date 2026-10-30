"""Código de autorización de soporte: generación, normalización y hash.

Sin BD y sin HTTP a propósito: así se puede razonar y probar el formato del
código por separado de las reglas de caducidad y de un solo uso.
"""

from __future__ import annotations

import secrets

from passlib.hash import argon2

# Crockford base32 sin I, L, O ni U: el código se dicta por teléfono y esos
# cuatro son los que se confunden con 1, 0 y entre sí.
ALFABETO_CODIGO = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
LONGITUD_CODIGO = 8

# Mismos parámetros que los PIN de kiosko (app/core/security.py).
_argon2id = argon2.using(type="ID")


def generar_codigo() -> str:
    return "".join(secrets.choice(ALFABETO_CODIGO) for _ in range(LONGITUD_CODIGO))


def normalizar_codigo(bruto: str) -> str:
    """Lo que teclea el super admin puede traer guiones, espacios o minúsculas."""
    return "".join(c for c in bruto.upper() if c in ALFABETO_CODIGO)


def hash_codigo(codigo: str) -> str:
    return _argon2id.hash(codigo)


def verificar_codigo(codigo: str, code_hash: str) -> bool:
    try:
        return _argon2id.verify(codigo, code_hash)
    except (TypeError, ValueError):
        return False


def formatear_codigo(codigo: str) -> str:
    mitad = LONGITUD_CODIGO // 2
    return f"{codigo[:mitad]}-{codigo[mitad:]}"
