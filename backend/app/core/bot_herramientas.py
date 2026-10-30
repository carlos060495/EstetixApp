"""Las herramientas que el bot puede pedir, y lo que NUESTRO código hace con ellas.

LA REGLA DE LA FASE, en una frase: Gemini entiende lenguaje natural y redacta;
la disponibilidad y la creación de la cita las decide y ejecuta este módulo,
contra la base de datos. El modelo nunca inventa un hueco ni confirma una cita.

CÓMO SE IMPONE ESO, que es lo interesante: TODO lo que cambia algo va en dos
tiempos, un `proponer_*` y un `confirmar`.

  · `proponer_cita`, `proponer_cancelacion` y `proponer_reprogramacion` validan
    y GUARDAN lo propuesto en `bot_sessions.propuesta_pendiente`. No tocan nada.
  · `confirmar` NO RECIBE NINGÚN ARGUMENTO. Lee la propuesta que guardamos
    nosotros, la vuelve a validar entera y solo entonces ejecuta.

Que la segunda no reciba argumentos es el diseño, no un descuido:

  1. El modelo no puede alucinar los datos en el momento de ejecutar, porque no
     aporta ninguno. Lo único que comunica es «el cliente ha dicho que sí». Y
     una sola `confirmar` para las tres acciones y no tres verbos distintos: a
     qué está diciendo que sí lo sabe el servidor, no el modelo.
  2. Si nunca se propuso nada, no hay propuesta y el servidor se niega. O sea:
     sin resumen previo no se crea, ni se cancela, ni se mueve nada, y eso lo
     garantiza la máquina, no una frase del prompt.
  3. Entre el resumen y el «sí» pasan mensajes y minutos. Se revalida porque en
     ese rato el hueco puede haberse ocupado o la cita puede haber entrado en
     las dos horas que el bot ya no toca.

DE QUIÉN SON LAS CITAS: el modelo maneja `cita_id`, así que podría pasar el de
otra persona. Las consultas salen filtradas por el cliente del TELÉFONO que
escribe, que resuelve el servidor. Una cita ajena no se rechaza: no existe. Ver
la sección «Citas del cliente».

TODO SE LEE Y SE ESCRIBE con el token del salón (RLS), nunca con service_role:
`db` es el cliente del inquilino que acuña `bot_tenant_token`.

PARA AÑADIR UNA HERRAMIENTA: su declaración en `DECLARACIONES` y su ejecutor en
`_EJECUTORES`. Si cambia algo, su `proponer_*` guarda con un `tipo` nuevo y
`confirmar` lo despacha. El bucle del webhook no se toca.
"""

from __future__ import annotations

import logging
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.core.citas import (
    ESTADOS_QUE_NO_OCUPAN,
    MENSAJE_PARA_LA_CLIENTA,
    CitaNoValida,
    es_violacion_de_exclusion,
)
from app.core.disponibilidad import (
    SEPARACION_ALTERNATIVAS,
    Hueco,
    hueco_concreto,
    huecos_del_dia,
    mas_cercanos,
    parsear_fecha,
    politica_del_bot,
    primeros_huecos,
    repartidos,
)
from app.core import zona as zonas
from app.core.horario_activo import (
    cargar_excepciones,
    cargar_horario,
    tramos_activos,
)
from app.core.clientes import asegurar_cliente, buscar_por_telefono
from app.core.email_valido import normalizar_email
from app.core.telefonos import variantes_en_digitos
from app.db.postgrest_client import PostgrestClient, PostgrestError

logger = logging.getLogger(__name__)

# Cuántas horas libres se le enseñan al modelo de una vez.
#
# ES EL MANDO DE COSTE PRINCIPAL DEL BOT, y por eso es pequeño. Un día laborable
# entero en pasos de 15 min son ~39 huecos; devolverlos todos metía la jornada
# completa en el contexto de CADA mensaje de reserva, y el bot solo le ofrece al
# cliente dos o tres. Se pagaba por unas horas que nadie iba a leer.
#
# Ocho es más de lo que el bot va a ofrecer y menos de lo que cuesta dinero. Y
# no esconde nada: si el cliente ha dicho una hora, son las ocho más cercanas a
# ella; si no, ocho repartidas por el día. Ver `_horas_para_el_modelo`.
VENTANA_HUECOS = 8

# Tope de seguridad, no de presentación: acota lo que se calcula y se recorre
# aunque un salón configure una jornada absurda. La ventana de arriba es la que
# decide lo que ve el modelo.
MAX_HUECOS = 40

# Cuántas alternativas se ofrecen cuando lo pedido no está libre. Tres es lo que
# cabe en un mensaje de WhatsApp sin que parezca un listado.
MAX_ALTERNATIVAS = 3

# Cuánto vive una propuesta sin confirmar. Media hora es de sobra para una
# conversación de WhatsApp y evita que un «sí» de mañana agende la cita de hoy.
VIDA_DE_LA_PROPUESTA = timedelta(minutes=30)

# Cuánta antelación hace falta para que el BOT pueda cancelar o mover una cita.
#
# Dos horas, y por debajo de eso el bot no lo hace: deriva al salón. No es un
# capricho técnico —el motor sabría hacerlo— sino de negocio: una cancelación a
# última hora es un hueco que ya no se puede vender y una persona que quizá ya
# está preparando el material. Eso lo habla el cliente con el salón, no con una
# máquina que dice que sí a todo.
#
# Es DISTINTA de `POLITICA_BOT.antelacion_minima` (1 h), que es lo que hace
# falta para RESERVAR: reservar con poco margen solo pide que alguien mire la
# agenda; cancelar con poco margen le cuesta dinero al salón.
ANTELACION_GESTION = timedelta(hours=2)

# Cuántas citas próximas se le listan al cliente. Con más de esto el problema no
# es el bot: que llame al salón.
MAX_CITAS_LISTADAS = 5


def politica_de_gestion(salon: dict | None = None) -> timedelta:
    """La antelación que este salón exige para cancelar o mover por el bot.

    Hoy devuelve siempre lo mismo, y por eso acepta un `salon` que no usa: es el
    punto único preparado para que mañana sea una columna configurable desde
    Ajustes —cada salón tiene su política de cancelación—. Hermana de
    `disponibilidad.politica_del_bot`.
    """
    return ANTELACION_GESTION

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]

# Por qué se derivó. Los dos caminos de activación que se decidieron en el
# diseño: lo pide el cliente, o lo ofrece el bot cuando no puede ayudar y el
# cliente acepta. El modelo manda uno de estos dos; cualquier otra cosa se
# sustituye por el primero, que es el caso de largo más frecuente.
MOTIVOS_DE_DERIVACION = ("cliente_lo_pide", "bot_lo_ofrece")

# LO QUE SE LE DICE AL CLIENTE AL DERIVAR, palabra por palabra, y no un dato
# suelto con el que el modelo redacte lo que le parezca.
#
# Es una PROMESA de servicio, y una promesa mal calibrada tiene consecuencias
# reales: «te responden en breve» a las once de la noche es un cliente enfadado
# a la mañana siguiente. Lo que decide cuál de las dos sale no es el modelo sino
# el horario del salón, leído aquí. El prompt (regla 9) le manda repetir este
# texto tal cual precisamente por eso.
MENSAJE_DERIVA_ABIERTO = (
    "Ahora mismo aviso a una persona del equipo para que siga por aquí contigo 🙌 "
    "Te responden en breve."
)
MENSAJE_DERIVA_CERRADO = (
    "Aviso a una persona del equipo 🙌 En este momento el salón está cerrado, "
    "así que te responderán en cuanto abramos."
)


@dataclass(frozen=True)
class ContextoBot:
    """Quién pregunta y en nombre de qué salón. Lo fija el webhook, no el modelo.

    Que el `salon_id` y el teléfono del cliente vengan de AQUÍ y no de los
    argumentos de la herramienta es parte de la seguridad: el modelo no puede
    pedir los huecos de otro salón ni agendar a nombre de otro número, porque
    esos dos datos no están a su alcance.
    """

    db: PostgrestClient
    salon_id: str
    session_id: str
    telefono_cliente: str | None
    # La zona del centro (0060). Toda hora que el bot lee o dice es de ESE
    # centro; la fija el webhook con el salón, igual que el salón mismo.
    zona: ZoneInfo
    nombre_perfil: str | None = None

    # Las derivaciones que han ocurrido atendiendo ESTE mensaje. La lista la
    # llena `pasar_a_persona` y la vacía el webhook cuando ya ha contestado al
    # cliente, que es cuando manda el aviso por correo.
    #
    # POR QUÉ NO SE MANDA EL CORREO AQUÍ MISMO: el cliente está esperando su
    # respuesta de WhatsApp, y un viaje a Resend en mitad del bucle de
    # herramientas se lo hace esperar a él. La lista es el recado; el envío pasa
    # después, cuando ya no hay nadie mirando el reloj.
    #
    # `frozen=True` impide reasignar el campo, no mutar la lista, que es
    # exactamente lo que se quiere: nadie puede cambiar de salón a mitad de
    # camino, pero apuntar una derivación sí.
    derivaciones: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Declaraciones — lo que ve el modelo
