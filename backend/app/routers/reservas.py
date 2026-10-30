from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core import publico
from app.core.security import CurrentUser, get_current_user, get_postgrest_client
from app.db.postgrest_client import PostgrestClient
from app.schemas.appointments import CANALES_VALIDOS
from app.schemas.reservas import ReservaOut, ReservasKpisOut, ReservasListOut

# Una "reserva" ES una cita (appointments) con su columna `canal`; este router
# solo la proyecta a la forma que necesita la pantalla de Reservas. No hay
# tabla nueva. Solo admin (modelo admin-only del SaaS).
router = APIRouter(prefix="/reservas", tags=["reservas"], dependencies=[publico.ADMINISTRADORA])

# Embeds de PostgREST para resolver nombres/precio en una sola consulta.
_SELECT = (
    "id,inicio,estado,canal,nombre_reserva,"
    "clients(nombre),services(nombre,precio),employees(nombre)"
)

_ESTADOS_CONFIRMADOS = {"confirmada", "completada"}


def _a_reserva(row: dict) -> dict:
    cliente = (row.get("clients") or {}).get("nombre") or "Cliente Express"
    servicio = row.get("services") or {}
    return {
        "id": row["id"],
        "cliente": cliente,
        # El nombre que dio quien reservó, si entró por el widget o por el bot.
        # Puede no coincidir con el de la ficha —se reserva para otra persona— y
        # entonces el salón necesita ver los dos para saber a quién espera.
        "nombre_reserva": row.get("nombre_reserva"),
        "servicio": servicio.get("nombre"),
        "precio": servicio.get("precio"),
        "profesional": (row.get("employees") or {}).get("nombre"),
        "inicio": row["inicio"],
        "canal": row["canal"],
        "estado": row["estado"],
    }


@router.get("", response_model=ReservasListOut)
async def list_reservas(
    desde: datetime = Query(..., description="Inicio del rango (ISO)"),
    hasta: datetime = Query(..., description="Fin EXCLUSIVO del rango (ISO)"),
    canal: str | None = Query(None, description="Filtrar por canal del enum"),
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> dict:
    """Reservas del rango (por fecha de INICIO de la cita, no por solape:
    cada reserva pertenece al día en que empieza) más los contadores por
    canal del rango completo, para que los chips cuadren con la tabla."""
    if canal is not None and canal not in CANALES_VALIDOS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"canal inválido: {canal}"
        )

    # TODAS, no las mil primeras: `select` se queda en mil sin avisar y esta lista crece con el tiempo.
    rows = await db.select_todo(
        "appointments",
        {
            "select": _SELECT,
            "salon_id": f"eq.{user.salon_id}",
            "and": f"(inicio.gte.{desde.isoformat()},inicio.lt.{hasta.isoformat()})",
            "order": "inicio.asc,id.asc",
        },
    )

    contadores = {c: 0 for c in sorted(CANALES_VALIDOS)}
    for row in rows:
        contadores[row["canal"]] = contadores.get(row["canal"], 0) + 1
    contadores["todos"] = len(rows)

    if canal is not None:
        rows = [r for r in rows if r["canal"] == canal]

    return {"reservas": [_a_reserva(r) for r in rows], "contadores": contadores}


@router.get("/kpis", response_model=ReservasKpisOut)
async def reservas_kpis(
    desde: datetime = Query(..., description="Inicio del período (ISO)"),
    hasta: datetime = Query(..., description="Fin EXCLUSIVO del período (ISO)"),
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> dict:
    """KPIs agregados del período (la pantalla envía el mes en curso).
    Fórmulas documentadas en ReservasKpisOut."""
    # TODAS: con `select` un mes de más de mil citas daba KPIs de las mil primeras.
    rows = await db.select_todo(
        "appointments",
        {
            "select": "estado",
            "salon_id": f"eq.{user.salon_id}",
            "and": f"(inicio.gte.{desde.isoformat()},inicio.lt.{hasta.isoformat()})",
            "order": "inicio.asc,id.asc",
        },
    )
    total = len(rows)
    pendientes = sum(1 for r in rows if r["estado"] == "pendiente")
    confirmadas = sum(1 for r in rows if r["estado"] in _ESTADOS_CONFIRMADOS)
    return {
        "total": total,
        "pendientes": pendientes,
        "confirmadas": confirmadas,
        "tasa_confirmacion_pct": round(confirmadas / total * 100, 1) if total else 0.0,
    }
