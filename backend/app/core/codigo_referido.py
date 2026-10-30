"""Código público de un referido.

Sin BD y sin HTTP a propósito, igual que `support_codes`: así el formato se
puede razonar y probar por separado de las reglas de aprobación.

Se parece al código de soporte pero NO es lo mismo, y conviene tener clara la
diferencia: el de soporte es un secreto de un solo uso que se guarda hasheado;
éste es PÚBLICO —su dueño lo reparte— y se guarda en claro, porque hay que
poder enseñarlo en el panel y buscarlo por él.
"""

from __future__ import annotations

import secrets

# Se reutiliza el alfabeto del código de soporte a propósito: es el mismo
# problema (alguien va a dictar esto por teléfono o copiarlo de una pantalla) y
# tener dos convenciones distintas en el mismo producto solo genera dudas.
from app.core.support_codes import ALFABETO_CODIGO

# Seis caracteres sobre 32 símbolos son 32^6 ≈ 1.000 millones de combinaciones.
# Con unos pocos miles de referidos la probabilidad de choque es despreciable, y
# de todas formas la unicidad la impone la BD (índice único) y el reintento de
# `aprobar_referido`, no la estadística.
LONGITUD_CODIGO_REFERIDO = 6


def generar_codigo_referido() -> str:
    return "".join(secrets.choice(ALFABETO_CODIGO) for _ in range(LONGITUD_CODIGO_REFERIDO))


def normalizar_codigo_referido(bruto: str) -> str:
    """Lo que teclea alguien puede traer espacios, guiones o minúsculas."""
    return "".join(c for c in bruto.upper() if c in ALFABETO_CODIGO)
