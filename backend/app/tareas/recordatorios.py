"""El cron que manda los recordatorios de cita por email.

ES LA PRIMERA COSA DEL PROYECTO QUE OCURRE SOLA. Todo lo demás pasa porque
alguien pulsa algo o porque llega un webhook; esto pasa porque son las nueve.
Eso cambia dos cosas de raíz:

  1. NO HAY NADIE MIRANDO. Si falla, no hay una pantalla que se quede en blanco
     ni un cliente que se queje. Por eso cada pasada deja un LATIDO y cada envío
     caído deja su MOTIVO: la única forma de ver un silencio es haber acordado
     antes qué aspecto tiene el ruido.
  2. PUEDE EJECUTARSE DOS VECES. Un reintento de Railway, dos réplicas, una
     pasada que se solapa con la anterior. Por eso el sello es un UPDATE
     CONDICIONAL y no un `select` seguido de un `update`.

CÓMO SE EJECUTA:  python -m app.tareas.recordatorios
Arranca, hace su trabajo y termina. No es un servidor, no escucha nada y no se
queda vivo: si Railway lo mata a mitad, la pasada siguiente recoge lo que quedó.

POR QUÉ NO SE ENCOLA NADA AL RESERVAR: porque entonces cada cancelación y cada
cambio de hora obligarían a ir a buscar la tarea y corregirla, y el día que una
se escapara saldría un recordatorio de una cita que ya no existe. Aquí se
PREGUNTA en el momento del envío, así que la verdad no puede quedar desfasada.

EL INVARIANTE, que es lo que ordena todo lo de abajo:

    si un cliente ha dado su correo, o recibe el recordatorio, o queda contado
    por qué no.

Ningún agujero silencioso. De ahí que la elegibilidad sea MONÓTONA (se entra por
`inicio <= ahora + horas` y no se sale hasta mandar o hasta que la cita empieza)
y que cada cita que se deja pase por `_motivo_para_saltarla` y salga contada en
el resumen. La versión anterior tenía una banda con suelo, y una cita creada
después de su propia banda no llegaba siquiera a la consulta: el log decía
`saltadas: {}` mientras se dejaba una cita perfectamente válida esperando. Un
agujero que la tarea no sabía que tenía es peor que uno que sí.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time, timedelta, timezone

from app.core import demo
from app.core.config import Settings, get_settings
from app.core.emails import EmailConfigError, EnvioFallido, clave_idempotencia, enviar_email_branded
from zoneinfo import ZoneInfo

from app.core import zona as zonas
from app.core.recordatorio_email import cuerpo_del_recordatorio
from app.core.security import get_service_postgrest_client
from app.db.postgrest_client import PostgrestClient, PostgrestError

logger = logging.getLogger("estetix.recordatorios")

# CON LA CLAVE DE SERVICIO, CENTRO A CENTRO (0086). Antes cada salón se leía con
# el token de su titular; con la cuenta cortada ese token ya no lee la ficha de
# la clienta ni sella la cita, y los recordatorios de lo ya reservado tienen que
# seguir saliendo: esa clienta no tiene nada que ver con la factura del salón.
# Sin la RLS, el filtro por centro de CADA consulta es lo único que separa un
# salón de otro de la plataforma, y lo vigila
# `tests/test_consultas_publicas_filtran_por_centro.py` (esta constante es la
# que hace que la vigilancia la encuentre).
PUERTA_DE_SERVICIO_POR_CENTRO = "recordatorios de cita, con service_role y salon_id en cada consulta"

NOMBRE_TAREA = "recordatorios_email"

# NO HAY VENTANA. Hay TECHO, y el techo es `recordatorio_horas`.
#
# ANTES HABÍA UNA BANDA [diana-4h, diana+4h] y ahí estaba la avería: una cita
# solo era elegible mientras el RELOJ estaba dentro de esa franja. Una cita
# creada después de que su franja hubiera pasado no entraba nunca — ni en esa
# pasada ni en ninguna. En la práctica, toda reserva hecha con menos de 20 h de
# antelación se quedaba sin recordatorio, en silencio y sin aparecer siquiera en
# la consulta, justo después de que el bot le hubiera pedido el correo al
# cliente prometiéndoselo.
#
# La banda existía para que perder pasadas no perdiera recordatorios, y para eso
# el techo solo es estrictamente mejor: la elegibilidad pasa a ser MONÓTONA —una
# cita entra cuando `inicio <= ahora + horas` y no vuelve a salir hasta que se
# manda o hasta que empieza—, así que una caída del cron RETRASA en vez de
# PERDER. La banda hacía justo lo contrario: una caída de más de ocho horas se
# llevaba por delante las citas de ese tramo.

# EL SUELO, y es este y no otro: la antelación con la que se RESERVÓ.
#
# Es la respuesta a «un recordatorio que llega 20 minutos antes es ruido», y va
# medida de la reserva a la cita —no del envío a la cita— a propósito. Medido
# así es una propiedad de la CITA: no cambia si el cron se cae, no cambia si el
# aviso se retiene por la noche, y por tanto retener nunca puede acabar en
# descartar. Medido contra el momento del envío sería una propiedad de nuestra
# puntualidad, y le cobraría al cliente nuestras averías.
#
# TRES HORAS Y NO UNA: `bot_herramientas.ANTELACION_GESTION` son las dos horas
# por debajo de las cuales el propio bot ya no cancela ni mueve una cita. Con
# tres, el recordatorio llega SIEMPRE mientras el cliente todavía puede actuar
# por donde se le avisa; con una o dos llegaría a decirle que tiene una cita que
# ya no puede tocar sin llamar por teléfono.
ANTELACION_MINIMA = timedelta(hours=3)

# LAS HORAS DE SILENCIO, en la hora local del salón.
#
# Un correo del salón a las 23:30 es del salón, no nuestro: la firma que lleva es
# la suya. Quitado el suelo, una reserva hecha de noche dispararía el aviso de
# noche, así que se RETIENE y se suelta al abrir la franja.
#
# RETENER NO ES DESCARTAR, y eso lo sostienen dos cosas: el suelo de arriba no
# se mide contra el momento del envío (así que soltar tarde no puede tirar
# nada), y una cita que ya no exista cuando termine el silencio NO se retiene
# —sale ahora, de madrugada—, porque callarse hasta que sea tarde sería la misma
# avería que acabamos de arreglar con otra cara.
SILENCIO_DESDE = time(22, 0)
SILENCIO_HASTA = time(8, 0)

# Estados en los que la cita sigue en pie. Una cancelada o un no_show no reciben
# nada, y no hace falta ir a cancelar ningún aviso: simplemente dejan de
# aparecer en la consulta.
ESTADOS_VIVOS = ("pendiente", "confirmada")

# CADA CUÁNTO CORRE ESTA TAREA. No lo decide este fichero: lo decide la
# expresión cron del servicio de Railway (`*/15 * * * *`). Está escrito aquí
# porque el vigía necesita saberlo para distinguir «todavía no le ha tocado» de
# «lleva tres pasadas sin salir», y eso hace que las dos cosas estén acopladas:
# si alguien cambia el horario en Railway y no toca esto, el número miente.
CADENCIA_CRON = timedelta(minutes=15)

# CUÁNTO PUEDE TARDAR UN RECORDATORIO ANTES DE QUE SEA UNA ALARMA.
#
# Tres cadencias, y el 3 no es redondo por gusto:
#
#   · UNA cadencia es la espera normal. Una cita entra en el techo en cualquier
#     instante y se manda en la siguiente pasada; avisar a los 15 min sería
#     avisar del funcionamiento correcto.
#   · DOS absorben una pasada perdida: un despliegue del servicio de cron, un
#     salto del planificador de Railway, una pasada que se solapa.
#   · TRES significa que tres pasadas seguidas han visto esa cita y no han hecho
#     nada con ella. Eso ya no es casualidad, y 45 minutos sigue siendo dentro
#     de la misma hora: se sabe pronto.
#
# El número se deriva de la cadencia a propósito, para que mover una mueva la
# otra. La constante que había antes (20 h) estaba calibrada contra el suelo de
# la banda vieja, que ya no existe.
TOLERANCIA_ATRASO = 3 * CADENCIA_CRON

# CUÁNTO SILENCIO ES UN CRON MUERTO. El latido se escribe al final de CADA
# pasada, así que en marcha nunca pasa de una cadencia. Se usa el mismo 3 que
# arriba y a propósito: las dos preguntas son la misma —«¿han fallado tres
# pasadas seguidas?»— y tener un solo número es tener una cosa menos que
# mantener sincronizada. En la práctica esta señal salta ANTES que el atraso,
# porque no necesita que exista ninguna cita para dispararse.
SIN_LATIDO = 3 * CADENCIA_CRON

# CUÁNTAS VECES SE REINTENTA UN ENVÍO QUE SABEMOS QUE NO SALIÓ.
#
# Solo cuentan los fallos SEGUROS: un ambiguo deja el sello puesto y no llega
# aquí. Cinco intentos son el primero más cuatro reintentos, uno por pasada, o
# sea una hora de margen a la cadencia del cron. Una hora aguanta cualquier
# tropiezo pasajero de Resend y es poco al lado de las 24 h que hay por delante
# hasta la cita.
#
# EL TOPE NO ESTÁ PARA AHORRAR LLAMADAS sino para que un fallo PERMANENTE —un
# dominio que rechaza, una dirección que no existe— deje de contarse como «lo
# estamos intentando» y pase a ser un fallo de verdad, visible en el vigía. Sin
# él, ese correo se reintentaría cada 15 minutos hasta la hora de la cita y
# nunca cruzaría la línea de «esto no va a salir».
MAX_INTENTOS_ENVIO = 5

MAX_ERROR = 300


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


async def _salones_con_recordatorios(servicio: PostgrestClient) -> list[dict]:
    """Sobre quién hay que trabajar.

    La que AVERIGUA los salones: todavía no hay un `salon_id`. Un select, una
    tabla, las columnas justas.
    """
    # TODOS: con `select` el salón mil uno se quedaba sin recordatorios, y sin
    # un solo error que lo dijera.
    filas = await servicio.select_todo(
        "salons",
        {
            "select": (
                "id,nombre_comercial,direccion,telefono,recordatorio_horas,zona_horaria,"
                "accounts(suspendido,es_demo)"
            ),
            "recordatorios_email_activo": "eq.true",
            "order": "id.asc",
        },
    )
    # UNA CUENTA SUSPENDIDA NO MANDA CORREOS EN NOMBRE DE NINGUNO DE SUS
    # CENTROS. Antes el filtro iba en la consulta —`salons.suspendido`—; ahora
    # la suspensión es de la cuenta y se trae por embed. Se descarta en Python
    # y no con un filtro sobre el recurso embebido porque son pocos salones y
    # esa sintaxis (`accounts!inner`) no la entiende el doble de los tests: un
    # filtro que el doble no aplica es un filtro que los tests no prueban.
    # NI UNA DEMO (0080): un recordatorio que sale de una demo le llega a una
    # persona real. Y si este filtro faltara, el envío lo corta igual
    # (`demo.actuando_por`, en `ejecutar`).
    return [f for f in filas
            if not (f.get("accounts") or {}).get("suspendido") and not (f.get("accounts") or {}).get("es_demo")]


async def _citas_a_avisar(db: PostgrestClient, salon: dict, ahora: datetime) -> list[dict]:
    """Las citas de ESTE salón que ya se pueden avisar. TECHO, no banda.

    Los cuatro filtros, y ninguno sobra:

      · `inicio > ahora` — una cita que ya ha empezado no se recuerda. Es el
        único suelo que hay en la consulta, y escribirle a alguien por una cita
        de ayer no es un fallo cosmético: es desconcertarlo.
      · El TECHO — `inicio <= ahora + recordatorio_horas`. Nada más lejos, que
        es lo que el salón ha pedido.
      · El SELLO a null — lo que ya salió no vuelve a salir.
      · El ESTADO — una cancelada desaparece sola de aquí.

    LO QUE YA NO HAY es un suelo atado a `recordatorio_horas`. Ver el comentario
    de las constantes: era el que dejaba fuera para siempre a lo reservado con
    poca antelación. Ahora la condición es monótona —se entra y no se sale— y
    todo lo que decide a QUIÉN se escribe vive en `_motivo_para_saltarla`, en
    Python, donde se lee y se cuenta.

    El embed trae de una vez el cliente, el servicio y el profesional: son los
    datos del correo y pedirlos por separado serían tres viajes por cita.
    """
    horas = int(salon.get("recordatorio_horas") or 24)
    techo = ahora + timedelta(hours=horas)

    return await db.select(
        "appointments",
        {
            "select": (
                "id,inicio,estado,created_at,nombre_reserva,recordatorio_intentos,"
                "clients(id,nombre,email,recordatorios_baja_at,recordatorios_token),"
                "services(nombre),employees(nombre)"
            ),
            "salon_id": f"eq.{salon['id']}",
            "inicio": f"gt.{ahora.isoformat()}",
            "and": f"(inicio.lte.{techo.isoformat()})",
            "recordatorio_enviado_at": "is.null",
            "estado": f"in.({','.join(ESTADOS_VIVOS)})",
            "order": "inicio.asc",
            "limit": "200",
        },
    )


def _en_silencio(ahora: datetime, z: ZoneInfo) -> bool:
    """¿Estamos en las horas en las que no se escribe? Hora local DE ESE salón."""
    local = zonas.hora_local(ahora, z)
    # El `or` y no un `and` porque la franja cruza la medianoche.
    return local >= SILENCIO_DESDE or local < SILENCIO_HASTA


def _fin_del_silencio(ahora: datetime, z: ZoneInfo) -> datetime:
    """El primer momento en que se vuelve a escribir, a partir de `ahora`."""
    # Las 08:00 de HOY en el centro; si ya pasaron, las de mañana. Día a día y
    # con la zona, nunca sumando 24 h: la noche del cambio de hora dura 23 o 25.
    dia = zonas.fecha_local(ahora, z)
    fin = zonas.instante_local(dia, SILENCIO_HASTA, z)
    if fin <= ahora:
        fin = zonas.instante_local(dia + timedelta(days=1), SILENCIO_HASTA, z)
    return zonas.a_utc(fin)


def _se_puede_retener(inicio: datetime | None, ahora: datetime, z: ZoneInfo) -> bool:
    """¿Aguanta esta cita hasta que se abra la franja, o hay que mandar ya?

    LA EXCEPCIÓN QUE HACE HONESTA LA REGLA. Retener un aviso de una cita que
    empieza a las 02:00 hasta las 08:00 no es retenerlo: es perderlo, porque a
    las 08:00 la cita ya ha pasado y la consulta ni la mira. Cuando retener
    significaría descartar, se manda ahora aunque sea de madrugada — molestar a
    alguien de noche es peor que casi nada, y «casi nada» no incluye dejarle sin
    el aviso de la cita que tiene dentro de unas horas.
    """
    return bool(inicio) and inicio > _fin_del_silencio(ahora, z)


def _motivo_para_saltarla(cita: dict, ahora: datetime) -> str | None:
    """¿Hay que dejar esta cita en paz? Devuelve el motivo, o None si toca avisar.

    Se comprueba AQUÍ y no en la consulta porque son reglas de producto, no de
    filtrado: escritas en Python se leen, se testean y se explican; metidas en
    los parámetros de PostgREST serían una cadena ilegible.
    """
    cliente = cita.get("clients") or {}
    if not (cliente.get("email") or "").strip():
        return "sin_email"
    if cliente.get("recordatorios_baja_at"):
        return "de_baja"

    creada = _fecha(cita.get("created_at"))
    inicio = _fecha(cita.get("inicio"))
    if creada and inicio and (inicio - creada) < ANTELACION_MINIMA:
        # EL SUELO. Quien acaba de reservar para dentro de dos horas ya tiene la
        # confirmación en la mano; recordárselo es ruido. Y va medido de la
        # reserva a la cita —ver `ANTELACION_MINIMA`—, así que ni una caída del
        # cron ni una retención nocturna pueden convertir un aviso legítimo en
        # uno descartado: esta resta da lo mismo se mire cuando se mire.
        return "reservada_con_prisa"
    return None


def _fecha(valor: object) -> datetime | None:
    """Una marca de PostgREST en `datetime` con zona. Nunca lanza.

    Se acepta la `Z` además del `+00:00` porque las dos son ISO-8601 válidas, y
    una fecha sin zona se toma como UTC —que es como la guarda Postgres—: dejarla
    ingenua haría reventar cualquier resta contra `_ahora()`.
    """
    if not isinstance(valor, str) or not valor:
        return None
    try:
        return zonas.instante_o_utc(valor)
    except ValueError:
        return None


def momento_en_que_debio_salir(
    cita: dict, horas: int, ahora: datetime, z: ZoneInfo
) -> datetime | None:
    """Cuándo tendría que estar mandado este recordatorio. `None` si no le toca.

    ES LA MISMA REGLA QUE APLICA LA PASADA, y por eso vive aquí y no en el panel.
    Un vigía que usara su propio criterio contaría como retrasos a gente que
    nunca iba a recibir nada — y un indicador que nunca baja a cero deja de
    mirarse, que es la forma más silenciosa de quedarse sin vigía.

    DE QUÉ TIENE QUE SER INDEPENDIENTE, que no es lo mismo: de que la tarea se
    EJECUTE y de lo que la tarea DIGA de sí misma. Esto se calcula desde las
    citas, así que sigue siendo verdad aunque el latido mienta, aunque la tabla
    esté vacía o aunque una pasada termine diciendo que todo fue bien. De lo que
    NO puede ser independiente es de a quién le toca recordatorio: ahí compartir
    la regla es justo lo que hace que el número signifique algo.

    Devuelve `None` cuando no hay nada que esperar:

      · La cita ya empezó, o no le toca por producto (sin correo, de baja,
        reservada con prisa) — eso lo decide `_motivo_para_saltarla`.
      · Todavía no ha entrado en el techo del salón.

    Y si entró de noche, el momento que devuelve es el del amanecer: una
    retención no es un retraso, es un turno de espera.
    """
    inicio = _fecha(cita.get("inicio"))
    if inicio is None or inicio <= ahora:
        return None
    if _motivo_para_saltarla(cita, ahora):
        return None

    creada = _fecha(cita.get("created_at"))
    entra_en_el_techo = inicio - timedelta(hours=horas)
    desde = max(creada, entra_en_el_techo) if creada else entra_en_el_techo
    if desde > ahora:
        return None  # aún no le ha tocado

    if _en_silencio(desde, z) and _se_puede_retener(inicio, desde, z):
        # Retenido por la noche: su turno empieza cuando se abre la franja.
        return _fin_del_silencio(desde, z)
    return desde


def esta_atrasado(cita: dict, horas: int, ahora: datetime, z: ZoneInfo) -> bool:
    """¿Este recordatorio lleva sin salir más de lo que se puede explicar?"""
    debio = momento_en_que_debio_salir(cita, horas, ahora, z)
    return debio is not None and (ahora - debio) > TOLERANCIA_ATRASO


async def _reclamar(db: PostgrestClient, salon_id: str, cita_id: str) -> bool:
    """¿Es nuestro el turno de mandar esta?

    UPDATE CONDICIONAL, y ahí está todo el asunto: se pide sellar la cita *solo
    si* no está sellada. Postgres aplica la condición y el sello en la misma
    sentencia, así que de dos réplicas compitiendo solo una se lleva la fila. Un
    `select` y luego un `update` tendrían entre medias justo el hueco que este
    método existe para no tener.

    Ante un fallo de base de datos devuelve False —no se manda—: repetir un
    correo molesta más que retrasarlo, y la pasada siguiente lo reintenta.
    """
    try:
        filas = await db.update(
            "appointments",
            {"id": f"eq.{cita_id}", "salon_id": f"eq.{salon_id}", "recordatorio_enviado_at": "is.null"},
            # SE LIMPIA EL ERROR AL RECLAMAR, no al terminar bien. Si no, una
            # cita que falló y luego salió se quedaría con el error puesto para
            # siempre, y «sellada + con error» dejaría de significar «este
            # correo se perdió» — que es justo lo que el vigía cuenta.
            {"recordatorio_enviado_at": _ahora().isoformat(), "recordatorio_error": None},
        )
    except PostgrestError:
        logger.exception("Recordatorios: no se pudo reclamar la cita %s.", cita_id)
        return False
    return bool(filas)


async def _anotar_el_fallo(
    db: PostgrestClient, salon_id: str, cita_id: str, motivo: str, *, seguro: bool, intentos: int
) -> None:
    """Deja por qué no salió, y decide si se vuelve a intentar.

    ES LA CONTRAPARTIDA DE SELLAR ANTES DE MANDAR. Ese orden evita el correo
    duplicado, pero deja un hueco: la cita queda sellada y nadie recibió nada.

    AQUÍ SE DECIDE SI ESE HUECO SE PUEDE CERRAR, y la decisión no se toma en
    esta función: viene hecha desde `core/emails.py`, que es el único sitio que
    sabe qué pasó en el transporte. Aquí solo se aplica.

      · SEGURO y quedan intentos → se suelta el sello (`recordatorio_enviado_at`
        vuelve a null) y la pasada siguiente lo coge otra vez, sin nada más que
        hacer: la elegibilidad es monótona, así que la cita sigue en la consulta.
      · AMBIGUO, o agotados los intentos → el sello se queda. En el primer caso
        porque el correo puede haber salido; en el segundo porque insistir ya no
        va a cambiar nada.

    El error se anota SIEMPRE, se reintente o no: es lo único que hace visible
    el hueco, y el texto dice en cuál de los dos casos estamos para que la fila
    se entienda sola.
    """
    se_reintenta = seguro and intentos < MAX_INTENTOS_ENVIO
    marca = (
        f"[reintento {intentos}/{MAX_INTENTOS_ENVIO}] "
        if se_reintenta
        else f"[definitivo, {intentos} intento{'s' if intentos != 1 else ''}] "
    )
    cuerpo: dict = {
        "recordatorio_error": (marca + motivo)[:MAX_ERROR],
        "recordatorio_intentos": intentos,
    }
    if se_reintenta:
        cuerpo["recordatorio_enviado_at"] = None

    try:
        await db.update("appointments", {"id": f"eq.{cita_id}", "salon_id": f"eq.{salon_id}"}, cuerpo)
    except PostgrestError:
        # Si ni siquiera se puede anotar el fallo, al menos que quede en el log.
        # El sello se queda puesto, que es el lado seguro: sin poder escribir no
        # se puede soltar, y no soltarlo nunca duplica un correo.
        logger.exception("Recordatorios: cita %s falló y no se pudo anotar.", cita_id)


async def _enviar_uno(
    settings: Settings, db: PostgrestClient, salon: dict, cita: dict
) -> bool:
    """Manda el recordatorio de una cita. True si salió.

    Cualquier fallo se registra y se traga: un salón con el correo mal
    configurado no puede impedir que los demás reciban los suyos, ni que la
    pasada termine y deje su latido.
    """
    cliente = cita.get("clients") or {}
    destino = (cliente.get("email") or "").strip()
    # El intento que empieza AHORA. Sale de la fila que ya trajo la consulta, así
    # que contar no cuesta un viaje más.
    intentos = int(cita.get("recordatorio_intentos") or 0) + 1

    if not await _reclamar(db, salon["id"], cita["id"]):
        return False  # Otra pasada se la llevó.

    try:
        asunto, html = cuerpo_del_recordatorio(settings, salon, cita)
        # LA CLAVE ES DE LA CITA Y DE SU HORA, no del intento: los reintentos
        # llevan la misma y Resend no manda el segundo. Si la cita se mueve,
        # es otro aviso (otra hora) y lleva otra.
        await enviar_email_branded(
            settings, destino, asunto, html, clave=clave_idempotencia("recordatorio", cita["id"], cita.get("inicio"))
        )
    except EmailConfigError as e:
        # Ni se intentó salir: falta una variable de entorno. Seguro, y además
        # se arregla solo en cuanto alguien la ponga.
        logger.error("Recordatorios: configuración de correo incompleta: %s", e)
        await _anotar_el_fallo(
            db, salon["id"], cita["id"], f"config: {e}", seguro=True, intentos=intentos
        )
        return False
    except EnvioFallido as e:
        logger.warning(
            "Recordatorios: cita %s no salió (%s): %s",
            cita["id"], "seguro" if e.seguro else "ambiguo", e,
        )
        await _anotar_el_fallo(db, salon["id"], cita["id"], str(e), seguro=e.seguro, intentos=intentos)
        return False
    except Exception as e:  # noqa: BLE001 — el motivo se anota, no se propaga
        # NO CLASIFICADO = AMBIGUO. Un fallo que no se ha entendido es, por
        # definición, uno del que no se sabe si el correo salió. Lo peor que
        # puede pasar por esta rama es perder un recordatorio; nunca duplicarlo.
        logger.exception("Recordatorios: no se pudo enviar la cita %s.", cita["id"])
        await _anotar_el_fallo(
            db, salon["id"], cita["id"], f"{type(e).__name__}: {e}", seguro=False, intentos=intentos
        )
        return False
    return True


async def _latir(servicio: PostgrestClient, procesados: int, fallidos: int) -> None:
    """Deja constancia de que esta pasada ocurrió.

    Lo que hace visible el silencio. `upsert` sobre la clave primaria: una fila
    por tarea, no una por ejecución — lo que se pregunta es «¿cuándo fue la
    última?», y eso se contesta con una fila; un histórico crecería sin parar
    para responder lo mismo.
    """
    try:
        await servicio.upsert_many(
            "tareas_latido",
            [
                {
                    "tarea": NOMBRE_TAREA,
                    "ultima_at": _ahora().isoformat(),
                    "procesados": procesados,
                    "fallidos": fallidos,
                }
            ],
            on_conflict="tarea",
        )
    except PostgrestError:
        logger.exception("Recordatorios: no se pudo dejar el latido.")


async def ejecutar(settings: Settings | None = None) -> dict:
    """Una pasada completa. Devuelve el resumen, para los tests y para el log."""
    settings = settings or get_settings()
    ahora = _ahora()
    # La dependencia de FastAPI es una función normal: fuera de una petición se
    # llama igual, y así el cron entra a la base de datos por la misma puerta
    # que el resto del backend en vez de abrirse una propia.
    servicio = get_service_postgrest_client(settings)

    procesados = 0
    fallidos = 0
    # DOS CONTADORES Y NO UNO, porque son dos cosas distintas y confundirlas fue
    # parte del fallo: `saltadas` son decisiones —a esa cita no se le escribe— y
    # `retenidas` son esperas —se le escribirá en cuanto se abra la franja—.
    # Meter una retención en `saltadas` diría que se ha descartado algo que no.
    saltadas: dict[str, int] = {}
    retenidas = 0

    try:
        salones = await _salones_con_recordatorios(servicio)
    except PostgrestError:
        logger.exception("Recordatorios: no se pudo leer la lista de salones.")
        # Sin latido: la pasada NO ha hecho su trabajo, y decir que sí sería
        # tapar justo lo que el latido existe para enseñar.
        return {"salones": 0, "procesados": 0, "fallidos": 0, "error": "salones"}

    for salon in salones:
        # La noche es la DE CADA centro: Las Palmas se despierta una hora
        # después que Madrid.
        z = zonas.zona(salon.get("zona_horaria"))
        silencio = _en_silencio(ahora, z)
        db = servicio

        try:
            citas = await _citas_a_avisar(db, salon, ahora)
        except PostgrestError:
            logger.exception("Recordatorios: no se pudieron leer las citas de %s.", salon["id"])
            fallidos += 1
            continue

        for cita in citas:
            motivo = _motivo_para_saltarla(cita, ahora)
            if motivo:
                saltadas[motivo] = saltadas.get(motivo, 0) + 1
                continue
            if silencio and _se_puede_retener(_fecha(cita.get("inicio")), ahora, z):
                # Ni se sella ni se manda: en la primera pasada de las 08:00
                # vuelve a salir en la consulta tal cual, porque la elegibilidad
                # ya no depende de a qué hora se mire.
                retenidas += 1
                continue
            try:
                with demo.actuando_por(bool((salon.get("accounts") or {}).get("es_demo"))):
                    enviado = await _enviar_uno(settings, db, salon, cita)
            except demo.SalidaDesdeUnaDemo:
                saltadas["demo"] = saltadas.get("demo", 0) + 1
                continue
            if enviado:
                procesados += 1
            else:
                fallidos += 1

    await _latir(servicio, procesados, fallidos)
    resumen = {
        "salones": len(salones),
        "procesados": procesados,
        "fallidos": fallidos,
        "saltadas": saltadas,
        "retenidas": retenidas,
    }
    logger.info("Recordatorios: %s", resumen)
    return resumen


def main() -> None:
    """El entrypoint del cron. `python -m app.tareas.recordatorios`."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    resumen = asyncio.run(ejecutar())
    # A stdout además del log: en el panel de Railway la salida de la ejecución
    # es lo primero que se mira cuando algo va mal.
    print(f"recordatorios: {resumen}")
    # LOS AVISOS DEL PLAZO DE LOS DATOS (0064) van en esta misma ejecución para
    # no depender de configurar otro cron en Railway, DESPUÉS de los
    # recordatorios y AISLADOS: nada de lo que falle ahí puede impedir los de
    # arriba, que ya salieron. Tienen su propio latido. No borran nada.
    try:
        from app.tareas import plazo_de_datos

        print(f"plazo_de_datos: {asyncio.run(plazo_de_datos.ejecutar())}")
    except Exception:  # noqa: BLE001 — el cron de recordatorios no puede caerse por esto
        logger.exception("Plazo de datos: la pasada falló.")
    # EL BARRIDO DE LAS DEMOS (0080), también aquí y también aislado. Nace
    # apagado: hasta que se encienda en la base, solo apunta «apagado».
    try:
        from app.tareas import barrido_demos

        print(f"barrido_demos: {asyncio.run(barrido_demos.ejecutar())}")
    except Exception:  # noqa: BLE001 — ídem
        logger.exception("Barrido de demos: la pasada falló.")
    # LAS ALTAS SIN CONFIRMAR (0087), también aquí y también aisladas: que
    # caduquen y su correo quede libre. Lo decide la base, con tope por pasada.
    try:
        from app.tareas import altas_sin_confirmar

        print(f"altas_sin_confirmar: {asyncio.run(altas_sin_confirmar.ejecutar())}")
    except Exception:  # noqa: BLE001 — ídem
        logger.exception("Altas sin confirmar: la pasada falló.")
    # EL BORRADO A 90 DÍAS (0089), el último y también aislado: va DESPUÉS de los
    # avisos del plazo, que son los que avisan de él. Nace apagado: hasta que se
    # encienda en la base, solo apunta «apagado» y no toca nada de fuera.
    try:
        from app.tareas import borrado_a_90_dias

        print(f"borrado_a_90_dias: {asyncio.run(borrado_a_90_dias.ejecutar())}")
    except Exception:  # noqa: BLE001 — ídem
        logger.exception("Borrado a 90 días: la pasada falló.")


if __name__ == "__main__":
    main()
