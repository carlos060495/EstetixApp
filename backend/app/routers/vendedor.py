"""EL SALÓN ASIGNA EL CÓDIGO DE SU VENDEDOR DESPUÉS DEL ALTA (0078).

Ajustes › Suscripción, al final. Un vendedor visitó el salón y le dejó su
código; el salón se dio de alta sin escribirlo. Aquí puede ponerlo él:

  · Solo si su cuenta no tiene ya una atribución, y solo en los 30 días
    siguientes al alta. Pasados, el campo no sale (`GET`) y la base lo rechaza.
  · Primero se COMPRUEBA el código y se le enseña el NOMBRE del vendedor; la
    confirmación lleva el vendedor que vio, y si el código ya no casa con él,
    no se asigna. Un código mal tecleado que coincida con otro real le
    regalaría el cliente a un desconocido.
  · Una sola vez. Devenga sobre los cobros que entren a partir de ahora
    (`atribucion.FRASE_DEL_SALON`).

Todo lo decide la base (`core/atribucion.py`). EN MODO SOPORTE NO: las dos
rutas POST están en `impersonation_policy.BLOQUEADOS` —asignarle un vendedor a
un salón desde una sesión suplantada es regalarle a alguien una comisión—.

Comprobar enseña un nombre, así que va frenado POR SALÓN: 10 comprobaciones por
hora. Un código son 32^6 combinaciones; a ese ritmo no se recorre la lista de
vendedores.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.core import atribucion, publico
from app.core.admin_security import VentanaPorIP
from app.core.cuenta import id_de_cuenta
from app.core.security import CurrentUser, get_current_user, get_postgrest_client, get_service_postgrest_client, sin_demo
from app.db.postgrest_client import PostgrestClient, PostgrestError
from app.schemas.vendedor import AsignarVendedorIn, AsignadoOut, ComprobarVendedorIn, EstadoVendedorOut, VendedorOut

# UNA DEMO NO CUENTA COMO SALÓN TRAÍDO POR NADIE (0080): ni se le enseña el campo.
router = APIRouter(prefix="/billing/vendedor", tags=["billing"], dependencies=[publico.ADMINISTRADORA, Depends(sin_demo)])

_freno = VentanaPorIP(max_fails=10, window_minutes=60)

_NO_PUEDE = {
    "ya_atribuida": "Tu cuenta ya tiene un vendedor asignado.",
    "fuera_de_plazo": "El plazo para asignar un vendedor (30 días desde el alta) ya ha pasado.",
    "sin_cuenta": "No encontramos tu cuenta.",
}


def _caida(e: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudo completar. Inténtalo de nuevo.")


async def _cuenta(db: PostgrestClient, user: CurrentUser) -> str:
    cuenta = await id_de_cuenta(db, user.salon_id)
    if not cuenta:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_NO_PUEDE["sin_cuenta"])
    return cuenta


async def _que_puede(servicio: PostgrestClient, cuenta: str) -> dict:
    try:
        return await atribucion.puede_asignar(servicio, cuenta)
    except PostgrestError as e:
        raise _caida(e) from e


@router.get("", response_model=EstadoVendedorOut)
async def estado(
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
    servicio: PostgrestClient = Depends(get_service_postgrest_client),
) -> EstadoVendedorOut:
    """¿Sale el campo? Solo «puede» y hasta cuándo: ni quién es su vendedor ni
    si lo tiene (con uno ya asignado, el campo desaparece y punto)."""
    p = await _que_puede(servicio, await _cuenta(db, user))
    puede = p["resultado"] == "puede"
    return EstadoVendedorOut(puede=puede, hasta=p["hasta"] if puede else None, frase=atribucion.FRASE_DEL_SALON)


@router.post("/comprobar", response_model=VendedorOut)
async def comprobar(
    payload: ComprobarVendedorIn,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
    servicio: PostgrestClient = Depends(get_service_postgrest_client),
) -> VendedorOut:
    """De quién es el código, PARA ENSEÑAR SU NOMBRE antes de confirmar. No
    escribe nada."""
    cuenta = await _cuenta(db, user)
    p = await _que_puede(servicio, cuenta)
    if p["resultado"] != "puede":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_NO_PUEDE.get(p["resultado"], "No se puede."))
    bloqueado, reintentar_en = _freno.is_blocked(cuenta)
    if bloqueado:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                            detail="Demasiadas comprobaciones. Inténtalo más tarde.",
                            headers={"Retry-After": str(reintentar_en)})
    _freno.add_failure(cuenta)
    try:
        v = await atribucion.vendedor_de_un_codigo(servicio, payload.codigo)
    except PostgrestError as e:
        raise _caida(e) from e
    if v is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Ese código no es de ningún vendedor. Revísalo con quien te lo dio.")
    return VendedorOut(**v, frase=atribucion.FRASE_DEL_SALON)


async def _quien(servicio: PostgrestClient, user: CurrentUser) -> tuple[str, str]:
    """La ficha de quien tiene la sesión (`users`), con su correo: queda en la atribución."""
    filas = await servicio.select("users", {"select": "id,email", "auth_user_id": f"eq.{user.sub}", "limit": "1"})
    fila = filas[0] if filas else {}
    return str(fila.get("id") or user.sub), fila.get("email") or user.email or "?"


@router.post("", response_model=AsignadoOut)
async def asignar(
    payload: AsignarVendedorIn,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
    servicio: PostgrestClient = Depends(get_service_postgrest_client),
) -> AsignadoOut:
    """CONFIRMAR. Con el vendedor que se le enseñó; una sola vez."""
    cuenta = await _cuenta(db, user)
    try:
        usuario, email = await _quien(servicio, user)
        r = await atribucion.asignar_su_vendedor(
            servicio, cuenta, payload.codigo, str(payload.referido_id),
            str(payload.trabajador_id) if payload.trabajador_id else None, usuario, email,
        )
    except PostgrestError as e:
        raise _caida(e) from e
    resultado = r.get("resultado")
    if resultado == "asignada":
        return AsignadoOut(nombre=r["referido_nombre"], trabajador_nombre=r.get("trabajador_nombre"),
                           devenga_desde=r["devenga_desde"], frase=atribucion.FRASE_DEL_SALON)
    if resultado == "no_vale":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ese código ya no es de ningún vendedor.")
    if resultado == "ha_cambiado":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Ese código ya no es de la persona que te enseñamos. Compruébalo otra vez.")
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_NO_PUEDE.get(resultado, "No se puede."))
