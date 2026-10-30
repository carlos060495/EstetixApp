"""LAS AGENCIAS (0077): un referido que vende con varios comerciales.

06-10-2026. LA AGENCIA ES LA ÚNICA CONTRAPARTE: su porcentaje, su
cuenta de Stripe, su mínimo y sus pagos. Lo que reparta con sus comerciales es
cosa suya: aquí no se calcula ni se enseña.

UN TRABAJADOR NO ES UN REFERIDO. Tiene nombre, código de alta y, si la agencia
quiere, un enlace de seguimiento. Nada más: ni porcentaje, ni cuenta de Stripe,
ni contraseña, ni sesión. Un salón que llega con su código es de SU AGENCIA
(`atribuir_alta`); la fila guarda además qué trabajador lo trajo.

Todo lo que decide vive en la base (`alta_de_trabajador`, `baja_de_trabajador`,
`poner_seguimiento`): cada una comprueba que el trabajador sea de esa agencia.
DAR DE BAJA REVOCA SU ENLACE en el mismo acto (06-10-2026): quien se va
no se lleva la cartera. Y a uno de baja no se le pone uno nuevo.
Aquí se genera el código (el mismo formato que el de un referido) y el token
del enlace, del que la base guarda SOLO el hash.
"""

from __future__ import annotations

import hashlib
import secrets

from app.core.codigo_referido import generar_codigo_referido
from app.db.postgrest_client import PostgrestClient

# Como `INTENTOS_CODIGO` de los referidos: con 32^6 no debería chocar nunca.
INTENTOS_CODIGO = 8

# Los estados de un salón en la página de seguimiento: estos tres, y ninguno más.
PAGA, EN_PRUEBA, CAIDO = "paga", "en_prueba", "caido"


class NoEsUnaAgencia(Exception):
    """El referido no es una agencia aprobada: no tiene trabajadores."""


class NoEsSuyo(Exception):
    """Ese trabajador no es de esta agencia."""


class DeBaja(Exception):
    """Ese trabajador está de baja: no se le pone un enlace nuevo."""


def token_de_seguimiento() -> str:
    """Largo y aleatorio (32 bytes): no sale del código de alta ni de ningún id."""
    return secrets.token_urlsafe(32)


def hash_de_seguimiento(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def es_agencia(db: PostgrestClient, referido_id: str) -> bool:
    filas = await db.select("referidos", {
        "select": "tipo", "id": f"eq.{referido_id}", "estado": "eq.aprobado", "limit": "1",
    })
    return bool(filas) and filas[0].get("tipo") == "agencia"


async def trabajadores(db: PostgrestClient, agencia_id: str) -> dict:
    """El desglose por trabajador: cuántos salones trajo, cuántos pagan hoy y
    lo devengado por ellos, por moneda. De las MISMAS filas de comisiones que el
    total; lo que trajo la agencia con su propio código va en `directos`, y
    todo junto suma el total. Es un desglose, no otra contabilidad."""
    filas = await db.rpc("trabajadores_de_una_agencia", {"p_agencia": agencia_id})
    devengado = await db.rpc("devengado_por_trabajador", {"p_agencia": agencia_id})
    por_trabajador: dict[str | None, list[dict]] = {}
    for d in devengado:
        if not d:
            continue
        clave = str(d["trabajador_id"]) if d.get("trabajador_id") else None
        por_trabajador.setdefault(clave, []).append({"moneda": d["moneda"], "neto": int(d["neto"])})
    return {
        "trabajadores": [
            {**f, "id": str(f["id"]), "devengado": por_trabajador.get(str(f["id"]), [])}
            for f in filas if f
        ],
        "directos": por_trabajador.get(None, []),
    }


async def dar_de_alta(db: PostgrestClient, agencia_id: str, nombre: str) -> str:
    """Un trabajador nuevo con su propio código. Devuelve su id."""
    for _ in range(INTENTOS_CODIGO):
        filas = await db.rpc("alta_de_trabajador", {
            "p_agencia": agencia_id, "p_nombre": nombre, "p_codigo": generar_codigo_referido(),
        })
        r = filas[0] if filas else {}
        if r.get("resultado") == "creado":
            return str(r["trabajador_id"])
        if r.get("resultado") == "no_es_una_agencia":
            raise NoEsUnaAgencia()
    raise RuntimeError("No se pudo generar un código libre.")


async def dar_de_baja(db: PostgrestClient, agencia_id: str, trabajador_id: str) -> str:
    r = await db.rpc("baja_de_trabajador", {"p_agencia": agencia_id, "p_trabajador": trabajador_id})
    dicho = str(r[0]) if r else "?"
    if dicho == "no_es_suyo":
        raise NoEsSuyo()
    return dicho


async def nuevo_seguimiento(db: PostgrestClient, agencia_id: str, trabajador_id: str) -> str:
    """Un enlace nuevo: el anterior, si lo había, deja de abrir. Devuelve el
    token EN CLARO, que solo se ve esta vez: la base guarda su hash."""
    token = token_de_seguimiento()
    r = await db.rpc("poner_seguimiento", {
        "p_agencia": agencia_id, "p_trabajador": trabajador_id, "p_hash": hash_de_seguimiento(token),
    })
    dicho = str(r[0]) if r else "?"
    if dicho == "de_baja":
        raise DeBaja()
    if dicho != "hecho":
        raise NoEsSuyo()
    return token


async def revocar_seguimiento(db: PostgrestClient, agencia_id: str, trabajador_id: str) -> None:
    r = await db.rpc("poner_seguimiento", {"p_agencia": agencia_id, "p_trabajador": trabajador_id, "p_hash": None})
    if not r or str(r[0]) != "hecho":
        raise NoEsSuyo()


async def seguimiento(db: PostgrestClient, token: str) -> dict | None:
    """Lo que enseña la página del enlace. None si el enlace no abre (no
    existe, se revocó o se cambió por otro, o la agencia ya no está aprobada)."""
    filas = [f for f in await db.rpc("seguimiento_de_un_trabajador", {"p_hash": hash_de_seguimiento(token)}) if f]
    if not filas:
        return None
    return {
        "trabajador": filas[0]["trabajador"],
        "agencia": filas[0]["agencia"],
        "salones": [
            {"salon": f["salon"], "plan": f.get("plan"), "plan_al_llegar": f.get("plan_al_llegar"),
             "desde": f["desde"], "estado": f["estado"]}
            for f in filas if f.get("salon")
        ],
    }
