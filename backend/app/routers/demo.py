"""El salón de muestra (0080): una demo por visitante, que vive 24 horas.

Dos puertas:
  · `POST /demo` — PÚBLICA. Abre la demo de ESTE navegador: la que ya tiene
    viva, o una nueva con datos inventados. El navegador se reconoce por la
    cookie `__Host-demo` (un token aleatorio; en la base, solo su hash). Un clic
    no crea una demo: la crea un navegador que no tiene ninguna viva. Pasado un
    tope (en la base), contesta `sin_sitio` y la portada dice que se intente en
    un rato.
  · `/admin/demos/*` — el panel: cuántas hay, los topes (los cambia el owner,
    con motivo) y el ENSAYO del barrido, que enseña qué borraría sin borrar.

La sesión de la demo es una sesión de Supabase normal, de su administradora:
la RLS es la de cualquier salón. Lo que NO puede hacer una demo lo cortan las
salidas al mundo (`core/demo.py`) y la base (`una_demo_no_es_un_cliente`).
"""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.core import publico
from app.core.admin_audit import append_admin_log, client_ip
from app.core.admin_deps import AdminClaims, get_admin_db, require_admin_roles
from app.core.admin_security import VentanaPorIP
from app.core.config import Settings, get_settings
from app.core.cookies_de_sesion import COOKIE_DEMO, poner_cookie_de_sesion
from app.core.demo import DOMINIO_DE_LAS_DEMOS, PINES_DE_MUESTRA
from app.core.security import CurrentUser, get_postgrest_client, hashear_pin, solo_en_una_demo
from app.core.tenant_token_broker import _extraer_hashed_token
from app.db.postgrest_client import PostgrestClient, PostgrestError
from app.db.supabase_auth_client import SupabaseAuthAdminClient, SupabaseAuthError

logger = logging.getLogger("estetix.demo")

router = APIRouter(prefix="/demo", tags=["demo"])
router_admin = APIRouter(prefix="/admin/demos", tags=["admin-demos"], dependencies=[publico.PLATAFORMA])

SIN_SITIO = (
    "Ahora mismo no hay sitio para abrir otro salón de muestra. Inténtalo dentro de un rato."
)
# Los TRES topes dicen lo mismo: nunca una pista de cuál saltó.
_SIN_SITIO = frozenset({"sin_sitio_vivas", "sin_sitio_hora", "sin_sitio_ip"})

# EL FRENO POR IP: la misma ventana deslizante que el del código de vendedor
# (`routers/vendedor.py`), de 60 minutos. Cuenta solo las demos ENTREGADAS (con
# su sesión): volver a la viva no gasta, y un alta que falla tampoco (0082: así
# se llenaba solo, con 0 demos creadas). La cifra NO vive aquí: se lee de
# `topes_de_demos` en cada petición (`por_ip_hora_max`), y la cambia el owner.
_freno_ip = VentanaPorIP(window_minutes=60)

# Redes que no son de nadie en internet: las del propio proxy, la red interna de
# Railway (100.64/10), la local. Si la IP que llega es de estas, no se sabe de
# quién es la petición.
_REDES_INTERNAS = tuple(ipaddress.ip_network(r) for r in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "fc00::/7",
))


def ip_para_el_freno(request: Request) -> str | None:
    """La IP de quien abre la demo, o `None` si no se sabe CON SEGURIDAD.

    Railway pone la IP real al final de `X-Forwarded-For`: lo de delante lo puede
    escribir quien llama, así que manda la ÚLTIMA. Sin esa cabecera, o si lo que
    trae no es una IP pública, no se sabe: NO se cae a la IP de la conexión (la
    del proxy, la misma para todo el mundo), porque entonces el freno por persona
    sería un tope global. Sin IP el freno por IP no se aplica: quedarse sin
    enseñar la aplicación cuesta ventas; una demo de más son filas que se van
    solas en 24 horas. Los topes de vivas y de la hora siguen."""
    reenviada = (request.headers.get("x-forwarded-for") or "").split(",")[-1].strip()
    try:
        ip = ipaddress.ip_address(reenviada)
    except ValueError:
        return None
    if ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast or ip.is_reserved:
        return None
    if any(ip in red for red in _REDES_INTERNAS if red.version == ip.version):
        return None
    return str(ip)