# ---------------------------------------------------------------------------
# Las descripciones son parte del comportamiento del bot, no documentación: es
# lo único que el modelo lee para decidir cuándo llamar a cada una. De ahí que
# digan explícitamente lo que NO hacen.
DECLARACIONES: list[dict] = [
    {
        "name": "consultar_disponibilidad",
        "description": (
            "Consulta las horas REALMENTE libres de un día para un servicio. "
            "Úsala siempre antes de decirle a un cliente si hay hueco: nunca "
            "supongas ni inventes horas. Si el cliente no ha dicho qué día, "
            "pregúntaselo antes de llamar. Devuelve una selección de horas, no "
            "todas: si trae 'total_libres', hay más de las que ves, entre "
            "'primera_libre' y 'ultima_libre' — dilo así y pregunta a qué hora "
            "le viene bien, en vez de dar por hecho que solo quedan esas. "
            "OFRECE SIEMPRE LAS PRIMERAS de 'horas': si has mandado "
            "'hora_preferida' vienen ya ordenadas por lo que mejor le encaja al "
            "cliente —primero de esa hora en adelante, y solo después las de "
            "antes—, y si no, repartidas por el día."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "servicio": {
                    "type": "string",
                    "description": "El servicio tal y como lo ha pedido el cliente, p. ej. 'un corte'.",
                },
                "fecha": {
                    "type": "string",
                    "description": (
                        "El día a consultar en formato AAAA-MM-DD. Resuélvelo tú a partir "
                        "de la fecha de hoy que tienes en los DATOS DEL SALÓN ('mañana', "
                        "'el sábado')."
                    ),
                },
                "hora_preferida": {
                    "type": "string",
                    "description": (
                        "Hora aproximada que quiere el cliente, en formato HH:MM. Mándala "
                        "SIEMPRE que dé alguna pista, aunque sea vaga: 'por la tarde' → "
                        "'17:00', 'a media mañana' → '11:00', 'sobre las 6' → '18:00', y "
                        "también si te ha dicho una hora exacta. Con esto recibes las horas "
                        "libres MÁS CERCANAS a la que quiere; sin esto, unas cuantas "
                        "repartidas por el día, que es lo correcto solo si no ha dicho nada."
                    ),
                },
                "profesional": {
                    "type": "string",
                    "description": (
                        "Nombre del profesional SOLO si el cliente ha pedido a alguien en "
                        "concreto. Si le da igual, no lo mandes: las horas que se devuelven "
                        "ya son de alguien que puede atenderle, y quién es se decide al "
                        "proponer la cita."
                    ),
                },
            },
            "required": ["servicio", "fecha"],
        },
    },
    {
        "name": "proponer_cita",
        "description": (
            "Prepara una cita y devuelve su resumen para que se lo enseñes al cliente. "
            "NO crea la cita todavía. Llámala cuando ya sepas servicio, día y hora. "
            "Después enséñale el resumen y espera a que confirme."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "servicio": {"type": "string", "description": "El servicio que quiere."},
                "fecha": {"type": "string", "description": "Día en formato AAAA-MM-DD."},
                "hora": {"type": "string", "description": "Hora de inicio en formato HH:MM."},
                "profesional": {
                    "type": "string",
                    "description": "Solo si el cliente ha pedido a alguien concreto.",
                },
                "nombre_cliente": {
                    "type": "string",
                    "description": (
                        "El nombre del cliente. Mándalo SOLO si una llamada anterior te ha "
                        "respondido que falta_nombre y el cliente te lo ha dicho."
                    ),
                },
            },
            "required": ["servicio", "fecha", "hora"],
        },
    },
    {
        "name": "consultar_mis_citas",
        "description": (
            "Las citas próximas del cliente que te está escribiendo. Úsala cuando "
            "pregunte por sus citas, o cuando quiera cambiar o cancelar una y no sepas "
            "cuál: necesitas su 'cita_id'. No lleva argumentos — se buscan por su "
            "teléfono, tú no eliges de quién."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "proponer_cancelacion",
        "description": (
            "Prepara la cancelación de una cita del cliente y devuelve sus datos para "
            "que se los confirmes. NO cancela todavía. Saca el 'cita_id' de "
            "consultar_mis_citas; si tiene varias y no está claro cuál, pregúntale."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "cita_id": {
                    "type": "string",
                    "description": "El 'cita_id' que te dio consultar_mis_citas. No te lo inventes.",
                }
            },
            "required": ["cita_id"],
        },
    },
    {
        "name": "proponer_reprogramacion",
        "description": (
            "Prepara el cambio de una cita a otro día u hora y devuelve el resumen "
            "ANTES→DESPUÉS para que se lo confirmes. NO la mueve todavía. Mira antes "
            "con consultar_disponibilidad que la hora nueva esté libre. El servicio no "
            "cambia: para otro servicio hay que cancelar y reservar de nuevo."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "cita_id": {
                    "type": "string",
                    "description": "El 'cita_id' que te dio consultar_mis_citas. No te lo inventes.",
                },
                "fecha": {"type": "string", "description": "Día nuevo en formato AAAA-MM-DD."},
                "hora": {"type": "string", "description": "Hora nueva en formato HH:MM."},
                "profesional": {
                    "type": "string",
                    "description": (
                        "Solo si el cliente pide a alguien concreto para la hora nueva. Si "
                        "no lo mandas se intenta mantener a quien ya la tenía."
                    ),
                },
            },
            "required": ["cita_id", "fecha", "hora"],
        },
    },
    {
        "name": "pasar_a_persona",
        "description": (
            "Deja la conversación en manos de una persona del salón, y hace que TÚ dejes "
            "de contestar en ella hasta que esa persona la dé por terminada. Llámala "
            "cuando el cliente pida hablar con alguien del equipo, o cuando le hayas "
            "ofrecido tú avisar al salón y haya dicho que sí. Después dile al cliente "
            "EXACTAMENTE el texto de 'mensaje_para_el_cliente' que te devuelva, sin "
            "cambiarlo y sin añadirle plazos por tu cuenta: ese texto ya sabe si el salón "
            "está abierto o cerrado ahora mismo. No la llames para librarte de algo que "
            "puedes resolver tú con tus otras herramientas."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "motivo": {
                    "type": "string",
                    "description": (
                        "'cliente_lo_pide' si ha sido él quien ha pedido hablar con una "
                        "persona. 'bot_lo_ofrece' si se lo has ofrecido tú porque no podías "
                        "ayudarle y ha aceptado. Uno de esos dos exactamente."
                    ),
                }
            },
        },
    },
    {
        "name": "guardar_email_cliente",
        "description": (
            "Apunta el correo que el cliente te acaba de dar para que le llegue el "
            "recordatorio de su cita. Llámala SOLO cuando él te lo haya escrito, y "
            "pásale lo que haya escrito TAL CUAL: no lo completes, no le pongas un "
            "dominio que falte, no lo corrijas y no te inventes ninguno. Si no es un "
            "correo válido te lo dirá: entonces pídeselo UNA vez más. Si la respuesta "
            "trae 'reintentar': false, no vuelvas a pedírselo — dale las gracias, dile "
            "que no pasa nada y sigue."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "email": {
                    "type": "string",
                    "description": "El correo tal y como lo ha escrito el cliente.",
                }
            },
            "required": ["email"],
        },
    },
    {
        "name": "no_quiere_recordatorios",
        "description": (
            "Apunta que este cliente NO quiere dar su correo para los recordatorios, "
            "para no volver a preguntárselo en futuras reservas. Llámala solo si te lo "
            "ha dicho claramente ('no', 'no quiero', 'prefiero que no'). NO la llames "
            "si simplemente no ha contestado a eso, ha cambiado de tema o ha dicho algo "
            "que no entiendes: en ese caso no hagas nada, no insistas y sigue la "
            "conversación."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "confirmar",
        "description": (
            "Ejecuta DEFINITIVAMENTE lo último que propusiste: crear la cita, cancelarla "
            "o moverla. Llámala solo cuando el cliente haya dicho que sí de forma clara "
            "al resumen que le enseñaste. No lleva argumentos: se ejecuta exactamente lo "
            "que se resumió. Si quiere cambiar algo, no la llames: vuelve a proponer."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
]


# ---------------------------------------------------------------------------
# Utilidades de texto
# ---------------------------------------------------------------------------


def _normalizar(texto: str | None) -> str:
    """Minúsculas y sin acentos, para comparar lo que escribe una persona.

    «Coloración», «coloracion» y «COLORACION» tienen que ser lo mismo: el
    cliente escribe desde el móvil y el salón dio de alta el servicio con la
    tilde puesta.
    """
    if not texto:
        return ""
    plano = unicodedata.normalize("NFKD", texto.strip().lower())
    return "".join(c for c in plano if not unicodedata.combining(c))


# Palabras que no distinguen un servicio de otro. Se descartan al comparar para
# que «quiero un corte» encuentre «Corte de caballero»: sin esto, el cliente que
# escribe una frase en vez de un sustantivo no encuentra nada, y escribir frases
# es exactamente lo que hace la gente por WhatsApp.
_VACIAS = {
    "un", "una", "unos", "unas", "el", "la", "los", "las", "lo",
    "de", "del", "para", "con", "por", "en", "a", "al", "y", "o",
    "mi", "me", "te", "se", "quiero", "queria", "querria", "quisiera",
    "necesito", "pedir", "reservar", "cita", "hora", "favor", "porfa",
}


def _palabras(texto: str | None) -> set[str]:
    """Las palabras que de verdad distinguen, normalizadas."""
    crudas = "".join(c if c.isalnum() else " " for c in _normalizar(texto)).split()
    return {p for p in crudas if p not in _VACIAS and len(p) > 1}


def _puntua(consulta: str, nombre: str) -> int:
    """Cuántas palabras significativas comparten. 0 = no tienen nada que ver.

    Se puntúa en vez de decir sí/no porque la desambiguación depende del margen:
    «corte» empata con «Corte de caballero» y «Corte infantil» (1 y 1) y eso es
    ambiguo, mientras que «corte de caballero» gana 2 a 1 y no lo es. Con un
    booleano, el segundo caso también saldría ambiguo y el bot preguntaría algo
    que el cliente ya había dicho.
    """
    return len(_palabras(consulta) & _palabras(nombre))


def _fecha_bonita(dia: date) -> str:
    return f"{DIAS[dia.isoweekday() - 1]} {zonas.dia_dd_mm(dia)}"


def _en_minutos(hhmm: str | None) -> int | None:
    """«17:30» -> 1050. `None` si no hay forma de leerlo.

    Tolerante porque la hora la escribe un modelo a partir de lo que dijo una
    persona: «17», «17:30», «17.30». Si no se entiende no se rompe nada, solo se
    pierde el centrado de la ventana.
    """
    if not hhmm:
        return None
    texto = str(hhmm).strip().replace(".", ":").replace("h", ":")
    horas, _, minutos = texto.partition(":")
    try:
        h, m = int(horas), int(minutos or 0)
    except ValueError:
        return None
    return h * 60 + m if 0 <= h < 24 and 0 <= m < 60 else None


def _inicio_del_hueco(h: dict) -> datetime:
    """El instante de un hueco tal y como lo devuelve `huecos_del_dia`."""
    return datetime.fromisoformat(h["inicio"])


def _momento_o_nada(dia: date, hora: str | None, z: ZoneInfo) -> datetime | None:
    """La hora que ha dicho el cliente, en ese día, o `None`.

    No lanza: la escribe un modelo a partir de lo que dijo una persona, así que
    puede llegar cualquier cosa. Si no se entiende, se pierde el ancla —y con
    ella el afinado de la oferta—, no la respuesta.
    """
    minutos = _en_minutos(hora)
    if minutos is None:
        return None
    return zonas.a_utc(zonas.instante_local(dia, time(minutos // 60, minutos % 60), z))


def _horas_para_el_modelo(huecos: list[dict], ancla: datetime | None) -> list[str]:
    """Las `VENTANA_HUECOS` horas que de verdad le interesan al modelo.

    POR QUÉ NO SE MANDAN TODAS: un día laborable son ~39 huecos y el bot ofrece
    dos o tres. La lista entera viajaba en el contexto de cada mensaje sin que
    nadie la leyera.

    CUÁLES SE ELIGEN depende de si el cliente ha dicho una hora:

      · CON hora («sobre la una», «por la tarde») → las que mejor le encajan:
        primero de esa hora EN ADELANTE y solo después las de antes, y dentro de
        cada lado por cercanía. El orden importa: el modelo ofrece las primeras,
        así que las primeras tienen que ser las que mejor le sirven. Si pide la
        una, lo que quiere son la 13:00, la 13:15 y la 13:30 —no las 14:00 y las
        15:00, y tampoco las 12:45, que sería adelantarle la cita sin que lo
        haya pedido—. De paso, esos cuartos de hora de en medio, que son los que
        peor se llenan, dejan de quedarse sin ofrecer.
      · SIN hora («¿qué tenéis el sábado?») → repartidas por el día, separadas
        entre sí. Una petición abierta pide una oferta variada; las ocho
        primeras seguidas serían siempre la mañana y el bot diría que no tiene
        nada por la tarde con el día medio libre.

    Las dos selecciones son las mismas funciones que usan las alternativas
    (`mas_cercanos` y `repartidos`, en el motor): una sola implementación.
    """
    if len(huecos) <= VENTANA_HUECOS:
        elegidos = huecos
    elif ancla is not None:
        elegidos = mas_cercanos(huecos, VENTANA_HUECOS, ancla, clave=_inicio_del_hueco)
    else:
        elegidos = repartidos(
            huecos, VENTANA_HUECOS, SEPARACION_ALTERNATIVAS, clave=_inicio_del_hueco
        )
    return [h["hora"] for h in elegidos]


# ---------------------------------------------------------------------------
# Resolución de servicio y profesional
# ---------------------------------------------------------------------------


def _no_se_puede(e: CitaNoValida) -> dict:
    """Un «no» del motor de citas, tal y como se le puede contar a la clienta.

    ERA UNA TUBERÍA, y en siete sitios: `{"mensaje": e.mensaje}` va al modelo, y
    el modelo habla por WhatsApp con quien está reservando. Cualquier texto que
    naciera dentro del motor llegaba entero a esa conversación.

    Con la Fase B ya hay textos que nombran otro centro —«Ana ya tiene una cita
    de 16:00 a 17:00 en Centro Norte»—, y eso es información de negocio del otro
    local: a una clienta del centro 1 no le corresponde saber que Ana trabaja
    hoy en el 2, ni a qué hora, ni que el 2 existe. Se le dice que a esa hora no
    puede, y el bot sigue ofreciéndole alternativas como siempre.

    Está aquí y no repetido en cada `except` para que el octavo sitio no pueda
    equivocarse.
    """
    if e.habla_de_otro_centro:
        return {"ok": False, "error": e.codigo, "mensaje": MENSAJE_PARA_LA_CLIENTA}
    return {"ok": False, "error": e.codigo, "mensaje": e.mensaje}


async def _resolver_servicio(ctx: ContextoBot, pedido: str | None) -> dict:
    """El servicio que ha pedido el cliente, o un error que el bot pueda contar.

    `visible_online` se respeta igual que en la base de conocimiento: si el
    salón decidió que un servicio no se anuncia, el bot tampoco lo agenda.
    """
    servicios = await ctx.db.select(
        "services",
        {
            "select": "id,nombre,precio,duracion_min",
            "salon_id": f"eq.{ctx.salon_id}",
            "visible_online": "eq.true",
            "order": "nombre.asc",
        },
    )
    if not servicios:
        return {"ok": False, "error": "sin_servicios"}

    exactos = [s for s in servicios if _normalizar(s["nombre"]) == _normalizar(pedido)]
    if exactos:
        candidatos = exactos
    else:
        # Solo los que MÁS palabras comparten: si el cliente ha dicho «corte de
        # caballero», «Corte infantil» también comparte «corte», pero no compite.
        puntuados = [(s, _puntua(pedido or "", s["nombre"])) for s in servicios]
        mejor = max((p for _, p in puntuados), default=0)
        candidatos = [s for s, p in puntuados if p == mejor and p > 0]

    if len(candidatos) == 1:
        return {"ok": True, "servicio": candidatos[0]}
    if len(candidatos) > 1:
        # Ambiguo: NO se elige por el bot. «Corte» puede ser el de caballero o
        # el de niño, y adivinar significa agendar mal y cobrar otro precio.
        return {
            "ok": False,
            "error": "servicio_ambiguo",
            "candidatos": [s["nombre"] for s in candidatos],
        }
    return {
        "ok": False,
        "error": "servicio_no_ofrecido",
        "servicios_del_salon": [s["nombre"] for s in servicios],
    }


async def _resolver_profesional(ctx: ContextoBot, pedido: str | None) -> dict:
    """El profesional que ha pedido el cliente, si ha pedido a alguien.

    Devolver `{"ok": True, "empleado": None}` cuando no ha pedido a nadie es
    deliberado: «cualquiera» es una respuesta válida y no un fallo.
    """
    if not pedido or not pedido.strip():
        return {"ok": True, "empleado": None}

    empleados = await ctx.db.select(
        "employees",
        {
            "select": "id,nombre",
            "salon_id": f"eq.{ctx.salon_id}",
            "estado": "eq.activo",
            "order": "nombre.asc",
        },
    )
    # Por nombre de pila: el salón da de alta «Ana García» y el cliente escribe
    # «con Ana». Se compara palabra a palabra, igual que los servicios.
    candidatos = [e for e in empleados if _puntua(pedido, e["nombre"]) > 0]
    if not candidatos:
        # También se cae aquí quien está en pausa o de baja. No se distingue a
        # propósito: a un desconocido no se le cuenta quién está de baja.
        return {
            "ok": False,
            "error": "profesional_desconocido",
            "profesionales_del_salon": [e["nombre"] for e in empleados],
        }
    if len(candidatos) > 1:
        return {
            "ok": False,
            "error": "profesional_ambiguo",
            "candidatos": [e["nombre"] for e in candidatos],
        }
    return {"ok": True, "empleado": candidatos[0]}


async def _verificar_que_lo_realiza(ctx: ContextoBot, empleado: dict, service_id: str) -> dict | None:
    """¿Esta persona hace este servicio? Devuelve el error si no, o None si sí.

    Sin esto, pedir «coloración con Bruno» —que solo corta— acabaría en un
    genérico «no hay hueco», y el cliente creería que el problema es la agenda.
    Lo que necesita oír es que Bruno no hace ese servicio y quién sí lo hace.
    """
    enlaces = await ctx.db.select(
        "employee_services",
        {
            "select": "employee_id",
            "employee_id": f"eq.{empleado['id']}",
            "service_id": f"eq.{service_id}",
            "limit": "1",
        },
    )
    if enlaces:
        return None

    del_salon = await ctx.db.select(
        "employees",
        {
            "select": "id,nombre",
            "salon_id": f"eq.{ctx.salon_id}",
            "estado": "eq.activo",
            "order": "nombre.asc",
        },
    )
    ids = ",".join(e["id"] for e in del_salon)
    capaces = (
        await ctx.db.select(
            "employee_services",
            {
                "select": "employee_id",
                "service_id": f"eq.{service_id}",
                "employee_id": f"in.({ids})",
            },
        )
        if ids
        else []
    )
    saben = {c["employee_id"] for c in capaces}
    return {
        "ok": False,
        "error": "profesional_no_realiza",
        "profesional": empleado["nombre"],
        "profesionales_que_lo_hacen": [e["nombre"] for e in del_salon if e["id"] in saben],
    }


def _dia_fuera_de_plazo(dia: date, z: ZoneInfo) -> dict | None:
    """Lo mismo que `_fuera_de_plazo` pero para un día entero, sin hora.

    No mira la antelación mínima: preguntar por HOY es legítimo aunque las
    primeras horas ya no valgan —de eso se encarga el motor, recortando la
    mañana—. Aquí solo se rechaza el día que no tiene ningún sentido consultar.
    """
    ahora = datetime.now(timezone.utc)
    hoy = zonas.hoy(z, ahora)
    if dia < hoy:
        return {"ok": False, "error": "fecha_pasada", "hoy": hoy.isoformat()}
    politica = politica_del_bot()
    if dia > zonas.fecha_local(ahora + politica.horizonte, z):
        return {"ok": False, "error": "demasiado_lejos", "horizonte_dias": politica.horizonte.days}
    return None


def _fuera_de_plazo(inicio: datetime) -> dict | None:
    """Los límites de CUÁNDO puede reservar el bot. `None` si está en plazo.

    Se comprueba aparte y con su propio código de error en vez de dejar que el
    motor devuelva «no hay hueco»: «esa hora ya ha pasado», «necesito al menos
    una hora de margen» y «tan lejos todavía no abrimos agenda» son tres cosas
    distintas, y un cliente al que le dicen «no hay hueco» a las 17:00 de un
    martes vacío se queda sin entender nada.

    Los números salen de `politica_del_bot()`, que es el punto preparado para
    que mañana sean configurables por salón.
    """
    politica = politica_del_bot()
    ahora = datetime.now(timezone.utc)
    if inicio < ahora:
        return {"ok": False, "error": "hora_pasada"}
    if inicio < ahora + politica.antelacion_minima:
        return {
            "ok": False,
            "error": "demasiado_pronto",
            "antelacion_minima_min": int(politica.antelacion_minima.total_seconds() // 60),
        }
    if inicio > ahora + politica.horizonte:
        return {
            "ok": False,
            "error": "demasiado_lejos",
            "horizonte_dias": politica.horizonte.days,
        }
    return None


# ---------------------------------------------------------------------------
# Cliente
# ---------------------------------------------------------------------------


async def _buscar_cliente(ctx: ContextoBot) -> dict | None:
    """La ficha de este teléfono en el CRM del salón, si existe.

    La reconciliación vive en `core/clientes.py` desde que el widget público
    entra por la misma puerta: dos copias se habrían separado, y separarse aquí
    significa duplicar la ficha de una clienta según por dónde reservase.
    """
    return await buscar_por_telefono(ctx.db, ctx.salon_id, ctx.telefono_cliente)


# ---------------------------------------------------------------------------
# Alternativas
# ---------------------------------------------------------------------------


def _a_alternativa(h: Hueco) -> dict:
    """Una alternativa: cuándo, y nada más.

    SIN el profesional a propósito, y no solo por tokens: quién atiende lo
    decide `proponer_cita` en el momento de proponer, mirando la carga del día.
    Nombrar aquí a alguien sería prometer una persona que puede no ser la que
    acabe asignada un minuto después.
    """
    return {
        "fecha": h.fecha_local.isoformat(),
        "dia": _fecha_bonita(h.fecha_local),
        "hora": h.hora_local,
    }


async def _alternativas(
    ctx: ContextoBot,
    service_id: str,
    desde: date,
    employee_id: str | None,
    ancla: datetime | None = None,
    excluir: str | None = None,
) -> list[dict]:
    """Los huecos que se le ofrecen cuando lo que pidió no pudo ser. Nunca lanza.

    `ancla` es la hora que pidió el cliente, cuando la hay. Manda sobre el
    reparto: si pidió la una y la una está cogida, lo que le sirve es la 12:45 y
    la 13:15, no las 14:00 y las 15:00. Sin ancla —el día está cerrado, o
    preguntó sin decir hora— se reparten por el día para que la oferta sea
    variada.

    Que esto falle no puede tumbar la respuesta: el cliente ya se va a llevar un
    «no», y quedarse además sin contestación sería el doble de malo.
    """
    try:
        huecos = await primeros_huecos(
            ctx.db,
            ctx.salon_id,
            service_id=service_id,
            desde=desde,
            employee_id=employee_id,
            politica=politica_del_bot(),
            limite=MAX_ALTERNATIVAS,
            ancla=ancla,
            # La cita que se está moviendo no cuenta como ocupada.
            excluir=excluir,
            # Solo se usa si no hay ancla. «Las 10:00, las 10:15 o las 10:30» no
            # es una elección; ver `repartidos` en el motor.
            separacion=SEPARACION_ALTERNATIVAS,
        )
    except (CitaNoValida, PostgrestError):
        logger.exception("Bot: no se pudieron calcular alternativas.")
        return []
    return [_a_alternativa(h) for h in huecos]


# ---------------------------------------------------------------------------
# Herramienta 1 — consultar disponibilidad
# ---------------------------------------------------------------------------


async def _consultar_disponibilidad(ctx: ContextoBot, args: dict) -> dict:
    servicio = await _resolver_servicio(ctx, args.get("servicio"))
    if not servicio["ok"]:
        return servicio
    profesional = await _resolver_profesional(ctx, args.get("profesional"))
    if not profesional["ok"]:
        return profesional

    s = servicio["servicio"]
    empleado = profesional["empleado"]

    if empleado:
        no_lo_hace = await _verificar_que_lo_realiza(ctx, empleado, s["id"])
        if no_lo_hace:
            return no_lo_hace

    try:
        dia = parsear_fecha(args.get("fecha"))
    except CitaNoValida as e:
        return _no_se_puede(e)

    # El día entero fuera de plazo se contesta con su motivo. Preguntarle al
    # motor por un día de hace un mes devolvería una lista vacía, y el bot diría
    # «no queda hueco» en vez de «ese día ya pasó».
    fuera = _dia_fuera_de_plazo(dia, ctx.zona)
    if fuera:
        return fuera

    try:
        datos = await huecos_del_dia(
            ctx.db,
            ctx.salon_id,
            service_id=s["id"],
            fecha=dia,
            employee_id=empleado["id"] if empleado else None,
            politica=politica_del_bot(),
        )
    except CitaNoValida as e:
        return _no_se_puede(e)

    salida = {
        "ok": True,
        "servicio": s["nombre"],
        "precio": s.get("precio"),
        "duracion_min": s["duracion_min"],
        "fecha": datos["fecha"],
        "dia": _fecha_bonita(dia),
    }
    if empleado:
        # De quién son estas horas. Solo cuando se preguntó por alguien: en el
        # caso normal no hay nadie a quien atribuirlas todavía.
        salida["profesional"] = empleado["nombre"]

    # EL ANCLA: la hora que ha dicho el cliente, si ha dicho alguna. Es lo que
    # decide si se le ofrecen las horas más cercanas a lo que pidió o un reparto
    # variado del día. Una hora ilegible no rompe nada: se queda sin ancla.
    ancla = _momento_o_nada(dia, args.get("hora_preferida"), ctx.zona)

    if datos["cerrado"]:
        # `cerrado` solo aparece cuando lo está. Un `false` en cada consulta son
        # tokens que no dicen nada: si el salón abre, lo que se ve son horas.
        salida["cerrado"] = True
        salida["motivo"] = datos["motivo"]
        salida["alternativas"] = await _alternativas(
            ctx, s["id"], dia, empleado["id"] if empleado else None, ancla=ancla
        )
        return salida

    todas = datos["huecos"][:MAX_HUECOS]
    salida["horas"] = _horas_para_el_modelo(todas, ancla)

    if not todas:
        # Un día sin huecos no se contesta con un «no» a secas: el cliente que
        # pregunta por el sábado acepta el lunes si se lo ofreces.
        salida["alternativas"] = await _alternativas(
            ctx, s["id"], dia, empleado["id"] if empleado else None, ancla=ancla
        )
    elif len(todas) > len(salida["horas"]):
        # Se le ha recortado la lista, así que hay que decírselo: si no, el bot
        # contestaría «solo me quedan estas» cuando el día está medio vacío. Con
        # el total y los extremos puede decir la verdad («tengo desde las 10:00
        # hasta las 19:30») y pedir una hora aproximada, sin pagar la lista.
        salida["total_libres"] = len(todas)
        salida["primera_libre"] = todas[0]["hora"]
        salida["ultima_libre"] = todas[-1]["hora"]

    return salida


# ---------------------------------------------------------------------------
# Herramienta 2 — proponer (validar y guardar, sin crear)
# ---------------------------------------------------------------------------


def _momento(fecha: str | None, hora: str | None, z: ZoneInfo) -> datetime:
    """«2026-09-07» + «17:30» -> el instante en UTC.

    Se construye en hora LOCAL del salón y se convierte, que es el mismo camino
    que hace el motor de disponibilidad: si se hiciera al revés, en verano la
    cita se iría dos horas.
    """
    try:
        dia = date.fromisoformat(str(fecha).strip())
    except (ValueError, AttributeError):
        raise CitaNoValida("fecha_invalida", f"«{fecha}» no es una fecha válida.", status=400)
    try:
        h, _, m = str(hora).strip().partition(":")
        reloj = time(int(h), int(m or 0))
    except (ValueError, AttributeError):
        raise CitaNoValida("hora_invalida", f"«{hora}» no es una hora válida.", status=400)
    return zonas.a_utc(zonas.instante_local(dia, reloj, z))


async def _proponer_cita(ctx: ContextoBot, args: dict) -> dict:
    servicio = await _resolver_servicio(ctx, args.get("servicio"))
    if not servicio["ok"]:
        return servicio
    profesional = await _resolver_profesional(ctx, args.get("profesional"))
    if not profesional["ok"]:
        return profesional

    s = servicio["servicio"]
    pedido = profesional["empleado"]

    if pedido:
        no_lo_hace = await _verificar_que_lo_realiza(ctx, pedido, s["id"])
        if no_lo_hace:
            return no_lo_hace

    try:
        inicio = _momento(args.get("fecha"), args.get("hora"), ctx.zona)
        # En plazo ANTES de mirar la agenda: si la hora ya pasó o queda a tres
        # meses, el motivo del no es ese y no «está ocupado».
        fuera = _fuera_de_plazo(inicio)
        if fuera:
            return fuera
        hueco = await hueco_concreto(
            ctx.db,
            ctx.salon_id,
            service_id=s["id"],
            inicio=inicio,
            employee_id=pedido["id"] if pedido else None,
            politica=politica_del_bot(),
        )
    except CitaNoValida as e:
        return _no_se_puede(e)

    dia = zonas.fecha_local(inicio, ctx.zona)
    if hueco is None:
        return {
            "ok": False,
            "error": "hueco_no_disponible",
            # ANCLADAS a la hora que pidió: si quería la una y la una está
            # cogida, lo que le sirve es la 13:15 y la 12:45, no las 15:00.
            "alternativas": await _alternativas(
                ctx, s["id"], dia, pedido["id"] if pedido else None, ancla=inicio
            ),
        }

    # Sin preferencia, el primero de la lista: el motor ya la ordenó por carga
    # del día, así que el reparto se decide allí y no aquí.
    elegido = hueco.empleados[0]

    cliente = await _buscar_cliente(ctx)
    nombre_nuevo = (args.get("nombre_cliente") or "").strip() or None
    if cliente is None and not nombre_nuevo:
        # ÚNICO caso en que se pide el nombre. A un cliente que ya está en el
        # CRM no se le vuelve a preguntar cómo se llama.
        return {"ok": False, "error": "falta_nombre"}

    ahora = datetime.now(timezone.utc)
    propuesta = {
        "service_id": s["id"],
        "servicio": s["nombre"],
        "precio": s.get("precio"),
        "duracion_min": s["duracion_min"],
        "employee_id": elegido.id,
        "profesional": elegido.nombre,
        "inicio": hueco.inicio.isoformat(),
        "fin": hueco.fin.isoformat(),
        "client_id": cliente["id"] if cliente else None,
        "nombre_cliente": (cliente["nombre"] if cliente else nombre_nuevo),
    }
    await _guardar_propuesta(ctx, {"tipo": "reserva", **propuesta})

    return {
        "ok": True,
        "pendiente_de_confirmacion": True,
        "resumen": {
            "servicio": s["nombre"],
            "fecha": dia.isoformat(),
            "dia": _fecha_bonita(dia),
            "hora": hueco.hora_local,
            "profesional": elegido.nombre,
            "precio": s.get("precio"),
            "duracion_min": s["duracion_min"],
            "cliente": propuesta["nombre_cliente"],
        },
    }


# ---------------------------------------------------------------------------
# Herramienta 3 — confirmar (crear de verdad)
# ---------------------------------------------------------------------------


async def _guardar_propuesta(ctx: ContextoBot, propuesta: dict) -> None:
    """Deja apuntado lo que se le acaba de resumir al cliente.

    PUNTO ÚNICO donde se escribe `propuesta_pendiente`, y por eso es quien pone
    la caducidad: si cada `proponer_*` se acordara de ponerla por su cuenta, el
    día que se añada el cuarto se olvidará, y una propuesta sin caducidad
    convierte un «sí» de mañana en la cita de hoy.

    `tipo` dice qué se ejecuta al confirmar (reserva / cancelacion /
    reprogramacion). Lo pone quien propone, nunca el modelo.
    """
    await ctx.db.update(
        "bot_sessions",
        {"id": f"eq.{ctx.session_id}"},
        {
            "propuesta_pendiente": {
                **propuesta,
                "expira_en": (datetime.now(timezone.utc) + VIDA_DE_LA_PROPUESTA).isoformat(),
            }
        },
    )


async def _leer_propuesta(ctx: ContextoBot) -> dict | None:
    filas = await ctx.db.select(
        "bot_sessions",
        {"select": "propuesta_pendiente", "id": f"eq.{ctx.session_id}", "limit": "1"},
    )
    return (filas[0].get("propuesta_pendiente") if filas else None) or None


async def _olvidar_propuesta(ctx: ContextoBot) -> None:
    try:
        await ctx.db.update(
            "bot_sessions", {"id": f"eq.{ctx.session_id}"}, {"propuesta_pendiente": None}
        )
    except PostgrestError:
        # Dejarla puesta es feo pero inofensivo: caduca sola, y todo el que la
        # lee la revalida antes de usarla.
        logger.exception("Bot: no se pudo limpiar la propuesta pendiente.")


async def _asegurar_cliente(ctx: ContextoBot, propuesta: dict) -> str | None:
    """El id del cliente al que se enlaza la cita; se crea si hace falta.

    Compartido con el widget público (`core/clientes.py`). Incluida la parte que
    parece de más y no lo es: se vuelve a buscar antes de crear aunque la
    propuesta traiga `client_id` a null, porque entre proponer y confirmar la
    recepcionista puede haber dado de alta a esa misma persona desde el panel.
    """
    return await asegurar_cliente(
        ctx.db,
        ctx.salon_id,
        telefono_e164=ctx.telefono_cliente,
        nombre=propuesta.get("nombre_cliente"),
        client_id=propuesta.get("client_id"),
    )


async def _confirmar(ctx: ContextoBot, args: dict) -> dict:
    """Ejecuta lo que se resumió: crear, cancelar o mover.

    `args` SE IGNORA A PROPÓSITO —ver la cabecera del módulo—: lo que se ejecuta
    sale de la propuesta que guardamos nosotros, no de lo que diga el modelo.
    Eso vale igual para las tres acciones: tampoco puede cancelar «otra» cita
    que la que se le enseñó al cliente.

    UNA SOLA HERRAMIENTA para las tres y no tres «confirmar_x»: el cliente dice
    «sí» una vez, y a qué está diciendo que sí lo sabe el servidor, no el
    modelo. Con tres verbos, elegir mal el verbo sería elegir mal la acción.
    """
    propuesta = await _leer_propuesta(ctx)
    if not propuesta:
        # No se ha resumido nada, así que no hay nada que confirmar. Es el gate:
        # aunque el modelo llamara aquí de primeras, no puede tocar nada.
        return {"ok": False, "error": "sin_propuesta"}

    expira = propuesta.get("expira_en")
    if expira and datetime.fromisoformat(expira) < datetime.now(timezone.utc):
        await _olvidar_propuesta(ctx)
        return {"ok": False, "error": "propuesta_caducada"}

    # `reserva` por defecto: las conversaciones que ya estaban en marcha cuando
    # se desplegó esta versión tienen propuestas guardadas SIN `tipo`, y todas
    # ellas son reservas. Sin esto, a esa gente le fallaría el «sí».
    tipo = propuesta.get("tipo") or "reserva"

    if tipo == "cancelacion":
        resultado = await _ejecutar_cancelacion(ctx, propuesta)
    elif tipo == "reprogramacion":
        resultado = await _ejecutar_reprogramacion(ctx, propuesta)
    elif tipo == "reserva":
        return await _ejecutar_reserva(ctx, propuesta)
    else:
        logger.error("Bot: propuesta guardada con un tipo desconocido: %s", tipo)
        await _olvidar_propuesta(ctx)
        return {"ok": False, "error": "sin_propuesta"}

    # La propuesta se consume gane o pierda: un segundo «sí» no puede volver a
    # cancelar ni volver a mover. Si falló, el modelo tendrá que proponer otra
    # vez, que es exactamente lo que queremos.
    await _olvidar_propuesta(ctx)
    return resultado


async def _ejecutar_reserva(ctx: ContextoBot, propuesta: dict) -> dict:
    inicio = datetime.fromisoformat(propuesta["inicio"])
    dia = zonas.fecha_local(inicio, ctx.zona)

    # RE-VALIDACIÓN. Aunque el modelo «sepa» que estaba libre, aquí se vuelve a
    # preguntar a la base de datos: entre el resumen y el sí han pasado minutos.
    try:
        sigue_libre = await hueco_concreto(
            ctx.db,
            ctx.salon_id,
            service_id=propuesta["service_id"],
            inicio=inicio,
            employee_id=propuesta["employee_id"],
            politica=politica_del_bot(),
        )
    except CitaNoValida as e:
        await _olvidar_propuesta(ctx)
        return _no_se_puede(e)

    if sigue_libre is None:
        await _olvidar_propuesta(ctx)
        return {
            "ok": False,
            "error": "hueco_ocupado",
            "alternativas": await _alternativas(
                ctx, propuesta["service_id"], dia, None, ancla=inicio
            ),
        }

    client_id = await _asegurar_cliente(ctx, propuesta)

    # RED DE SEGURIDAD. No debería pasar —`proponer_cita` no guarda una
    # propuesta de cliente nuevo sin nombre—, pero si alguna vez se cuela, la
    # cita entraría sin ficha y el salón se quedaría SIN forma de llamar a quien
    # la pidió. Antes que eso, el teléfono va en las notas, que la agenda pinta.
    notas = None if client_id else f"Reserva por WhatsApp · tel. {ctx.telefono_cliente or '?'}"
    if not client_id:
        logger.warning("Bot: cita creada sin ficha de cliente; el teléfono va en las notas.")

    try:
        cita = await ctx.db.insert(
            "appointments",
            {
                "salon_id": ctx.salon_id,
                "client_id": client_id,
                "employee_id": propuesta["employee_id"],
                "service_id": propuesta["service_id"],
                "inicio": propuesta["inicio"],
                "fin": propuesta["fin"],
                "notas": notas,
                # MISMO CASO QUE EL WIDGET: si el teléfono ya está fichado, la
                # cita se enlaza a esa ficha y el nombre que dio quien escribe
                # se perdía. Alguien que reserva por WhatsApp para otra persona
                # dejaba al salón esperando a quien no venía.
                "nombre_reserva": (propuesta.get("nombre_cliente") or "").strip() or None,
                # Confirmada y no pendiente: la decisión de producto es que el
                # salón NO aprueba la cita del bot. Queda agendada al momento y
                # bloquea el hueco, como cualquier otra.
                "estado": "confirmada",
                "canal": "whatsapp",
                # El ORIGEN: no es lo mismo que `canal`, que también vale para
                # una reserva de WhatsApp apuntada a mano por la recepcionista.
                "bot_session_id": ctx.session_id,
            },
        )
    except PostgrestError as e:
        if es_violacion_de_exclusion(e):
            # Ha perdido la carrera: dos clientes pedían el mismo hueco y el
            # candado de la migración 0030 ha dejado pasar solo al otro. Este es
            # el caso que la comprobación de arriba NO puede cubrir, porque
            # entre mirar e insertar cabe otra petición entera.
            logger.info("Bot: hueco ocupado por otra reserva simultánea.")
            await _olvidar_propuesta(ctx)
            return {
                "ok": False,
                "error": "hueco_ocupado",
                "alternativas": await _alternativas(
                ctx, propuesta["service_id"], dia, None, ancla=inicio
            ),
            }
        raise

    await _olvidar_propuesta(ctx)
    resultado = {
        "ok": True,
        "cita_creada": True,
        "cita": {
            "id": cita.get("id"),
            "servicio": propuesta["servicio"],
            "fecha": dia.isoformat(),
            "dia": _fecha_bonita(dia),
            "hora": zonas.hhmm(inicio, ctx.zona),
            "profesional": propuesta["profesional"],
            "precio": propuesta.get("precio"),
            "duracion_min": propuesta.get("duracion_min"),
        },
    }

    # EL RECORDATORIO POR CORREO SE OFRECE AQUÍ, Y AQUÍ QUIERE DECIR DESPUÉS.
    #
    # La cita ya está insertada y la propuesta ya está consumida: lo que ocurra
    # de esta línea para abajo no puede deshacer nada de eso. Es deliberado —
    # ofrecer el correo ANTES habría metido una pregunta opcional en mitad de la
    # única cosa que la persona vino a hacer, y un fallo al mirarlo habría
    # convertido una cita creada en un «no he podido».
    #
    # Si no toca ofrecerlo, la clave no aparece y el modelo confirma la cita
    # como siempre: la respuesta de la reserva no cambia de forma para nadie.
    ofrecimiento = await _ofrecimiento_de_recordatorio(ctx, client_id)
    if ofrecimiento:
        resultado["ofrecer_recordatorio"] = ofrecimiento

    # Y SI ES LA PRIMERA VEZ DE ESTA PERSONA, LA DIRECCIÓN. Mismo sitio y mismo
    # motivo que el ofrecimiento de arriba: después de que la cita esté hecha,
    # para que un fallo aquí no convierta una cita creada en un «no he podido».
    primera = await _direccion_si_es_su_primera_vez(ctx, client_id)
    if primera:
        resultado["primera_vez"] = primera
    return resultado


# ---------------------------------------------------------------------------
# Citas del cliente — consultar, cancelar, mover
# ---------------------------------------------------------------------------
#
# ══ DÓNDE ESTÁ LA SEGURIDAD DE TODO ESTO ══
#
# El modelo maneja `cita_id`, así que podría pasar el id de la cita de otra
# persona: se lo inventa, lo arrastra de otra conversación o alguien se lo dicta.
# La defensa NO es comprobarlo después: es que las consultas SALEN filtradas por
# el cliente del teléfono que escribe (`client_id=eq.…`), que lo resuelve el
# servidor y el modelo no puede tocar.
#
# O sea: una cita ajena no es que se rechace, es que NO EXISTE para este
# teléfono. No hay orden de comprobaciones que se pueda colar ni `if` que se
# pueda olvidar, porque la fila no llega a leerse. Y como el filtro va además
# por `salon_id`, un salón tampoco puede tocar las citas de otro.


async def _mis_citas_proximas(ctx: ContextoBot) -> list[dict]:
    """Las citas futuras y vivas de QUIEN ESCRIBE. Nunca las de otro."""
    cliente = await _buscar_cliente(ctx)
    if cliente is None:
        return []

    filas = await ctx.db.select(
        "appointments",
        {
            "select": "id,client_id,inicio,fin,estado,service_id,employee_id",
            "salon_id": f"eq.{ctx.salon_id}",
            "client_id": f"eq.{cliente['id']}",
            "estado": f"not.in.({','.join(ESTADOS_QUE_NO_OCUPAN)})",
            "inicio": f"gte.{datetime.now(timezone.utc).isoformat()}",
            "order": "inicio.asc",
            "limit": str(MAX_CITAS_LISTADAS),
        },
    )
    return await _con_nombres(ctx, filas)


async def _con_nombres(ctx: ContextoBot, citas: list[dict]) -> list[dict]:
    """Añade servicio y profesional a unas citas, en dos consultas.

    Se resuelve así y no con un embed de PostgREST para que el bot no dependa de
    la forma anidada: aquí solo hacen falta dos nombres y un precio, y una lista
    de ids es más fácil de leer, de cachear y de probar.
    """
    if not citas:
        return []

    def _ids(campo: str) -> str:
        return ",".join({c[campo] for c in citas if c.get(campo)})

    servicios, empleados = {}, {}
    if _ids("service_id"):
        servicios = {
            s["id"]: s
            for s in await ctx.db.select(
                "services",
                {
                    "select": "id,nombre,precio,duracion_min",
                    "salon_id": f"eq.{ctx.salon_id}",
                    "id": f"in.({_ids('service_id')})",
                },
            )
        }
    if _ids("employee_id"):
        empleados = {
            e["id"]: e
            for e in await ctx.db.select(
                "employees",
                {
                    "select": "id,nombre",
                    "salon_id": f"eq.{ctx.salon_id}",
                    "id": f"in.({_ids('employee_id')})",
                },
            )
        }

    for c in citas:
        c["_servicio"] = servicios.get(c.get("service_id")) or {}
        c["_empleado"] = empleados.get(c.get("employee_id")) or {}
    return citas


def _a_cita_legible(cita: dict, z: ZoneInfo) -> dict:
    """La cita en la forma que se le pasa al modelo, en la hora del centro."""
    dia = zonas.fecha_local(cita["inicio"], z)
    return {
        "cita_id": cita["id"],
        "servicio": cita["_servicio"].get("nombre"),
        "fecha": dia.isoformat(),
        "dia": _fecha_bonita(dia),
        "hora": zonas.hhmm(cita["inicio"], z),
        "profesional": cita["_empleado"].get("nombre"),
        "precio": cita["_servicio"].get("precio"),
    }


async def _cita_propia(ctx: ContextoBot, cita_id: str | None) -> dict | None:
    """Esa cita, SI es de quien escribe y todavía se puede tocar. Si no, `None`.

    Ver la cabecera de esta sección: el filtro por `client_id` es la seguridad,
    no un atajo. Un id de la cita de otra persona no devuelve nada.
    """
    if not cita_id or not str(cita_id).strip():
        return None
    cliente = await _buscar_cliente(ctx)
    if cliente is None:
        return None

    filas = await ctx.db.select(
        "appointments",
        {
            "select": "id,client_id,inicio,fin,estado,service_id,employee_id",
            "id": f"eq.{str(cita_id).strip()}",
            "salon_id": f"eq.{ctx.salon_id}",
            "client_id": f"eq.{cliente['id']}",
            "estado": f"not.in.({','.join(ESTADOS_QUE_NO_OCUPAN)})",
            "limit": "1",
        },
    )
    if not filas:
        return None
    return (await _con_nombres(ctx, filas))[0]


def _solo_esta_cita(ctx: ContextoBot, cita: dict) -> dict:
    """El WHERE de un UPDATE sobre una cita, con la propiedad puesta.

    La propiedad va TAMBIÉN aquí y no solo en la lectura de antes: entre leer y
    escribir cabe otra petición entera, y es lo que hace que no exista ninguna
    ventana en la que se pueda tocar una cita ajena. Es la misma idea que el
    `salon_id` en el WHERE de todos los endpoints del panel: la RLS ya protege,
    y el filtro explícito también.
    """
    donde = {"id": f"eq.{cita['id']}", "salon_id": f"eq.{ctx.salon_id}"}
    if cita.get("client_id"):
        donde["client_id"] = f"eq.{cita['client_id']}"
    return donde


def _demasiado_tarde(inicio: datetime) -> dict | None:
    """¿Queda tan poco para la cita que esto ya no lo hace el bot?

    Devuelve el error listo para el modelo, que lo convertirá en la derivación
    al salón —teléfono y dirección—, igual que hacía la v1 con cualquier cambio.
    """
    margen = politica_de_gestion()
    if inicio - datetime.now(timezone.utc) > margen:
        return None
    return {
        "ok": False,
        "error": "demasiado_tarde_para_cambiar",
        "horas_de_antelacion": int(margen.total_seconds() // 3600),
    }


async def _consultar_mis_citas(ctx: ContextoBot, args: dict) -> dict:
    """`args` se ignora: de quién son las citas lo decide el teléfono, no el modelo."""
    citas = await _mis_citas_proximas(ctx)
    return {"ok": True, "citas": [_a_cita_legible(c, ctx.zona) for c in citas]}


# ---------------------------------------------------------------------------
# Cancelar
# ---------------------------------------------------------------------------


async def _proponer_cancelacion(ctx: ContextoBot, args: dict) -> dict:
    cita = await _cita_propia(ctx, args.get("cita_id"))
    if cita is None:
        # No existe, no es suya, ya está cancelada o ya pasó. No se distingue a
        # propósito: decirle a quien pregunta por un id ajeno «esa cita existe
        # pero no es tuya» ya sería contarle algo de otra persona.
        return {"ok": False, "error": "cita_no_encontrada"}

    inicio = datetime.fromisoformat(cita["inicio"])
    tarde = _demasiado_tarde(inicio)
    if tarde:
        return tarde

    legible = _a_cita_legible(cita, ctx.zona)
    await _guardar_propuesta(ctx, {"tipo": "cancelacion", "cita_id": cita["id"], "cita": legible})
    return {"ok": True, "pendiente_de_confirmacion": True, "cancelar": legible}


async def _ejecutar_cancelacion(ctx: ContextoBot, propuesta: dict) -> dict:
    # Se vuelve a leer con el filtro de propiedad puesto: entre el resumen y el
    # «sí» han pasado minutos, y en ese rato el salón puede haberla cancelado ya
    # o la cita puede haber entrado en las dos horas.
    cita = await _cita_propia(ctx, propuesta.get("cita_id"))
    if cita is None:
        return {"ok": False, "error": "cita_no_encontrada"}

    tarde = _demasiado_tarde(datetime.fromisoformat(cita["inicio"]))
    if tarde:
        return tarde

    filas = await ctx.db.update(
        "appointments",
        _solo_esta_cita(ctx, cita),
        # El hueco se libera solo: el candado de la 0030 y el motor de huecos
        # leen los dos `ESTADOS_QUE_NO_OCUPAN`, y `cancelada` está dentro.
        {"estado": "cancelada", "bot_session_id": ctx.session_id},
    )
    if not filas:
        return {"ok": False, "error": "cita_no_encontrada"}

    return {"ok": True, "cita_cancelada": True, "cita": _a_cita_legible(cita, ctx.zona)}


# ---------------------------------------------------------------------------
# Reprogramar — mover la cita, no borrarla y crear otra
# ---------------------------------------------------------------------------
# Se hace con UPDATE de la MISMA fila: conserva el id, el historial y el enlace
# con el cliente. Cancelar y volver a crear dejaría en la agenda del salón una
# cita cancelada y otra nueva por cada cambio de hora, que es ruido, y perdería
# la traza de que son la misma cita.


async def _hueco_para_mover(
    ctx: ContextoBot, cita: dict, inicio: datetime, pedido: dict | None
) -> Hueco | None:
    """El hueco nuevo, intentando conservar al mismo profesional.

    ORDEN DELIBERADO: si el cliente no pide a nadie, primero se mira si quien ya
    tenía la cita está libre a la hora nueva. Cambiar de persona al mover la
    hora es una sorpresa que nadie ha pedido —y en un salón la persona importa—,
    así que solo se cambia cuando no queda otra.

    `excluir` deja fuera la propia cita que se está moviendo: sin eso, correrla
    quince minutos sería imposible porque se chocaría consigo misma.
    """
    quien = pedido["id"] if pedido else cita.get("employee_id")
    comun = dict(
        service_id=cita["service_id"],
        inicio=inicio,
        politica=politica_del_bot(),
        excluir=cita["id"],
    )
    if quien:
        hueco = await hueco_concreto(ctx.db, ctx.salon_id, employee_id=quien, **comun)
        if hueco or pedido:
            # Con profesional PEDIDO no se busca a otro: el cliente ha elegido.
            return hueco
    return await hueco_concreto(ctx.db, ctx.salon_id, employee_id=None, **comun)


async def _proponer_reprogramacion(ctx: ContextoBot, args: dict) -> dict:
    cita = await _cita_propia(ctx, args.get("cita_id"))
    if cita is None:
        return {"ok": False, "error": "cita_no_encontrada"}

    antes = _a_cita_legible(cita, ctx.zona)
    tarde = _demasiado_tarde(datetime.fromisoformat(cita["inicio"]))
    if tarde:
        return tarde

    profesional = await _resolver_profesional(ctx, args.get("profesional"))
    if not profesional["ok"]:
        return profesional
    pedido = profesional["empleado"]
    if pedido:
        no_lo_hace = await _verificar_que_lo_realiza(ctx, pedido, cita["service_id"])
        if no_lo_hace:
            return no_lo_hace

    try:
        nuevo = _momento(args.get("fecha"), args.get("hora"), ctx.zona)
        # Los MISMOS límites que reservar: una hora de antelación y tres
        # semanas de horizonte. Mover una cita no es una puerta trasera para
        # saltárselos.
        fuera = _fuera_de_plazo(nuevo)
        if fuera:
            return fuera
        hueco = await _hueco_para_mover(ctx, cita, nuevo, pedido)
    except CitaNoValida as e:
        return _no_se_puede(e)

    dia = zonas.fecha_local(nuevo, ctx.zona)
    if hueco is None:
        return {
            "ok": False,
            "error": "hueco_no_disponible",
            "alternativas": await _alternativas(
                ctx,
                cita["service_id"],
                dia,
                pedido["id"] if pedido else None,
                ancla=nuevo,
                excluir=cita["id"],
            ),
        }

    elegido = hueco.empleados[0]
    despues = {
        "servicio": antes["servicio"],
        "fecha": dia.isoformat(),
        "dia": _fecha_bonita(dia),
        "hora": hueco.hora_local,
        "profesional": elegido.nombre,
        "precio": antes["precio"],
    }
    await _guardar_propuesta(
        ctx,
        {
            "tipo": "reprogramacion",
            "cita_id": cita["id"],
            "inicio": hueco.inicio.isoformat(),
            "fin": hueco.fin.isoformat(),
            "employee_id": elegido.id,
            "antes": antes,
            "despues": despues,
        },
    )
    return {
        "ok": True,
        "pendiente_de_confirmacion": True,
        "antes": antes,
        "despues": despues,
        # Que el modelo pueda decirlo si pasa: «te la muevo, pero te atenderá
        # Bruno». Cambiar de persona sin avisar se nota al llegar al salón.
        "cambia_de_profesional": antes["profesional"] != despues["profesional"],
    }


async def _ejecutar_reprogramacion(ctx: ContextoBot, propuesta: dict) -> dict:
    cita = await _cita_propia(ctx, propuesta.get("cita_id"))
    if cita is None:
        return {"ok": False, "error": "cita_no_encontrada"}

    tarde = _demasiado_tarde(datetime.fromisoformat(cita["inicio"]))
    if tarde:
        return tarde

    nuevo = datetime.fromisoformat(propuesta["inicio"])
    dia = zonas.fecha_local(nuevo, ctx.zona)

    # RE-VALIDACIÓN, igual que al crear: entre el resumen y el «sí» el hueco
    # nuevo puede haberse ocupado.
    try:
        sigue_libre = await hueco_concreto(
            ctx.db,
            ctx.salon_id,
            service_id=cita["service_id"],
            inicio=nuevo,
            employee_id=propuesta["employee_id"],
            politica=politica_del_bot(),
            excluir=cita["id"],
        )
    except CitaNoValida as e:
        return _no_se_puede(e)

    if sigue_libre is None:
        return {
            "ok": False,
            "error": "hueco_ocupado",
            "alternativas": await _alternativas(
                ctx, cita["service_id"], dia, None, ancla=nuevo, excluir=cita["id"]
            ),
        }

    try:
        filas = await ctx.db.update(
            "appointments",
            _solo_esta_cita(ctx, cita),
            {
                "inicio": propuesta["inicio"],
                "fin": propuesta["fin"],
                "employee_id": propuesta["employee_id"],
                # La conversación que la movió pasa a ser la enlazada: si el
                # salón pregunta por qué cambió de hora, el rastro lleva aquí.
                "bot_session_id": ctx.session_id,
            },
        )
    except PostgrestError as e:
        if es_violacion_de_exclusion(e):
            # Carrera perdida contra otra reserva: el candado de la 0030 vale
            # también en el UPDATE, así que la cita NO se ha movido.
            logger.info("Bot: el hueco nuevo se ocupó al mover la cita.")
            return {
                "ok": False,
                "error": "hueco_ocupado",
                "alternativas": await _alternativas(
                    ctx, cita["service_id"], dia, None, ancla=nuevo, excluir=cita["id"]
                ),
            }
        raise

    if not filas:
        return {"ok": False, "error": "cita_no_encontrada"}

    return {
        "ok": True,
        "cita_movida": True,
        "antes": propuesta.get("antes"),
        "despues": propuesta.get("despues"),
    }


# ---------------------------------------------------------------------------
# Despacho
# ---------------------------------------------------------------------------
# Paso a persona
# ---------------------------------------------------------------------------


async def _abierto_ahora(ctx: ContextoBot) -> bool:
    """¿Está el salón atendiendo en este preciso momento?

    Se pregunta al MISMO sitio que la agenda y el endpoint de citas
    (`core/horario_activo.py`), festivos y cierres puntuales incluidos: sería
    absurdo que el bot dijera «te responden en breve» un 15 de agosto porque
    mira un horario distinto del que mira el resto de la aplicación.

    El fin del tramo va excluido, igual que en `Tramo`: a la hora de cierre el
    salón ya no atiende.

    El horario se guarda en hora LOCAL del centro, así que «ahora» se pasa a esa
    hora con la conversión de `core/zona.py`, la de todos: con la zona del
    centro y no la del servidor, que vive en UTC.
    """
    ahora = datetime.now(timezone.utc)
    semana = await cargar_horario(ctx.db, ctx.salon_id)
    excepciones = await cargar_excepciones(ctx.db, ctx.salon_id)
    reloj = zonas.hora_local(ahora, ctx.zona)
    return any(
        t.inicio <= reloj < t.fin
        for t in tramos_activos(zonas.hoy(ctx.zona, ahora), semana, excepciones)
    )


async def _pasar_a_persona(ctx: ContextoBot, args: dict) -> dict:
    """Deja la conversación en manos del salón y calla al bot.

    ES LA ÚNICA HERRAMIENTA QUE NO VA EN DOS TIEMPOS, y a propósito: el patrón
    `proponer_*` + `confirmar` existe para que nada IRREVERSIBLE ocurra sin un
    «sí» explícito del cliente. Derivar no crea, no cancela y no mueve nada; se
    deshace desde la bandeja en un clic, y pedirle confirmación a alguien que
    acaba de pedir hablar con una persona sería un obstáculo justo donde peor
    cae.

    IDEMPOTENTE: si ya estaba derivada no se toca `handoff_at`. Reponerlo
    mandaría la conversación al final de la cola de la bandeja cada vez que se
    insistiera, que es lo contrario de lo que tiene que pasar: quien más lleva
    esperando va primero.

    Al derivar se limpian las marcas del desenlace anterior (`atendida_por`,
    `resuelta_at`, `expirada_at`). Una conversación que se resolvió en marzo y
    se vuelve a derivar hoy es una derivación NUEVA: dejar puesto quién la
    atendió entonces haría que la bandeja se la asignara a esa persona sin que
    nadie la haya cogido.
    """
    motivo = args.get("motivo")
    if motivo not in MOTIVOS_DE_DERIVACION:
        # El modelo se inventa valores de vez en cuando y esto es una etiqueta
        # de estadística, no una decisión: se cae al caso frecuente en vez de
        # fallar y dejar al cliente sin derivar por un texto mal escrito.
        motivo = MOTIVOS_DE_DERIVACION[0]

    abierto = await _abierto_ahora(ctx)

    filas = await ctx.db.select(
        "bot_sessions",
        {"select": "atencion", "id": f"eq.{ctx.session_id}", "limit": "1"},
    )
    if not (filas and filas[0].get("atencion") == "humano"):
        # Solo se apunta en esta rama: si ya estaba derivada, el salón ya recibió
        # su aviso y quien insiste no tiene por qué generar otro.
        ctx.derivaciones.append({"motivo": motivo, "session_id": ctx.session_id})
        await ctx.db.update(
            "bot_sessions",
            {"id": f"eq.{ctx.session_id}"},
            {
                "atencion": "humano",
                "handoff_at": datetime.now(timezone.utc).isoformat(),
                "handoff_motivo": motivo,
                "atendida_por": None,
                "resuelta_at": None,
                "expirada_at": None,
            },
        )

    return {
        "ok": True,
        "abierto": abierto,
        "mensaje_para_el_cliente": (
            MENSAJE_DERIVA_ABIERTO if abierto else MENSAJE_DERIVA_CERRADO
        ),
    }


# ---------------------------------------------------------------------------
# El correo para el recordatorio
# ---------------------------------------------------------------------------
#
# QUIÉN DECIDE QUÉ, que aquí se separa más que en ninguna otra herramienta:
#
#   · SI SE OFRECE lo decide el SERVIDOR, en `_ofrecimiento_de_recordatorio` y
#     después de que la cita ya exista. El modelo no puede sacarlo por su cuenta
#     porque no se entera de que toca hasta que se lo decimos: las tres
#     condiciones se miran en la base de datos, no en la conversación.
#   · CÓMO SE OFRECE lo decide el MODELO, y tiene que ser así. La conversación
#     puede ir en cualquier idioma (regla 6), y una frase nuestra en español
#     metida en una conversación en inglés se lee como lo que es: una máquina
#     recitando. Por eso lo que se le manda es un GUION, no un texto a repetir
#     —al revés que en `pasar_a_persona`, donde el texto sí va palabra por
#     palabra porque es una promesa de plazo y no una forma de decir algo—.
#   · SI EL CORREO VALE lo decide el SERVIDOR otra vez, con el mismo criterio
#     que el widget (`core/email_valido.py`). Un modelo que «completa» un correo
#     a medias se inventa el dominio, y un correo inventado no da un error:
#     se acepta, rebota, y el hueco se ve semanas después.

# Cuántas veces se le puede pedir el correo en una conversación. Dos: la vez que
# se ofrece y una repetición si lo que escribió no era un correo. A la tercera
# se deja estar — insistir por un dato opcional que la persona ya ha intentado
# dar dos veces es exactamente lo que no queremos que haga el bot.
MAX_INTENTOS_EMAIL = 2


def _antelacion_legible(horas: int) -> str:
    """«el día antes» / «unas 4 horas antes», para que el bot sepa qué ofrecer.

    Sale de `salons.recordatorio_horas` y no de dar por hecho 24: un salón puede
    tenerlo en otra cosa, y ofrecer «el día antes» un aviso que va a salir cuatro
    horas antes es prometer algo que no va a pasar.
    """
    return "el día antes" if 20 <= horas <= 30 else f"unas {horas} horas antes"


async def _direccion_si_es_su_primera_vez(ctx: ContextoBot, client_id: str) -> dict | None:
    """La dirección del salón, SOLO si esta persona no ha venido nunca.

    EL CASO: el QR está pegado en Instagram y lo escanea gente que no ha pisado
    el local. Reserva por WhatsApp, el bot le confirma la cita… y no le dice
    dónde es. A quien ya ha venido, repetírselo es ruido.

    ══ CÓMO SE SABE QUE ES LA PRIMERA VEZ ══

    POR SUS CITAS ANTERIORES, y NO por `clients.visitas`: esa columna no la
    escribe nadie y está a cero para todo el mundo, así que mirarla daría
    «primera vez» para siempre y a todo el mundo. Está pendiente de arreglar;
    hasta entonces, aquí no se usa.

    Se cuenta una cita que YA PASÓ y a la que vino: `inicio` en el pasado y un
    estado que ocupa hora —ni cancelada ni no presentada—. Las tres decisiones,
    por lo que pasaría si fueran al revés:

      · la cita que se acaba de crear es futura, así que no se cuenta a sí
        misma —si no, nunca sería la primera vez de nadie—;
      · una cita futura ya reservada tampoco cuenta: todavía no ha venido, y
        justo esa persona es la que más necesita la dirección;
      · una cancelada o un «no vino» tampoco: no llegó a pisar el local.

    FILTRA POR CENTRO, y no es un detalle: el token del salón ve los dos
    centros de la cuenta, así que sin `salon_id` una clienta del otro local
    pasaría por veterana en este — que es otra calle.

    NUNCA LANZA: la cita ya está hecha. Si esto falla, se calla y el modelo
    confirma como siempre.
    """
    try:
        anteriores = await ctx.db.select(
            "appointments",
            {
                "select": "id",
                "salon_id": f"eq.{ctx.salon_id}",
                "client_id": f"eq.{client_id}",
                "inicio": f"lt.{datetime.now(timezone.utc).isoformat()}",
                "estado": f"not.in.({','.join(ESTADOS_QUE_NO_OCUPAN)})",
                "limit": "1",
            },
        )
        if anteriores:
            return None

        filas = await ctx.db.select(
            "salons",
            {"select": "direccion", "id": f"eq.{ctx.salon_id}", "limit": "1"},
        )
        direccion = ((filas[0].get("direccion") if filas else None) or "").strip()
        if not direccion:
            # Sin dirección no hay nada que dar. Desde el aterrizaje del 20-09
            # no se puede crear un centro así, pero los de antes existen.
            return None
    except PostgrestError:
        logger.exception("No se pudo mirar si es su primera vez; se confirma sin dirección.")
        return None

    return {
        "direccion": direccion,
        # EL GUION VIAJA EN EL RESULTADO Y NO EN EL PROMPT, y eso es lo que lo
        # hace barato: el prompt viaja en CADA mensaje de CADA conversación de
        # CADA salón; esto solo en el turno en que alguien reserva por primera
        # vez. Medido: la regla en el prompt son ~47 tokens por mensaje; esto,
        # ~60 tokens una vez. Es el mismo mecanismo que `ofrecer_recordatorio`.
        "guion": (
            "Es la PRIMERA cita de esta persona en el salón. En esta misma "
            "respuesta, al confirmarle la cita, dile también la dirección que "
            "viene aquí, con tus palabras y en su idioma. Una frase, sin "
            "repetirla después."
        ),
    }


async def _salon_con_recordatorios(ctx: ContextoBot) -> dict | None:
    """El salón si tiene los recordatorios ENCENDIDOS; `None` si no.

    ES UN GATE, no una consulta de conveniencia, y por eso lo miran las tres
    puertas —el ofrecimiento y las dos herramientas— y no solo la primera.

    El prompt le dice al modelo que no saque el correo por su cuenta (regla
    10), pero una regla del prompt es una petición: nada impide que un modelo
    confundido escriba «¿me das tu email?» y llame a `guardar_email_cliente`.
    Si el interruptor solo se mirara al ofrecer, ese camino guardaría correos en
    salones que nunca los pidieron. Aquí la respuesta es que no, y no depende de
    que nadie obedezca nada.
    """
    try:
        filas = await ctx.db.select(
            "salons",
            {
                "select": "recordatorios_email_activo,recordatorio_horas",
                "id": f"eq.{ctx.salon_id}",
                "limit": "1",
            },
        )
    except PostgrestError:
        logger.warning("Bot: no se pudo leer el interruptor de recordatorios.")
        return None
    if not filas or not filas[0].get("recordatorios_email_activo"):
        return None
    return filas[0]


async def _ofrecimiento_de_recordatorio(ctx: ContextoBot, client_id: str | None) -> dict | None:
    """¿Toca ofrecerle el recordatorio por correo? Devuelve el guion, o `None`.

    LAS TRES CONDICIONES, y ninguna se le pregunta al modelo:

      1. El salón tiene los recordatorios ENCENDIDOS. Es el mismo interruptor
         que ya existe y no hay un segundo para «pedir correos»: un salón que no
         manda recordatorios no tiene por qué recoger correos, y dos
         interruptores para una sola decisión es una forma de que uno acabe mal
         puesto sin que nadie sepa por qué.
      2. La ficha NO tiene correo. A quien ya está en el CRM no se le pregunta.
      3. No ha dicho antes que no, ni se ha dado de baja de los recordatorios.

    NO LANZA NUNCA, y eso es la otra mitad. Se llama justo después de crear la
    cita, así que cualquier cosa que reventara aquí convertiría una cita YA
    CREADA en un «no he podido» para el cliente. Si algo falla, no se ofrece y
    ya está: es un extra, y un extra no puede llevarse por delante lo que la
    persona vino a hacer.
    """
    if not client_id:
        return None
    try:
        return await _decidir_el_ofrecimiento(ctx, client_id)
    except Exception:
        # `Exception` y no solo `PostgrestError`, y envolviendo la decisión
        # ENTERA y no solo sus consultas: lo que se protege no es una clase de
        # fallo ni una línea concreta, es la cita que acaba de crearse. Cualquier
        # cosa que reviente ahí dentro —una consulta, una conversión, algo que
        # todavía no existe— tiene que salir por aquí.
        logger.exception("Bot: no se pudo decidir si ofrecer el recordatorio.")
        return None


async def _decidir_el_ofrecimiento(ctx: ContextoBot, client_id: str) -> dict | None:
    """Las tres condiciones. Esta sí puede lanzar; quien la llama la envuelve."""
    salon = await _salon_con_recordatorios(ctx)
    if not salon:
        return None

    fichas = await ctx.db.select(
        "clients",
        {
            "select": "email,recordatorios_email_rechazado_at,recordatorios_baja_at",
            "id": f"eq.{client_id}",
            "limit": "1",
        },
    )
    if not fichas:
        return None
    ficha = fichas[0]
    if (ficha.get("email") or "").strip():
        return None
    if ficha.get("recordatorios_email_rechazado_at") or ficha.get("recordatorios_baja_at"):
        # Ya dijo que no una vez, o se dio de baja desde el pie de un correo.
        # Volver a preguntar convierte un «no» en el principio de una
        # negociación, que es justo lo que un «no» no es.
        return None

    cuando = _antelacion_legible(int(salon.get("recordatorio_horas") or 24))
    return {
        "cuando": cuando,
        "guion": (
            "En esta MISMA respuesta, después de confirmarle la cita, ofrécele un "
            f"recordatorio por correo {cuando}. Con tus palabras y en el idioma en que "
            "te esté escribiendo, en una sola frase: di para qué es, que es opcional y "
            "que si no quiere no pasa nada. Si te da un correo, llama a "
            "`guardar_email_cliente`; si te dice claramente que no, llama a "
            "`no_quiere_recordatorios`. Si lo ignora, cambia de tema o contesta otra "
            "cosa, DÉJALO ESTAR: no lo repitas ni ahora ni más adelante, y no llames a "
            "ninguna de las dos. La cita ya está hecha pase lo que pase con esto."
        ),
    }


async def _intentos_de_email(ctx: ContextoBot) -> int:
    """Cuántas veces se ha intentado ya guardar un correo en esta conversación.

    SE CUENTA EN EL SERVIDOR Y NO SE LE PREGUNTA AL MODELO, porque «no insistas»
    es una regla sobre lo que le pasa a una persona y no puede depender de que
    el modelo se acuerde. Sale de `bot_messages`, donde el bucle del webhook ya
    apunta cada llamada a cada herramienta: no hace falta un contador aparte
    para un dato que ya está escrito.

    El registro de la llamada EN CURSO todavía no está —se escribe después de
    ejecutar—, así que esto cuenta estrictamente las anteriores.

    Si no se puede contar, se devuelve el tope: sin saber cuántas van, callarse
    es el lado seguro. Como mucho se pierde un correo; al otro lado está
    perseguir a alguien que ya ha intentado dar lo que le pedían.
    """
    try:
        filas = await ctx.db.select(
            "bot_messages",
            {
                "select": "id",
                "session_id": f"eq.{ctx.session_id}",
                "role": "eq.tool",
                "content": "like.guardar_email_cliente*",
                "limit": str(MAX_INTENTOS_EMAIL),
            },
        )
    except PostgrestError:
        logger.warning("Bot: no se pudieron contar los intentos de email.")
        return MAX_INTENTOS_EMAIL
    return len(filas)


async def _guardar_email_cliente(ctx: ContextoBot, args: dict) -> dict:
    """Valida y guarda el correo que ha dado el cliente que escribe.

    EN LA FICHA DEL TELÉFONO QUE ESCRIBE, que la resuelve el servidor. El modelo
    no manda ningún identificador de cliente, así que no puede escribirle el
    correo a la ficha de otra persona: no tiene forma de nombrarla. Es la misma
    defensa que la de las citas.

    Y NUNCA ENCIMA DE UNO QUE YA HAYA, con la condición dentro del propio
    UPDATE. El correo del CRM lo puso el salón; que una conversación de WhatsApp
    lo pudiera cambiar sería dejar que quien tenga ese teléfono redirija adónde
    escribe el salón.
    """
    if not await _salon_con_recordatorios(ctx):
        # El salón no manda recordatorios: aquí no se recoge ningún correo,
        # diga lo que diga la conversación. Ver `_salon_con_recordatorios`.
        logger.warning("Bot: se pidió guardar un email con los recordatorios apagados.")
        return {"ok": False, "error": "no_procede"}

    email = normalizar_email(args.get("email"))
    if not email:
        agotado = await _intentos_de_email(ctx) >= MAX_INTENTOS_EMAIL - 1
        return {
            "ok": False,
            "error": "email_invalido",
            # Lo que de verdad manda: cuándo parar no lo decide el modelo.
            "reintentar": not agotado,
        }

    cliente = await _buscar_cliente(ctx)
    if not cliente:
        # No debería pasar —esto solo se ofrece con la cita ya creada, y crearla
        # asegura la ficha— pero si pasa, no se abre una ficha nueva solo para
        # colgarle un correo: una ficha sin cita ni nombre no le sirve a nadie.
        logger.warning("Bot: se pidió guardar un email sin ficha para ese teléfono.")
        return {"ok": False, "error": "sin_ficha"}

    filas = await ctx.db.update(
        "clients",
        {"id": f"eq.{cliente['id']}", "email": "is.null"},
        {"email": email},
    )
    # Sin filas = la ficha ya tenía correo (se lo apuntaron entre medias). No es
    # un error y no se le cuenta como tal al cliente: para él está resuelto.
    return {"ok": True, "guardado": bool(filas)}


async def _no_quiere_recordatorios(ctx: ContextoBot, args: dict) -> dict:
    """Apunta que este cliente no quiere dar su correo. NO es una baja.

    Escribe `recordatorios_email_rechazado_at`, que solo apaga la PREGUNTA. La
    baja de verdad —`recordatorios_baja_at`, la del pie de los correos— es otra
    columna a propósito: un «no, gracias» de pasada por WhatsApp no puede valer
    como una baja que esa persona no ha pedido, porque entonces el día que el
    salón le apuntara el correo a mano no le llegaría nada y nadie sabría por
    qué. Ver la migración 0037.
    """
    if not await _salon_con_recordatorios(ctx):
        # Nunca se le debió preguntar, así que no hay ningún «no» que apuntar.
        # Escribirlo igualmente sería peor que no hacer nada: dejaría a esa
        # persona marcada, y el día que el salón encendiera los recordatorios de
        # verdad ya no se le ofrecería nunca.
        return {"ok": False, "error": "no_procede"}

    cliente = await _buscar_cliente(ctx)
    if not cliente:
        # Sin ficha no hay dónde apuntarlo. Se contesta que sí igualmente: para
        # el cliente el efecto es el mismo —no se le vuelve a preguntar en esta
        # conversación— y un error aquí solo conseguiría que el bot insistiera.
        return {"ok": True, "apuntado": False}

    await ctx.db.update(
        "clients",
        {"id": f"eq.{cliente['id']}"},
        # La marca en ISO, como hace el cron con su sello. El `"now()"` que usa
        # la baja también vale —comprobado contra Postgres, que lo acepta como
        # entrada de fecha—, pero depende de que su analizador siga siendo
        # tolerante con esos paréntesis. Un instante explícito no depende de nada.
        {"recordatorios_email_rechazado_at": datetime.now(timezone.utc).isoformat()},
    )
    return {"ok": True, "apuntado": True}


# ---------------------------------------------------------------------------

_EJECUTORES = {
    "consultar_disponibilidad": _consultar_disponibilidad,
    "proponer_cita": _proponer_cita,
    "consultar_mis_citas": _consultar_mis_citas,
    "proponer_cancelacion": _proponer_cancelacion,
    "proponer_reprogramacion": _proponer_reprogramacion,
    "pasar_a_persona": _pasar_a_persona,
    "guardar_email_cliente": _guardar_email_cliente,
    "no_quiere_recordatorios": _no_quiere_recordatorios,
    "confirmar": _confirmar,
}


async def ejecutar(ctx: ContextoBot, nombre: str, args: dict | None) -> dict:
    """Ejecuta una herramienta y devuelve SIEMPRE un dict para el modelo.

    Nunca propaga una excepción: lo que aquí reviente tiene que volver al modelo
    como un resultado que sepa contar, no tumbar la respuesta al cliente. Un
    fallo de base de datos sale como `error: no_disponible` y el bot dice que
    ahora no puede consultarlo, que es la verdad.

    Un nombre desconocido también se contesta en vez de lanzar: los modelos se
    inventan nombres de herramienta de vez en cuando, y eso no es un error del
    servidor.
    """
    ejecutor = _EJECUTORES.get(nombre)
    if ejecutor is None:
        logger.warning("Bot: el modelo pidió una herramienta que no existe: %s", nombre)
        return {"ok": False, "error": "herramienta_desconocida"}

    try:
        return await ejecutor(ctx, args or {})
    except CitaNoValida as e:
        return _no_se_puede(e)
    except PostgrestError:
        logger.exception("Bot: fallo de base de datos ejecutando %s.", nombre)
        return {"ok": False, "error": "no_disponible"}
    except Exception:
        logger.exception("Bot: fallo inesperado ejecutando %s.", nombre)
        return {"ok": False, "error": "no_disponible"}
