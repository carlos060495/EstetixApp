from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import ValidationError

from app.core import publico
from app.core.clientas import corte_de_dormida, telefono_tapado
from app.core.errores_visibles import visible
from app.core import zona as zonas
from app.core.security import CurrentUser, get_current_user, get_postgrest_client
from app.db.postgrest_client import PostgrestClient, PostgrestError
from app.schemas.clients import (
    ClientaParaCitaOut,
    ClientCreate,
    ClientOut,
    ClientsBulkIn,
    ClientsBulkOut,
    ClientUpdate,
)

router = APIRouter(prefix="/clients", tags=["clients"], dependencies=[publico.ADMINISTRADORA])

# LO QUE SE LEE SALE DE LA VISTA (0058), no de `clients`: visitas, total
# gastado y última visita se calculan de las citas completadas al leer. Las
# columnas `clients.visitas` y `clients.total_gastado` no las escribía nadie.
_VISTA = "clientas_con_actividad"
_SELECT = "id,nombre,telefono,email,cumpleanos,alergias,notas,vip,total_gastado,visitas,ultima_visita"


async def _de_la_vista(db: PostgrestClient, salon_id: str, client_id: str) -> dict | None:
    rows = await db.select(
        _VISTA, {"select": _SELECT, "id": f"eq.{client_id}", "salon_id": f"eq.{salon_id}", "limit": "1"}
    )
    return rows[0] if rows else None


# Payload cap for /bulk, on top of the MAX_BULK_ROWS row cap: rejects abusive
# bodies before parsing them (a real 1000-row contact file weighs ~100 KB).
_MAX_BULK_BYTES = 2 * 1024 * 1024

# Phones per in.() filter when checking duplicates, to keep URLs short.
_DUP_CHUNK = 100


def _busqueda(q: str) -> str | None:
    """Búsqueda por nombre, teléfono o email sobre TODAS las clientas: la
    pantalla buscaba solo en las 50 de la página, y una clienta que existe y no
    sale hace pensar que se han perdido los datos. Se eliminan los chars con
    significado en la sintaxis or=() / patrones de PostgREST."""
    safe = "".join(ch for ch in q if ch not in '(),*"\\')
    if not safe.strip():
        return None
    return f"or(nombre.ilike.*{safe}*,telefono.ilike.*{safe}*,email.ilike.*{safe}*)"


