import secrets
from uuid import uuid4
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status

from app.core import publico
from app.billing.plans import employee_limit
from app.billing.rastro import instantanea, usuario_de_la_sesion
from app.core import baja as bajas
from app.core import correcciones_fichaje as correcciones
from app.core import zona as zonas
from app.core.errores_visibles import visible
from app.core.impacto import (
    SIN_PROFESIONAL,
    CitasAfectadas,
    a_http,
    cargar_marco,
    citas_por_venir,
    exigir_que_se_hayan_visto,
    que_quedan_fuera,
)
from app.core.cuenta import leer_cuenta
from app.core.security import (
    admite_cuenta_cortada,
    CurrentUser,
    get_current_user,
    get_postgrest_client,
    get_service_postgrest_client,
    hashear_pin,
)
from app.core.citas import es_violacion_de_exclusion
from app.db.postgrest_client import PostgrestClient, PostgrestError
from app.schemas.impacto import ConCitasVistas
from app.schemas.employees import (
    BajaOut,
    BajaPreviaOut,
    EmployeeCreate,
    CorreccionDeFichajeIn,
    CorreccionDeFichajeOut,
    EmployeeFichajeCorreccionOut,
    EmployeeFichajeDiaOut,
    EmployeeFichajeHistorialOut,
    EmployeeFichajeMarcaOut,
    EmployeeHorasCentroOut,
    EmployeeHorasPersonaOut,
    EmployeeOut,
    EmpleadaDeLaAgendaOut,
    EmployeePinGeneratedOut,
    EmployeePinIn,
    EmployeePinOut,
    EmployeeUpdate,
    EmployeeVinculableOut,
)

router = APIRouter(prefix="/employees", tags=["employees"], dependencies=[publico.ADMINISTRADORA])

# employee_services(service_id) is a PostgREST embed over the FK: each row
# arrives with its assigned service ids in a single query.
# puestos(nombre) es otro embed sobre la FK: la ficha pinta el nombre del
# puesto sin una segunda consulta, y renombrarlo se ve al instante en todos sus
# empleados porque lo que se guarda es el id, no el texto.
#
# SIN `pin_fichaje_hash`, y no es un olvido: desde la 0047 la sesión no puede
# leer esa columna (ni pedir `*`, que la incluye). Si la ficha tiene PIN lo
# responde `_con_pin`, con service_role. Ver `_COLUMNAS`.
_SELECT = (
    "id,nombre,telefono,email,puesto_id,estado,comision_tipo,comision_servicios,"
    "comision_productos,persona_id,baja_el,"
    "employee_services(service_id),puestos(nombre)"
)

# Lo que devuelve un INSERT o un UPDATE de `employees` hecho con la sesión.
# Hay que decirlo: sin lista de columnas PostgREST devuelve `*`, y `*` incluye
# el hash, así que responde 403 aunque la escritura sea legítima (medido).
_COLUMNAS = (
    "id,nombre,telefono,email,puesto_id,estado,comision_tipo,comision_servicios,"
    "comision_productos,persona_id,baja_el"
)

# Ids per in.() filter when validating ownership, to keep URLs short.
_ID_CHUNK = 100


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# LO MÁS LARGO QUE PUEDE DURAR UNA SESIÓN DE FICHAJE. Una salida del día
# siguiente cierra la sesión de la noche anterior (un turno de 20:00 a 01:00 son
# 5 h del día en que empezó), pero sin un límite, una entrada olvidada el lunes
# a las 09:00 y la salida del martes a las 18:00 serían 33 horas seguidas. Más de
# esto no es un turno: es una marca que falta, y el día sale incompleto.
SESION_MAXIMA = timedelta(hours=16)


def _month_bounds(mes: str | None, z: ZoneInfo) -> tuple[datetime, datetime]:
    """Dónde empieza y acaba el mes EN EL CENTRO, como instantes.

    Era la medianoche de UTC: un fichaje a las 00:30 del día 1 en Madrid caía
    en el mes anterior.
    """
    hoy = zonas.hoy(z, _utc_now())
    primero = hoy.replace(day=1)
    if mes is not None:
        try:
            anio, numero = (int(x) for x in mes.split("-"))
            primero = date(anio, numero, 1)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Formato de mes inválido. Usa YYYY-MM") from exc
    siguiente = date(primero.year + (primero.month == 12), primero.month % 12 + 1, 1)
    return zonas.inicio_del_dia(primero, z), zonas.inicio_del_dia(siguiente, z)


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _to_out(row: dict, tiene_pin: bool) -> dict:
    """DB row (with embed) → EmployeeOut shape.

    The Argon2id hash never leaves the API (only `tiene_pin`, que llega aparte
    porque la sesión ya no lee el hash: ver `_con_pin`). Las comisiones salen
    tal cual: solo la administradora tiene sesión (0063, 0069), y son suyas.
    """
    out = dict(row)
    links = out.pop("employee_services", None) or []
    out["service_ids"] = [link["service_id"] for link in links]
    # El nombre del puesto se aplana a un campo suelto: el frontend solo quiere
    # pintarlo, y así no tiene que saber que por debajo hay un embed.
    puesto = out.pop("puestos", None) or {}
    out["puesto"] = puesto.get("nombre")
    out.pop("pin_fichaje_hash", None)
    out["tiene_pin"] = tiene_pin
    return out


async def _con_pin(service: PostgrestClient, ids: list[str]) -> set[str]:
    """De estas fichas, cuáles tienen PIN de quiosco.

    CON service_role, porque la sesión no puede leer `pin_fichaje_hash` —ni
    filtrar por él— desde la 0047: quien tuviera el dispositivo del mostrador se
    llevaba los hashes y probaba las diez mil combinaciones en su casa.

    Los `ids` salen SIEMPRE de una consulta hecha antes con la sesión, así que la
    RLS ya ha decidido qué fichas se ven; esto solo añade un sí o un no a cada
    una. Lo único que viaja de vuelta es el `id`.
    """
    unicos = sorted(set(ids))
    con: set[str] = set()
    for i in range(0, len(unicos), _ID_CHUNK):
        filas = await service.select(
            "employees",
            {
                "select": "id",
                "id": f"in.({','.join(unicos[i : i + _ID_CHUNK])})",
                "pin_fichaje_hash": "not.is.null",
            },
        )
        con.update(f["id"] for f in filas)
    return con


async def _ensure_services_in_salon(
    db: PostgrestClient, salon_id: str, service_ids: list[str]
) -> list[str]:
    """Valida que todos los service_ids pertenezcan al salón (la RLS de
    employee_services solo comprueba el lado del empleado, no el del
    servicio) y devuelve la lista deduplicada."""
    unique = sorted(set(service_ids))
    for i in range(0, len(unique), _ID_CHUNK):
        chunk = unique[i : i + _ID_CHUNK]
        rows = await db.select(
            "services",
            {"select": "id", "salon_id": f"eq.{salon_id}", "id": f"in.({','.join(chunk)})"},
        )
        if len(rows) != len(chunk):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Algún servicio asignado no pertenece a este salón",
            )
    return unique


