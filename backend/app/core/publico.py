"""QUIÉN PUEDE USAR CADA RUTA, DICHO EN VOZ ALTA. Y LA QUE NO LO DICE, DENIEGA.

Hasta aquí era al revés: una ruta nueva nacía abierta a cualquiera con sesión, o
a cualquiera sin ella, según lo que su autor se acordara de poner, y nadie se
enteraba hasta que alguien entraba por ella. 05-10-2026: «que nazca
cerrada, y que el que la escriba tenga que decir en voz alta para quién es».

CÓMO SE DICE. Cada ruta lleva UNA de estas declaraciones, en el router entero
(`APIRouter(..., dependencies=[publico.ADMINISTRADORA])`) o en la ruta
(`@router.post(..., dependencies=[publico.CUALQUIERA])`):

  · ADMINISTRADORA — la sesión de un salón. Desde la 0063 solo la administradora
    tiene cuenta de acceso (la base y el backend rechazan cualquier otra), así
    que «tener sesión de salón» y «ser la administradora» son lo mismo.
  · ADMINISTRADORA_AUNQUE_SUSPENDIDA — la puerta de la suspensión
    (`core/security.py`): la baja y llevarse los datos.
  · QUIOSCO — el quiosco de fichaje. Corre con la sesión de la administradora
    en el dispositivo del salón, y cada empleada se identifica con SU PIN de
    `employees.pin_fichaje_hash`. Las empleadas no tienen cuenta: no confundir
    con el `users.pin_fichaje_hash` de antes de la 0027, que está muerto.
  · MOSTRADOR — las puertas de un dispositivo mostrador (0090): abrir y cerrar
    la gestión, salir del modo. Las autoriza su cookie `__Host-mostrador`
    (`security.dispositivo_del_mostrador`), no una sesión. Lo demás que pide un
    mostrador va por ADMINISTRADORA y lo filtra `core/mostrador.py`.
  · PLATAFORMA — el panel de super-admin (cookie `__Host-sa-access-token`).
  · REFERIDO — el portal de quien recomienda EstetixApp (0071): su propia
    contraseña y su propia sesión (cookie `__Host-ref-sesion`), ni Supabase Auth ni
    `admin_users`. Ninguna otra ruta la acepta (`core/sesion_referido.py`).
  · CUALQUIERA — sin sesión: el alta, la página pública del salón, la salud…
  · CON_ENLACE — sin sesión, pero lo autoriza un token de un solo uso que llegó
    por correo (darse de baja de los recordatorios, restablecer el PIN).
  · WEBHOOK — Stripe y WhatsApp: los autoriza la firma, que se comprueba dentro.
  · SEGUIMIENTO — PÚBLICO A PROPÓSITO (0077): la página del enlace de seguimiento
    de un trabajador de agencia. Sin cuenta ni contraseña: la abre quien tenga
    el enlace, largo y aleatorio, que la agencia le pasó y puede revocar. Solo
    nombres de salón, de plan y si pagan; nunca dinero
    (`tests/test_seguimiento_sin_dinero.py`). No es CON_ENLACE: aquel es de un
    solo uso y llega por correo; este se abre muchas veces.

CADA DECLARACIÓN EXIGE LO QUE DICE, para que la etiqueta no pueda mentir:
ADMINISTRADORA y QUIOSCO dependen de `get_current_user`, PLATAFORMA de
`get_admin_claims`, la puerta de `get_current_user_aunque_suspendida`, REFERIDO de
`sesion_de_referido`. Al ponerlas
no cambió nada: cada ruta ya tenía exactamente eso (FastAPI no repite una
dependencia en la misma petición). Las otras cuatro no exigen nada porque su
público es, precisamente, quien no tiene sesión.

LA QUE NO DICE NADA, DENIEGA. `exigir_publico_declarado` va en la app entera,
antes que cualquier otra cosa: busca la declaración en la ruta que ha casado y,
si no hay ninguna (o hay dos), contesta 403 sin ejecutar la ruta.
`tests/test_publico_declarado.py` lo vigila además antes de desplegar.
"""

from __future__ import annotations

import logging
from enum import Enum

from fastapi import Depends, HTTPException, Request, status
from fastapi.routing import APIRoute

