from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status

from app.core import demo, publico
from app.core.config import Settings, get_settings
from app.core.security import CurrentUser, get_current_user, verificar_pin
from app.db.postgrest_client import PostgrestClient, PostgrestError
from app.schemas.fichaje import FichajeCreate, FichajeCreateOut, FichajeEstadoItem, FichajeEstadoOut

router = APIRouter(prefix="/fichaje", tags=["fichaje"], dependencies=[publico.QUIOSCO])
logger = logging.getLogger("estetix.fichaje")

_MAX_PIN_FAILS = 5
_LOCK_MINUTES = 1
_ACCENT_PALETTE = ["#C9A84C", "#5B8DB8", "#D98AA8", "#9B7BC9", "#4FA86B", "#C5524A"]


def _service_db(settings: Settings) -> PostgrestClient:
    # Service-role client for kiosk internals (time_entries + anti-bruteforce).
    # Tenant isolation remains enforced in this router using salon_id from JWT.
    return PostgrestClient(settings.supabase_url, settings.supabase_secret_key)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _today_start_utc() -> datetime:
    now = _utc_now()
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _estado_from_tipo(tipo: str) -> str:
    if tipo in {"entrada", "reanudacion"}:
        return "working"
    if tipo == "pausa":
        return "pause"
    return "out"


def _accent_from_name(nombre: str) -> str:
    h = sum(ord(ch) for ch in nombre)
    return _ACCENT_PALETTE[h % len(_ACCENT_PALETTE)]


def _initials(nombre: str) -> str:
    parts = nombre.strip().split()
    return "".join(p[:1].upper() for p in parts[:2])


def _log_fichaje(salon_id: str, employee_id: str, accion: str, resultado: str) -> None:
    logger.info(
        "fichaje_event %s",
        json.dumps(
            {
                "salon_id": salon_id,
                "employee_id": employee_id,
                "accion": accion,
                "resultado": resultado,
            },
            ensure_ascii=True,
        ),
    )


def _allowed_tipos_for_estado(estado: str) -> set[str]:
    if estado == "out":
        return {"entrada"}
    if estado == "working":
        return {"pausa", "salida"}
    return {"reanudacion", "salida"}


