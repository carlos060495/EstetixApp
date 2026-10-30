"""La cuenta: lo que se paga, leído donde ahora vive.

EL TOKEN SIGUE TRAYENDO `salon_id` Y NO `account_id`, a propósito. Un claim
nuevo solo aparece en los tokens emitidos después de ponerlo, así que el día
del despliegue habría que echar a todo el mundo de su sesión para que la
aplicación volviera a funcionar. La cuenta se alcanza desde el salón, que es un
salto de una clave foránea y cabe DENTRO de la consulta que ya se hacía:

    salons?select=account_id,accounts(plan)&id=eq.<salon>

PostgREST devuelve el lado «muchos a uno» como objeto, no como lista —medido
contra el PostgREST local, no supuesto—, así que `fila["accounts"]` es un
diccionario o falta. Aquí se aplana para que quien llama no tenga que saberlo.
"""

from __future__ import annotations

from app.db.postgrest_client import PostgrestClient

# Las nueve columnas que subieron del centro a la cuenta en la 0040.
CAMPOS_DE_LA_CUENTA = (
    "plan",
    "pending_plan",
    "stripe_customer_id",
    "suspendido",
    "suspendido_at",
    "suspendido_por",
    "cif",
    "cif_pais",
    "logo_url",
    "accent_color",
)


def campos(*nombres: str) -> str:
    """El `select` de PostgREST para leer la cuenta desde su salón."""
    for nombre in nombres:
        if nombre not in CAMPOS_DE_LA_CUENTA:
            # Un nombre que no está en la lista es casi siempre una columna que
            # se quedó en `salons`, y leerla por aquí devolvería null en
            # silencio. Mejor romper en el primer test que la use.
            raise ValueError(f"{nombre!r} no es una columna de la cuenta")
    return f"account_id,accounts({','.join(nombres)})"


def _embebida(fila: dict) -> dict:
    """El objeto de la cuenta que PostgREST cuelga de la fila del salón."""
    cuenta = fila.get("accounts") or {}
    if isinstance(cuenta, list):
        # PostgREST devuelve objeto para «muchos a uno», pero el doble de los
        # tests devuelve lo que le pongan. Que una fixture escrita como lista no
        # se traduzca en un `plan` nulo silencioso.
        cuenta = cuenta[0] if cuenta else {}
    return cuenta


def la_cuenta_de(fila: dict) -> dict:
    """El objeto de la cuenta colgado de una fila de `salons`, o `{}`.

    Para quien llega al salón desde OTRA tabla (el directorio del bot:
    `salon_whatsapp_config → salons → accounts`) y solo necesita la cuenta.
    """
    return _embebida(fila or {})


def con_la_cuenta_dentro(fila: dict) -> dict:
    """La fila del salón con los campos de su cuenta mezclados, sin el embed.

    Para las pantallas que ya existían y siguen enseñando «un salón» con su
    plan y su marca: la respuesta conserva exactamente la forma de antes.
    """
    plano = {k: v for k, v in fila.items() if k != "accounts"}
    plano.update(_embebida(fila))
    return plano


def aplanar(filas: list[dict]) -> dict:
    """La fila de `salons` con embed, convertida en los datos de la cuenta.

    Devuelve `{}` si no hay salón, y `account_id` siempre que lo haya: quien
    llama distingue «no existe ese salón» de «existe y su cuenta dice esto».
    """
    if not filas:
        return {}
    fila = filas[0]
    return {"account_id": fila.get("account_id"), **_embebida(fila)}


async def leer_cuenta(db: PostgrestClient, salon_id: str, *nombres: str) -> dict:
    """Los campos pedidos de la cuenta a la que pertenece este salón."""
    filas = await db.select(
        "salons", {"select": campos(*nombres), "id": f"eq.{salon_id}", "limit": "1"}
    )
    return aplanar(filas)


async def id_de_cuenta(db: PostgrestClient, salon_id: str) -> str | None:
    """La cuenta de un salón, para cuando hay que ESCRIBIR en ella."""
    filas = await db.select(
        "salons", {"select": "account_id", "id": f"eq.{salon_id}", "limit": "1"}
    )
    return filas[0].get("account_id") if filas else None
