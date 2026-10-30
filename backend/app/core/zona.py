"""La zona horaria de cada centro, y la ÚNICA conversión entre instante y hora local.

POR QUÉ UN SOLO SITIO. Las citas y los fichajes se guardan como instantes
(`timestamptz`); los horarios, como hora de pared del centro («abre a las
10:00»). Cada vez que una cosa se compara con la otra, o que una hora se le
enseña a alguien, hay que convertir, y la conversión depende de DÓNDE está el
centro: Las Palmas va una hora por detrás de Madrid todo el año. Hasta la 0060
la zona era `Europe/Madrid` escrita en tres sitios del backend, y en las
pantallas la del dispositivo de quien miraba; dos sitios convirtiendo por su
cuenta acaban discrepando (nos pasó con la ocupación y la agenda).

Aquí se convierte, y en ningún otro sitio: `tests/test_horas_por_un_solo_sitio.py`
recorre el código y se pone rojo si una hora se convierte, se formatea o se
parte en día y hora fuera de este módulo.

NUNCA SE SUMAN HORAS. Una zona con nombre (IANA) sabe del cambio de hora; un
«+1» fijo deja el último domingo de octubre mal durante medio año.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from app.db.postgrest_client import PostgrestClient

# La lista cerrada de la 0060. El nombre que se guarda es el IANA; lo que ve el
# salón, el de la derecha.
ZONAS: dict[str, str] = {
    "Europe/Madrid": "Península, Baleares, Ceuta y Melilla",
    "Atlantic/Canary": "Canarias",
}
POR_DEFECTO = "Europe/Madrid"

_CACHE: dict[str, ZoneInfo] = {}


class ZonaDesconocida(ValueError):
    """Una zona que no está en la lista, o un centro que no se encuentra."""


def zona(nombre: str | None) -> ZoneInfo:
    """La zona por su nombre IANA. Solo las de la lista: nada de inventar."""
    if nombre not in ZONAS:
        raise ZonaDesconocida(f"zona horaria desconocida: {nombre!r}")
    if nombre not in _CACHE:
        _CACHE[nombre] = ZoneInfo(nombre)
    return _CACHE[nombre]


async def zona_del_centro(db: PostgrestClient, salon_id: str) -> ZoneInfo:
    """La zona guardada del centro. Sin centro, LANZA: no se supone Madrid."""
    filas = await db.select("salons", {"select": "zona_horaria", "id": f"eq.{salon_id}", "limit": "1"})
    if not filas:
        raise ZonaDesconocida(f"no se encuentra el centro {salon_id}")
    return zona(filas[0].get("zona_horaria"))


def _con_zona(instante: datetime) -> datetime:
    if instante.tzinfo is None:
        # Un instante sin zona no se sabe de dónde es: convertirlo sería
        # adivinar. Se para aquí en vez de enseñar una hora equivocada.
        raise ValueError("instante sin zona horaria: no se puede convertir")
    return instante


def instante_de(texto: str | datetime) -> datetime:
    """Un instante leído de la base o de la API («…+00:00» o «…Z»), con zona."""
    if isinstance(texto, datetime):
        return _con_zona(texto)
    return _con_zona(datetime.fromisoformat(str(texto).replace("Z", "+00:00")))


def local(instante: datetime | str, z: ZoneInfo) -> datetime:
    """El instante, en la hora de pared del centro."""
    if not isinstance(z, ZoneInfo):
        # `astimezone(None)` convertiría a la zona del SERVIDOR sin avisar.
        raise ZonaDesconocida("hace falta la zona del centro para convertir una hora")
    return instante_de(instante).astimezone(z)


def a_utc(instante: datetime | str) -> datetime:
    """El instante en UTC, para guardarlo o compararlo."""
    return instante_de(instante).astimezone(timezone.utc)


def instante_local(fecha: date, hora: time, z: ZoneInfo) -> datetime:
    """La hora de pared `hora` del día `fecha` en el centro, como instante.

    Es lo contrario de `local`. El cambio de hora lo resuelve la zona: el último
    domingo de marzo las 02:30 no existen en la península y salen las 03:30; el
    último de octubre las 02:30 existen dos veces y vale la primera.
    """
    return datetime.combine(fecha, hora).replace(tzinfo=z)


def fecha_local(instante: datetime | str, z: ZoneInfo) -> date:
    """El día del centro al que pertenece el instante."""
    return local(instante, z).date()


def hora_local(instante: datetime | str, z: ZoneInfo) -> time:
    return local(instante, z).time()


def hhmm(instante: datetime | str, z: ZoneInfo) -> str:
    """«09:00», en la hora del centro."""
    return local(instante, z).strftime("%H:%M")


def fecha_dd_mm_aaaa(instante: datetime | str, z: ZoneInfo) -> str:
    return local(instante, z).strftime("%d/%m/%Y")


def fecha_y_hora(instante: datetime | str, z: ZoneInfo) -> str:
    """«20/10/2026 09:00», en la hora del centro."""
    return local(instante, z).strftime("%d/%m/%Y %H:%M")


def ahora_local(z: ZoneInfo, ahora: datetime | None = None) -> datetime:
    """Este momento, en la hora del centro."""
    return local(ahora or datetime.now(timezone.utc), z)


def hoy(z: ZoneInfo, ahora: datetime | None = None) -> date:
    """La fecha de hoy en el centro (no la del servidor, que vive en UTC)."""
    return ahora_local(z, ahora).date()


def inicio_del_dia(fecha: date, z: ZoneInfo) -> datetime:
    """La medianoche del centro de ese día, como instante."""
    return instante_local(fecha, time.min, z)


def reloj(hora: time) -> str:
    """Una hora de pared ya local (las de un horario): «10:00». No convierte nada."""
    return hora.strftime("%H:%M")


def dia_dd_mm_aaaa(fecha: date) -> str:
    """Un día de calendario del centro: «20/10/2026». No convierte nada."""
    return fecha.strftime("%d/%m/%Y")


def utc_texto(instante: datetime | str) -> str:
    """«2026-10-01 14:00 UTC», para lo que lee la plataforma, que no es de ningún
    centro. Lleva «UTC» escrito: una hora sin zona dicha se lee como local."""
    return a_utc(instante).strftime("%Y-%m-%d %H:%M UTC")


def dia_dd_mm(fecha: date) -> str:
    """Un día de calendario del centro sin año: «20/10». No convierte nada."""
    return fecha.strftime("%d/%m")


def instante_o_utc(valor: datetime | str) -> datetime:
    """Un instante de la base, de la API o de Stripe. Sin zona, se toma como UTC.

    Solo para valores que vienen de máquinas (PostgREST, Stripe), que siempre
    hablan en UTC. Nunca para una hora tecleada por una persona: esa es hora de
    pared del centro y va por `instante_desde_reloj`.
    """
    momento = valor if isinstance(valor, datetime) else datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    return momento if momento.tzinfo else momento.replace(tzinfo=timezone.utc)


def instante_desde_reloj(reloj: datetime, z: ZoneInfo) -> datetime:
    """Una hora de pared sin zona («2026-10-20T10:00») leída en la del centro."""
    if reloj.tzinfo is not None:
        return reloj
    return instante_local(reloj.date(), reloj.time(), z)


def minutos_entre(desde: time, hasta: time) -> int:
    """Minutos de reloj entre dos horas de pared del mismo día («10:00»–«14:00»).
    Un tramo de horario no cruza la medianoche ni la hora del cambio de hora."""
    return (hasta.hour * 60 + hasta.minute) - (desde.hour * 60 + desde.minute)