async def _puesto_del_salon(
    db: PostgrestClient, salon_id: str, puesto_id: str | None
) -> str | None:
    """Valida que el puesto sea de ESTE salón y devuelve su nombre.

    La RLS de `employees` valida la fila del empleado, no lo que referencia:
    sin este paso se podría enlazar el puesto de otro salón (mismo patrón que
    `_ensure_services_in_salon` aquí al lado y que `_ensure_refs_in_salon` en
    /appointments).
    """
    if not puesto_id:
        return None
    filas = await db.select(
        "puestos",
        {"select": "nombre", "id": f"eq.{puesto_id}", "salon_id": f"eq.{salon_id}", "limit": "1"},
    )
    if not filas:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El puesto no pertenece a este salón",
        )
    return filas[0]["nombre"]


async def _replace_service_links(
    db: PostgrestClient, employee_id: str, service_ids: list[str]
) -> None:
    # service_ids ya validados/deduplicados. La RLS de employee_services
    # exige rol admin y que el empleado sea del salón del caller.
    await db.delete("employee_services", {"employee_id": f"eq.{employee_id}"})
    if service_ids:
        await db.insert_many(
            "employee_services",
            [{"employee_id": employee_id, "service_id": sid} for sid in service_ids],
        )


async def _ensure_below_employee_limit(db: PostgrestClient, salon_id: str) -> None:
    """Gating REAL por plan (DIRECTRICES §4.5): el nº de empleados ACTIVOS del
    salón no puede alcanzar el máximo del plan. Revalidado en backend, nunca
    solo en el frontend.

    EL PLAN ES DE LA CUENTA; EL LÍMITE, DE CADA CENTRO. Un Elite con dos
    centros son dieciséis empleados en cada uno, no dieciséis repartidos entre
    los dos: el límite existe porque un salón más grande cuesta más de
    atender, y abrir un segundo local no hace más pequeño al primero. Por eso
    el plan se lee de `accounts` y el recuento sigue filtrando por `salon_id`.
    """
    cuenta = await leer_cuenta(db, salon_id, "plan")
    limite = employee_limit(cuenta.get("plan"))

    activos = await db.select(
        "employees",
        {"select": "id", "salon_id": f"eq.{salon_id}", "estado": "eq.activo"},
    )
    if len(activos) >= limite:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Has alcanzado el límite de empleados de tu plan ({limite}). "
                "Mejora tu plan para añadir más."
            ),
        )


async def _fetch_one(db: PostgrestClient, salon_id: str, employee_id: str) -> dict:
    rows = await db.select(
        "employees",
        {"select": _SELECT, "id": f"eq.{employee_id}", "salon_id": f"eq.{salon_id}"},
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Empleado no encontrado")
    return rows[0]


def _random_pin() -> str:
    return "".join(secrets.choice("0123456789") for _ in range(4))


async def _update_pin_hash(
    service: PostgrestClient, salon_id: str, employee_id: str, pin_hash: str | None
) -> None:
    """Escribe el hash del PIN del quiosco. CON service_role (0047).

    Quien llama tiene que haber comprobado ANTES, con la sesión, que la ficha es
    visible (`_fetch_one`, o el INSERT recién hecho): aquí la RLS ya no pregunta,
    así que el `salon_id` del filtro es el de la sesión verificada, nunca uno que
    venga en la petición.

    Y BORRA SU ESPERA. Desde la 0047 los fallos del quiosco escalan (1, 5 y 15
    minutos, con tope en 15), así que alguien que teclee mal el PIN de otra a
    propósito puede dejarla un rato sin fichar. La salida es esta: el
    administrador le pone o le genera un PIN nuevo y la espera desaparece.
    """
    rows = await service.update(
        "employees",
        {"id": f"eq.{employee_id}", "salon_id": f"eq.{salon_id}"},
        {"pin_fichaje_hash": pin_hash},
        select="id",
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Empleado no encontrado")
    await service.delete(
        "pin_attempt_locks", {"salon_id": f"eq.{salon_id}", "employee_id": f"eq.{employee_id}"}
    )


@router.post("/{employee_id}/pin/generar", response_model=EmployeePinGeneratedOut)
async def generate_employee_pin(
    employee_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
    service: PostgrestClient = Depends(get_service_postgrest_client),
) -> dict:
    await _fetch_one(db, user.salon_id, employee_id)
    pin = _random_pin()
    try:
        await _update_pin_hash(service, user.salon_id, employee_id, await hashear_pin(pin))
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=visible("generar el PIN")
        ) from e

    # El PIN real solo se devuelve una vez. Se genera después de validar acceso.
    return EmployeePinGeneratedOut(pin=pin)


@router.put("/{employee_id}/pin", response_model=EmployeePinOut)
async def set_employee_pin(
    employee_id: str,
    payload: EmployeePinIn,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
    service: PostgrestClient = Depends(get_service_postgrest_client),
) -> dict:
    await _fetch_one(db, user.salon_id, employee_id)
    try:
        await _update_pin_hash(service, user.salon_id, employee_id, await hashear_pin(payload.pin))
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=visible("guardar el PIN")
        ) from e
    return EmployeePinOut(tiene_pin=True)


@router.delete("/{employee_id}/pin", response_model=EmployeePinOut)
async def delete_employee_pin(
    employee_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
    service: PostgrestClient = Depends(get_service_postgrest_client),
) -> dict:
    await _fetch_one(db, user.salon_id, employee_id)
    try:
        await _update_pin_hash(service, user.salon_id, employee_id, None)
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=visible("quitar el PIN")
        ) from e
    return EmployeePinOut(tiene_pin=False)