def contrasena_de_muestra() -> str:
    """Legible (se enseña en pantalla) y que pasa la política más estricta de
    Supabase: minúscula, MAYÚSCULA, número y símbolo, 19 caracteres. El GoTrue de
    producción (v2.197) la aplica también al crear por la API de administración:
    sin mayúscula, la 0081 no abría ninguna demo."""
    return f"Muestra-{secrets.token_hex(3)}-{secrets.randbelow(900) + 100}!"


class DemoOut(BaseModel):
    # creada · de_vuelta · sin_sitio
    estado: str
    mensaje: str | None = None
    access_token: str | None = None
    refresh_token: str | None = None
    caduca_at: datetime | None = None


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _servicio(settings: Settings) -> PostgrestClient:
    return PostgrestClient(settings.supabase_url, settings.supabase_secret_key)


def _caida(e: Exception) -> HTTPException:
    logger.warning("demo: %s", type(e).__name__)
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                         detail="No se ha podido abrir el salón de muestra. Inténtalo otra vez.")


async def _sesion(auth: SupabaseAuthAdminClient, correo: str) -> dict:
    """Una sesión de su administradora, sin contraseña ni correo de por medio:
    el enlace mágico se pide y se canjea aquí mismo (como el modo soporte)."""
    generado = await auth.generate_magiclink(correo)
    hashed = _extraer_hashed_token(generado)
    if not hashed:
        raise SupabaseAuthError(502, "sin hashed_token")
    return await auth.verify_magiclink(hashed)


# ── Lo primero que ve quien abre la muestra (29-10-2026) ───────────────────
#
# La siembra (0080) dejaba el centro sin teléfono —y sin teléfono la página de
# reservas no se enciende— y la bandeja vacía aunque Reservas contaba citas de
# WhatsApp. Es lo primero que ve quien llega desde la portada.
#
# Los teléfonos son INVENTADOS a la vista (600 000 000 y 600 000 1xx), como los
# correos @example.com de las clientas. Desde una demo no sale nada, así que
# nadie recibe un mensaje. Las dos conversaciones nombran servicios, precios y
# personas que existen en la muestra.

TELEFONO_DE_LA_MUESTRA = "+34600000000"

_CONVERSACIONES_DE_MUESTRA = (
    {
        "clienta": "Laura Gómez",
        "telefono": "34600000101",
        "atencion": "humano",
        "motivo": "Pregunta por una prueba de alergia antes del tinte",
        "hace_min": 25,
        "mensajes": (
            ("user", "Hola! Quería pedir cita para unas mechas balayage el sábado por la mañana"),
            ("model", "¡Hola, Laura! El sábado por la mañana tengo hueco con Lucía a las 10:00 o a las 11:30. "
                      "¿Te viene bien alguna?"),
            ("user", "Una pregunta antes: el año pasado un tinte me dio reacción. ¿Me podéis hacer la prueba "
                     "de alergia antes?"),
            ("model", "Eso prefiero que te lo confirme alguien del salón. Te paso con el equipo y te escriben "
                      "en un momento."),
            ("system", "Pasa a una persona: pregunta por una prueba de alergia antes del tinte."),
        ),
    },
    {
        "clienta": "Nuria Castro",
        "telefono": "34600000102",
        "atencion": "bot",
        "motivo": None,
        "hace_min": 60 * 20,
        "mensajes": (
            ("user", "Buenas, ¿cuánto cuesta la limpieza facial?"),
            ("model", "¡Hola, Nuria! La limpieza facial son 48 € y dura una hora. ¿Quieres que te busque hueco?"),
            ("user", "De momento no, gracias. Lo miro y os digo"),
            ("model", "¡Perfecto! Cuando quieras, escríbeme por aquí y te busco hora."),
        ),
    },
)