async def _get_employee(db: PostgrestClient, salon_id: str, employee_id: str) -> dict:
    rows = await db.select(
        "employees",
        {
            # Sin el puesto: aquí solo hacen falta el nombre (para el mensaje de
            # vuelta) y el hash del PIN (para verificarlo). Pedía `rol` sin
            # llegar a leerlo nunca, y esa columna la eliminó la 0027.
            "select": "id,nombre,estado,pin_fichaje_hash",
            "salon_id": f"eq.{salon_id}",
            "id": f"eq.{employee_id}",
            "estado": "eq.activo",
            "pin_fichaje_hash": "not.is.null",
            "limit": "1",
        },
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Empleado no encontrado")
    return rows[0]


async def _get_last_entry_today(
    db: PostgrestClient, salon_id: str, employee_id: str
) -> dict | None:
    rows = await db.select(
        "time_entries",
        {
            "select": "tipo,ts",
            "salon_id": f"eq.{salon_id}",
            "employee_id": f"eq.{employee_id}",
            "ts": f"gte.{_today_start_utc().isoformat()}",
            "order": "ts.desc",
            "limit": "1",
        },
    )
    return rows[0] if rows else None


async def _read_pin_attempt(
    db: PostgrestClient, salon_id: str, employee_id: str
) -> dict | None:
    rows = await db.select(
        "pin_attempt_locks",
        {
            "select": "failed_count,locked_until",
            "salon_id": f"eq.{salon_id}",
            "employee_id": f"eq.{employee_id}",
            "limit": "1",
        },
    )
    return rows[0] if rows else None


async def _store_pin_failure(db: PostgrestClient, salon_id: str, employee_id: str) -> None:
    now = _utc_now()
    row = await _read_pin_attempt(db, salon_id, employee_id)
    if not row:
        await db.insert(
            "pin_attempt_locks",
            {
                "salon_id": salon_id,
                "employee_id": employee_id,
                "failed_count": 1,
                "last_failed_at": now.isoformat(),
                "locked_until": None,
            },
        )
        return

    next_failed = int(row.get("failed_count") or 0) + 1
    lock_until = now + timedelta(minutes=_LOCK_MINUTES) if next_failed >= _MAX_PIN_FAILS else None
    await db.update(
        "pin_attempt_locks",
        {"salon_id": f"eq.{salon_id}", "employee_id": f"eq.{employee_id}"},
        {
            "failed_count": next_failed,
            "last_failed_at": now.isoformat(),
            "locked_until": lock_until.isoformat() if lock_until else None,
        },
    )


async def _clear_pin_failures(db: PostgrestClient, salon_id: str, employee_id: str) -> None:
    await db.delete(
        "pin_attempt_locks",
        {"salon_id": f"eq.{salon_id}", "employee_id": f"eq.{employee_id}"},
    )


def _mensaje_espera(bloqueado_hasta: str, *, tras_fallar: bool) -> str:
    minutos = max(1, math.ceil((_parse_iso(bloqueado_hasta) - _utc_now()).total_seconds() / 60))
    cuando = "1 minuto" if minutos == 1 else f"{minutos} minutos"
    if tras_fallar:
        return f"PIN incorrecto. Demasiados intentos: vuelve a probar en {cuando}."
    return f"Demasiados intentos fallidos. Vuelve a probar en {cuando}."


async def _apuntar_intento(db: PostgrestClient, salon_id: str, employee_id: str) -> dict | None:
    """Apunta el intento EN LA BASE, antes de comprobar el PIN (0047).

    EL AGUJERO QUE CIERRA. Lo de abajo —`_check_pin_lock`, comprobar, y
    `_store_pin_failure`— lee si hay bloqueo, comprueba el PIN y SOLO DESPUÉS
    apunta el fallo. En ese hueco cabe una ráfaga entera: medido el 15-09-2026,
    tres ráfagas de 12 PINES malos comprobaron 36 y dejaron el contador en 3, y
    una cuarta con el bueno entre once malos fichó de verdad. La función apunta
    con la fila bloqueada, así que cada petición cuenta aunque lleguen juntas.

    Devuelve `None` si la función todavía no existe: la 0047 se aplica DESPUÉS
    de desplegar este backend (quita permisos que el anterior usa), y durante
    esos minutos se sigue por el camino antiguo. Ese camino se borra cuando
    el backend anterior ya no pueda volver.
    """
    try:
        filas = await db.rpc("pin_fichaje_intento", {"p_salon_id": salon_id, "p_employee_id": employee_id})
    except PostgrestError as e:
        if e.status_code == status.HTTP_404_NOT_FOUND:
            logger.warning("fichaje: pin_fichaje_intento no existe todavía (¿0047 sin aplicar?); camino antiguo")
            return None
        raise
    return filas[0] if filas else None


async def _check_pin_lock(db: PostgrestClient, salon_id: str, employee_id: str) -> None:
    row = await _read_pin_attempt(db, salon_id, employee_id)
    if not row:
        return
    locked_until_raw = row.get("locked_until")
    if not locked_until_raw:
        return
    if _parse_iso(locked_until_raw) > _utc_now():
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Demasiados intentos fallidos. Inténtalo de nuevo en un minuto.",
        )


@router.post(
    "",
    response_model=FichajeCreateOut,
    responses={
        409: {"description": "Transición de fichaje no válida"},
        422: {"description": "PIN incorrecto"},
        429: {"description": "Demasiados intentos"},
        502: {"description": "Error de pasarela al registrar fichaje"},
    },
)
async def create_fichaje(
    payload: FichajeCreate,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> FichajeCreateOut:
    # EN UNA DEMO (0081): FUNCIONA con el PIN sembrado de cada empleada, que el
    # quiosco de la demo enseña (`demo.PINES_DE_MUESTRA`).
    demo.funciona_en_la_muestra("pin_de_empleada")
    db = _service_db(settings)

    try:
        employee = await _get_employee(db, user.salon_id, payload.employee_id)
        pin_hash = employee.get("pin_fichaje_hash")
        if not pin_hash:
            _log_fichaje(user.salon_id, payload.employee_id, payload.accion, "employee_no_pin")
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Empleado no encontrado")

        intento = await _apuntar_intento(db, user.salon_id, payload.employee_id)
        if intento is None:
            # Camino antiguo, solo mientras la 0047 no esté aplicada.
            await _check_pin_lock(db, user.salon_id, payload.employee_id)
            if not await verificar_pin(payload.pin, pin_hash):
                await _store_pin_failure(db, user.salon_id, payload.employee_id)
                _log_fichaje(user.salon_id, payload.employee_id, payload.accion, "pin_incorrecto")
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="PIN incorrecto",
                )
        else:
            if not intento.get("permitido"):
                # En la espera no se comprueba NADA, ni el PIN bueno: si entrara,
                # la espera no frenaría a quien prueba, solo le diría cuándo acierta.
                _log_fichaje(user.salon_id, payload.employee_id, payload.accion, "en_espera")
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=_mensaje_espera(intento["bloqueado_hasta"], tras_fallar=False),
                )
            if not await verificar_pin(payload.pin, pin_hash):
                _log_fichaje(user.salon_id, payload.employee_id, payload.accion, "pin_incorrecto")
                if intento.get("bloqueado_hasta"):
                    raise HTTPException(
                        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                        detail=_mensaje_espera(intento["bloqueado_hasta"], tras_fallar=True),
                    )
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="PIN incorrecto",
                )

        last_entry = await _get_last_entry_today(db, user.salon_id, payload.employee_id)
        estado_actual = _estado_from_tipo(last_entry["tipo"]) if last_entry else "out"
        if payload.accion not in _allowed_tipos_for_estado(estado_actual):
            _log_fichaje(user.salon_id, payload.employee_id, payload.accion, "transicion_invalida")
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Transición de fichaje no válida para el estado actual",
            )

        now = _utc_now()
        await db.insert(
            "time_entries",
            {
                "salon_id": user.salon_id,
                "employee_id": payload.employee_id,
                "tipo": payload.accion,
                "ts": now.isoformat(),
                "created_at": now.isoformat(),
            },
        )

        await _clear_pin_failures(db, user.salon_id, payload.employee_id)
        nuevo_estado = _estado_from_tipo(payload.accion)
        _log_fichaje(user.salon_id, payload.employee_id, payload.accion, "ok")
    except HTTPException:
        raise
    except PostgrestError:
        # Igual que en `/estado`: `_log_fichaje` deja constancia de QUÉ se
        # intentaba, pero no de por qué falló. Las dos cosas hacen falta.
        logger.exception("fichaje: PostgREST rechazó la consulta")
        _log_fichaje(user.salon_id, payload.employee_id, payload.accion, "error_db")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo registrar el fichaje",
        )

    return FichajeCreateOut(
        ok=True,
        nombre=employee["nombre"],
        accion=payload.accion,
        ts=now,
        nuevo_estado=nuevo_estado,
    )