def _resumir_fichajes(
    rows: list[dict],
    desde: datetime,
    hasta: datetime,
    z: ZoneInfo,
    detalle: dict[str, EmployeeFichajeMarcaOut] | None = None,
    anuladas: dict[str, datetime] | None = None,
) -> tuple[list[EmployeeFichajeDiaOut], int]:
    """Marcas sueltas → días con total, POR SESIONES y en la hora DEL CENTRO.

    Una sesión va de una `entrada` a su `salida`, descontando sus pausas, y
    CUENTA EN EL DÍA LOCAL DE SU ENTRADA aunque la salida sea al día siguiente:
    un turno de 20:00 a 01:00 son 5 horas del día en que empezó, no un día
    incompleto y otro con una salida suelta. Un día puede tener varias sesiones
    (turno partido); el hueco entre ellas no cuenta.

    UNA SESIÓN SIN SALIDA NO SABE CUÁNTAS HORAS FUERON, Y NO SE LAS INVENTA:
    esa sesión no suma nada. Pero LAS SESIONES YA CERRADAS DE ESE DÍA SÍ: si
    alguien cerró la mañana, esas horas están trabajadas y se saben
    (02-10-2026). Antes se apartaba el día entero, y el total del mes salía más
    bajo sin decir cuánto. El día sale `incompleto` y la pantalla lo avisa. Si
    la sesión abierta empezó hace menos de `SESION_MAXIMA` es `en_curso`: puede
    estar trabajando ahora mismo. Una salida que llegaría más tarde de `SESION_MAXIMA` no la
    cierra, y una marca suelta (una salida sin entrada) también deja el día
    incompleto: en los dos casos falta algo.

    `rows` llega ordenado por instante y con margen por los dos lados del mes:
    una sesión de la última noche del mes anterior se cierra con su salida del
    día 1 y no ensucia este mes, y la de la última noche de este se cierra con
    la del mes que viene. Solo cuentan las sesiones que EMPIEZAN en [desde, hasta).

    `rows` ya trae las CORRECCIONES aplicadas (0061; ver `_marcas_corregidas`).
    Con `detalle` —las marcas con su historia, por id—, cada día lleva sus marcas
    —también las anuladas— y dice si tiene alguna corrección.
    """
    ahora = _utc_now()
    dias: dict[date, dict] = {}

    def dia_de(ts: datetime) -> dict:
        return dias.setdefault(
            zonas.fecha_local(ts, z),
            {"entrada": None, "pausa": None, "salida": None, "minutos": 0, "falta": False, "en_curso": False,
             "marcas": []},
        )

    abierta: dict | None = None  # {"desde", "pausa_desde", "pausa_min", "cuenta"}

    def dejar_abierta(sesion: dict) -> None:
        if sesion["cuenta"]:
            d = dia_de(sesion["desde"])
            if ahora - sesion["desde"] < SESION_MAXIMA:
                d["en_curso"] = True
            else:
                d["falta"] = True

    for row in rows:
        ts = _parse_ts(row["ts"])
        tipo = row["tipo"]
        if abierta is not None and ts - abierta["desde"] > SESION_MAXIMA:
            # Ya no puede ser la misma sesión: se queda sin salida.
            dejar_abierta(abierta)
            abierta = None
        cuenta = desde <= ts < hasta
        # Cada marca va al día de su SESIÓN (el de su entrada); una suelta, al suyo.
        if abierta is not None and tipo != "entrada":
            if abierta["cuenta"]:
                dia_de(abierta["desde"])["marcas"].append(row.get("id"))
        elif cuenta:
            if tipo == "entrada" and abierta is not None and \
                    zonas.fecha_local(ts, z) == zonas.fecha_local(abierta["desde"], z):
                if abierta["cuenta"]:
                    dia_de(abierta["desde"])["marcas"].append(row.get("id"))
            else:
                dia_de(ts)["marcas"].append(row.get("id"))
        if tipo == "entrada":
            if abierta is not None:
                if zonas.fecha_local(ts, z) == zonas.fecha_local(abierta["desde"], z):
                    continue  # una segunda entrada del mismo turno no abre otro
                dejar_abierta(abierta)
            abierta = {"desde": ts, "pausa_desde": None, "pausa_min": 0, "cuenta": cuenta}
            if cuenta:
                d = dia_de(ts)
                d["entrada"] = d["entrada"] or ts
        elif abierta is None:
            # Una salida, pausa o reanudación sin entrada: falta una marca.
            if cuenta:
                dia_de(ts)["falta"] = True
        elif tipo == "pausa" and abierta["pausa_desde"] is None:
            abierta["pausa_desde"] = ts
            if abierta["cuenta"]:
                d = dia_de(abierta["desde"])
                d["pausa"] = d["pausa"] or ts
        elif tipo == "reanudacion" and abierta["pausa_desde"] is not None:
            abierta["pausa_min"] += int((ts - abierta["pausa_desde"]).total_seconds() // 60)
            abierta["pausa_desde"] = None
        elif tipo == "salida":
            if abierta["pausa_desde"] is not None:
                abierta["pausa_min"] += int((ts - abierta["pausa_desde"]).total_seconds() // 60)
            if abierta["cuenta"]:
                d = dia_de(abierta["desde"])
                trabajado = int((ts - abierta["desde"]).total_seconds() // 60)
                d["minutos"] += max(trabajado - abierta["pausa_min"], 0)
                d["salida"] = ts
            abierta = None
    if abierta is not None:
        dejar_abierta(abierta)

    # LAS ANULADAS no cuentan, pero se enseñan: tachadas, en el día de su hora.
    # Un día que solo tenía marcas anuladas sale igual, con 0 y corregido.
    for mid, ts_m in (anuladas or {}).items():
        if desde <= ts_m < hasta:
            dia_de(ts_m)["marcas"].append(mid)

    dias_out: list[EmployeeFichajeDiaOut] = []
    total_mes_min = 0
    for dia in sorted(dias, reverse=True):
        d = dias[dia]
        # Lo cerrado suma SIEMPRE; lo abierto, nunca. El estado dice si queda algo.
        estado = "incompleto" if d["falta"] else "en_curso" if d["en_curso"] else "completo"
        total_min = d["minutos"]
        total_mes_min += d["minutos"]
        salida = None
        if d["salida"] is not None:
            salida = zonas.hhmm(d["salida"], z)
            if zonas.fecha_local(d["salida"], z) != dia:
                salida += " (+1)"  # salió al día siguiente
        marcas = [detalle[i] for i in d["marcas"] if detalle and i in detalle]
        dias_out.append(
            EmployeeFichajeDiaOut(
                fecha=dia,
                entrada=zonas.hhmm(d["entrada"], z) if d["entrada"] else None,
                pausa=zonas.hhmm(d["pausa"], z) if d["pausa"] else None,
                salida=salida,
                total_min=total_min,
                estado=estado,
                corregido=any(m.correcciones for m in marcas),
                marcas=marcas,
            )
        )
    return dias_out, total_mes_min


async def _marcas_del_mes(
    db: PostgrestClient, salon_id: str, employee_id: str, desde: datetime, hasta: datetime
) -> list[dict]:
    """Las marcas del mes, con `SESION_MAXIMA` de margen a cada lado: lo justo
    para cerrar la sesión de la última noche y reconocer la salida de la del
    mes anterior."""
    return await db.select_todo(
        "time_entries",
        {
            "select": "id,tipo,ts",
            "salon_id": f"eq.{salon_id}",
            "employee_id": f"eq.{employee_id}",
            "and": f"(ts.gte.{(desde - SESION_MAXIMA).isoformat()},ts.lt.{(hasta + SESION_MAXIMA).isoformat()})",
            "order": "ts.asc,id.asc",
        },
    )


# ═══ LAS CORRECCIONES (0061) ═══════════════════════════════════════════════

_COLUMNAS_CORRECCION = (
    "id,lote,ocurrido_at,accion,marca_id,tipo,ts_antes,ts_despues,motivo,"
    "usuario_nombre,usuario_email,sobre_si_misma"
)


async def _correcciones_de(service: PostgrestClient, salon_id: str, employee_id: str) -> list[dict]:
    """Todas las correcciones de una ficha. Son pocas —una persona no corrige
    cientos de marcas— y hacen falta todas: una 'nueva' de un mes puede
    cambiarse desde otro. Con `service_role`: el registro no tiene políticas."""
    return await service.select_todo(
        "correcciones_de_fichaje",
        {
            "select": _COLUMNAS_CORRECCION,
            "salon_id": f"eq.{salon_id}",
            "employee_id": f"eq.{employee_id}",
            "order": "id.asc",
        },
    )


async def _marcas_corregidas(
    db: PostgrestClient, service: PostgrestClient, salon_id: str, employee_id: str,
    desde: datetime, hasta: datetime,
) -> list[correcciones.Marca]:
    """Las marcas del mes —con su margen— con las correcciones aplicadas. Si una
    corrección toca una marca del reloj que cae fuera del margen (se le movió la
    hora hacia aquí), esa marca se lee aparte por su id."""
    filas = await _marcas_del_mes(db, salon_id, employee_id, desde, hasta)
    corr = await _correcciones_de(service, salon_id, employee_id)
    vistas = {str(f["id"]) for f in filas}
    nuevas = {str(c["marca_id"]) for c in corr if c["accion"] == "nueva"}
    faltan = sorted({str(c["marca_id"]) for c in corr} - vistas - nuevas)
    if faltan:
        filas = filas + await db.select(
            "time_entries",
            {"select": "id,tipo,ts", "salon_id": f"eq.{salon_id}", "employee_id": f"eq.{employee_id}",
             "id": f"in.({','.join(faltan)})", "limit": str(len(faltan))},
        )
    margen_desde, margen_hasta = desde - correcciones.SESION_MAXIMA, hasta + correcciones.SESION_MAXIMA
    return [m for m in correcciones.aplicar(filas, corr) if margen_desde <= m.ts < margen_hasta]


def _detalle(marcas: list[correcciones.Marca], z: ZoneInfo) -> dict[str, EmployeeFichajeMarcaOut]:
    """Cada marca como la enseña el historial: la hora que cuenta, la del reloj
    si se cambió, y cada corrección con su motivo, quién y cuándo."""

    def hora(v) -> str | None:
        return zonas.hhmm(v, z) if v else None

    out = {}
    for m in marcas:
        out[m.id] = EmployeeFichajeMarcaOut(
            id=m.id,
            tipo=m.tipo,
            fecha=zonas.fecha_local(m.ts, z),
            hora=zonas.hhmm(m.ts, z),
            hora_reloj=zonas.hhmm(m.ts_reloj, z) if m.ts_reloj is not None and m.ts_reloj != m.ts else None,
            nueva=m.nueva,
            anulada=m.anulada,
            correcciones=[
                EmployeeFichajeCorreccionOut(
                    accion=c["accion"],
                    antes=hora(c.get("ts_antes")),
                    despues=hora(c.get("ts_despues")),
                    motivo=c["motivo"],
                    quien=c.get("usuario_nombre") or c.get("usuario_email"),
                    cuando=zonas.fecha_y_hora(c["ocurrido_at"], z),
                    sobre_si_misma=bool(c.get("sobre_si_misma")),
                )
                for c in m.correcciones
            ],
        )
    return out


def _resumir_corregidas(
    marcas: list[correcciones.Marca], desde: datetime, hasta: datetime, z: ZoneInfo
) -> tuple[list[EmployeeFichajeDiaOut], int]:
    return _resumir_fichajes(
        correcciones.como_filas(marcas), desde, hasta, z,
        detalle=_detalle(marcas, z),
        anuladas={m.id: m.ts for m in marcas if m.anulada},
    )


async def _horas_de_la_persona(
    db: PostgrestClient, service: PostgrestClient, ficha: dict, mes: str | None
) -> EmployeeHorasPersonaOut | None:
    """Las horas de la PERSONA en el mes, centro a centro.

    Devuelve None si la ficha no está enlazada, que es casi siempre: así el
    endpoint contesta EXACTAMENTE lo que contestaba antes para el 99% de la
    plantilla, y la pantalla no cambia sin motivo.
    """
    persona_id = ficha.get("persona_id")
    if not persona_id:
        return None

    hermanas = await db.select(
        "employees",
        {
            # `!employees_salon_id_fkey` DESAMBIGUA: hay dos caminos de `employees` a
            # `salons` (el propio y el que pasa por `pin_attempt_locks`), y sin
            # nombrar el bueno PostgREST contesta 300 PGRST201.
            "select": "id,salon_id,salons!employees_salon_id_fkey(nombre_comercial)",
            "persona_id": f"eq.{persona_id}",
            "order": "salon_id.asc",
        },
    )
    if len(hermanas) < 2:
        # Enlazada pero sola: no hay nada que sumar y enseñar un «total» de un
        # solo centro sería ruido.
        return None

    centros: list[EmployeeHorasCentroOut] = []
    total = 0
    sin_cerrar = 0
    for h in hermanas:
        # Cada centro, en SU zona y con SU mes: Las Palmas cierra el mes una
        # hora después que Madrid.
        z = await zonas.zona_del_centro(db, h["salon_id"])
        desde_c, hasta_c = _month_bounds(mes, z)
        marcas = await _marcas_corregidas(db, service, h["salon_id"], h["id"], desde_c, hasta_c)
        dias_c, minutos = _resumir_corregidas(marcas, desde_c, hasta_c, z)
        total += minutos
        sin_cerrar += sum(1 for d in dias_c if d.estado != "completo")
        centros.append(
            EmployeeHorasCentroOut(
                salon_id=h["salon_id"],
                centro=(h.get("salons") or {}).get("nombre_comercial") or "Centro",
                minutos=minutos,
            )
        )
    return EmployeeHorasPersonaOut(total_min=total, centros=centros, dias_sin_cerrar=sin_cerrar)


@router.get("/{employee_id}/fichajes", response_model=EmployeeFichajeHistorialOut)
async def get_employee_fichajes(
    employee_id: str,
    mes: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}$"),
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
    service: PostgrestClient = Depends(get_service_postgrest_client),
) -> dict:
    ficha = await _fetch_one(db, user.salon_id, employee_id)

    z = await zonas.zona_del_centro(db, user.salon_id)
    month_start, next_month = _month_bounds(mes, z)
    try:
        marcas = await _marcas_corregidas(db, service, user.salon_id, employee_id, month_start, next_month)
    except PostgrestError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudo leer el historial de fichajes") from e

    dias_out, total_mes_min = _resumir_corregidas(marcas, month_start, next_month, z)

    # ═══ LA SUMA DE LA PERSONA ═══
    # `horas_mes_min` sigue siendo lo de ESTA ficha y no se toca: la nómina de
    # este centro se paga con las horas de este centro. Lo que se añade es el
    # total de la persona, que hoy no se puede ver en ningún sitio y es la
    # segunda razón por la que existe el enlace.
    #
    # No hace falta service_role: este endpoint ya es admin-only y la RLS de
    # `time_entries` es `salon_id = any(salones_visibles())`, o sea que un
    # administrador ve los centros de SU cuenta y ninguno más.
    persona = await _horas_de_la_persona(db, service, ficha, mes)

    return EmployeeFichajeHistorialOut(
        horas_mes_min=total_mes_min,
        dias=dias_out,
        persona=persona,
        dias_incompletos=sum(1 for d in dias_out if d.estado == "incompleto"),
        dias_en_curso=sum(1 for d in dias_out if d.estado == "en_curso"),
    )


async def apuntar_correcciones(
    service: PostgrestClient,
    *,
    salon_id: str,
    usuario_id: str,
    empleada_nombre: str | None,
    sobre_si_misma: bool,
    cambios: list[dict],
) -> None:
    """Escribe las filas de una corrección en el registro (0061), de una vez:
    o entran todas —la entrada y la salida de una jornada— o ninguna.

    Con la cuenta y la INSTANTÁNEA de nombres (0057): la fila sigue diciendo
    de qué cuenta, qué centro, quién corrigió y a quién, aunque ya no existan.
    Es la misma función que usa el guarda de los registros.
    """
    centro = await service.select("salons", {"select": "account_id", "id": f"eq.{salon_id}", "limit": "1"})
    account_id = centro[0]["account_id"]
    nombres = await instantanea(service, account_id=account_id, salon_id=salon_id, usuario_id=usuario_id)
    await service.insert_many(
        "correcciones_de_fichaje",
        [
            {
                # Columna a columna: al registro no entra nada que no esté aquí.
                "lote": c["lote"],
                "salon_id": c["salon_id"],
                "employee_id": c["employee_id"],
                "accion": c["accion"],
                "marca_id": c["marca_id"],
                "tipo": c["tipo"],
                "ts_antes": c.get("ts_antes"),
                "ts_despues": c.get("ts_despues"),
                "motivo": c["motivo"],
                "account_id": account_id,
                "usuario_id": usuario_id,
                "cuenta_nombre": nombres.get("cuenta_nombre"),
                "centro_nombre": nombres.get("centro_nombre"),
                "usuario_nombre": nombres.get("usuario_nombre"),
                "usuario_email": nombres.get("usuario_email"),
                "empleada_nombre": empleada_nombre,
                "sobre_si_misma": sobre_si_misma,
            }
            for c in cambios
        ],
    )


@router.post(
    "/{employee_id}/fichajes/correcciones",
    response_model=CorreccionDeFichajeOut,
    status_code=status.HTTP_201_CREATED,
)
async def corregir_fichaje(
    employee_id: str,
    payload: CorreccionDeFichajeIn,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
    service: PostgrestClient = Depends(get_service_postgrest_client),
) -> dict:
    """CORREGIR UN FICHAJE ES DEJAR CONSTANCIA (0061).

    Nunca toca `time_entries`: añade filas a `correcciones_de_fichaje`, que es a
    la vez la corrección y su registro (quién, cuándo, qué había, qué hay y por
    qué), y que no se puede editar ni borrar.

    QUIÉN. Solo la administradora del centro: una empleada que pudiera corregir
    horas —las suyas o las de otra— quitaría al registro todo su valor. Y una
    sesión de SOPORTE tampoco, aunque actúe como la administradora: el registro
    de jornada lo corrige el salón. Son dos guardas: esta ruta está en
    `BLOQUEADOS` (`core/impersonation_policy.py`) y, por si la lista se tocara,
    aquí se vuelve a mirar.

    LO IMPOSIBLE lo para el servidor, no la pantalla (`nuevos_problemas`): una
    hora en el futuro, una salida anterior a su entrada, dos sesiones que se
    solapan o una sesión de más de 16 horas. Solo los problemas que TRAE la
    corrección: lo que ya estaba mal en el día no impide arreglar otra cosa.
    """
    if user.impersonation is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Una sesión de soporte no puede corregir fichajes: el registro de jornada lo corrige el salón.",
        )
    ficha = await _fetch_one(db, user.salon_id, employee_id)
    z = await zonas.zona_del_centro(db, user.salon_id)
    ahora = _utc_now()

    try:
        corr = await _correcciones_de(service, user.salon_id, employee_id)
        # Las marcas que se cambian o se anulan: del reloj o 'nuevas'.
        ids = sorted({c.marca_id for c in payload.cambios if c.marca_id})
        del_reloj = []
        if ids:
            del_reloj = await db.select(
                "time_entries",
                {"select": "id,tipo,ts", "salon_id": f"eq.{user.salon_id}", "employee_id": f"eq.{employee_id}",
                 "id": f"in.({','.join(ids)})", "limit": str(len(ids))},
            )
    except PostgrestError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudo leer el fichaje.") from e

    actuales = {m.id: m for m in correcciones.aplicar(del_reloj, corr)}
    for c in payload.cambios:
        if c.marca_id is None:
            continue
        m = actuales.get(c.marca_id)
        if m is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Esa marca no es de esta ficha.")
        if m.anulada:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Esa marca ya está anulada.")
        if c.accion == "cambia" and c.ts == m.ts:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                                detail="La hora nueva es la misma que ya tiene.")

    # NADA EN EL FUTURO: no se corrige lo que todavía no ha pasado.
    for c in payload.cambios:
        if c.ts is not None and c.ts > ahora:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Las {zonas.hhmm(c.ts, z)} del {zonas.dia_dd_mm(zonas.fecha_local(c.ts, z))} "
                       "todavía no han llegado: no se puede fichar en el futuro.",
            )

    # Las filas que se van a escribir, ya con la forma del registro.
    lote = str(uuid4())
    nuevas: list[dict] = []
    for i, c in enumerate(payload.cambios):
        actual = actuales.get(c.marca_id) if c.marca_id else None
        nuevas.append({
            "lote": lote,
            "salon_id": user.salon_id,
            "employee_id": employee_id,
            "accion": c.accion,
            "marca_id": c.marca_id or str(uuid4()),
            "tipo": actual.tipo if actual else c.tipo,
            "ts_antes": actual.ts.isoformat() if actual else None,
            "ts_despues": c.ts.isoformat() if c.ts else None,
            "motivo": payload.motivo,
            # Para simular el orden antes de escribir: después de todas.
            "ocurrido_at": ahora.isoformat(),
            "id": 10**15 + i,
        })

    # LO IMPOSIBLE: las marcas de alrededor, antes y después de la corrección.
    afectados = [zonas.instante_de(x) for n in nuevas for x in (n["ts_antes"], n["ts_despues"]) if x]
    desde_v = min(afectados) - 2 * correcciones.SESION_MAXIMA
    hasta_v = max(afectados) + 2 * correcciones.SESION_MAXIMA
    try:
        alrededor = await _marcas_corregidas(db, service, user.salon_id, employee_id, desde_v, hasta_v)
    except PostgrestError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudo leer el fichaje.") from e
    del_reloj_v = [{"id": m.id, "tipo": m.tipo, "ts": m.ts_reloj.isoformat()} for m in alrededor if not m.nueva]
    antes = correcciones.aplicar(del_reloj_v, corr)
    despues = correcciones.aplicar(del_reloj_v, corr + nuevas)
    problemas = correcciones.nuevos_problemas(antes, despues, z)
    if problemas:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=" ".join(p.mensaje for p in problemas),
        )

    # QUIÉN corrige.
    usuario_id = await usuario_de_la_sesion(service, user.sub)
    if usuario_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No se sabe quién corrige.")
    duena = await db.select("employees", {"select": "user_id", "id": f"eq.{employee_id}", "limit": "1"})
    try:
        await apuntar_correcciones(
            service,
            salon_id=user.salon_id,
            usuario_id=usuario_id,
            empleada_nombre=ficha.get("nombre"),
            sobre_si_misma=bool(duena and duena[0].get("user_id") == usuario_id),
            cambios=[{k: v for k, v in n.items() if k not in ("id", "ocurrido_at")} for n in nuevas],
        )
    except PostgrestError as e:
        if "23505" in (e.detail or "") or "una_anulacion" in (e.detail or ""):
            # Dos administradoras a la vez anulando la misma marca.
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Esa marca ya está anulada.") from e
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudo guardar la corrección.") from e
    return {"lote": lote, "filas": len(nuevas)}


