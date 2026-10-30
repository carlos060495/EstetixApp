from __future__ import annotations

from pydantic import BaseModel, Field


class ImpersonarIn(BaseModel):
    # Se acepta con guiones y en minúsculas: se normaliza en el endpoint.
    codigo: str = Field(min_length=1, max_length=32)


class ImpersonacionOut(BaseModel):
    """Lo justo para pintar la barra de "estás en modo soporte".

    Aquí NO viaja nada del salón más allá de su nombre: el access_token real
    se queda en la custodia del backend (app/core/tenant_token_broker.py).
    """

    sid: str
    salon_id: str
    salon_nombre: str
    expires_at: str
