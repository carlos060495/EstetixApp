"""Lo que el bot sabe del salón, en el menor número de caracteres posible.

Esto viaja en el prompt de sistema de CADA mensaje, así que cada carácter se
paga en cada turno de cada conversación de cada salón. De ahí que el formato
sea telegráfico: etiquetas cortas, precios sin decimales cuando son enteros,
horario colapsado por tramos iguales. No es tacañería estética — es la
diferencia entre una factura razonable y una que crece sola.

TODO se lee con el token del salón acuñado en la 1a (`bot_tenant_token`), es
decir CON RLS: si este módulo pidiera datos de otro salón, PostgREST no se los
daría. Nunca se usa service_role.

Los servicios se filtran por `visible_online`: si el salón ha decidido que un
servicio no se anuncia en el widget público, tampoco lo cuenta el bot. Es la
misma decisión, y sería raro que el WhatsApp contradijera a la web.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from app.core import zona as zonas
from app.core.horario_activo import (
    cargar_excepciones,
    cargar_horario,
    excepcion_de,
    tramos_activos,
)
from app.db.postgrest_client import PostgrestClient
from app.schemas.horario import HorarioSemana

# Cuántos días de festivos/cierres se cuentan hacia delante. Dos semanas cubre
# lo que un cliente pregunta por WhatsApp («¿abrís en Semana Santa?») sin meter
# en el prompt el calendario entero del año.
DIAS_DE_AVISO = 14

# Tope de servicios listados. Un salón con 60 servicios no puede meterlos todos
# en cada mensaje; con los más baratos primero se cubre la pregunta típica
# («¿cuánto cuesta un corte?») y el resto se resuelve preguntando.
MAX_SERVICIOS = 40

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


@dataclass(frozen=True)
class Conocimiento:
    """La base de conocimiento ya montada, más lo que el prompt necesita aparte."""

    nombre: str
    tono: str | None
    texto: str


def _precio(valor) -> str:
    # 25.00 -> «25 €»; 25.50 -> «25,50 €». Coma decimal porque el cliente es
    # español y el modelo copia el formato que le des.
    try:
        n = float(valor)
    except (TypeError, ValueError):
        return "?"
    return f"{int(n)} €" if n == int(n) else f"{n:.2f} €".replace(".", ",")


def _duracion(minutos) -> str:
    try:
        m = int(minutos)
    except (TypeError, ValueError):
        return "?"
    if m < 60:
        return f"{m}min"
    horas, resto = divmod(m, 60)
    return f"{horas}h" if not resto else f"{horas}h{resto}"


def _linea_horario(semana: HorarioSemana, hoy: date) -> list[str]:
    """El horario semanal, colapsando días consecutivos que coinciden.

    «lun-vie 10:00-20:00 / sáb 10:00-14:00 / dom cerrado» en vez de siete
    líneas. Además de ahorrar, se lee mejor —y el modelo repite lo que lee—.
    """
    def texto_de(dia) -> str:
        tramos = tramos_activos(_proximo(dia.dia_semana, hoy), semana)
        if not tramos:
            return "cerrado"
        return " y ".join(f"{zonas.reloj(t.inicio)}-{zonas.reloj(t.fin)}" for t in tramos)

    por_dia = [(d.dia_semana, texto_de(d)) for d in sorted(semana.dias, key=lambda d: d.dia_semana)]

    fuera: list[str] = []
    i = 0
    while i < len(por_dia):
        j = i
        while j + 1 < len(por_dia) and por_dia[j + 1][1] == por_dia[i][1]:
            j += 1
        nombre = DIAS[por_dia[i][0] - 1][:3]
        if j > i:
            nombre = f"{nombre}-{DIAS[por_dia[j][0] - 1][:3]}"
        fuera.append(f"{nombre} {por_dia[i][1]}")
        i = j + 1
    return fuera


def _proximo(dia_semana: int, hoy: date) -> date:
    """Una fecha cualquiera con ese día de la semana, para preguntarle al
    cálculo de horas activas. Se usa el horario SEMANAL puro: las excepciones
    se cuentan aparte, con su fecha y su motivo, que es como las entiende una
    persona («el día 15 cerramos»), no como un horario distinto."""
    return hoy + timedelta(days=(dia_semana - hoy.isoweekday()) % 7)


def _proximos_cierres(hoy: date, semana, excepciones) -> list[str]:
    """Festivos y cierres de las próximas dos semanas, con su motivo."""
    fuera: list[str] = []
    vistas: set[str] = set()
    for n in range(DIAS_DE_AVISO):
        dia = hoy + timedelta(days=n)
        exc = excepcion_de(dia, excepciones)
        if exc is None or exc.id in vistas:
            continue
        vistas.add(exc.id)
        motivo = f" ({exc.motivo})" if exc.motivo else ""
        cuando = (
            zonas.dia_dd_mm(exc.fecha_inicio)
            if exc.fecha_inicio == exc.fecha_fin
            else f"{zonas.dia_dd_mm(exc.fecha_inicio)}-{zonas.dia_dd_mm(exc.fecha_fin)}"
        )
        if exc.cerrado:
            fuera.append(f"{cuando} CERRADO{motivo}")
        else:
            tramos = tramos_activos(exc.fecha_inicio, semana, excepciones)
            horas = " y ".join(f"{zonas.reloj(t.inicio)}-{zonas.reloj(t.fin)}" for t in tramos) or "cerrado"
            fuera.append(f"{cuando} {horas}{motivo}")
    return fuera


async def cargar_conocimiento(db: PostgrestClient, salon_id: str) -> Conocimiento:
    """Monta la base de conocimiento del salón leyendo con su propio token.

    Si alguna consulta no devuelve nada, esa sección simplemente no aparece: un
    salón sin servicios dados de alta debe tener un bot que sabe menos, no un
    bot roto. Lo único imprescindible es el nombre.
    """
    salones = await db.select(
        "salons",
        {"select": "nombre_comercial,direccion,telefono,email,zona_horaria", "id": f"eq.{salon_id}", "limit": "1"},
    )
    salon = salones[0] if salones else {}
    # Sin centro no hay zona y no se supone ninguna: lanza.
    z = zonas.zona(salon.get("zona_horaria")) if salones else await zonas.zona_del_centro(db, salon_id)
    nombre = salon.get("nombre_comercial") or "el salón"

    config = await db.select(
        "salon_whatsapp_config",
        {"select": "bot_tono,bot_politicas", "salon_id": f"eq.{salon_id}", "limit": "1"},
    )
    tono = (config[0].get("bot_tono") if config else None) or None
    politicas = (config[0].get("bot_politicas") if config else None) or None

    servicios = await db.select(
        "services",
        {
            "select": "nombre,precio,duracion_min",
            "salon_id": f"eq.{salon_id}",
            "visible_online": "eq.true",
            "order": "precio.asc",
            "limit": str(MAX_SERVICIOS),
        },
    )

    semana = await cargar_horario(db, salon_id)
    excepciones = await cargar_excepciones(db, salon_id)
    # «Hoy» es el del CENTRO. Era `date.today()`, la fecha del servidor, que
    # vive en UTC: entre las 00:00 y las 02:00 de Madrid el bot creía que aún
    # era ayer y contestaba «¿abrís hoy?» con el horario del día anterior.
    hoy = zonas.hoy(z)

    lineas = [f"SALÓN: {nombre}"]
    if salon.get("direccion"):
        lineas.append(f"DIRECCIÓN: {salon['direccion']}")
    contacto = [c for c in (salon.get("telefono"), salon.get("email")) if c]
    if contacto:
        lineas.append(f"CONTACTO: {' · '.join(contacto)}")

    if servicios:
        lineas.append(
            "SERVICIOS (nombre · precio · duración): "
            + " | ".join(
                f"{s['nombre']} {_precio(s.get('precio'))} {_duracion(s.get('duracion_min'))}"
                for s in servicios
            )
        )

    lineas.append(f"HORARIO: {' / '.join(_linea_horario(semana, hoy))}")

    cierres = _proximos_cierres(hoy, semana, excepciones)
    if cierres:
        lineas.append(f"EXCEPCIONES PRÓXIMAS: {' / '.join(cierres)}")

    if politicas:
        lineas.append(f"POLÍTICAS DEL SALÓN: {politicas}")

    # La fecha va SIEMPRE. Sin ella el modelo no puede resolver «¿abrís
    # mañana?», y lo peor que puede hacer un bot con horarios es contestar con
    # confianza a partir de un día que se ha inventado.
    lineas.append(
        f"HOY ES: {DIAS[hoy.isoweekday() - 1]} {zonas.dia_dd_mm_aaaa(hoy)} (zona {z.key})"
    )

    return Conocimiento(nombre=nombre, tono=tono, texto="\n".join(lineas))