@router.get("/agenda", response_model=list[EmpleadaDeLaAgendaOut])
@admite_cuenta_cortada("agenda")
async def equipo_de_la_agenda(
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> list[dict]:
    """El equipo como lo pinta la Agenda: nombre, estado y servicios.

    CON LAS BAJAS: sus citas pasadas siguen en la rejilla y necesitan su
    columna. Antes la Agenda pedía `/employees` y le llegaba la ficha entera
    —teléfono, correo, comisiones, puesto, si tiene PIN— a una pantalla fuera
    de la cortina del PIN que no enseña nada de eso. De la base se piden solo
    las columnas que se devuelven.
    """
    filas = await db.select(
        "employees",
        {
            "select": "id,nombre,estado,employee_services(service_id)",
            "salon_id": f"eq.{user.salon_id}",
            "order": "nombre.asc",
        },
    )
    return [
        {
            "id": f["id"],
            "nombre": f["nombre"],
            "estado": f["estado"],
            "service_ids": [e["service_id"] for e in f.get("employee_services") or []],
        }
        for f in filas
    ]


@router.get("", response_model=list[EmployeeOut])
async def list_employees(
    con_bajas: bool = Query(False, description="Incluir a quien está de baja (la pantalla de Empleados)."),
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
    service: PostgrestClient = Depends(get_service_postgrest_client),
) -> list[dict]:
    # SIN BAJAS POR DEFECTO. Quien pide la lista para ofrecer a alguien —un
    # selector de profesional, la reserva— no tiene que acordarse de quitarlas:
    # las pide solo quien las enseña a propósito.
    params = {"select": _SELECT, "salon_id": f"eq.{user.salon_id}", "order": "nombre.asc"}
    if not con_bajas:
        params["estado"] = "neq.baja"
    rows = await db.select("employees", params)
    con_pin = await _con_pin(service, [r["id"] for r in rows])
    return [_to_out(r, r["id"] in con_pin) for r in rows]


async def _resolver_persona(db: PostgrestClient, salon_id: str, ficha_id: str) -> str:
    """«Esta persona ya trabaja en el otro centro» → el id de la PERSONA.

    Tres cosas se comprueban, y las tres importan:

      1. QUE LA FICHA SEA VISIBLE. El `select` va con el token del inquilino y
         la RLS de `employees` es `salon_id = any(salones_visibles())`: una
         ficha de otra cuenta no aparece, y aquí se traduce en un 400. La
         frontera no la pone este `if`; la pone la base.
      2. QUE NO SEA DE ESTE MISMO CENTRO. Enlazar dos fichas del mismo sitio no
         es «trabaja en dos centros», es un duplicado — y el índice único lo
         rechazaría de todos modos, pero con un error que no dice nada.
      3. QUE LA PERSONA NO TENGA YA FICHA AQUÍ. También lo caza el índice
         único; se adelanta para poder explicarlo con palabras.
    """
    filas = await db.select(
        "employees",
        {"select": "id,salon_id,persona_id", "id": f"eq.{ficha_id}", "limit": "1"},
    )
    if not filas:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Esa persona no trabaja en ninguno de tus centros",
        )
    otra = filas[0]
    if otra["salon_id"] == salon_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Esa ficha es de este mismo centro; el enlace es para quien trabaja en otro",
        )

    persona = otra.get("persona_id") or otra["id"]

    ya_aqui = await db.select(
        "employees",
        {
            "select": "id",
            "salon_id": f"eq.{salon_id}",
            "persona_id": f"eq.{persona}",
            "limit": "1",
        },
    )
    if ya_aqui:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Esa persona ya tiene una ficha en este centro",
        )

    # La PRIMERA ficha se sella con su propio id: a partir de aquí las dos
    # apuntan a la misma persona. El trigger `repuntar_citas_de_la_persona`
    # arrastra sus citas ya existentes, así que la protección contra solapes
    # cubre también la agenda que ya estaba puesta.
    if not otra.get("persona_id"):
        try:
            await db.update(
                "employees", {"id": f"eq.{otra['id']}"}, {"persona_id": persona}, select="id"
            )
        except PostgrestError as e:
            if not es_violacion_de_exclusion(e):
                raise
            # SELLAR LA PRIMERA FICHA DISPARA EL REPUNTE de sus citas, y si las
            # dos fichas YA tienen citas que se pisan, la restricción rechaza el
            # sellado entero. Es correcto —la garantía no puede aflojarse para
            # dejar entrar un enlace— pero sin esto salía como un 500 con el
            # texto crudo de Postgres.
            #
            # Y es un caso REAL, no rebuscado: normalmente uno se acuerda de
            # enlazar a alguien JUSTO cuando se ha dado cuenta de que la han
            # citado en dos sitios a la vez.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Esa persona ya tiene citas que se pisan entre los dos centros. "
                    "Cambia una de ellas y vuelve a enlazarla."
                ),
            ) from e
    return persona


