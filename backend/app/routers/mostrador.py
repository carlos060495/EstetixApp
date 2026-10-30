"""El dispositivo del mostrador (0090): activarlo, abrir y cerrar la gestión, y
salir del modo. Qué contesta la cookie en el resto de la API lo decide
`core/mostrador.py`, desde `core/security.py`.

NINGUNA RESPUESTA DE AQUÍ LLEVA UN TOKEN DE LA BASE. Lo que el navegador recibe
es la cookie httpOnly con un secreto que solo entiende este backend; el token
del salón se acuña y se queda aquí. Lo vigila `tests/test_mostrador.py`.

TODO LO QUE CAMBIA EL MODO PIDE EL PIN DE GESTIÓN, comprobado aquí con el mismo
contador y la misma espera que la cortina (`pin_gestion._comprobar`). Y un
mostrador SIN PIN no existe: sin PIN no habría con qué abrir la gestión ni con
qué salir del modo.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel

from app.core import demo, mostrador, publico
from app.core import zona as zonas
from app.core.config import Settings, get_settings
from app.core.contrasena import comprobar_contrasena, minutos_hasta
from app.core.cuenta import id_de_cuenta
from app.core.cookies_de_sesion import borrar_cookie_de_sesion, poner_cookie_de_sesion
from app.core.security import (
    CurrentUser,
    dispositivo_del_mostrador,
    get_current_user,
    get_service_postgrest_client,
)
from app.db.postgrest_client import PostgrestClient, PostgrestError
from app.routers.pin_gestion import exigir_pin_de_gestion, tiene_pin_de_gestion
from app.schemas.pin_gestion import PinGestionVerificarIn

router = APIRouter(prefix="/mostrador", tags=["mostrador"])

CODIGO_SIN_PIN = "mostrador_sin_pin"
CODIGO_YA_ES = "mostrador_ya_es"


class DispositivoOut(BaseModel):
    id: str
    centro: str
    # La zona del centro del mostrador: las fechas se enseñan en ella.
    zona: str
    descripcion: str | None
    creado_el: datetime
    usado_el: datetime | None
    # El dispositivo desde el que se mira: no se retira desde la lista, que lo
    # dejaría en el inicio de sesión a media pantalla. Para eso está «Quitar el
    # modo mostrador», con el PIN.
    es_este: bool


class EstadoMostrador(BaseModel):
    es_mostrador: bool
    gestion_abierta: bool = False
    minutos_gestion: int = mostrador.MINUTOS_GESTION


def _502(que: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"No se pudo {que}.")


_SIN_PIN_AL_ACTIVAR = "Pon antes un PIN de gestión: es lo que abre la gestión en el mostrador."
# Un mostrador ya en pie y la cuenta sin PIN (quitado antes de que `quitar` lo
# impidiera, o en una carrera con `activar`): la salida es la contraseña.
_SIN_PIN_EN_EL_MOSTRADOR = (
    "La cuenta no tiene PIN de gestión. Para quitarle a este dispositivo el modo mostrador, "
    "usa el correo y la contraseña de la administradora."
)


async def _exigir_pin(
    service: PostgrestClient, settings: Settings, salon_id: str, pin: str, *, sin_pin: str = _SIN_PIN_EN_EL_MOSTRADOR
) -> None:
    # `exigir_pin_de_gestion` deja pasar a quien NO tiene PIN; aquí no: un
    # mostrador sin PIN se abriría con cualquier cosa.
    if not await tiene_pin_de_gestion(service, settings, salon_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"codigo": CODIGO_SIN_PIN, "mensaje": sin_pin},
        )
    await exigir_pin_de_gestion(service, settings, salon_id, pin)


@router.get("", dependencies=[publico.CUALQUIERA], response_model=EstadoMostrador,
            summary="¿Es este dispositivo un mostrador, y tiene la gestión abierta?")
async def estado(
    request: Request,
    service: PostgrestClient = Depends(get_service_postgrest_client),
) -> EstadoMostrador:
    # Sin sesión a propósito: lo pregunta el login ANTES de iniciar sesión, para
    # no hacerlo en un mostrador.
    try:
        dispositivo = await mostrador.dispositivo_de(service, request.cookies.get(mostrador.COOKIE))
    except PostgrestError as e:
        raise _502("comprobar el dispositivo") from e
    if dispositivo is None:
        return EstadoMostrador(es_mostrador=False)
    return EstadoMostrador(es_mostrador=True, gestion_abierta=dispositivo.gestion_abierta())


@router.post("/activar", dependencies=[publico.ADMINISTRADORA], response_model=EstadoMostrador,
             summary="Convertir este dispositivo en el mostrador del centro")
async def activar(
    payload: PinGestionVerificarIn,
    request: Request,
    response: Response,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    service: PostgrestClient = Depends(get_service_postgrest_client),
) -> EstadoMostrador:
    if user.mostrador_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"codigo": CODIGO_YA_ES, "mensaje": "Este dispositivo ya es el mostrador."},
        )
    if user.impersonation is not None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Soporte no convierte dispositivos.")
    # EN UNA DEMO, NO: convertir el navegador le quitaría la sesión de la muestra,
    # que es lo único que tiene quien la prueba.
    demo.no_en_una_demo(user.es_demo, "mostrador")
    await _exigir_pin(service, settings, user.salon_id, payload.pin, sin_pin=_SIN_PIN_AL_ACTIVAR)
    secreto = mostrador.nuevo_secreto()
    try:
        ficha = await service.select(
            "users", {"select": "id", "auth_user_id": f"eq.{user.sub}", "rol": "eq.admin", "limit": "1"}
        )
        if not ficha:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciales inválidas")
        await service.insert(
            "dispositivos_mostrador",
            {
                "user_id": ficha[0]["id"],
                "salon_id": user.salon_id,
                "token_hash": mostrador.huella(secreto),
                "descripcion": mostrador.describir(request.headers.get("user-agent")),
            },
            select="id",
        )
    except PostgrestError as e:
        raise _502("activar el mostrador") from e
    poner_cookie_de_sesion(response, mostrador.COOKIE, secreto, mostrador.SEGUNDOS_COOKIE)
    return EstadoMostrador(es_mostrador=True)


@router.post("/gestion", dependencies=[publico.MOSTRADOR], response_model=EstadoMostrador,
             summary="Abrir la gestión en este mostrador")
async def abrir_gestion(
    payload: PinGestionVerificarIn,
    dispositivo: mostrador.Dispositivo = Depends(dispositivo_del_mostrador),
    settings: Settings = Depends(get_settings),
    service: PostgrestClient = Depends(get_service_postgrest_client),
) -> EstadoMostrador:
    await _exigir_pin(service, settings, dispositivo.salon_id, payload.pin)
    try:
        await mostrador.abrir_gestion(service, dispositivo.id)
    except PostgrestError as e:
        raise _502("abrir la gestión") from e
    return EstadoMostrador(es_mostrador=True, gestion_abierta=True)


@router.delete("/gestion", dependencies=[publico.MOSTRADOR], status_code=status.HTTP_204_NO_CONTENT,
               summary="Cerrar la gestión en este mostrador, ya")
async def cerrar_gestion(
    dispositivo: mostrador.Dispositivo = Depends(dispositivo_del_mostrador),
    service: PostgrestClient = Depends(get_service_postgrest_client),
) -> Response:
    try:
        await mostrador.cerrar_gestion(service, dispositivo.id)
    except PostgrestError as e:
        raise _502("cerrar la gestión") from e
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/salir", dependencies=[publico.MOSTRADOR], status_code=status.HTTP_204_NO_CONTENT,
             summary="Quitarle a este dispositivo el modo mostrador")
async def salir(
    payload: PinGestionVerificarIn,
    dispositivo: mostrador.Dispositivo = Depends(dispositivo_del_mostrador),
    settings: Settings = Depends(get_settings),
    service: PostgrestClient = Depends(get_service_postgrest_client),
) -> Response:
    await _exigir_pin(service, settings, dispositivo.salon_id, payload.pin)
    return await _retirar(service, dispositivo)


async def _retirar(service: PostgrestClient, dispositivo: mostrador.Dispositivo) -> Response:
    try:
        await service.update(
            "dispositivos_mostrador",
            {"id": f"eq.{dispositivo.id}"},
            {"retirado_el": datetime.now(timezone.utc).isoformat(), "gestion_hasta": None},
            select="id",
        )
    except PostgrestError as e:
        raise _502("quitar el modo mostrador") from e
    # SIN SESIÓN NINGUNA: ni la del mostrador ni la de la dueña. Quien quiera
    # usar este dispositivo, inicia sesión.
    respuesta = Response(status_code=status.HTTP_204_NO_CONTENT)
    borrar_cookie_de_sesion(respuesta, mostrador.COOKIE)
    return respuesta


CODIGO_CREDENCIALES = "credenciales_incorrectas"
CODIGO_EN_ESPERA = "contrasena_en_espera"


async def _credenciales_del_cuerpo(request: Request) -> tuple[str, str]:
    """Leídas A MANO, como en `datos.py`: un 422 de FastAPI devolvería lo
    recibido, y aquí lo recibido es una contraseña."""
    try:
        cuerpo = json.loads(await request.body() or b"{}")
    except (ValueError, UnicodeDecodeError):
        cuerpo = None
    cuerpo = cuerpo if isinstance(cuerpo, dict) else {}
    email, contrasena = cuerpo.get("email"), cuerpo.get("contrasena")
    if not (
        isinstance(email, str) and email.strip() and len(email) <= 320
        and isinstance(contrasena, str) and contrasena and len(contrasena) <= 200
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"codigo": CODIGO_CREDENCIALES, "mensaje": "Escribe el correo y la contraseña."},
        )
    return email.strip().lower(), contrasena


@router.post(
    "/salir-con-contrasena", dependencies=[publico.MOSTRADOR], status_code=status.HTTP_204_NO_CONTENT,
    summary="Quitarle a este dispositivo el modo mostrador con el correo y la contraseña de la administradora",
    openapi_extra={"requestBody": {"required": True, "content": {"application/json": {"schema": {
        "type": "object", "required": ["email", "contrasena"],
        "properties": {"email": {"type": "string"}, "contrasena": {"type": "string"}}}}}}},
)
async def salir_con_contrasena(
    request: Request,
    dispositivo: mostrador.Dispositivo = Depends(dispositivo_del_mostrador),
    settings: Settings = Depends(get_settings),
    service: PostgrestClient = Depends(get_service_postgrest_client),
) -> Response:
    """LA SALIDA DE UN MOSTRADOR ENCERRADO (29-10-2026): sin PIN, o con
    uno que nadie recuerda, la contraseña de una administradora de la cuenta lo
    libera. Vale siempre: quien la sabe ya lo puede todo desde cualquier sitio,
    y salir solo deja el dispositivo sin sesión.

    NO ES UN ORÁCULO. Solo se pregunta a Supabase por el correo de una
    administradora de ESTA cuenta. Con cualquier otro se hace la misma pregunta
    por la de quien lo activó, con una contraseña al azar: falla siempre, cuenta
    como intento y tarda lo mismo. La respuesta no dice si el correo existe, ni
    aquí ni en ninguna cuenta. Mismo contador que el PIN y la descarga de datos
    (`core/contrasena.py`), por cuenta.
    """
    email, contrasena = await _credenciales_del_cuerpo(request)
    try:
        cuenta = await id_de_cuenta(service, dispositivo.salon_id)
        admins = await service.select(
            "users", {"select": "auth_user_id,email", "account_id": f"eq.{cuenta}", "rol": "eq.admin"}
        ) if cuenta else []
    except PostgrestError as e:
        raise _502("comprobar el correo") from e
    if not cuenta:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Salón no encontrado")
    quien = next(
        (a["auth_user_id"] for a in admins
         if a.get("auth_user_id") and (a.get("email") or "").strip().lower() == email),
        None,
    )
    if quien is None:
        quien, contrasena = dispositivo.auth_user_id, secrets.token_urlsafe(32)
    r = await comprobar_contrasena(settings, service, cuenta, quien, contrasena)
    del contrasena
    if r.estado == "fallo":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No hemos podido comprobar la contraseña ahora. Inténtalo de nuevo en unos minutos.",
        )
    if r.estado == "en_espera":
        minutos = minutos_hasta(r.bloqueado_hasta)
        cuando = "1 minuto" if minutos == 1 else f"{minutos} minutos"
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"codigo": CODIGO_EN_ESPERA, "mensaje": f"Demasiados intentos. Vuelve a probar en {cuando}.",
                    "bloqueado_hasta": r.bloqueado_hasta},
            headers={"Retry-After": str(minutos * 60)},
        )
    if r.estado == "incorrecta":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"codigo": CODIGO_CREDENCIALES, "mensaje": "El correo o la contraseña no son correctos.",
                    "intentos_restantes": r.intentos_restantes},
        )
    return await _retirar(service, dispositivo)


# ══════════════════════════ los mostradores de la cuenta (29-10-2026)
#
# Una tablet que se rompe, se borra o caduca deja su fila en pie, y con ella el
# PIN de gestión imposible de quitar (`pin_gestion.quitar` no deja con
# mostradores en pie). Desde aquí la dueña los ve y retira cualquiera. Retirar
# no encierra a nadie: el dispositivo recibe un 401 `mostrador_retirado` y su
# pantalla lo explica, sin sesión ninguna.


async def _centros_de_la_cuenta(service: PostgrestClient, salon_id: str) -> dict[str, dict]:
    """{id: {nombre, zona}} de los centros de la cuenta."""
    try:
        cuenta = await id_de_cuenta(service, salon_id)
        filas = await service.select(
            "salons", {"select": "id,nombre_comercial,zona_horaria", "account_id": f"eq.{cuenta}"}
        ) if cuenta else []
    except PostgrestError as e:
        raise _502("leer los centros") from e
    return {
        f["id"]: {"nombre": f.get("nombre_comercial") or "Centro", "zona": zonas.zona(f.get("zona_horaria")).key}
        for f in filas
    }


@router.get("/dispositivos", dependencies=[publico.ADMINISTRADORA], response_model=list[DispositivoOut],
            summary="Los mostradores en pie de la cuenta")
async def dispositivos(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    service: PostgrestClient = Depends(get_service_postgrest_client),
) -> list[DispositivoOut]:
    centros = await _centros_de_la_cuenta(service, user.salon_id)
    if not centros:
        return []
    try:
        filas = await service.select(
            "dispositivos_mostrador",
            {
                "select": "id,salon_id,descripcion,creado_el,usado_el",
                "salon_id": f"in.({','.join(centros)})",
                "retirado_el": "is.null",
                "order": "creado_el.asc",
            },
        )
    except PostgrestError as e:
        raise _502("leer los mostradores") from e
    return [
        DispositivoOut(
            id=f["id"],
            centro=centros[f["salon_id"]]["nombre"],
            zona=centros[f["salon_id"]]["zona"],
            descripcion=f.get("descripcion"),
            creado_el=f["creado_el"],
            usado_el=f.get("usado_el"),
            es_este=f["id"] == user.mostrador_id,
        )
        for f in filas
    ]


@router.delete("/dispositivos/{dispositivo_id}", dependencies=[publico.ADMINISTRADORA],
               status_code=status.HTTP_204_NO_CONTENT, summary="Retirar un mostrador de la cuenta")
async def retirar(
    dispositivo_id: str,
    user: CurrentUser = Depends(get_current_user),
    service: PostgrestClient = Depends(get_service_postgrest_client),
) -> Response:
    """Sin PIN, a propósito: Ajustes ya está detrás de la cortina, y retirar solo
    deja un dispositivo en el inicio de sesión, sin abrir nada. Pedirlo aquí
    dejaría sin salida justo al caso para el que existe: la cuenta sin PIN con
    una tablet perdida."""
    if user.mostrador_id == dispositivo_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Este es el dispositivo desde el que estás: quítale el modo con «Quitar el modo mostrador».",
        )
    centros = await _centros_de_la_cuenta(service, user.salon_id)
    try:
        filas = await service.update(
            "dispositivos_mostrador",
            # El centro va en el filtro: un id de otra cuenta no encuentra nada.
            {"id": f"eq.{dispositivo_id}", "salon_id": f"in.({','.join(centros)})", "retirado_el": "is.null"},
            {"retirado_el": datetime.now(timezone.utc).isoformat(), "gestion_hasta": None},
            select="id",
        ) if centros else []
    except PostgrestError as e:
        if e.status_code == status.HTTP_400_BAD_REQUEST:
            filas = []  # un id que no es un uuid: no es de nadie
        else:
            raise _502("retirar el mostrador") from e
    if not filas:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ese mostrador ya no está.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
