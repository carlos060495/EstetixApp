"""Las reglas que tiene que cumplir una cita, valga quien valga la pida.

Esto vivía dentro de `routers/appointments.py` como cuatro funciones privadas, y
una de ellas ya llevaba escrito en su docstring que «lo necesitará igualmente el
bot de reservas, que no pasa por el modal». Pues ya está aquí: el bot de la Fase
2 crea citas sin pasar por el endpoint, y estas reglas no pueden depender de por
qué puerta se entre.

QUÉ CAMBIA respecto a cuando estaban en el router: NADA de la lógica. Solo dejan
de lanzar `HTTPException` —que es un detalle de FastAPI y aquí no pinta nada— y
lanzan `CitaNoValida`, que lleva el mismo status y el mismo texto. El router se
queda con envoltorios finos que la traducen, de modo que sus respuestas son
idénticas a las de antes.

QUÉ NO ESTÁ AQUÍ: la política de antelación mínima y horizonte (eso es del bot,
no del panel: la recepcionista tiene que poder agendar a la clienta que tiene
delante ahora mismo) y el cálculo de huecos libres. Están en
`core/disponibilidad.py`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.core import zona as zonas
from app.core.horario_activo import (
    cargar_excepciones,
    cargar_horario,
    dentro_del_horario,
    describe_horario,
)
from app.core.horario_empleada import (
    cabe_en_su_horario,
    cargar_horarios_propios,
    describe_horario_propio,
)
from app.db.postgrest_client import PostgrestClient, PostgrestError


class CitaNoValida(Exception):
    """Una regla de negocio que la cita no cumple.

    Lleva el `status` que le corresponde para que el router no tenga que
    volver a decidirlo, y un `codigo` estable para que quien la atienda pueda
    distinguir el motivo sin comparar textos —el bot lo necesita: a un hueco
    ocupado le ofrece alternativas, y a un empleado que no hace el servicio le
    ofrece otro profesional—.
    """

    def __init__(
        self,
        codigo: str,
        mensaje: str,
        status: int = 422,
        *,
        habla_de_otro_centro: bool = False,
    ):
        self.codigo = codigo
        self.mensaje = mensaje
        self.status = status
        # A UNA CLIENTA NO SE LE HABLA DEL OTRO CENTRO.
        #
        # El detalle de quién, cuándo y dónde es para quien es dueño de los dos
        # locales. Una clienta del centro 1 no puede enterarse de que Ana
        # trabaja hoy en el centro 2, ni a qué hora, ni siquiera de que el
        # centro 2 existe: es información de negocio del otro local.
        #
        # La marca viaja con la excepción y no con el texto porque quien decide
        # si algo se cuenta es el CANAL, no quien lo escribe: el panel lo pinta
        # entero y el widget y el bot lo cambian por su respuesta neutra. Sin
        # esto, `publico.py` reenvía `e.mensaje` tal cual —lo hace en dos
        # sitios— y el bot se lo pasa al modelo, que habla con la clienta.
        self.habla_de_otro_centro = habla_de_otro_centro
        super().__init__(mensaje)


MENSAJE_HUECO_OCUPADO = "El empleado ya tiene una cita en ese horario"

# Lo que el salón ve cuando pierde una carrera contra otra reserva. Es distinto
# del de arriba a propósito: aquel significa «mira la agenda, ahí ya había algo»
# y este significa «lo había libre hace un segundo y ya no». Para la
# recepcionista son dos situaciones distintas y la segunda pide recargar.
MENSAJE_CARRERA_PERDIDA = (
    "Ese hueco acaba de ocuparse. Actualiza la agenda y elige otra hora."
)

# Y el tercero, que faltaba y por eso el segundo mentía. Cuando el choque viene
# de OTRO centro, «actualiza la agenda» manda a mirar donde no hay nada: la
# agenda de aquí está vacía a esa hora y quien lo lea concluirá que la app
# falla. Aquí se dice la situación real —quién, a qué hora y dónde— porque es
# la única forma de que la recepcionista pueda hacer algo al respecto.
def mensaje_ocupada_en_otro_centro(nombre: str, desde: str, hasta: str, centro: str) -> str:
    """SIN GÉNERO, y no por delicadeza: porque el que había era ADIVINADO.

    Decía «{nombre} ya está ocupada», así que a Luis Ramírez le salía «Luis
    Ramírez ya está ocupada». El género no estaba en ningún dato —no existe esa
    columna, ni debería— sino en una `a` que escribí yo suponiendo que quien
    trabaja en un salón es mujer.

    La forma correcta no es elegir mejor el género: es que no haga falta
    ninguno. «Ya tiene una cita» dice exactamente lo mismo y no supone nada de
    nadie. Cualquier texto futuro que hable de una persona con nombre propio
    tiene que poder leerse igual sea quien sea.
    """
    return (
        f"{nombre} ya tiene una cita de {desde} a {hasta} en {centro}. "
        "Es la misma persona en los dos centros."
    )


# Y el caso en que SABEMOS que está en otro centro pero no CUÁL. Pasa cuando la
# fila la esconde la RLS: quien agenda no tiene permiso sobre ese centro, así
# que la comprobación previa no ve el choque y salta el candado de la base. Se
# dice lo que se sabe y nada más; lo que no se puede es repetir «actualiza la
# agenda», que sigue siendo falso.
# LO QUE SE LE DICE A UNA CLIENTA cuando el motivo es del otro centro: que a esa
# hora no puede, y nada más. Sin nombrar el otro centro, sin decir que existe y
# sin dar un motivo que lo deje adivinar —«está en otra sede» ya lo delata—.
MENSAJE_PARA_LA_CLIENTA = "A esa hora no hay disponibilidad. Elige otra, por favor."

MENSAJE_OCUPADA_EN_OTRO_CENTRO_SIN_NOMBRE = (
    "Esta persona ya tiene una cita a esa hora en otro de vuestros centros. "
    "Elige otra hora o pregunta a quien lleve ese centro."
)

_REF_TABLAS = {
    "client_id": ("clients", "Cliente"),
    "employee_id": ("employees", "Empleado"),
    "service_id": ("services", "Servicio"),
}

# Los dos estados que NO ocupan hueco. Se nombra una vez y lo usan la
# comprobación de solape, el motor de disponibilidad y el candado de la
# migración 0030: si los tres no dijeran lo mismo, cancelar una cita no
# liberaría la hora en alguno de ellos.
ESTADOS_QUE_NO_OCUPAN = ("cancelada", "no_show")


def _ya_paso(inicio: object) -> bool:
    if not isinstance(inicio, str):
        return False
    try:
        cuando = datetime.fromisoformat(inicio.replace("Z", "+00:00"))
    except ValueError:
        return False
    if cuando.tzinfo is None:
        return False
    return cuando < datetime.now(timezone.utc)


async def asegurar_refs_del_salon(db: PostgrestClient, salon_id: str, body: dict) -> None:
    """Cliente, empleado y servicio tienen que ser DE ESTE SALÓN.

    La RLS valida la fila de la cita, no lo que referencia: sin este check se
    podrían enlazar clientes/empleados/servicios de OTRO salón (mismo patrón que
    `category_id` en /services y `service_ids` en /employees).
    """
    for campo, (tabla, etiqueta) in _REF_TABLAS.items():
        ref_id = body.get(campo)
        if not ref_id:
            continue
        es_ficha = campo == "employee_id"
        filas = await db.select(
            tabla,
            {"select": "id,estado" if es_ficha else "id", "id": f"eq.{ref_id}", "salon_id": f"eq.{salon_id}"},
        )
        if not filas:
            raise CitaNoValida(
                "ref_de_otro_salon", f"{etiqueta} no encontrado en este salón", status=400
            )
        # A QUIEN SE FUE NO SE LE DA HORA (0062). Las pantallas ya no la
        # ofrecen; esto es para lo que llegue por otro lado. Una cita PASADA
        # suya sí se puede seguir tocando (marcarla atendida, corregir el
        # precio): esa es su historia, no una hora nueva.
        if es_ficha and filas[0].get("estado") == "baja" and not _ya_paso(body.get("inicio")):
            raise CitaNoValida("profesional_de_baja", "Esa persona está dada de baja.", status=400)


async def asegurar_empleado_realiza_servicio(
    db: PostgrestClient, employee_id: str | None, service_id: str | None
) -> None:
    """El empleado tiene que saber hacer el servicio que se le asigna.

    El desplegable de la agenda ya solo ofrece a quien lo realiza, pero eso es
    comodidad: la verdad está aquí. Sin esta comprobación el filtro del frontend
    es decorativo —basta una llamada a la API para saltárselo— y el bot, que no
    pasa por el modal, no tendría ninguna.

    ORDEN: se llama SIEMPRE después de `asegurar_refs_del_salon`, que es quien
    garantiza que ambos ids son de este salón. `employee_services` no tiene
    columna `salon_id` (su RLS va por el empleado), así que sin ese paso previo
    esta consulta no acotaría el inquilino por sí sola.

    Cualquiera de los dos puede venir a null —la columna lo permite—: sin
    empleado o sin servicio no hay nada que validar.
    """
    if not employee_id or not service_id:
        return
    enlaces = await db.select(
        "employee_services",
        {
            "select": "service_id",
            "employee_id": f"eq.{employee_id}",
            "service_id": f"eq.{service_id}",
            "limit": "1",
        },
    )
    if enlaces:
        return

    # Los nombres se piden SOLO en el camino de error: el caso normal no paga
    # dos consultas por un mensaje que no se va a usar.
    empleado = await db.select(
        "employees", {"select": "nombre", "id": f"eq.{employee_id}", "limit": "1"}
    )
    servicio = await db.select(
        "services", {"select": "nombre", "id": f"eq.{service_id}", "limit": "1"}
    )
    quien = empleado[0]["nombre"] if empleado else "Ese empleado"
    que = f"«{servicio[0]['nombre']}»" if servicio else "ese servicio"
    raise CitaNoValida(
        "no_realiza_servicio",
        f"{quien} no realiza el servicio {que}. "
        f"Asígnaselo en Empleados o elige a otra persona.",
    )


async def asegurar_dentro_del_horario(
    db: PostgrestClient, salon_id: str, inicio: str, fin: str
) -> None:
    """No se agenda fuera de las horas de atención del salón.

    Segunda capa, igual que en el horario del salón: la agenda ya solo ofrece huecos
    válidos, pero esto se cumple aunque no pase por la agenda —y es lo que
    aplica el bot—. Solo aplica al CREAR o mover una cita: las que ya existían
    fuera de horario se siguen leyendo con normalidad, porque esconder datos ya
    guardados sería peor que el problema.
    """
    semana = await cargar_horario(db, salon_id)
    # Los festivos y cierres por fecha ganan al horario semanal, así que entran
    # en la misma cuenta. Van juntos y no por separado: el punto único de
    # decisión es `dentro_del_horario`, no quien llame aquí.
    excepciones = await cargar_excepciones(db, salon_id)
    z = await zonas.zona_del_centro(db, salon_id)
    ini = zonas.instante_de(inicio)
    f = zonas.instante_de(fin)
    if dentro_del_horario(ini, f, semana, excepciones, z=z):
        return

    raise CitaNoValida(
        "fuera_de_horario",
        f"Esa hora queda fuera del horario del salón: "
        f"{describe_horario(zonas.fecha_local(ini, z), semana, excepciones)}.",
    )


async def asegurar_en_su_horario(
    db: PostgrestClient, employee_id: str | None, inicio: str, fin: str, *, z: ZoneInfo
) -> None:
    """No se agenda a una persona fuera de SU horario (0045).

    LA MISMA REGLA QUE EL MOTOR, POR LA OTRA PUERTA. El bot y el widget no
    ofrecen a Ana en su día libre porque el motor de huecos ya la quita; pero la
    agenda crea y mueve citas validando aquí, sin pasar por el motor. Sin esta
    función, la recepcionista podría agendarla justo donde el bot no.

    SOLO LO PROPIO: el techo del salón lo comprueba `asegurar_dentro_del_horario`,
    y van separadas porque no se exigen en los mismos casos (ver
    `horario_empleada.cabe_en_su_horario`).

    LA EQUIVALENCIA: una ficha sin horario propio pasa siempre, así que para
    todas las fichas de hoy esto no rechaza nada que antes se aceptara.
    """
    if not employee_id:
        return
    propio = (await cargar_horarios_propios(db, [employee_id]))[employee_id]
    ini = zonas.instante_de(inicio)
    f = zonas.instante_de(fin)
    if cabe_en_su_horario(ini, f, propio, z=z):
        return

    # El nombre solo en el camino de error: el caso normal no paga la consulta.
    filas = await db.select("employees", {"select": "nombre", "id": f"eq.{employee_id}", "limit": "1"})
    quien = filas[0]["nombre"] if filas else "Esa persona"
    raise CitaNoValida(
        "fuera_de_su_horario",
        f"{quien} no trabaja a esa hora: "
        f"{describe_horario_propio(zonas.fecha_local(ini, z), propio)}. "
        f"Elige otra hora u otra persona.",
    )


async def ficha_de_la_persona(db: PostgrestClient, employee_id: str) -> dict:
    """La ficha y la persona detrás de ella. Una consulta por escritura de cita.

    Hace falta porque `persona_id` es EL ID DE LA PRIMERA ficha: si te dan la
    segunda, no hay forma de deducir la primera sin preguntar. Al revés sí
    funcionaría, y esa asimetría es justo lo que descarta el atajo de buscar
    `or=(employee_id.eq.X, persona_id.eq.X)` en una sola consulta.

    Va con el token de quien llama, no con service_role: la ficha del empleado
    al que se está agendando es de ESTE centro, así que la RLS la deja ver
    siempre.
    """
    filas = await db.select(
        "employees", {"select": "id,nombre,persona_id", "id": f"eq.{employee_id}", "limit": "1"}
    )
    return filas[0] if filas else {"id": employee_id, "nombre": "Esta persona", "persona_id": None}


def _hhmm(iso: str, z: ZoneInfo) -> str:
    """La hora como la lee una persona, en la del salón.

    Las citas se guardan en UTC y el mensaje se lee en el mostrador: sin esta
    conversión, «ocupada de 14:00 a 15:00» saldría dos horas antes en verano y
    el aviso pasaría de aclarar a confundir.
    """
    return zonas.hhmm(iso, z)


async def asegurar_sin_solape(
    db: PostgrestClient,
    salon_id: str,
    employee_id: str | None,
    inicio: str,
    fin: str,
    exclude_id: str | None = None,
) -> None:
    """Una PERSONA no puede estar en dos citas a la vez, aunque sean de centros
    distintos.

    Antes decía «un empleado», y con una ficha por centro eso dejó de ser lo
    mismo: la consulta filtraba por `salon_id` y por `employee_id`, o sea por LA
    FICHA, de modo que un choque con el otro centro era invisible aquí y solo lo
    cazaba el candado de la base — con el mensaje equivocado, porque el candado
    no sabe distinguir una carrera perdida de una persona que está en otro
    sitio.

    ESTO SIGUE SIN SER LA GARANTÍA. La garantía es el EXCLUDE de la 0044, que
    ahora tiene por clave la persona. Esto es el mensaje bonito, y el mensaje
    bonito tenía que dejar de mentir.

    LA FRONTERA NO SE TOCA, y por eso la consulta NO lleva filtro de salón: se
    apoya en la RLS de `appointments` (`salon_id = any(salones_visibles())`) con
    el token de quien llama. Las consecuencias, medidas y queridas:

      · Una administradora ve los centros de SU cuenta, así que ve el choque y
        se le puede decir dónde y a qué hora. No hay puerta lateral: es la RLS
        la que autoriza, no un `if` mío.
      · Alguien sin permiso sobre el otro centro NO ve la fila, la comprobación
        pasa de largo y el candado de la base rechaza igual. Ahí se le dice que
        está ocupada en otro centro, SIN decir en cuál — que es exactamente lo
        que sabemos y lo que se puede contar.

    Lo que nunca sale es de QUIÉN es la cita: el nombre de la clienta del otro
    centro no hace falta para entender el problema.
    """
    if not employee_id:
        return

    ficha = await ficha_de_la_persona(db, employee_id)
    persona = ficha.get("persona_id") or employee_id

    params = {
        # `salons(...)` para nombrar el centro sin una consulta más. El embed no
        # es ambiguo aquí: `appointments` llega a `salons` por un solo camino.
        "select": "id,salon_id,inicio,fin,salons(nombre_comercial)",
        "persona_id": f"eq.{persona}",
        "estado": f"not.in.({','.join(ESTADOS_QUE_NO_OCUPAN)})",
        "inicio": f"lt.{fin}",
        "fin": f"gt.{inicio}",
        "limit": "1",
    }
    if exclude_id:
        params["id"] = f"neq.{exclude_id}"

    choques = await db.select("appointments", params)
    if not choques:
        return

    choque = choques[0]
    # `.get` y no `[...]`: si por lo que sea faltara el centro, se cae al
    # mensaje de siempre —que es correcto aquí el 99,99 % de las veces— en vez
    # de reventar con un 500 en mitad de un alta de cita.
    if choque.get("salon_id", salon_id) == salon_id:
        # El de toda la vida: está aquí mismo, en la agenda que tiene delante.
        raise CitaNoValida("hueco_ocupado", MENSAJE_HUECO_OCUPADO, status=409)

    centro = (choque.get("salons") or {}).get("nombre_comercial") or "otro centro"
    # Las horas, en la zona de QUIEN LEE: las compara con su propia agenda.
    z = await zonas.zona_del_centro(db, salon_id)
    raise CitaNoValida(
        "hueco_ocupado",
        mensaje_ocupada_en_otro_centro(
            ficha.get("nombre") or "Esta persona",
            _hhmm(choque["inicio"], z),
            _hhmm(choque["fin"], z),
            centro,
        ),
        status=409,
        habla_de_otro_centro=True,
    )


async def esta_enlazada(db: PostgrestClient, employee_id: str | None) -> bool:
    """¿Esta ficha es de alguien que trabaja en más de un centro?

    Lo usa quien atrapa una violación del candado para elegir el mensaje: si la
    persona NO está enlazada, un choque solo puede ser una carrera perdida aquí;
    si lo está y la comprobación previa no vio nada, lo más probable con
    diferencia es que el choque esté en un centro que la RLS le esconde.
    """
    if not employee_id:
        return False
    ficha = await ficha_de_la_persona(db, employee_id)
    return bool(ficha.get("persona_id"))


def es_violacion_de_exclusion(e: PostgrestError) -> bool:
    """¿Este error de PostgREST es el candado de solape de la migración 0030?

    Postgres da 23P01 (`exclusion_violation`). Se mira el código y, como red, el
    nombre del constraint; NO el status 409 a secas, porque PostgREST devuelve
    ese mismo 409 para una violación de unique (23505), que es otra cosa y pide
    otro mensaje.

    Sin esto, perder una carrera le enseñaba a la recepcionista el texto crudo
    de Postgres («conflicting key value violates exclusion constraint…»).
    """
    detalle = e.detail or ""
    return "23P01" in detalle or "appointments_sin_solape" in detalle