async def _lo_primero_que_se_ve(db: PostgrestClient, salon_id: str) -> None:
    """El teléfono del centro y dos conversaciones. Si algo falla, la demo se
    abre igual: es decorado, no lo que la sostiene."""
    try:
        await db.update("salons", {"id": f"eq.{salon_id}"}, {"telefono": TELEFONO_DE_LA_MUESTRA})
        ahora = datetime.now(timezone.utc)
        for c in _CONVERSACIONES_DE_MUESTRA:
            await db.update("clients", {"salon_id": f"eq.{salon_id}", "nombre": f"eq.{c['clienta']}"},
                            {"telefono": f"+{c['telefono']}"})
            inicio = ahora - timedelta(minutes=c["hace_min"])
            paso = timedelta(minutes=1)
            ultimo = inicio + paso * (len(c["mensajes"]) - 1)
            ultimo_de_ella = inicio + paso * max(n for n, (rol, _) in enumerate(c["mensajes"]) if rol == "user")
            sesion = await db.insert("bot_sessions", {
                "salon_id": salon_id,
                "customer_phone": c["telefono"],
                "atencion": c["atencion"],
                "handoff_at": ultimo.isoformat() if c["atencion"] == "humano" else None,
                "handoff_motivo": c["motivo"],
                "ultimo_mensaje_cliente_at": ultimo_de_ella.isoformat(),
                "created_at": inicio.isoformat(),
                "updated_at": ultimo.isoformat(),
            })
            await db.insert_many("bot_messages", [
                {"salon_id": salon_id, "session_id": sesion["id"], "role": rol, "content": texto,
                 "created_at": (inicio + paso * n).isoformat()}
                for n, (rol, texto) in enumerate(c["mensajes"])
            ])
    except PostgrestError:
        logger.exception("demo: no se pudo poner el teléfono o las conversaciones de muestra en %s", salon_id)