@router.get("", response_model=list[ClientOut])
async def list_clients(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    q: str | None = Query(default=None, max_length=100),
    filtro: Literal["dormidas", "vip"] | None = Query(
        default=None,
        description=(
            "«dormidas»: sin ninguna cita atendida en los últimos meses (ver core/clientas.py). "
            "«vip»: las marcadas como VIP."
        ),
    ),
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> list[dict]:
    # salon_id filter is redundant with RLS by construction (defense in
    # depth, not a substitute for it): user.salon_id comes from the verified
    # JWT, never from the request.
    params = {
        "select": _SELECT,
        "salon_id": f"eq.{user.salon_id}",
        "limit": str(limit),
        "offset": str(offset),
        "order": "nombre.asc",
    }
    condiciones: list[str] = []
    if q and (busca := _busqueda(q)):
        condiciones.append(busca)
    if filtro == "vip":
        params["vip"] = "eq.true"
    if filtro == "dormidas":
        # En el SERVIDOR y sobre TODAS las clientas, no sobre la página que la
        # pantalla tiene cargada. El corte, a medianoche de Madrid.
        z = await zonas.zona_del_centro(db, user.salon_id)
        corte = zonas.a_utc(zonas.inicio_del_dia(corte_de_dormida(zonas.hoy(z)), z))
        condiciones.append(f"or(ultima_visita.is.null,ultima_visita.lt.{corte.isoformat()})")
    if len(condiciones) == 1:
        params["or"] = condiciones[0][2:]  # «or(a,b)» → «(a,b)»: PostgREST exige los paréntesis
    elif condiciones:
        params["and"] = f"({','.join(condiciones)})"
    return await db.select(_VISTA, params)


@router.get("/para-cita", response_model=list[ClientaParaCitaOut])
async def buscar_para_cita(
    q: str = Query(min_length=2, max_length=100),
    limit: int = Query(default=8, ge=1, le=20),
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> list[dict]:
    """El buscador de «Nueva cita», en la Agenda: el nombre y el final del
    teléfono, nada más. Antes pedía `/clients` y le llegaba la ficha entera de
    cada clienta —alergias, notas, cumpleaños, correo, lo gastado— a una
    pantalla que está fuera de la cortina del PIN y solo enseña el nombre.

    Se busca igual que en Clientas (también por el teléfono entero, si la
    clienta lo dicta), pero de la base solo se piden tres columnas y el
    teléfono se tapa antes de salir."""
    busca = _busqueda(q)
    if not busca:
        return []
    filas = await db.select(
        "clients",
        {
            "select": "id,nombre,telefono",
            "salon_id": f"eq.{user.salon_id}",
            "or": busca[2:],
            "order": "nombre.asc",
            "limit": str(limit),
        },
    )
    return [
        {"id": f["id"], "nombre": f["nombre"], "telefono_final": telefono_tapado(f.get("telefono"))}
        for f in filas
    ]


@router.post("", response_model=ClientOut, status_code=status.HTTP_201_CREATED)
async def create_client(
    payload: ClientCreate,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> dict:
    body = payload.model_dump(mode="json", exclude_none=True)
    body["salon_id"] = user.salon_id
    try:
        creada = await db.insert("clients", body)
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=visible("guardar la clienta")
        )
    # Se devuelve leída de la vista: lo que se enseña de una clienta sale
    # siempre del mismo sitio.
    return await _de_la_vista(db, user.salon_id, creada["id"]) or creada


def _check_bulk_payload_size(content_length: int | None = Header(default=None)) -> None:
    if content_length is not None and content_length > _MAX_BULK_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="La importación es demasiado grande (máx. 2 MB). Divide el archivo en partes.",
        )


def _motivo_de_error(exc: ValidationError) -> str:
    """Turns the first pydantic error of a row into a human motivo."""
    err = exc.errors()[0]
    campo = str(err["loc"][0]) if err["loc"] else "fila"
    tipo = err["type"]
    if campo == "nombre":
        return "falta nombre" if tipo in ("missing", "string_too_short") else "nombre inválido"
    if campo == "telefono":
        return "falta teléfono" if tipo == "missing" else "teléfono inválido"
    if campo == "email":
        return "email inválido"
    if campo == "cumpleanos":
        return "cumpleaños inválido (formato AAAA-MM-DD)"
    return f"{campo} inválido"


@router.post(
    "/bulk",
    response_model=ClientsBulkOut,
    dependencies=[Depends(_check_bulk_payload_size)],
)
async def bulk_create_clients(
    payload: ClientsBulkIn,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> dict:
    """Importación masiva desde el asistente CSV/Excel del frontend.

    Valida cada fila con ClientCreate (allow-list: salon_id, total_gastado y
    visitas nunca vienen del archivo), omite teléfonos duplicados dentro del
    salón —re-importar el mismo archivo no crea duplicados— e inserta las
    filas válidas en un único lote. Mismos permisos que crear un cliente.
    """
    # 1) Per-row validation. `fila` is 1-based over the rows sent (the
    #    frontend re-maps it to the file's row number, header included).
    validados: list[tuple[int, ClientCreate | None, str | None]] = []
    for fila, raw in enumerate(payload.clients, start=1):
        try:
            client = ClientCreate.model_validate(raw)
        except ValidationError as exc:
            validados.append((fila, None, _motivo_de_error(exc)))
            continue
        if not client.nombre.strip():
            validados.append((fila, None, "falta nombre"))
        elif not (client.telefono or "").strip():
            # ClientCreate admits a null phone (alta unitaria), but the bulk
            # import requires it: it is the dedup key.
            validados.append((fila, None, "falta teléfono"))
        else:
            validados.append((fila, client, None))

    # 2) Phones already present in this salon. RLS re-checks salon_id, the
    #    eq filter is defense in depth like in list_clients.
    telefonos = sorted(
        {(c.telefono or "").strip() for _, c, _ in validados if c is not None}
    )
    existentes: set[str] = set()
    for i in range(0, len(telefonos), _DUP_CHUNK):
        chunk = telefonos[i : i + _DUP_CHUNK]
        # PostgREST in.() with quoted values; escape \ and " inside them.
        quoted = ",".join(
            '"' + t.replace("\\", "\\\\").replace('"', '\\"') + '"' for t in chunk
        )
        rows = await db.select(
            "clients",
            {
                "select": "telefono",
                "salon_id": f"eq.{user.salon_id}",
                "telefono": f"in.({quoted})",
            },
        )
        existentes.update((r["telefono"] or "").strip() for r in rows)

    # 3) Assemble the batch, skipping duplicates (against the DB and within
    #    the file itself).
    skipped: list[dict] = []
    to_insert: list[dict] = []
    vistos: set[str] = set()
    for fila, client, motivo in validados:
        if client is None:
            skipped.append({"fila": fila, "motivo": motivo})
            continue
        telefono = (client.telefono or "").strip()
        if telefono in existentes:
            skipped.append({"fila": fila, "motivo": "teléfono duplicado"})
            continue
        if telefono in vistos:
            skipped.append({"fila": fila, "motivo": "teléfono duplicado en el archivo"})
            continue
        vistos.add(telefono)
        body = client.model_dump(mode="json", exclude_none=True)
        body["telefono"] = telefono
        body["salon_id"] = user.salon_id
        to_insert.append(body)

    # 4) One batched insert: a single transaction, all rows or none.
    if to_insert:
        try:
            await db.insert_many("clients", to_insert)
        except PostgrestError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=visible("importar las clientas")
            )

    return {"imported": len(to_insert), "skipped": skipped, "total": len(payload.clients)}


@router.get("/{client_id}", response_model=ClientOut)
async def get_client(
    client_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> dict:
    fila = await _de_la_vista(db, user.salon_id, client_id)
    if fila is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cliente no encontrado")
    return fila


@router.patch("/{client_id}", response_model=ClientOut)
async def update_client(
    client_id: str,
    payload: ClientUpdate,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> dict:
    body = payload.model_dump(mode="json", exclude_none=True)
    if not body:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nada que actualizar")
    rows = await db.update(
        "clients",
        {"id": f"eq.{client_id}", "salon_id": f"eq.{user.salon_id}"},
        body,
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cliente no encontrado")
    # La fila de `clients` no trae lo calculado: se relee de la vista, o la
    # ficha enseñaría 0 visitas después de editar un teléfono.
    return await _de_la_vista(db, user.salon_id, client_id) or rows[0]


@router.delete("/{client_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_client(
    client_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> None:
    await db.delete("clients", {"id": f"eq.{client_id}", "salon_id": f"eq.{user.salon_id}"})