def _vinculable(fila: dict, tiene_pin: bool) -> dict:
    """Fila de `employees` (con embeds) → lo que la pantalla puede copiar.

    Aquí es donde la comisión se queda fuera, y por eso está en una función con
    nombre en vez de en un diccionario suelto dentro del bucle: la regla «el
    dinero no se hereda» tiene que ser visible al leer, no deducirse de que
    falta una clave.

    Es la SEGUNDA barrera, y la de fuera es `EmployeeVinculableOut`: como es el
    `response_model`, pydantic descarta cualquier campo que no declare. Medido
    con una mutación —meter `comision_servicios` en la consulta Y en este
    diccionario— y la comisión NO sale igualmente. Las dos, porque un día
    alguien añadirá un campo al esquema sin pensar en esto.
    """
    servicios = [
        (enlace.get("services") or {}).get("nombre")
        for enlace in (fila.get("employee_services") or [])
    ]
    return {
        "id": fila["id"],
        "nombre": fila["nombre"],
        "centro": (fila.get("salons") or {}).get("nombre_comercial") or "Otro centro",
        "telefono": fila.get("telefono"),
        "email": fila.get("email"),
        "puesto": (fila.get("puestos") or {}).get("nombre"),
        "servicios": sorted({x for x in servicios if x}),
        # El hash NUNCA sale de la API; solo si lo tiene o no.
        "tiene_pin": tiene_pin,
    }


