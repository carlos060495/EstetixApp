"""EL MOSTRADOR NO GUARDA SESIÓN DE LA BASE (0090, 20-10-2026).

La tablet del mostrador llevaba el token de Supabase de la administradora al
alcance del JavaScript: con él se habla con PostgREST y con GoTrue sin pasar por
aquí, y el PIN de gestión solo escondía pantallas. Un dispositivo marcado como
mostrador no tiene ese token. Tiene la cookie httpOnly `__Host-mostrador`, cuyo
valor es un secreto aleatorio (en la base, solo su huella), y el token del salón
lo acuña y lo guarda el backend: la custodia del bot, por centro
(`bot_tenant_token.cliente_del_salon`). Son las piezas del modo soporte.

QUÉ CONTESTA ESA COOKIE, en este orden (`security.py`):

  1. Las rutas de `RUTAS_DEL_MOSTRADOR`: siempre. Son las 19 que piden la
     Agenda y el Fichaje, ni una más.
  2. Cualquier otra: solo con la GESTIÓN ABIERTA en ese dispositivo, y cada una
     la renueva. Diez minutos sin usarla y se cierra sola.
  3. Si no: 403 `mostrador_pide_pin`. FALLA CERRADO: una ruta que nadie ha
     puesto en la lista se rechaza, no se deja pasar «por si acaso».

La lista vive AQUÍ y solo aquí. La vigila
`tests/test_lo_que_viaja_es_lo_que_se_ensena.py`: la compara con lo que de
verdad llaman las pantallas libres de la cortina (RUTAS_LIBRES).

LA COOKIE GANA AL TOKEN. Si en un dispositivo mostrador alguien inicia sesión
«normal», las peticiones siguen llevando la cookie y el backend las sigue
tratando como mostrador: la sesión completa no se recupera por esa vía.

EL CENTRO DEL MOSTRADOR es el activo cuando se activó, y no cambia: la cabecera
del centro activo que mande el navegador no se mira.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.core.cookies_de_sesion import COOKIE_MOSTRADOR
from app.db.postgrest_client import PostgrestClient

COOKIE = COOKIE_MOSTRADOR
TABLA = "dispositivos_mostrador"

# Lo que dura la cookie. El mostrador es un dispositivo fijo: se retira con PIN,
# no caduca. 400 días es lo máximo que guarda un navegador, y SE RENUEVA CON EL
# USO (29-10-2026): caduca solo tras 400 días sin pedir nada.
SEGUNDOS_COOKIE = 400 * 24 * 3600

# Cada cuánto se apunta el uso (`usado_el`) y se renueva la cookie: no en cada
# petición, que sería una escritura por cada toque en la agenda.
MINUTOS_USO = 5

CODIGO_RETIRADO = "mostrador_retirado"
MENSAJE_RETIRADO = "Este dispositivo ya no es el mostrador: lo han retirado desde Ajustes."

# La gestión se cierra por INACTIVIDAD, no por reloj fijo: cada uso la renueva.
MINUTOS_GESTION = 10

# LO QUE CUENTA COMO USO. La manda el navegador solo en las peticiones que salen
# justo después de un toque o una tecla (`client.ts`). Lo que una pantalla pide
# sola cada pocos segundos —Conversaciones, Soporte— NO renueva la gestión: si
# renovara, con esa pantalla abierta no se cerraría nunca. Fingirla no da nada
# que no se tenga ya: hace falta la gestión abierta para que cuente.
CABECERA_USO = "X-Uso"

CODIGO_PIDE_PIN = "mostrador_pide_pin"
MENSAJE_PIDE_PIN = "Esta pantalla es de gestión. Escribe el PIN de gestión para entrar."

# ═══ LAS 19 RUTAS DEL MOSTRADOR ═══
#
# (método, ruta tal como la declara FastAPI). Lo que piden la Agenda y el
# Fichaje, medido el 20-10-2026. Añadir una aquí es decidir que el mostrador la
# usa SIN PIN: la guarda exige que una pantalla libre de verdad la llame.
RUTAS_DEL_MOSTRADOR: frozenset[tuple[str, str]] = frozenset({
    # La agenda: leer
    ("GET", "/agenda/horarios"),
    ("GET", "/agenda/por-resolver"),
    ("GET", "/agenda/alternativas"),
    ("GET", "/appointments"),
    ("GET", "/clients/para-cita"),
    ("GET", "/employees/agenda"),
    ("GET", "/salon/schedule"),
    ("GET", "/salon/schedule/exceptions"),
    ("GET", "/services"),
    ("GET", "/service-categories"),
    # La agenda: escribir
    ("POST", "/appointments"),
    ("PATCH", "/appointments/{appointment_id}"),
    ("DELETE", "/appointments/{appointment_id}"),
    ("POST", "/clients"),
    # El fichaje
    ("GET", "/fichaje/estado"),
    ("POST", "/fichaje"),
    # El marco de la pantalla: quién soy, en qué centro, la campana del bot
    ("GET", "/auth/me"),
    ("GET", "/salon/centros"),
    ("GET", "/bot/bandeja/resumen"),
})


def _ahora() -> datetime:
    # En función para que los tests muevan el reloj sin parchear datetime.
    return datetime.now(timezone.utc)


def nuevo_secreto() -> str:
    return secrets.token_urlsafe(32)


def huella(valor: str) -> str:
    return hashlib.sha256(valor.encode("utf-8")).hexdigest()


def es_ruta_del_mostrador(metodo: str, ruta) -> bool:
    """`ruta` es la de FastAPI ya resuelta (`request.scope["route"]`)."""
    return (metodo.upper(), getattr(ruta, "path", None)) in RUTAS_DEL_MOSTRADOR


@dataclass(frozen=True)
class Dispositivo:
    id: str
    # El centro del mostrador.
    salon_id: str
    # Quién lo activó: con su identidad corre cada petición.
    auth_user_id: str
    # El centro de SU FICHA, que es el que lleva su token (puede no ser el del
    # mostrador si la cuenta tiene dos).
    salon_de_la_ficha: str
    email: str | None
    gestion_hasta: datetime | None
    usado_el: datetime | None = None

    def gestion_abierta(self, ahora: datetime | None = None) -> bool:
        return self.gestion_hasta is not None and self.gestion_hasta > (ahora or _ahora())


def _fecha(valor) -> datetime | None:
    return datetime.fromisoformat(str(valor).replace("Z", "+00:00")) if valor else None


async def dispositivo_de(service_db: PostgrestClient, valor: str | None) -> Dispositivo | None:
    """El dispositivo de esa cookie, si sigue en pie. Sin cookie o retirado: None."""
    if not valor:
        return None
    filas = await service_db.select(
        TABLA,
        {
            "select": "id,salon_id,gestion_hasta,usado_el,users(auth_user_id,salon_id,email)",
            "token_hash": f"eq.{huella(valor)}",
            "retirado_el": "is.null",
            "limit": "1",
        },
    )
    if not filas:
        return None
    fila = filas[0]
    ficha = fila.get("users") or {}
    if not ficha.get("auth_user_id"):
        return None
    return Dispositivo(
        id=fila["id"],
        salon_id=fila["salon_id"],
        auth_user_id=ficha["auth_user_id"],
        salon_de_la_ficha=ficha["salon_id"],
        email=ficha.get("email"),
        gestion_hasta=_fecha(fila.get("gestion_hasta")),
        usado_el=_fecha(fila.get("usado_el")),
    )


async def retirado(service_db: PostgrestClient, valor: str) -> bool:
    """¿Es la cookie de un mostrador que alguien retiró? Para decirlo, en vez de
    un 401 mudo. Solo se pregunta cuando la cookie ya no vale."""
    filas = await service_db.select(
        TABLA, {"select": "id", "token_hash": f"eq.{huella(valor)}", "retirado_el": "not.is.null", "limit": "1"}
    )
    return bool(filas)


async def apuntar_uso(service_db: PostgrestClient, dispositivo: Dispositivo) -> bool:
    """Apunta el uso si el último tiene más de MINUTOS_USO. Devuelve si lo ha
    apuntado: es entonces cuando también se renueva la cookie. Sin poder
    escribir, no se renueva y no pasa nada más: el uso no es frontera."""
    ahora = _ahora()
    if dispositivo.usado_el and ahora - dispositivo.usado_el < timedelta(minutes=MINUTOS_USO):
        return False
    try:
        await service_db.update(TABLA, {"id": f"eq.{dispositivo.id}"}, {"usado_el": ahora.isoformat()}, select="id")
    except Exception:  # noqa: BLE001 - ver docstring
        return False
    return True


def describir(user_agent: str | None) -> str | None:
    """«Chrome en Android», a partir del User-Agent: para reconocer el
    dispositivo en la lista, no para identificarlo. El orden importa: Edge y
    Samsung se anuncian también como Chrome, y Chrome como Safari."""
    ua = user_agent or ""
    navegador = next((n for clave, n in (
        ("Edg/", "Edge"), ("SamsungBrowser", "Samsung Internet"), ("Firefox/", "Firefox"),
        ("FxiOS", "Firefox"), ("CriOS", "Chrome"), ("Chrome/", "Chrome"), ("Safari/", "Safari"),
    ) if clave in ua), None)
    sistema = next((s for clave, s in (
        ("iPad", "iPad"), ("iPhone", "iPhone"), ("Android", "Android"), ("CrOS", "ChromeOS"),
        ("Windows", "Windows"), ("Mac OS X", "Mac"), ("Linux", "Linux"),
    ) if clave in ua), None)
    if navegador and sistema:
        return f"{navegador} en {sistema}"
    return navegador or sistema


async def usar_la_gestion(service_db: PostgrestClient, dispositivo: Dispositivo, *, renovar: bool) -> bool:
    """¿Sigue abierta la gestión? Si es un USO (`renovar`), además la renueva, en
    UNA escritura: la condición va en el filtro, así que no hay hueco entre mirar
    y renovar. Si no lo es, solo se mira."""
    ahora = _ahora()
    if not renovar:
        return dispositivo.gestion_abierta(ahora)
    filas = await service_db.update(
        TABLA,
        {"id": f"eq.{dispositivo.id}", "gestion_hasta": f"gt.{ahora.isoformat()}", "retirado_el": "is.null"},
        {"gestion_hasta": (ahora + timedelta(minutes=MINUTOS_GESTION)).isoformat()},
        select="id",
    )
    return bool(filas)


async def abrir_gestion(service_db: PostgrestClient, dispositivo_id: str) -> datetime:
    hasta = _ahora() + timedelta(minutes=MINUTOS_GESTION)
    await service_db.update(TABLA, {"id": f"eq.{dispositivo_id}"}, {"gestion_hasta": hasta.isoformat()}, select="id")
    return hasta


async def cerrar_gestion(service_db: PostgrestClient, dispositivo_id: str) -> None:
    await service_db.update(TABLA, {"id": f"eq.{dispositivo_id}"}, {"gestion_hasta": None}, select="id")