@router.get("/estado", response_model=FichajeEstadoOut)
async def get_fichaje_estado(
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> FichajeEstadoOut:
    db = _service_db(settings)
    try:
        employees = await db.select(
            "employees",
            {
                # `puestos(nombre)` es un embed sobre la FK, el mismo que usa
                # /employees: el puesto se lee en la misma consulta y renombrarlo
                # se ve al instante en el quiosco. Antes se pedía `employees.rol`,
                # una columna de texto que la migración 0027 eliminó al sacar el
                # puesto a su propia tabla.
                "select": "id,nombre,puestos(nombre)",
                "salon_id": f"eq.{user.salon_id}",
                "estado": "eq.activo",
                "pin_fichaje_hash": "not.is.null",
                "order": "nombre.asc",
            },
        )
        today_entries = await db.select(
            "time_entries",
            {
                "select": "employee_id,tipo,ts",
                "salon_id": f"eq.{user.salon_id}",
                "ts": f"gte.{_today_start_utc().isoformat()}",
                "order": "ts.desc",
            },
        )
    except PostgrestError:
        # CON TRAZA, y no es un adorno: este `except` convertía cualquier fallo
        # de PostgREST en un 502 sin una línea en el log. Cuando la columna
        # `employees.rol` desapareció, PostgREST decía exactamente qué pasaba
        # («column employees.rol does not exist») y ese mensaje se tiraba a la
        # basura aquí: en producción solo quedaba un 502 mudo que parecía un
        # cuelgue. El error de fuera se registra; al cliente le sigue llegando
        # el mensaje de siempre, sin detalles internos.
        logger.exception("fichaje_estado: PostgREST rechazó la consulta")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo cargar el estado de fichaje",
        )

    latest_by_employee: dict[str, dict] = {}
    for row in today_entries:
        eid = row["employee_id"]
        if eid not in latest_by_employee:
            latest_by_employee[eid] = row

    equipo: list[FichajeEstadoItem] = []
    for emp in employees:
        last = latest_by_employee.get(emp["id"])
        estado = "out"
        desde: datetime | None = None

        if not last:
            estado = "out"
        else:
            estado = _estado_from_tipo(last["tipo"])
            # LA HORA DEL ÚLTIMO FICHAJE DE HOY, también si ya salió: el
            # quiosco decía «Sin fichar hoy» a quien acababa de fichar la salida.
            desde = _parse_iso(last["ts"])

        equipo.append(
            FichajeEstadoItem(
                id=emp["id"],
                nombre=emp["nombre"],
                # Puede no tener puesto: la 0027 lo dejó nullable a propósito
                # —un salón recién creado no tiene ninguno todavía—, así que el
                # quiosco tiene que saber pintar a alguien sin él.
                puesto=(emp.get("puestos") or {}).get("nombre"),
                iniciales=_initials(emp["nombre"]),
                accent=_accent_from_name(emp["nombre"]),
                estado_fichaje=estado,
                desde=desde,
            )
        )

    return FichajeEstadoOut(server_time=_utc_now(), equipo=equipo)