@router.get("/vinculables", response_model=list[EmployeeVinculableOut])
async def listar_vinculables(
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
    service: PostgrestClient = Depends(get_service_postgrest_client),
) -> list[dict]:
    """Quién, de los otros centros, puede ser «la misma persona».

    VA ANTES QUE `/{employee_id}` en el fichero a propósito: FastAPI resuelve
    por orden y `vinculables` encajaría en el parámetro de ruta.

    Con un solo centro devuelve una lista vacía y la pantalla no enseña nada:
    la misma equivalencia que el selector.
    """
    try:
        filas = await db.select(
            "employees",
            {
                # Todo lo COPIABLE viene ya en esta consulta: si el formulario
                # tuviera que pedir la ficha entera después, sería una consulta
                # a `/employees/{id}` que devolvería 404 —ese endpoint filtra
                # por el centro activo y la ficha es del otro—.
                #
                # `comision_*` NO se pide. No es un olvido: lo que no viaja no
                # se puede copiar por descuido.
                "select": (
                    "id,nombre,salon_id,persona_id,telefono,email,"
                    "salons!employees_salon_id_fkey(nombre_comercial),"
                    "puestos(nombre),"
                    "employee_services(services(nombre))"
                ),
                "salon_id": f"neq.{user.salon_id}",
                "estado": "eq.activo",
                "order": "nombre.asc",
            },
        )
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo leer el equipo de tus otros centros",
        ) from e

    # Quien YA tenga ficha aquí no se ofrece: el enlace fallaría contra el
    # índice único, y ofrecer algo que no se puede pulsar es peor que no
    # ofrecerlo.
    aqui = await db.select(
        "employees",
        {"select": "persona_id", "salon_id": f"eq.{user.salon_id}", "persona_id": "not.is.null"},
    )
    ocupadas = {f["persona_id"] for f in aqui}

    ofrecibles = [f for f in filas if (f.get("persona_id") or f["id"]) not in ocupadas]
    con_pin = await _con_pin(service, [f["id"] for f in ofrecibles])
    return [_vinculable(f, f["id"] in con_pin) for f in ofrecibles]