@router.post("", response_model=DemoOut, dependencies=[publico.CUALQUIERA])
async def abrir_demo(request: Request, response: Response, settings: Settings = Depends(get_settings)) -> DemoOut:
    db = _servicio(settings)
    auth = SupabaseAuthAdminClient(settings.supabase_url, settings.supabase_secret_key)
    token = request.cookies.get(COOKIE_DEMO) or ""
    if len(token) < 32:
        token = secrets.token_urlsafe(32)
    correo = f"demo-{uuid.uuid4().hex}@{DOMINIO_DE_LAS_DEMOS}"
    ip = ip_para_el_freno(request)

    try:
        topes = await db.select("topes_de_demos", {"select": "por_ip_hora_max", "limit": "1"})
        bloqueada = ip is not None and _freno_ip.is_blocked(ip, int(topes[0]["por_ip_hora_max"]))[0]
        filas = await db.rpc("crear_demo", {"p_navegador_hash": _hash(token), "p_correo": correo,
                                            "p_puede_crear": not bloqueada})
    except (PostgrestError, IndexError, KeyError, TypeError, ValueError) as e:
        raise _caida(e) from e
    fila = filas[0] if filas else {}
    resultado = fila.get("resultado")

    if resultado in _SIN_SITIO:
        # No es un error: es que hay muchas a la vez, o esta conexión ya abrió
        # las suyas. Se dice lo mismo en los tres casos y ya.
        logger.info("demo: %s", resultado)
        return DemoOut(estado="sin_sitio", mensaje=SIN_SITIO)
    if resultado not in ("creada", "ya_tiene"):
        raise _caida(RuntimeError(str(resultado)))

    cuenta = str(fila["account_id"])
    try:
        if fila.get("auth_user_id"):
            # VOLVER: su administradora ya existe; su correo, de su ficha.
            usuarios = await db.select("users", {"select": "email", "account_id": f"eq.{cuenta}", "limit": "1"})
            correo = usuarios[0]["email"]
        else:
            # NUEVA (o una a la que se le cortó el alta a medias): su usuario.
            if resultado == "ya_tiene":
                usuarios = await db.select("users", {"select": "email", "account_id": f"eq.{cuenta}", "limit": "1"})
                correo = usuarios[0]["email"]
            # LA CONTRASEÑA DE MUESTRA (0081): se guarda para enseñarla (la
            # descarga de datos la pide). Legible a propósito: se ve en pantalla.
            contrasena = contrasena_de_muestra()
            usuario = await auth.create_confirmed_user(
                correo, contrasena, {"salon_id": str(fila["salon_id"]), "role": "admin"},
            )
            await db.rpc("enlazar_demo", {"p_cuenta": cuenta, "p_auth_user": usuario["id"],
                                          "p_contrasena": contrasena})
            # Y el PIN de fichaje de las empleadas sembradas, para que el quiosco
            # funcione (sin PIN, una ficha no ficha). La pantalla los enseña.
            for nombre, pin in PINES_DE_MUESTRA.items():
                await db.update("employees", {"salon_id": f"eq.{fila['salon_id']}", "nombre": f"eq.{nombre}"},
                                {"pin_fichaje_hash": await hashear_pin(pin)})
            await _lo_primero_que_se_ve(db, str(fila["salon_id"]))
        sesion = await _sesion(auth, correo)
    except (PostgrestError, SupabaseAuthError, KeyError, IndexError, ValueError) as e:
        if resultado == "creada":
            # Sin usuario no sirve: se deshace en el acto (solo esa, recién nacida).
            try:
                await db.rpc("deshacer_demo", {"p_cuenta": cuenta})
            except PostgrestError:
                logger.exception("demo: no se pudo deshacer %s; la recogerá el barrido", cuenta)
        raise _caida(e) from e

    if resultado == "creada" and ip is not None:
        # Entregada: ahora sí gasta una de las de esta conexión.
        _freno_ip.add_failure(ip)
    caduca = datetime.fromisoformat(str(fila["caduca_at"]).replace("Z", "+00:00"))
    segundos = max(60, int((caduca - datetime.now(timezone.utc)).total_seconds()))
    poner_cookie_de_sesion(response, COOKIE_DEMO, token, segundos)
    logger.info("demo: %s", "creada" if resultado == "creada" else "de_vuelta")
    return DemoOut(
        estado="creada" if resultado == "creada" else "de_vuelta",
        access_token=sesion.get("access_token"),
        refresh_token=sesion.get("refresh_token"),
        caduca_at=caduca,
    )


# ── El plan, dentro de la demo (0081) ──────────────────────────────────────


class PlanDeDemoIn(BaseModel):
    plan: Literal["basico", "pro", "elite"]


class PlanDeDemoOut(BaseModel):
    plan: str


@router.post("/plan", response_model=PlanDeDemoOut, dependencies=[publico.ADMINISTRADORA])
async def cambiar_plan_de_la_demo(
    payload: PlanDeDemoIn,
    user: CurrentUser = Depends(solo_en_una_demo),
    settings: Settings = Depends(get_settings),
    db_sesion: PostgrestClient = Depends(get_postgrest_client),
) -> PlanDeDemoOut:
    """Básico · Pro · Elite, al instante y SIN STRIPE. Solo dentro de una demo
    (`solo_en_una_demo`; y la base vuelve a mirarlo). Cambia `accounts.plan`, la
    misma columna que el webhook de Stripe escribe a un cliente de verdad: lo
    que se tapa en cada plan lo decide el mecanismo de siempre."""
    try:
        filas = await db_sesion.select("salons", {"select": "account_id", "id": f"eq.{user.salon_id}", "limit": "1"})
        cuenta = filas[0]["account_id"]
        await _servicio(settings).rpc("cambiar_plan_de_demo", {"p_cuenta": cuenta, "p_plan": payload.plan})
    except (PostgrestError, IndexError, KeyError) as e:
        raise _caida(e) from e
    return PlanDeDemoOut(plan=payload.plan)