from app.core.admin_deps import AdminClaims, get_admin_claims
from app.core.mostrador import Dispositivo
from app.core.security import (
    CurrentUser,
    dispositivo_del_mostrador,
    get_current_user,
    get_current_user_aunque_suspendida,
)
from app.core.sesion_referido import ReferidoActual, sesion_de_referido

logger = logging.getLogger("estetix.publico")


class Publico(str, Enum):
    ADMINISTRADORA = "administradora"
    ADMINISTRADORA_AUNQUE_SUSPENDIDA = "administradora_aunque_suspendida"
    QUIOSCO = "quiosco"
    MOSTRADOR = "mostrador"
    PLATAFORMA = "plataforma"
    REFERIDO = "referido"
    CUALQUIERA = "cualquiera"
    CON_ENLACE = "con_enlace"
    WEBHOOK = "webhook"
    SEGUIMIENTO = "seguimiento"


def _marca(publico: Publico):
    def decorar(f):
        f.__publico__ = publico
        return f
    return decorar


@_marca(Publico.ADMINISTRADORA)
async def _para_la_administradora(user: CurrentUser = Depends(get_current_user)) -> None:
    return None


@_marca(Publico.ADMINISTRADORA_AUNQUE_SUSPENDIDA)
async def _para_la_administradora_aunque_suspendida(
    user: CurrentUser = Depends(get_current_user_aunque_suspendida),
) -> None:
    return None


@_marca(Publico.QUIOSCO)
async def _para_el_quiosco(user: CurrentUser = Depends(get_current_user)) -> None:
    return None


@_marca(Publico.MOSTRADOR)
async def _para_el_mostrador(_d: Dispositivo = Depends(dispositivo_del_mostrador)) -> None:
    return None


@_marca(Publico.PLATAFORMA)
def _para_la_plataforma(claims: AdminClaims = Depends(get_admin_claims)) -> None:
    return None


@_marca(Publico.REFERIDO)
def _para_el_referido(referido: ReferidoActual = Depends(sesion_de_referido)) -> None:
    return None


@_marca(Publico.CUALQUIERA)
def _para_cualquiera() -> None:
    return None


@_marca(Publico.CON_ENLACE)
def _para_quien_tenga_el_enlace() -> None:
    return None


@_marca(Publico.WEBHOOK)
def _para_un_webhook() -> None:
    return None


@_marca(Publico.SEGUIMIENTO)
def _publico_a_proposito_con_el_enlace_de_seguimiento() -> None:
    return None


ADMINISTRADORA = Depends(_para_la_administradora)
ADMINISTRADORA_AUNQUE_SUSPENDIDA = Depends(_para_la_administradora_aunque_suspendida)
QUIOSCO = Depends(_para_el_quiosco)
MOSTRADOR = Depends(_para_el_mostrador)
PLATAFORMA = Depends(_para_la_plataforma)
REFERIDO = Depends(_para_el_referido)
CUALQUIERA = Depends(_para_cualquiera)
CON_ENLACE = Depends(_para_quien_tenga_el_enlace)
WEBHOOK = Depends(_para_un_webhook)
SEGUIMIENTO = Depends(_publico_a_proposito_con_el_enlace_de_seguimiento)


def publicos_de(dependant) -> set[Publico]:
    """Las declaraciones que hay en el grafo de dependencias de una ruta."""
    encontrados: set[Publico] = set()
    for d in dependant.dependencies:
        marca = getattr(d.call, "__publico__", None)
        if marca is not None:
            encontrados.add(marca)
        encontrados |= publicos_de(d)
    return encontrados


_CACHE: dict[int, set[Publico]] = {}


async def exigir_publico_declarado(request: Request) -> None:
    """La que no dice para quién es, no la usa nadie. Ver la cabecera."""
    ruta = request.scope.get("route")
    if not isinstance(ruta, APIRoute):
        return  # 404/405: no ha casado ninguna ruta, no hay nada que ejecutar
    clave = id(ruta)
    if clave not in _CACHE:
        _CACHE[clave] = publicos_de(ruta.dependant)
    if len(_CACHE[clave]) != 1:
        logger.error("Ruta sin público declarado (o con dos): %s %s → %s",
                     sorted(ruta.methods or ()), ruta.path, sorted(p.value for p in _CACHE[clave]))
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Esta ruta no declara quién puede usarla, y por eso no la puede usar nadie.",
        )