@router.post(
    "",
    response_model=EmployeeOut,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_409_CONFLICT: {
            "description": "Límite de empleados del plan alcanzado (muro de pago)."
        }
    },
)
async def create_employee(
    payload: EmployeeCreate,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
    service: PostgrestClient = Depends(get_service_postgrest_client),
) -> dict:
    # Muro de pago real: el cupo del plan se comprueba ANTES de crear.
    await _ensure_below_employee_limit(db, user.salon_id)
    service_ids = await _ensure_services_in_salon(db, user.salon_id, payload.service_ids)
    nombre_puesto = await _puesto_del_salon(db, user.salon_id, payload.puesto_id)
    body = payload.model_dump(
        mode="json", exclude_none=True, exclude={"pin_fichaje", "service_ids", "vincular_con"}
    )
    body["salon_id"] = user.salon_id
    if payload.vincular_con:
        # OCUPA CUPO IGUAL. El límite es de cada centro (ver
        # `_ensure_below_employee_limit`): una persona que trabaja en los dos
        # ocupa un puesto en cada uno, porque en los dos hay que atenderla,
        # pagarla y ponerla en cuadro. Por eso esto va DESPUÉS del muro y no
        # antes.
        body["persona_id"] = await _resolver_persona(db, user.salon_id, payload.vincular_con)
    try:
        row = await db.insert("employees", body, select=_COLUMNAS)
    except PostgrestError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=visible("dar de alta a la empleada")
        )
    if payload.pin_fichaje:
        # EL PIN SE ESCRIBE DESPUÉS, CON service_role: la sesión ya no puede
        # escribir el hash (0047). Y cuando se enlaza con otra ficha tendría que
        # ir después de todos modos: el trigger `la_ficha_adopta_el_pin_de_la_
        # persona` impone en el INSERT el PIN que ya tenía la persona, y un PIN
        # nuevo pedido a propósito se aplica con un UPDATE que dispara
        # `el_pin_es_de_la_persona` y lo cambia en TODAS sus fichas. Que cambie en
        # las dos es la regla, y el formulario lo avisa en la misma frase.
        try:
            await _update_pin_hash(service, user.salon_id, row["id"], await hashear_pin(payload.pin_fichaje))
        except (PostgrestError, HTTPException) as e:
            # Antes era un solo INSERT: o entraba la ficha con su PIN, o nada. Se
            # mantiene: una ficha creada SIN el PIN que se pidió es una persona
            # que mañana no puede fichar y nadie sabe por qué.
            await db.delete("employees", {"id": f"eq.{row['id']}", "salon_id": f"eq.{user.salon_id}"})
            detalle = e.detail if isinstance(e, PostgrestError) else "No se pudo guardar el PIN"
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detalle) from e
    try:
        if service_ids:
            await db.insert_many(
                "employee_services",
                [{"employee_id": row["id"], "service_id": sid} for sid in service_ids],
            )
    except PostgrestError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=visible("dar de alta a la empleada")
        )
    # El INSERT no trae los embeds, así que se le pegan a mano: el alta
    # devuelve la misma forma que el resto de endpoints.
    return _to_out(
        {
            **row,
            "employee_services": [{"service_id": s} for s in service_ids],
            "puestos": {"nombre": nombre_puesto} if nombre_puesto else None,
        },
        # Se pregunta y no se deduce de `payload.pin_fichaje`: al enlazarla con
        # otra ficha puede haber heredado el PIN de la persona sin pedir ninguno.
        row["id"] in await _con_pin(service, [row["id"]]),
    )


@router.get("/{employee_id}", response_model=EmployeeOut)
async def get_employee(
    employee_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
    service: PostgrestClient = Depends(get_service_postgrest_client),
) -> dict:
    fila = await _fetch_one(db, user.salon_id, employee_id)
    return _to_out(fila, fila["id"] in await _con_pin(service, [fila["id"]]))


