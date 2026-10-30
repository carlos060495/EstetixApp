"""Qué centro se queda cuando el plan incluye menos de los que hay (0049, 0088).

EL ÚNICO ESCRITOR DE `salons.sigue_al_bajar`. Lo usan la bajada de plan
(`routers/billing.py`), el cambio de centro desde Ajustes
(`routers/centros.py`) y el webhook, que borra la marca al subir a un plan que
cubre todos los centros: si no, una elección vieja ganaría a la regla «se queda
el más antiguo» la próxima vez que el plan baje sin que nadie elija.
Lo vigila `tests/test_la_pausa_se_lee_y_el_dialogo_la_cuenta.py`.

UNA VEZ CADA 30 DÍAS (14-10-2026). Cambiar de centro es legítimo —una
reforma, una mudanza, un local de temporada— y dejarlo fijo convierte cada
error en un apaño a mano en producción. Rotar no: con 30 días cada local
trabajaría meses alternos. Son DÍAS y no periodos de facturación a propósito,
para que no dependa de si el plan es mensual o anual. La fuente es el propio
registro (`decisiones_de_dinero`, suceso `elige_centro`): no hay una segunda
columna que pueda contar otra cosa.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.billing.lo_que_incluye_el_plan import lo_que_incluye
from app.db.postgrest_client import PostgrestClient

DIAS_ENTRE_CAMBIOS = 30

# Lo que el reparto necesita de cada centro, más el nombre para la pantalla.
SELECT_CENTROS = "id,nombre_comercial,created_at,sigue_al_bajar"


async def centros_de(servicio: PostgrestClient, account_id: str) -> list[dict]:
    """Los centros de la CUENTA, con service_role: se cuenta la cuenta, no lo que ve la sesión."""
    return await servicio.select(
        "salons", {"select": SELECT_CENTROS, "account_id": f"eq.{account_id}", "order": "created_at.asc"}
    )


async def marcar_el_que_sigue(servicio: PostgrestClient, account_id: str, centro_id: str | None) -> None:
    """Deja marcado `centro_id` (o ninguno, con None).

    PRIMERO SE QUITA LA MARCA VIEJA Y DESPUÉS SE PONE LA NUEVA: la base solo
    admite un elegido por cuenta (índice único parcial de la 0049).
    """
    await servicio.update(
        "salons",
        {"account_id": f"eq.{account_id}", "sigue_al_bajar": "is.true"},
        {"sigue_al_bajar": False},
    )
    if centro_id:
        await servicio.update(
            "salons",
            {"id": f"eq.{centro_id}", "account_id": f"eq.{account_id}"},
            {"sigue_al_bajar": True},
        )


def marcado(centros: list[dict]) -> str | None:
    """El centro que la titular dejó marcado, si hay alguno."""
    return next((str(c["id"]) for c in centros if c.get("sigue_al_bajar")), None)


def en_pausa_si_se_queda(plan: str | None, centros: list[dict], centro_id: str | None) -> tuple[str, ...]:
    """Los centros que quedarían en pausa con este plan si se queda `centro_id`.

    El mismo reparto (`lo_que_incluye`), con la marca puesta donde se va a
    poner. Sin elección, el reparto tal cual: el marcado o el más antiguo.
    """
    if centro_id:
        centros = [{**c, "sigue_al_bajar": str(c.get("id")) == str(centro_id)} for c in centros]
    return lo_que_incluye(plan, centros).centros_en_pausa


async def se_puede_cambiar_desde(servicio: PostgrestClient, account_id: str, ahora: datetime) -> datetime | None:
    """El día a partir del cual puede volver a cambiar. None = ya puede."""
    filas = await servicio.select(
        "decisiones_de_dinero",
        {
            "select": "ocurrido_at",
            "account_id": f"eq.{account_id}",
            "suceso": "eq.elige_centro",
            "order": "ocurrido_at.desc",
            "limit": "1",
        },
    )
    if not filas:
        return None
    desde = datetime.fromisoformat(str(filas[0]["ocurrido_at"]).replace("Z", "+00:00")) + timedelta(
        days=DIAS_ENTRE_CAMBIOS
    )
    return desde if desde > ahora else None
