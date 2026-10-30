"""LO QUE SOPORTE LEE DE UNA PERSONA QUEDA APUNTADO (20-10-2026).

El guarda no se fía de una lista escrita a mano: recorre TODAS las rutas GET a
las que llega una sesión de soporte —las que resuelven la identidad con
`get_current_user*`, que es el único sitio donde existe el modo soporte— y mira
lo que devuelven. Una ruta es «de personas» si:

  · no declara `response_model`: no se sabe qué devuelve, así que se supone que
    sí (falla cerrado);
  · lleva en la URL el id de una persona (`{client_id}`, `{employee_id}`…);
  · su respuesta, a cualquier profundidad, tiene un campo que nombra o contacta
    a alguien (`CAMPOS_DE_PERSONA`), o un campo opaco (`dict`, `Any`), que puede
    llevar cualquier cosa.

Si una ruta así no está en `LECTURAS_AUDITADAS` ni en `NO_SON_DE_PERSONAS` (con
su porqué), rojo con su nombre.
"""

from __future__ import annotations

import re
import typing

from fastapi.routing import APIRoute
from pydantic import BaseModel

import app.core.security as seguridad
from app.core.impersonation_policy import LECTURAS_AUDITADAS, PREFIJO_PANEL, iterar_rutas
from app.main import app

CAMPOS_DE_PERSONA = frozenset(
    {
        "nombre",
        "nombre_reserva",
        "telefono",
        "telefono_final",
        "email",
        "alergias",
        "cumpleanos",
        "clienta",
        "cliente",
        "profesional",
        "persona",
        "iniciales",
        "quien",
        "atendida_por",
        "trabajador_nombre",
    }
)

# `nombre` que es de una COSA, no de una persona. Un modelo nuevo con `nombre`
# cuenta como de persona hasta que alguien lo apunte aquí.
NOMBRE_DE_UNA_COSA = frozenset(
    {
        "ServiceOut",
        "CategoryOut",
        "ProductOut",
        # El nombre del producto de una venta o una compra (0092).
        "ProductoNombreEmbed",
        "PuestoOut",
        "ServicioDelHueco",
        "ServicioPublico",
        "PaisOut",
    }
)

PARAMETROS_DE_PERSONA = frozenset({"client_id", "employee_id", "session_id", "doc_id"})

# Rutas que el guarda marca y que NO se apuntan, cada una con su porqué.
NO_SON_DE_PERSONAS: dict[str, str] = {
    "GET /auth/me": "la ficha de la cuenta suplantada (su propio email); se pide en cada pantalla",
    "GET /salon/profile": "datos del negocio: el email y el teléfono del centro",
}

_PUERTAS = {seguridad.get_current_user, seguridad.get_current_user_aunque_suspendida}


def _llega_soporte(dependant) -> bool:
    return any(d.call in _PUERTAS or _llega_soporte(d) for d in dependant.dependencies)


def _motivos_en(tipo, vistos: set) -> typing.Iterator[str]:
    origen = typing.get_origin(tipo)
    if tipo is typing.Any or tipo is dict or origen is dict:
        yield "opaco"
        return
    if origen is not None:
        for arg in typing.get_args(tipo):
            yield from _motivos_en(arg, vistos)
        return
    if isinstance(tipo, type) and issubclass(tipo, BaseModel) and tipo not in vistos:
        vistos.add(tipo)
        for nombre, campo in tipo.model_fields.items():
            if nombre in CAMPOS_DE_PERSONA and not (
                nombre == "nombre" and tipo.__name__ in NOMBRE_DE_UNA_COSA
            ):
                yield f"{tipo.__name__}.{nombre}"
            yield from _motivos_en(campo.annotation, vistos)


def lecturas_de_personas() -> dict[str, list[str]]:
    """`{"GET /clients": ["ClientOut.nombre", ...], ...}`: lo que soporte puede
    leer y devuelve datos de alguien, con el porqué."""
    encontradas: dict[str, list[str]] = {}
    for ruta in iterar_rutas(app.routes):
        if not isinstance(ruta, APIRoute) or "GET" not in ruta.methods:
            continue
        if ruta.path.startswith(PREFIJO_PANEL) or not _llega_soporte(ruta.dependant):
            continue
        motivos: list[str] = []
        if ruta.response_model is None:
            motivos.append("sin response_model")
        motivos += [
            f"{{{p}}} en la URL"
            for p in re.findall(r"{(\w+)}", ruta.path)
            if p in PARAMETROS_DE_PERSONA
        ]
        motivos += sorted(set(_motivos_en(ruta.response_model, set())))
        if motivos:
            encontradas[f"GET {ruta.path}"] = motivos
    return encontradas


def test_toda_lectura_de_personas_queda_apuntada() -> None:
    sin_apuntar = {
        clave: motivos
        for clave, motivos in lecturas_de_personas().items()
        if clave not in LECTURAS_AUDITADAS and clave not in NO_SON_DE_PERSONAS
    }
    assert not sin_apuntar, (
        "Rutas que devuelven datos de personas y soporte lee SIN APUNTAR: "
        + "; ".join(f"{k} ({', '.join(v)})" for k, v in sorted(sin_apuntar.items()))
        + ". Añádelas a LECTURAS_AUDITADAS en app/core/impersonation_policy.py."
    )


def test_el_guarda_no_pasa_en_vacio() -> None:
    """Si el recorrido dejase de ver las rutas (pasó con FastAPI 0.139, ver
    `iterar_rutas`), el test de arriba pasaría sin mirar nada."""
    encontradas = lecturas_de_personas()
    assert {"GET /clients", "GET /appointments", "GET /employees/{employee_id}/fichajes"} <= set(
        encontradas
    )
    assert "GET /services" not in encontradas, "un catálogo no es de personas"


def test_las_excepciones_siguen_siendo_de_personas() -> None:
    """Una excepción que ya no hace falta es una puerta que nadie mira."""
    sobran = set(NO_SON_DE_PERSONAS) - set(lecturas_de_personas())
    assert not sobran, f"Excepciones que el guarda ya no marca: {sorted(sobran)}"


def test_lo_apuntado_es_lo_descubierto() -> None:
    """Al revés: lo que se apunta sin devolver personas solo mete ruido en un
    registro que no se puede corregir."""
    de_mas = LECTURAS_AUDITADAS - set(lecturas_de_personas())
    assert not de_mas, f"Lecturas apuntadas que el guarda no ve como de personas: {sorted(de_mas)}"