# ── El panel ────────────────────────────────────────────────────────────────

CUALQUIER_ADMIN = ("owner", "support", "viewer")
SOLO_EL_OWNER = ("owner",)


class EstadoDemosOut(BaseModel):
    vivas: int
    ultima_hora: int
    caducadas: int
    vivas_max: int
    por_hora_max: int
    por_ip_hora_max: int
    barrido_encendido: bool
    tope_por_pasada: int
    # De la última hora (0082): cuántas veces no se pudo abrir una, y por qué.
    rechazos_vivas: int = 0
    rechazos_hora: int = 0
    rechazos_ip: int = 0
    fallos_al_crear: int = 0


class EnsayoFilaOut(BaseModel):
    account_id: str
    centro: str | None = None
    creada_at: datetime
    caduca_at: datetime
    entra_en_esta_pasada: bool


class CambioDeTopeOut(BaseModel):
    tope: str
    antes: int
    despues: int
    motivo: str
    hecho_por_email: str
    hecho_at: datetime


class PanelDemosOut(BaseModel):
    estado: EstadoDemosOut
    ensayo: list[EnsayoFilaOut]
    cambios: list[CambioDeTopeOut]


class CambiarTopeIn(BaseModel):
    valor: int = Field(gt=0)
    motivo: str = Field(max_length=500)

    @field_validator("motivo")
    @classmethod
    def motivo_de_verdad(cls, v: str) -> str:
        if len(v.strip()) < 10:
            raise ValueError("Hace falta un motivo escrito (10 caracteres o más).")
        return v.strip()


@router_admin.get("", response_model=PanelDemosOut)
async def panel_de_demos(
    _admin: AdminClaims = Depends(require_admin_roles(*CUALQUIER_ADMIN)),
    db: PostgrestClient = Depends(get_admin_db),
) -> PanelDemosOut:
    """Cuántas hay, los topes, el interruptor del barrido y su ENSAYO: qué
    borraría la próxima pasada, sin borrar nada."""
    try:
        estado = (await db.rpc("estado_de_las_demos", {}))[0]
        ensayo = await db.rpc("ensayo_del_barrido", {})
        cambios = await db.select("cambios_de_tope_de_demos", {
            "select": "tope,antes,despues,motivo,hecho_por_email,hecho_at", "order": "hecho_at.desc", "limit": "20"})
    except (PostgrestError, IndexError) as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudieron leer las demos.") from e
    return PanelDemosOut(estado=EstadoDemosOut(**estado), ensayo=[EnsayoFilaOut(**f) for f in ensayo],
                         cambios=[CambioDeTopeOut(**c) for c in cambios])


@router_admin.put("/topes/{tope}", response_model=PanelDemosOut)
async def cambiar_tope(
    tope: str,
    payload: CambiarTopeIn,
    request: Request,
    # SOLO EL OWNER, como el mínimo de pago.
    admin: AdminClaims = Depends(require_admin_roles(*SOLO_EL_OWNER)),
    db: PostgrestClient = Depends(get_admin_db),
) -> PanelDemosOut:
    if tope not in ("vivas_max", "por_hora_max", "por_ip_hora_max"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ese tope no existe.")
    try:
        filas = await db.rpc("cambiar_tope_de_demos", {
            "p_tope": tope, "p_valor": payload.valor, "p_motivo": payload.motivo,
            "p_admin": admin.sub, "p_admin_email": admin.email})
    except PostgrestError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudo cambiar el tope.") from e
    if filas and filas[0].get("resultado") == "ya_es":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ese ya es el tope.")
    try:
        await append_admin_log(db, admin_id=admin.sub, accion="cambiar_tope_de_demos", ip=client_ip(request),
                               detalle={"tope": tope, "valor": payload.valor})
    except PostgrestError:
        pass
    return await panel_de_demos(_admin=admin, db=db)