@router.patch(
    "/{employee_id}",
    response_model=EmployeeOut,
    responses={
        status.HTTP_409_CONFLICT: {
            "description": "Reactivación bloqueada: límite de empleados del plan alcanzado."
        }
    },
)
async def update_employee(
    employee_id: str,
    payload: EmployeeUpdate,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
    service: PostgrestClient = Depends(get_service_postgrest_client),
) -> dict:
    body = payload.model_dump(
        mode="json", exclude_none=True, exclude={"pin_fichaje", "service_ids", "citas_vistas"}
    )
    # `exclude_none` no distingue «no lo mando» de «quiero dejarlo vacío», y en
    # el puesto esa diferencia importa: sin esto, quitarle el puesto a alguien
    # sería imposible —el null se caería por el camino y el valor viejo se
    # quedaría—. `model_fields_set` sí sabe si el campo venía en el JSON.
    if "puesto_id" in payload.model_fields_set:
        body["puesto_id"] = payload.puesto_id
    if not body and payload.service_ids is None and not payload.pin_fichaje:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nada que actualizar")

    # LA BAJA ES DE UNA SOLA DIRECCIÓN (0062). La base ya no deja sacarla de la
    # baja cambiándole el estado; esto lo dice con palabras antes de llegar ahí.
    # Si la ficha no existe, sigue el camino de siempre (su 404 sale más abajo).
    estado_actual = await db.select(
        "employees",
        {"select": "estado", "id": f"eq.{employee_id}", "salon_id": f"eq.{user.salon_id}", "limit": "1"},
    )
    if estado_actual and estado_actual[0].get("estado") == "baja":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Está dada de baja: su ficha se conserva tal cual. Si vuelve, hazle una ficha nueva.",
        )

    # Reactivación (pausa → activo): revalida el cupo del plan igual que el alta,
    # para que no se pueda superar el límite reactivando en vez de creando. Solo
    # cuenta como reactivación si el empleado NO estaba ya activo (evita bloquear
    # ediciones no-op). La bajada de plan con exceso de activos no se toca.
    if payload.estado == "activo":
        actual = await _fetch_one(db, user.salon_id, employee_id)
        if actual["estado"] != "activo":
            await _ensure_below_employee_limit(db, user.salon_id)

    # PONERLA EN PAUSA DEJA SUS CITAS SIN NADIE. Es la misma pregunta que al
    # cambiar un horario —qué citas ya dadas quedan fuera— y pasa por el mismo
    # cálculo y el mismo protocolo (core/impacto.py): 409 con la lista hasta que
    # se hayan visto. Solo al pasar de activa a pausa; reactivar solo ensancha.
    if payload.estado == "pausa":
        actual = await _fetch_one(db, user.salon_id, employee_id)
        if actual["estado"] == "activo":
            antes = await cargar_marco(db, user.salon_id, [employee_id])
            citas = await citas_por_venir(db, user.salon_id, employee_id=employee_id)
            try:
                await exigir_que_se_hayan_visto(
                    db, que_quedan_fuera(citas, antes, antes.con_pausa(employee_id)), payload.citas_vistas
                )
            except CitasAfectadas as e:
                raise a_http(e) from e

    await _puesto_del_salon(db, user.salon_id, payload.puesto_id)

    service_ids = None
    if payload.service_ids is not None:
        service_ids = await _ensure_services_in_salon(db, user.salon_id, payload.service_ids)

    if body:
        try:
            rows = await db.update(
                "employees",
                {"id": f"eq.{employee_id}", "salon_id": f"eq.{user.salon_id}"},
                body,
                select="id",
            )
        except PostgrestError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=visible("guardar los cambios de la empleada"),
            )
        if not rows:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Empleado no encontrado"
            )
    else:
        # Solo cambian las asignaciones o el PIN: confirma, CON LA SESIÓN, que el
        # empleado existe y es visible antes de escribir nada con service_role.
        await _fetch_one(db, user.salon_id, employee_id)

    if payload.pin_fichaje:
        try:
            await _update_pin_hash(service, user.salon_id, employee_id, await hashear_pin(payload.pin_fichaje))
        except PostgrestError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=visible("guardar los cambios de la empleada"),
            )

    if service_ids is not None:
        try:
            await _replace_service_links(db, employee_id, service_ids)
        except PostgrestError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=visible("guardar los cambios de la empleada"),
            )

    fila = await _fetch_one(db, user.salon_id, employee_id)
    return _to_out(fila, fila["id"] in await _con_pin(service, [fila["id"]]))


_NO_A_LA_ADMINISTRADORA = (
    "Es la administradora de la cuenta: darle de baja le quitaría la entrada a la "
    "app. Si ya no trabaja aquí, ponla en pausa."
)


async def _ficha_con_usuario(service: PostgrestClient, salon_id: str, employee_id: str) -> dict:
    """La ficha con su `user_id`. Se lee con service_role, acotada al centro
    activo, que ya ha resuelto la frontera."""
    filas = await service.select(
        "employees",
        {"select": "id,estado,user_id", "id": f"eq.{employee_id}", "salon_id": f"eq.{salon_id}", "limit": "1"},
    )
    if not filas:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Empleado no encontrado")
    return filas[0]


@router.get("/{employee_id}/baja", response_model=BajaPreviaOut)
async def ver_baja(
    employee_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
    service: PostgrestClient = Depends(get_service_postgrest_client),
) -> BajaPreviaOut:
    """Lo que pasará al pulsar «Dar de baja», contado, para que el diálogo diga
    la verdad: qué se conserva, si se borra del todo y si pierde el acceso."""
    ficha = await _ficha_con_usuario(service, user.salon_id, employee_id)
    ahora = _utc_now()
    historia = await bajas.contar_historia(db, service, user.salon_id, employee_id, ahora)
    acceso = await bajas.acceso_de_la_ficha(service, ficha)
    por_venir = await citas_por_venir(db, user.salon_id, employee_id=employee_id, ahora=ahora)
    return BajaPreviaOut(
        borrar_del_todo=not historia.tiene_algo,
        fichajes=historia.fichajes,
        citas_pasadas=historia.citas_pasadas,
        ausencias=historia.ausencias,
        correcciones=historia.correcciones,
        citas_por_venir=len(por_venir),
        acceso=acceso.que,
    )


@router.delete("/{employee_id}", response_model=BajaOut)
async def delete_employee(
    employee_id: str,
    payload: ConCitasVistas | None = Body(default=None),
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
    service: PostgrestClient = Depends(get_service_postgrest_client),
) -> BajaOut:
    """«Dar de baja». Un solo botón, y decide el servidor (core/baja.py).

    El camino sigue siendo `DELETE` para que un frontend anterior que pulse
    «Eliminar» tampoco destruya nada: le llega esto mismo.
    """
    ficha = await _ficha_con_usuario(service, user.salon_id, employee_id)
    if ficha["estado"] == "baja":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ya está dada de baja.")
    acceso = await bajas.acceso_de_la_ficha(service, ficha)
    if acceso.que == "administradora":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_NO_A_LA_ADMINISTRADORA)

    # LA CUARTA PUERTA, la de siempre: sus citas por venir se quedan sin nadie,
    # y eso se enseña antes (core/impacto.py).
    ahora = _utc_now()
    citas = await citas_por_venir(db, user.salon_id, employee_id=employee_id, ahora=ahora)
    try:
        await exigir_que_se_hayan_visto(
            db, citas, (payload or ConCitasVistas()).citas_vistas, motivo=SIN_PROFESIONAL
        )
    except CitasAfectadas as e:
        raise a_http(e) from e

    historia = await bajas.contar_historia(db, service, user.salon_id, employee_id, ahora)

    try:
        # Las que aún no han empezado se quedan sin profesional, en los dos
        # caminos: es lo que hacía el SET NULL de antes, ahora a propósito y solo
        # con las de después de ahora. Las pasadas se quedan con ella.
        await db.update(
            "appointments",
            {"employee_id": f"eq.{employee_id}", "salon_id": f"eq.{user.salon_id}", "inicio": f"gte.{ahora.isoformat()}"},
            {"employee_id": None},
            select="id",
        )
        resultado = "baja"
        if not historia.tiene_algo:
            try:
                await db.delete("employees", {"id": f"eq.{employee_id}", "salon_id": f"eq.{user.salon_id}"})
                resultado = "borrada"
            except PostgrestError:
                # La base se ha negado: algo apareció entre contar y borrar (un
                # fichaje en el quiosco, por ejemplo). Se da de baja, que es lo
                # que se habría hecho de haberlo visto.
                pass
        if resultado == "baja":
            # Con service_role: la sesión no puede escribir `baja_el` (0062). La
            # ficha ya está comprobada arriba, acotada al centro activo.
            await service.update(
                "employees",
                {"id": f"eq.{employee_id}", "salon_id": f"eq.{user.salon_id}"},
                {"estado": "baja", "baja_el": ahora.isoformat()},
                select="id",
            )
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=visible("dar de baja a la empleada")
        ) from e

    return BajaOut(resultado=resultado, acceso=acceso.que)
