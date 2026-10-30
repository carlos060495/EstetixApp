"""La atribución de un alta a quien la trajo (0070).

Todo lo que decide vive en la base, en `atribuir_alta`: compara el código sin
mayúsculas y sin espacios alrededor, y escribe en una sola transacción la
atribución (si el referido está aprobado) o la constancia de que el código no
valió. Desde la 0077 el código puede ser el de un trabajador de una agencia: el
salón se atribuye A SU AGENCIA. Aquí solo se llama.

LA ATRIBUCIÓN QUE NO SE HIZO EN EL ALTA (0078), dos caminos con dos reglas:

  · EL SALÓN la pone él mismo en sus 30 primeros días (`asignar_su_vendedor`).
    Devenga sobre los cobros que entren A PARTIR de la asignación.
  · EL OWNER la repesca desde un código que no valió en el alta, en sus 90
    primeros días (`repescar`). Devenga DESDE EL ALTA: los cobros ya hechos los
    devenga `comisiones.devengar_lo_cobrado` con la función de siempre.

Plazos, «ya tiene una», el vendedor que se enseñó y desde cuándo devenga: lo
decide la base. Aquí solo se llama y se traduce lo que contesta.
"""

from __future__ import annotations

from app.db.postgrest_client import PostgrestClient

# Lo que puede contestar la base. `ya_atribuida`: esa cuenta ya tenía una, y
# gana la primera. `trabajador_de_baja` (0077): el código de un trabajador de
# agencia que ya no está; el alta sigue igual, sin atribución.
RESULTADOS = frozenset({"atribuida", "ya_atribuida", "no_existe", "pendiente", "rechazado", "sin_codigo",
                        "trabajador_de_baja"})

# LA FRASE que ven el salón y el vendedor cuando la atribución la puso el salón
# después del alta (07-10-2026). La misma en las dos pantallas.
FRASE_DEL_SALON = "Se le empieza a pagar desde el siguiente recibo; el que ya se cobró no cuenta."


async def atribuir_alta(servicio: PostgrestClient, cuenta_id: str, codigo: str) -> str:
    """Con la clave de servicio: la función solo la puede ejecutar ella."""
    # Una función escalar: PostgREST devuelve el texto suelto y `rpc` lo envuelve.
    respuesta = await servicio.rpc("atribuir_alta", {"p_cuenta": cuenta_id, "p_codigo": codigo})
    return str(respuesta[0])


def _primera(filas) -> dict:
    return (filas[0] if filas else None) or {}


async def vendedor_de_un_codigo(servicio: PostgrestClient, codigo: str) -> dict | None:
    """De quién es un código que VALE (las condiciones del alta), para enseñar
    su nombre antes de confirmar. `referido_id` es quien cobra: con el código de
    un trabajador, su agencia. Nada si no vale."""
    fila = _primera(await servicio.rpc("vendedor_de_un_codigo", {"p_codigo": codigo}))
    if not fila.get("referido_id"):
        return None
    return {
        "referido_id": str(fila["referido_id"]),
        "nombre": fila["referido_nombre"],
        "tipo": fila.get("tipo") or "individual",
        "trabajador_id": str(fila["trabajador_id"]) if fila.get("trabajador_id") else None,
        "trabajador_nombre": fila.get("trabajador_nombre"),
    }


async def puede_asignar(servicio: PostgrestClient, cuenta_id: str) -> dict:
    """puede | ya_atribuida | fuera_de_plazo | sin_cuenta, y hasta cuándo."""
    fila = _primera(await servicio.rpc("puede_asignar_su_vendedor", {"p_cuenta": cuenta_id}))
    return {"resultado": fila.get("resultado") or "sin_cuenta", "hasta": fila.get("hasta")}


async def asignar_su_vendedor(servicio: PostgrestClient, cuenta_id: str, codigo: str, referido_id: str,
                              trabajador_id: str | None, usuario_id: str, usuario_email: str) -> dict:
    """asignada | ya_atribuida | fuera_de_plazo | no_vale | ha_cambiado."""
    return _primera(await servicio.rpc("asignar_su_vendedor", {
        "p_cuenta": cuenta_id, "p_codigo": codigo, "p_referido": referido_id, "p_trabajador": trabajador_id,
        "p_usuario": usuario_id, "p_usuario_email": usuario_email,
    }))


async def salones_para_repescar(servicio: PostgrestClient) -> list[dict]:
    return [f for f in await servicio.rpc("salones_para_repescar", {}) if f]


async def repescar(servicio: PostgrestClient, codigo_que_no_valio: int, codigo: str, referido_id: str,
                   trabajador_id: str | None, motivo: str, admin_id: str, admin_email: str) -> dict:
    """repescada | sin_rastro | sin_cuenta | ya_atribuida | fuera_de_plazo | no_vale | ha_cambiado."""
    return _primera(await servicio.rpc("repescar_atribucion", {
        "p_codigo_que_no_valio": codigo_que_no_valio, "p_codigo": codigo, "p_referido": referido_id,
        "p_trabajador": trabajador_id, "p_motivo": motivo, "p_admin": admin_id, "p_admin_email": admin_email,
    }))


async def atribucion_de(servicio: PostgrestClient, cuenta_id: str) -> dict | None:
    """De dónde vino la atribución de una cuenta viva, y desde cuándo devenga."""
    fila = _primera(await servicio.rpc("atribucion_de_la_cuenta", {"p_cuenta": cuenta_id}))
    return fila or None
