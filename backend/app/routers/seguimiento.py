"""EL ENLACE DE SEGUIMIENTO DE UN TRABAJADOR DE AGENCIA (0077).

PÚBLICO A PROPÓSITO (`publico.SEGUIMIENTO`): sin cuenta ni contraseña. Lo abre
quien tenga el enlace, que la agencia genera desde su portal, le pasa a su
trabajador y puede revocar o cambiar por otro.

LO QUE ENSEÑA, Y NADA MÁS (06-10-2026): los salones que ese
trabajador trajo, su nombre comercial, el NOMBRE del plan que tienen hoy
—y, si al darse de alta eligieron otro, cuál: es lo que vendió—, desde
cuándo, y si hoy pagan, están en prueba o se cayeron. Un trabajador de baja
no abre: su enlace se revoca al darlo de baja. Ni un importe, ni un porcentaje, ni una comisión, ni datos de
contacto del salón. Lo vigila `tests/test_seguimiento_sin_dinero.py`
recorriendo lo que devuelve esta ruta, y las columnas de la función de la
base que la alimenta.

No se indexa (`X-Robots-Tag`), no se guarda en ninguna caché y no deja el
enlace en el `Referer` de nada.
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel

from app.billing.rastro import NOMBRE_DEL_PLAN
from app.core import agencias, publico
from app.core.admin_audit import client_ip
from app.core.config import Settings, get_settings
from app.core.limites import AttemptRateLimiter
from app.core.sesion_referido import servicio
from app.db.postgrest_client import PostgrestError

logger = logging.getLogger("estetix.referidos")

router = APIRouter(prefix="/seguimiento", tags=["seguimiento"], dependencies=[publico.SEGUIMIENTO])

# Un trabajador mira su página unas cuantas veces al día; quien prueba enlaces
# al azar, muchas. 60 por IP y ventana es de sobra para lo primero.
_freno = AttemptRateLimiter(max_attempts_per_ip=60, max_attempts_per_email=10_000)

NO_ABRE = "Este enlace ya no abre. Si lo necesitas, pídeselo a tu agencia."

CABECERAS = {
    "X-Robots-Tag": "noindex, nofollow, noarchive",
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
}


class SalonSeguidoOut(BaseModel):
    """Un salón que trajo. Sin un solo número de dinero."""

    salon: str
    plan: str | None = None  # el NOMBRE del plan: «Pro». Nunca su precio.
    # El que eligió al darse de alta, SOLO si no es el de hoy (lo decide la base).
    plan_al_llegar: str | None = None
    desde: datetime
    estado: str  # paga · en_prueba · caido


class SeguimientoOut(BaseModel):
    trabajador: str
    agencia: str
    salones: list[SalonSeguidoOut]


def _nombre(plan: str | None) -> str | None:
    return NOMBRE_DEL_PLAN.get(plan, plan) if plan else None


@router.get("/{token}", response_model=SeguimientoOut)
async def ver_seguimiento(token: str, request: Request, response: Response,
                          settings: Settings = Depends(get_settings)) -> SeguimientoOut:
    ip = client_ip(request)
    bloqueado, reintentar = _freno.is_blocked(ip, "seguimiento")
    if bloqueado:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                            detail="Demasiadas peticiones. Espera unos minutos.",
                            headers={"Retry-After": str(reintentar), **CABECERAS})
    _freno.add_attempt(ip, "seguimiento")
    if not 20 <= len(token) <= 200:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NO_ABRE, headers=CABECERAS)
    try:
        datos = await agencias.seguimiento(servicio(settings), token)
    except PostgrestError as e:
        logger.exception("seguimiento: la base no contestó")
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudo cargar.",
                            headers=CABECERAS) from e
    if datos is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NO_ABRE, headers=CABECERAS)
    response.headers.update(CABECERAS)
    return SeguimientoOut(
        trabajador=datos["trabajador"],
        agencia=datos["agencia"],
        salones=[SalonSeguidoOut(salon=s["salon"], plan=_nombre(s["plan"]), plan_al_llegar=_nombre(s.get("plan_al_llegar")),
                                 desde=s["desde"], estado=s["estado"]) for s in datos["salones"]],
    )
