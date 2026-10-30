"""El portal del referido (0071): entrar, ponerse contraseña, y ver lo suyo.

Dos routers con el mismo prefijo, como en `referidos.py`:

  · `router_puerta` — lo que se usa SIN sesión: entrar (CUALQUIERA), pedir un
    enlace para cambiar la contraseña (CUALQUIERA) y usarlo (CON_ENLACE).
  · `router_portal` — lo que se usa CON la sesión del referido: todo el router
    declara REFERIDO. Cada consulta sale del `referido.id` que devuelve
    `sesion_de_referido`; ninguna ruta acepta un identificador del cliente.

QUÉ VE, y nada más (05-10-2026): su código, su enlace listo para
copiar, los salones que ha traído —nombre, fecha y si pagan— y en qué punto
está su cuenta de cobro de Stripe (0072): sin conectar, pendiente de lo que
pida Stripe, o lista. Antes de mandarlo a Stripe se le pregunta el país, y si
no es uno de `core/paises_cobro.py` se le para aquí, con el porqué. Ni clientas, ni agendas, ni datos del salón, ni ningún
dato bancario: esos los tiene Stripe.

UNA AGENCIA (0077) ve además a sus trabajadores y los gestiona desde aquí:
darlos de alta (solo el nombre: el código lo pone la plataforma), de baja, y
generar o revocar el enlace de seguimiento de cada uno. Cada ruta de
trabajadores comprueba que quien entra sea una agencia, y la base, que el
trabajador sea suyo. Un trabajador no tiene portal: no entra a ningún sitio.

«NO EXISTE» Y «CONTRASEÑA MALA» SE VEN IGUAL: la misma respuesta, el mismo
tiempo (se comprueba siempre contra un hash), y el mismo contador de intentos,
que cuenta por correo exista o no.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

import stripe

from app.core import agencias, atribucion, cobros_referido, comisiones, pagos_referido, publico
from app.core.acceso_referido import mandar_enlace
from app.core.admin_audit import client_ip
from app.core.config import Settings, get_settings
from app.core.cookies_de_sesion import borrar_cookie_de_sesion, poner_cookie_de_sesion
from app.core.limites import AttemptRateLimiter
from app.core.paises_cobro import PAISES_COBRO, POR_QUE_NO
from app.core.sesion_referido import (
    COOKIE_SESION_REFERIDO,
    HORAS_SESION,
    ReferidoActual,
    exigir_origen_permitido,
    hash_de_token,
    hashear_contrasena,
    servicio,
    sesion_de_referido,
    token_nuevo,
    verificar_contrasena,
)
from app.db.postgrest_client import PostgrestError
from app.schemas.auth import exigir_contrasena_fuerte
from app.schemas.referidos import ComisionesOut, TrabajadoresOut

logger = logging.getLogger("estetix.referidos")

router_puerta = APIRouter(prefix="/referido", tags=["portal-referido"], dependencies=[Depends(exigir_origen_permitido)])
router_portal = APIRouter(prefix="/referido", tags=["portal-referido"], dependencies=[publico.REFERIDO])

NO_ENTRA = "Correo o contraseña incorrectos."
ESPERA = "Demasiados intentos. Espera unos minutos y vuelve a probar."
ENLACE_NO_VALE = "Este enlace ya no vale: o ya se usó, o ha caducado, o se pidió otro después. Pide uno nuevo."
RECUPERAR_OK = "Si ese correo está en el programa, te hemos mandado un enlace para cambiar la contraseña."

# Además del contador de la base (por correo), un freno por IP en memoria para
# quien pruebe muchos correos distintos. Como el de recuperar de los salones.
_freno_entrar = AttemptRateLimiter(max_attempts_per_ip=30, max_attempts_per_email=1000)
_freno_recuperar = AttemptRateLimiter()


class EntrarIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class RecuperarIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr


class ContrasenaIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=20, max_length=200)
    password: str = Field(min_length=10, max_length=256)

    @field_validator("password")
    @classmethod
    def _fuerte(cls, value: str) -> str:
        return exigir_contrasena_fuerte(value)


class Mensaje(BaseModel):
    mensaje: str


class AltaDelReferido(BaseModel):
    cuenta_nombre: str
    atribuida_el: datetime
    # paga · en_prueba · pago_pendiente · no_paga · cuenta_borrada
    situacion: str
    # Solo de una agencia: qué trabajador lo trajo (0077). Nulo: su propio código.
    trabajador_nombre: str | None = None
    # De dónde vino (0078): alta · salon (lo asignó el salón después) · repesca.
    origen: str = "alta"
    # Desde cuándo devenga, si no es desde el alta. Con `salon`, la pantalla
    # dice la frase de `core/atribucion.FRASE_DEL_SALON`.
    devenga_desde: datetime | None = None
    # La frase que ve también el salón, cuando lo asignó él (`origen` salon).
    aviso: str | None = None


class CobrosOut(BaseModel):
    """En qué punto está su cuenta de Stripe, PARA ENSEÑARLO (sale de la copia
    de lo que dijo Stripe, con su fecha). Sin el identificador de la cuenta."""

    estado: str  # sin_conectar · pendiente · lista
    pausado: bool = False
    fecha_limite: datetime | None = None
    copia_de: datetime | None = None
    # Solo si está lista: se le pregunta a Stripe al pintarlo, no se guarda.
    ultimos_digitos: str | None = None


class PortalOut(BaseModel):
    nombre: str
    email: str
    codigo: str | None
    enlace: str | None
    cobros: CobrosOut
    altas: list[AltaDelReferido]
    # Lo que lleva ganado (0075): por moneda, por salón y por mes, con las
    # correcciones y su porqué. Del salón, solo su nombre.
    comisiones: ComisionesOut
    # individual · agencia (0077). Solo una agencia lleva sus trabajadores.
    tipo: str = "individual"
    trabajadores: TrabajadoresOut | None = None


class EnlaceStripeOut(BaseModel):
    url: str


class TrabajadorIn(BaseModel):
    """Un trabajador nuevo: el nombre y NADA MÁS (06-10-2026)."""

    model_config = ConfigDict(extra="forbid")
    nombre: str = Field(min_length=1, max_length=80)

    @field_validator("nombre")
    @classmethod
    def _con_algo(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Escribe su nombre.")
        return value.strip()


class EnlaceDeSeguimientoOut(BaseModel):
    # Se enseña UNA vez: la base guarda solo su hash.
    url: str


class ConectarIn(BaseModel):
    """El país solo hace falta la PRIMERA vez (crea la cuenta). Con la cuenta
    ya creada, se ignora: el país de una cuenta de Stripe no se cambia."""

    model_config = ConfigDict(extra="forbid")
    pais: str | None = Field(default=None, min_length=2, max_length=2)


class PaisOut(BaseModel):
    codigo: str
    nombre: str


class PaisesOut(BaseModel):
    paises: list[PaisOut]
    por_que_no: str


def _poner_cookie(response: Response, token: str) -> None:
    poner_cookie_de_sesion(response, COOKIE_SESION_REFERIDO, token, HORAS_SESION * 3600)


# ── La puerta ───────────────────────────────────────────────────────────────


@router_puerta.post("/entrar", dependencies=[publico.CUALQUIERA], status_code=status.HTTP_204_NO_CONTENT)
async def entrar(payload: EntrarIn, request: Request, response: Response,
                 settings: Settings = Depends(get_settings)) -> Response:
    ip = client_ip(request)
    email = str(payload.email).lower()
    bloqueado, reintentar = _freno_entrar.is_blocked(ip, email)
    if bloqueado:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=ESPERA,
                            headers={"Retry-After": str(reintentar)})
    _freno_entrar.add_attempt(ip, email)

    db = servicio(settings)
    try:
        # SE APUNTA ANTES DE COMPROBAR, con la fila bloqueada (0071, como el PIN).
        intento = (await db.rpc("referido_intento", {"p_email": email}))[0]
        if not intento.get("permitido"):
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=ESPERA)
        filas = await db.select("referidos", {
            "select": "id,password_hash", "email": f"eq.{email}", "estado": "eq.aprobado", "limit": "1",
        })
    except PostgrestError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudo comprobar. Inténtalo otra vez.") from e

    guardado = filas[0].get("password_hash") if filas else None
    # Siempre se comprueba, haya referido o no: mismo tiempo, misma respuesta.
    if not await verificar_contrasena(payload.password, guardado) or not filas:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=NO_ENTRA)

    token = token_nuevo()
    try:
        await db.rpc("referido_entrada_buena", {"p_email": email})
        await db.insert("referido_sesiones", {
            "token_hash": hash_de_token(token), "referido_id": filas[0]["id"],
            "caduca_at": (datetime.now(timezone.utc) + timedelta(hours=HORAS_SESION)).isoformat(),
        })
    except PostgrestError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudo abrir la sesión.") from e
    respuesta = Response(status_code=status.HTTP_204_NO_CONTENT)
    _poner_cookie(respuesta, token)
    return respuesta


@router_puerta.post("/recuperar", dependencies=[publico.CUALQUIERA], response_model=Mensaje)
async def recuperar(payload: RecuperarIn, request: Request, settings: Settings = Depends(get_settings)) -> Mensaje:
    """SIEMPRE la misma respuesta, exista el correo o no: si no, esto listaría
    quién está en el programa. Los fallos del camino «sí existe» se registran y
    se responden igual."""
    ip = client_ip(request)
    email = str(payload.email).lower()
    bloqueado, reintentar = _freno_recuperar.is_blocked(ip, email)
    if bloqueado:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=ESPERA,
                            headers={"Retry-After": str(reintentar)})
    _freno_recuperar.add_attempt(ip, email)
    db = servicio(settings)
    try:
        filas = await db.select("referidos", {
            "select": "id,nombre,email", "email": f"eq.{email}", "estado": "eq.aprobado", "limit": "1",
        })
        if filas:
            await mandar_enlace(db, settings, filas[0], "recuperar")
    except PostgrestError:
        logger.exception("referidos: no se pudo preparar el enlace de recuperar")
    return Mensaje(mensaje=RECUPERAR_OK)


@router_puerta.post("/contrasena", dependencies=[publico.CON_ENLACE], status_code=status.HTTP_204_NO_CONTENT)
async def poner_contrasena(payload: ContrasenaIn, settings: Settings = Depends(get_settings)) -> Response:
    """El enlace de un solo uso. Lo comprueba y lo gasta la base en una sola
    sentencia (`referido_usar_enlace`), y cierra todas sus sesiones abiertas."""
    nuevo = await hashear_contrasena(payload.password)
    try:
        filas = await servicio(settings).rpc(
            "referido_usar_enlace", {"p_token_hash": hash_de_token(payload.token), "p_password_hash": nuevo}
        )
    except PostgrestError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudo guardar. Inténtalo otra vez.") from e
    if not filas or not filas[0]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=ENLACE_NO_VALE)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router_puerta.get("/paises", dependencies=[publico.CUALQUIERA], response_model=PaisesOut)
async def paises() -> PaisesOut:
    """En qué países se puede cobrar: lo enseñan la página pública del
    programa y el portal. Sale de `core/paises_cobro.py`, el único sitio."""
    return PaisesOut(
        paises=[PaisOut(codigo=c, nombre=n) for c, n in PAISES_COBRO.items()], por_que_no=POR_QUE_NO
    )


# ── El portal ───────────────────────────────────────────────────────────────


def _alta(fila: dict) -> AltaDelReferido:
    return AltaDelReferido(**fila, aviso=atribucion.FRASE_DEL_SALON if fila.get("origen") == "salon" else None)


@router_portal.get("/portal", response_model=PortalOut)
async def portal(referido: ReferidoActual = Depends(sesion_de_referido),
                 settings: Settings = Depends(get_settings)) -> PortalOut:
    try:
        filas = await servicio(settings).rpc("portal_del_referido", {"p_referido": referido.id})
    except PostgrestError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudo cargar.") from e
    enlace = f"{settings.frontend_base_url.rstrip('/')}/?ref={referido.codigo}" if referido.codigo else None
    db = servicio(settings)
    # NUNCA SE INVENTA UN ESTADO: si la base falla, error (no «sin conectar», que
    # le diría que no ha hecho algo que sí hizo). Y si lo que falla son los
    # últimos dígitos —se le piden a Stripe—, el estado se queda como está y solo
    # faltan los dígitos (lo encontraron las capturas, 05-10-2026).
    try:
        cobros = CobrosOut(**await cobros_referido.estado_del_referido(db, referido.id))
        salones, cambios = await comisiones.salones_y_cambios(db, referido.id)
        ganado = ComisionesOut(
            # Los cambios de %, como movimientos; su MOTIVO no sale de aquí (0077).
            **comisiones.resumen(await comisiones.filas_del_referido(db, referido.id),
                                 pct_hoy={x["cuenta_id"]: x["pct"] for x in salones},
                                 pagos=await pagos_referido.pagos(db, referido.id), cambios=cambios),
            avisos_de_cambio=comisiones.avisos_de_cambio(cambios),
        )
        es_agencia = await agencias.es_agencia(db, referido.id)
        equipo = TrabajadoresOut(**await agencias.trabajadores(db, referido.id)) if es_agencia else None
        cuenta = await cobros_referido.cuenta_de(db, referido.id) if cobros.estado == cobros_referido.LISTA else None
    except PostgrestError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudo cargar.") from e
    if cuenta:
        try:
            cobros.ultimos_digitos = await cobros_referido.ultimos_digitos(settings, cuenta)
        except (RuntimeError, stripe.StripeError):
            logger.exception("referidos: Stripe no dio los últimos dígitos")
    return PortalOut(
        nombre=referido.nombre, email=referido.email, codigo=referido.codigo, enlace=enlace,
        cobros=cobros, altas=[_alta(f) for f in filas if f], comisiones=ganado,
        tipo="agencia" if es_agencia else "individual", trabajadores=equipo,
    )


# ── Los trabajadores de una agencia (0077) ──────────────────────────────────


async def _solo_una_agencia(settings: Settings, referido: ReferidoActual):
    db = servicio(settings)
    try:
        if not await agencias.es_agencia(db, referido.id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail="Solo una agencia tiene trabajadores.")
    except PostgrestError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudo comprobar.") from e
    return db


async def _equipo(db, agencia_id: str) -> TrabajadoresOut:
    return TrabajadoresOut(**await agencias.trabajadores(db, agencia_id))


@router_portal.post("/trabajadores", response_model=TrabajadoresOut, status_code=status.HTTP_201_CREATED)
async def alta_de_trabajador(payload: TrabajadorIn, referido: ReferidoActual = Depends(sesion_de_referido),
                             settings: Settings = Depends(get_settings)) -> TrabajadoresOut:
    """Un trabajador nuevo, con su propio código de alta (el mismo formato que
    el de un referido). Sin porcentaje, sin cuenta, sin contraseña."""
    db = await _solo_una_agencia(settings, referido)
    try:
        await agencias.dar_de_alta(db, referido.id, payload.nombre)
        return await _equipo(db, referido.id)
    except agencias.NoEsUnaAgencia as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Solo una agencia tiene trabajadores.") from e
    except (PostgrestError, RuntimeError) as e:
        logger.exception("agencias: no se pudo dar de alta a un trabajador")
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudo dar de alta. Inténtalo otra vez.") from e


@router_portal.post("/trabajadores/{trabajador_id}/baja", response_model=TrabajadoresOut)
async def baja_de_trabajador(trabajador_id: UUID, referido: ReferidoActual = Depends(sesion_de_referido),
                             settings: Settings = Depends(get_settings)) -> TrabajadoresOut:
    """Conserva los salones que ya trajo; su código deja de admitir altas y su
    enlace de seguimiento deja de abrir, en el mismo acto (lo hace la base)."""
    db = await _solo_una_agencia(settings, referido)
    try:
        await agencias.dar_de_baja(db, referido.id, str(trabajador_id))
        return await _equipo(db, referido.id)
    except agencias.NoEsSuyo as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ese trabajador no es tuyo.") from e
    except PostgrestError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudo dar de baja.") from e


@router_portal.post("/trabajadores/{trabajador_id}/seguimiento", response_model=EnlaceDeSeguimientoOut)
async def nuevo_enlace_de_seguimiento(trabajador_id: UUID, referido: ReferidoActual = Depends(sesion_de_referido),
                                      settings: Settings = Depends(get_settings)) -> EnlaceDeSeguimientoOut:
    """Un enlace NUEVO para ese trabajador: el anterior, si lo había, deja de
    abrir. Se enseña esta vez y no más (la base guarda solo su hash)."""
    db = await _solo_una_agencia(settings, referido)
    try:
        token = await agencias.nuevo_seguimiento(db, referido.id, str(trabajador_id))
    except agencias.DeBaja as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Está de baja: ya no tiene enlace de seguimiento.") from e
    except agencias.NoEsSuyo as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ese trabajador no es tuyo.") from e
    except PostgrestError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudo crear el enlace.") from e
    return EnlaceDeSeguimientoOut(url=f"{settings.frontend_base_url.rstrip('/')}/seguimiento/{token}")


@router_portal.post("/trabajadores/{trabajador_id}/seguimiento/revocar", response_model=TrabajadoresOut)
async def revocar_enlace_de_seguimiento(trabajador_id: UUID, referido: ReferidoActual = Depends(sesion_de_referido),
                                        settings: Settings = Depends(get_settings)) -> TrabajadoresOut:
    """Revocado, el enlace deja de abrir. Se puede generar otro después."""
    db = await _solo_una_agencia(settings, referido)
    try:
        await agencias.revocar_seguimiento(db, referido.id, str(trabajador_id))
        return await _equipo(db, referido.id)
    except agencias.NoEsSuyo as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ese trabajador no es tuyo.") from e
    except PostgrestError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudo revocar.") from e


@router_portal.post("/cobros/conectar", response_model=EnlaceStripeOut)
async def conectar_cobros(payload: ConectarIn | None = None,
                          referido: ReferidoActual = Depends(sesion_de_referido),
                          settings: Settings = Depends(get_settings)) -> EnlaceStripeOut:
    """El botón «Conectar» y «Terminar en Stripe», y el que pide otro enlace
    cuando el anterior caducó: crea su cuenta la primera vez (una sola, por
    idempotencia) y devuelve un enlace NUEVO a la pantalla de Stripe. Volver a
    entrar a medias es esto mismo: Stripe sigue donde lo dejó.

    La primera vez, el país: uno de fuera de la lista se para AQUÍ (422, con
    el porqué), sin crear nada en Stripe."""
    pais = payload.pais if payload else None
    db = servicio(settings)
    try:
        cuenta = await cobros_referido.cuenta_de(db, referido.id)
        if cuenta is None:
            cuenta = await cobros_referido.crear_cuenta(
                db, settings, referido.id, referido.email, referido.nombre, pais or ""
            )
        return EnlaceStripeOut(url=await cobros_referido.enlace_de_alta(settings, cuenta))
    except cobros_referido.PaisNoAdmitido as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=POR_QUE_NO) from e
    except (PostgrestError, RuntimeError, stripe.StripeError) as e:
        logger.exception("referidos: no se pudo preparar el enlace de Stripe")
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail="No hemos podido abrir Stripe ahora. Inténtalo en un momento.") from e


@router_portal.post("/cobros/sincronizar", status_code=status.HTTP_204_NO_CONTENT)
async def sincronizar_cobros(referido: ReferidoActual = Depends(sesion_de_referido),
                             settings: Settings = Depends(get_settings)) -> Response:
    """Al volver de Stripe: el webhook puede no haber llegado aún, así que se le
    pregunta a Stripe y se guarda la copia. Sin cuenta, no hay nada que mirar."""
    db = servicio(settings)
    try:
        cuenta = await cobros_referido.cuenta_de(db, referido.id)
        if cuenta:
            await cobros_referido.sincronizar(db, settings, cuenta)
    except (PostgrestError, RuntimeError, stripe.StripeError):
        logger.exception("referidos: no se pudo sincronizar con Stripe al volver")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router_portal.post("/salir", status_code=status.HTTP_204_NO_CONTENT)
async def salir(request: Request, referido: ReferidoActual = Depends(sesion_de_referido),
                settings: Settings = Depends(get_settings)) -> Response:
    token = request.cookies.get(COOKIE_SESION_REFERIDO, "")
    try:
        await servicio(settings).delete("referido_sesiones", {
            "token_hash": f"eq.{hash_de_token(token)}", "referido_id": f"eq.{referido.id}",
        })
    except PostgrestError:
        logger.exception("referidos: no se pudo cerrar la sesión")
    respuesta = Response(status_code=status.HTTP_204_NO_CONTENT)
    borrar_cookie_de_sesion(respuesta, COOKIE_SESION_REFERIDO)
    return respuesta
