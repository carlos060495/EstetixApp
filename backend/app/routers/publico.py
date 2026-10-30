"""La página pública de reservas: sin login, sin sesión, sin cookie.

QUIÉN LLAMA AQUÍ: un cliente del salón con el móvil, que ha escaneado un QR o ha
pulsado un enlace en Instagram. No tiene cuenta, no va a tenerla, y no debe.

CÓMO SE ENTRA A LA BASE DE DATOS, que es lo que hace que esto sea seguro:

  1. `slug` → `salon_id`. UNA consulta con service_role, porque es justo la que
     AVERIGUA de qué salón hablamos: todavía no hay `salon_id` con el que acuñar
     nada. Misma frontera que ya tienen el webhook del bot y la impersonación,
     y por el mismo motivo.
  2. A partir de ahí, TOKEN DEL INQUILINO. Se reutiliza `cliente_del_salon`, el
     mismo mecanismo y la misma custodia que usa el webhook. La RLS se evalúa
     como si las consultas las hiciera un usuario de ese salón.
  3. El token vive en memoria del servidor. No se persiste, no vuelve en ninguna
     respuesta y no se escribe en ningún log. El navegador nunca lo ve.

Y LO QUE DE VERDAD PROTEGE: la RLS aísla una CUENTA de otra, pero dentro de la
cuenta ese token lo lee todo, y eso incluye los demás centros. Dos cosas lo
contienen, y hacen falta las dos:
  · el `salon_id` explícito en cada consulta, que separa un centro de su
    hermano (el token no lo hace: ver `core/bot_tenant_token.py`). Lo vigila
    `tests/test_consultas_publicas_filtran_por_centro.py`;
  · la proyección campo a campo de `schemas/publico.py`. Si algún día alguien
    devuelve aquí una fila cruda de PostgREST, se acabó la frontera.

CUATRO PUERTAS CERRADAS, y las cuatro contestan LO MISMO —404 «no disponible»—
porque distinguirlas ya sería contar algo:
  · el slug no existe;
  · el salón está suspendido;
  · el salón tiene el widget apagado;
  · la cuenta está cancelada (`billing/estado_suscripcion`: canceled, unpaid,
    incomplete_expired). Medido el 16-09-2026: seguía admitiendo reservas,
    gratis y sin fecha de fin. Los recordatorios de lo ya reservado NO pasan
    por aquí y siguen saliendo.

Y UNA QUINTA QUE NO CIERRA LA PÁGINA: el centro está EN PAUSA porque el plan de
la cuenta incluye menos centros. Es el único caso en el que el salón está
abierto, paga y tiene un QR en su puerta, así que un 404 le quitaría reservas
que habría cogido por teléfono. La página se abre, las reservas no, y el
teléfono queda a la vista.

A LA CLIENTA NO SE LE CUENTA NUESTRA FACTURACIÓN. Ni en esta puerta ni en las
otras cuatro: ni plan, ni suscripción, ni pausa. «Ahora mismo no se puede
reservar por aquí» y el teléfono. El motivo se queda en el log, que es donde le
importa a alguien.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from app.core import publico
from app.billing.estado_suscripcion import atiende_al_publico, estados_embebidos
from app.billing.lo_que_incluye_el_plan import (
    SIN_RESERVAS_ONLINE,
    centros_de_la_cuenta,
    lo_que_incluye,
)
from app.core.clientes import asegurar_cliente
from app.core.email_valido import normalizar_email
from app.core.config import Settings, get_settings
from app.core.citas import MENSAJE_PARA_LA_CLIENTA, es_violacion_de_exclusion
from app.core.disponibilidad import (
    CitaNoValida,
    hueco_concreto,
    huecos_del_dia,
    politica_del_bot,
    profesionales_del_servicio,
)
from app.core import zona as zonas
from app.core.security import get_service_postgrest_client
from app.core.telefonos import a_e164
from app.core.bot_tenant_token import BotSinAcceso, SalonSinBot, cliente_del_salon
from app.core.cuenta import con_la_cuenta_dentro, la_cuenta_de
from app.db.postgrest_client import PostgrestClient, PostgrestError
from app.schemas.publico import (
    DisponibilidadPublica,
    HuecoPublico,
    PaginaPublica,
    ProfesionalPublico,
    ReservaIn,
    ReservaOut,
    SalonPublico,
    ServicioPublico,
)

logger = logging.getLogger("estetix.publico")

router = APIRouter(prefix="/publico", tags=["publico"], dependencies=[publico.CUALQUIERA])

# La región con la que se normaliza el teléfono que teclea el cliente. Va atada
# a la misma zona que el resto del agendado: hoy España. Cuando `salons` tenga
# zona horaria y país propios (deuda anotada), sale de ahí.
REGION_TELEFONO = "ES"

# ── Anti-spam ───────────────────────────────────────────────────────────────
# Se cuenta CONTRA LA BASE DE DATOS y no en memoria del proceso, igual que el
# tope del bot. Con varias réplicas un contador por proceso deja pasar tantas
# ráfagas como réplicas haya, que es justo el escenario del que protege. Por eso
# NO se reutiliza `AdminLoginRateLimiter`, que sí es en memoria: sirve para el
# login de admin, no para una puerta abierta a internet.
MAX_POR_TELEFONO = 3
VENTANA_TELEFONO = timedelta(hours=24)
MAX_POR_SALON = 12
VENTANA_SALON = timedelta(minutes=15)

# Mensaje único para las cuatro puertas cerradas. Que sea el mismo es deliberado.
_NO_DISPONIBLE = "Esta página de reservas no está disponible."


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def _cerrado() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NO_DISPONIBLE)


async def _salon_por_slug(servicio: PostgrestClient, slug: str) -> dict:
    """De slug a salón. La única consulta con service_role de todo el fichero.

    Es la que AVERIGUA el salón, así que no puede hacerse con el token del
    inquilino: ese token todavía no existe. Está acotada a un select sobre una
    tabla y a las columnas que la página necesita.
    """
    try:
        filas = await servicio.select(
            "salons",
            {
                # La marca y la suspensión son de la cuenta desde la 0040; el
                # slug, los interruptores y el contacto siguen siendo del
                # centro, porque cada local tiene su propia página.
                "select": (
                    "id,nombre_comercial,direccion,telefono,account_id,zona_horaria,"
                    "reservas_online_activo,recordatorios_email_activo,"
                    "accounts(logo_url,accent_color,suspendido,plan,es_demo,"
                    "subscriptions(estado),salons(id,created_at,sigue_al_bajar))"
                ),
                "slug": f"eq.{slug.lower()}",
                "limit": "1",
            },
        )
    except PostgrestError as e:
        logger.warning("Público: no se pudo resolver el slug.")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo abrir la página de reservas.",
        ) from e

    if not filas:
        raise _cerrado()
    cuenta = la_cuenta_de(filas[0])
    salon = con_la_cuenta_dentro(filas[0])
    # Las tres puertas cerradas restantes. Mismo error que «no existe»: decirle
    # a un desconocido «ese salón existe pero está suspendido» es contar algo
    # que no le corresponde, y decirle «lo tiene apagado» o «ha dejado de
    # pagar» tampoco le sirve de nada.
    if (
        salon.get("suspendido")
        or not salon.get("reservas_online_activo")
        or not atiende_al_publico(estados_embebidos(salon), es_demo=bool(cuenta.get("es_demo")))
    ):
        raise _cerrado()

    # LA QUINTA PUERTA NO CIERRA LA PÁGINA, Y ES LA ÚNICA ASÍ. Las otras cuatro
    # son un salón que no existe, que está suspendido, que lo ha apagado o que
    # ha dejado de pagar. Este salón está abierto, paga, y tiene un QR en la
    # puerta: un 404 le quitaría reservas que habría cogido por teléfono. Se
    # abre la página con el teléfono a la vista y las reservas cerradas.
    incluido = lo_que_incluye(cuenta.get("plan"), centros_de_la_cuenta(cuenta))
    if not incluido.esta_activo(salon["id"]):
        logger.info(
            "Público: el centro %s está en pausa (plan %s); página abierta sin reservas.",
            salon["id"],
            incluido.plan,
        )
        salon["reservas_cerradas"] = True
    return salon


async def _db_del_salon(
    settings: Settings, servicio: PostgrestClient, salon_id: str
) -> PostgrestClient:
    """Cliente con el token del inquilino. Mismo mecanismo que el webhook."""
    try:
        return await cliente_del_salon(settings, servicio, salon_id)
    except (BotSinAcceso, SalonSinBot) as e:
        # El salón no tiene un admin utilizable con el que entrar. No es culpa
        # de quien mira, y no se le cuenta el motivo.
        logger.warning("Público: no se pudo acuñar token del salón %s.", salon_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Las reservas online no están disponibles ahora mismo.",
        ) from e


async def _contexto(
    slug: str, settings: Settings, servicio: PostgrestClient
) -> tuple[dict, PostgrestClient]:
    salon = await _salon_por_slug(servicio, slug)
    return salon, await _db_del_salon(settings, servicio, salon["id"])


# ═══════════════════════════════════════════════════════ leer


def _para_la_clienta(e: CitaNoValida) -> str:
    """Lo que se le puede contar a quien reserva desde la calle.

    ESTA FUNCIÓN EXISTE PORQUE AQUÍ HABÍA UNA TUBERÍA: `detail=e.mensaje`
    reenviaba al público, tal cual, cualquier texto que naciera dentro del motor
    de citas. Hoy ninguno de esos textos nombra otro centro, pero con la Fase B
    ya existen mensajes que sí —«Ana ya tiene una cita de 16:00 a 17:00 en
    Centro Norte»— y basta con que un día el camino público pase por
    `asegurar_sin_solape` para que una clienta lea el nombre de un local del que
    no tiene por qué saber nada.

    La regla es la misma que ya se aplica al no distinguir «servicio
    inexistente» de «servicio de otro salón»: al cliente no se le confirma nada
    que no le corresponda.
    """
    return MENSAJE_PARA_LA_CLIENTA if e.habla_de_otro_centro else e.mensaje


@router.get("/{slug}", response_model=PaginaPublica)
async def pagina(
    slug: str = Path(min_length=3, max_length=40),
    settings: Settings = Depends(get_settings),
    servicio: PostgrestClient = Depends(get_service_postgrest_client),
) -> dict:
    """Cabecera del salón y sus servicios reservables, en un solo viaje.

    Van juntos a propósito: con datos flojos, tres peticiones antes de ver nada
    es la diferencia entre reservar y cerrar la pestaña.
    """
    salon, db = await _contexto(slug, settings, servicio)

    try:
        filas = await db.select(
            "services",
            {
                "select": "id,nombre,precio,duracion_min",
                # ESTE CENTRO, Y NO LO DEJA EN MANOS DE LA RLS. El token ve la
                # cuenta entera: sin este filtro, la página de un centro ofrecía
                # también los servicios de su hermano, y al pulsarlos la
                # clienta se encontraba un error. Medido el 16-09-2026.
                "salon_id": f"eq.{salon['id']}",
                # La MISMA casilla que respeta el bot y que el salón ya gestiona
                # desde Servicios. Cero superficie nueva de producto.
                "visible_online": "eq.true",
                "order": "nombre.asc",
            },
        )
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudieron leer los servicios."
        ) from e

    return {
        "salon": SalonPublico(
            nombre=salon["nombre_comercial"],
            logo_url=salon.get("logo_url"),
            color=salon.get("accent_color") or "#C9A84C",
            direccion=salon.get("direccion"),
            telefono=salon.get("telefono"),
            # YA NO ES UN FALSE FIJO: sale del interruptor del salón. Si está
            # apagado, la pantalla no pinta la casilla y el correo ni se pide.
            recordatorios_email=bool(salon.get("recordatorios_email_activo")),
            reservas_cerradas=bool(salon.get("reservas_cerradas")),
            zona_horaria=zonas.zona(salon.get("zona_horaria")).key,
        ),
        # Campo a campo, no la fila. Si mañana `services` gana una columna, no
        # se publica sola.
        "servicios": [
            ServicioPublico(
                id=f["id"],
                nombre=f["nombre"],
                precio=f.get("precio"),
                duracion_min=f["duracion_min"],
            )
            for f in filas
        ],
    }


@router.get("/{slug}/profesionales", response_model=list[ProfesionalPublico])
async def profesionales(
    slug: str = Path(min_length=3, max_length=40),
    service_id: str = Query(...),
    settings: Settings = Depends(get_settings),
    servicio: PostgrestClient = Depends(get_service_postgrest_client),
) -> list[dict]:
    """Quién puede hacer ESTE servicio, para TODAS las fechas.

    La pantalla pide esta lista una sola vez, antes de elegir día, así que no
    puede depender de ninguno. Salía de los huecos de HOY y escondía para
    siempre a quien se quedaba sin hueco hoy: ver `profesionales_del_servicio`.
    El filtro es el del motor, así que no enseña a nadie que el motor descarte.
    """
    _salon, db = await _contexto(slug, settings, servicio)
    try:
        quienes = await profesionales_del_servicio(db, _salon["id"], service_id)
    except CitaNoValida:
        # Servicio inexistente o de otro salón. No se distinguen: al cliente le
        # da igual y distinguirlo confirmaría la existencia de ids.
        raise _cerrado()
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudo leer la agenda."
        ) from e

    return [ProfesionalPublico(id=e.id, nombre=e.nombre) for e in quienes]


@router.get("/{slug}/disponibilidad", response_model=DisponibilidadPublica)
async def disponibilidad(
    slug: str = Path(min_length=3, max_length=40),
    service_id: str = Query(...),
    fecha: str = Query(...),
    employee_id: str | None = Query(default=None),
    settings: Settings = Depends(get_settings),
    servicio: PostgrestClient = Depends(get_service_postgrest_client),
) -> dict:
    """Los huecos reales de un día, con la política del bot: 1 h de antelación y
    3 semanas de horizonte. Las mismas constantes, sin copiarlas."""
    _salon, db = await _contexto(slug, settings, servicio)
    try:
        dia = await huecos_del_dia(
            db,
            _salon["id"],
            service_id=service_id,
            fecha=fecha,
            employee_id=employee_id,
            politica=politica_del_bot(),
        )
    except CitaNoValida as e:
        raise HTTPException(status_code=e.status, detail=_para_la_clienta(e)) from e
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudo leer la agenda."
        ) from e

    # Copiado campo a campo. `dia` trae además el servicio completo, que la
    # pantalla ya tiene: no hace falta repetirlo por el cable.
    return {
        "fecha": dia["fecha"],
        "cerrado": dia["cerrado"],
        "motivo": dia.get("motivo"),
        "huecos": [
            HuecoPublico(
                inicio=h["inicio"],
                fin=h["fin"],
                hora=h["hora"],
                empleados=[ProfesionalPublico(id=e["id"], nombre=e["nombre"])
                           for e in h["empleados"]],
            )
            for h in dia.get("huecos", [])
        ],
    }


# ═══════════════════════════════════════════════════════ escribir


async def _pasa_del_limite(db: PostgrestClient, salon_id: str, telefono: str | None) -> bool:
    """¿Hay que frenar esta reserva?

    Dos contadores, contra la BD, y cada uno tapa el agujero del otro:

      · POR TELÉFONO — que un número reserve la agenda entera. Se cuenta sobre
        las citas ya creadas con ese teléfono, sin tabla nueva.
      · POR SALÓN — un script que va cambiando el teléfono en cada intento. El
        de arriba no lo vería; este sí, porque cuenta las reservas ONLINE
        recientes del salón, vengan de donde vengan.

    El segundo puede frenar a un cliente legítimo en una ráfaga rara. Se acepta:
    el tope es alto para un salón real y el mensaje invita a reintentar, mientras
    que sin él una sola tarde de script deja la agenda inservible.
    """
    ahora = _ahora()
    if telefono:
        desde = (ahora - VENTANA_TELEFONO).isoformat()
        try:
            fila = await db.select(
                "clients",
                {"select": "id", "salon_id": f"eq.{salon_id}",
                 "telefono": f"eq.{telefono}", "limit": "1"},
            )
        except PostgrestError:
            fila = []
        if fila:
            try:
                suyas = await db.select(
                    "appointments",
                    {"select": "id", "client_id": f"eq.{fila[0]['id']}",
                     "canal": "eq.online", "created_at": f"gte.{desde}",
                     "limit": str(MAX_POR_TELEFONO + 1)},
                )
            except PostgrestError:
                suyas = []
            if len(suyas) > MAX_POR_TELEFONO:
                return True

    desde_salon = (ahora - VENTANA_SALON).isoformat()
    try:
        recientes = await db.select(
            "appointments",
            {"select": "id", "salon_id": f"eq.{salon_id}", "canal": "eq.online",
             "created_at": f"gte.{desde_salon}", "limit": str(MAX_POR_SALON + 1)},
        )
    except PostgrestError:
        return False
    return len(recientes) > MAX_POR_SALON


@router.post("/{slug}/reservar", response_model=ReservaOut, status_code=status.HTTP_201_CREATED)
async def reservar(
    payload: ReservaIn,
    slug: str = Path(min_length=3, max_length=40),
    settings: Settings = Depends(get_settings),
    servicio: PostgrestClient = Depends(get_service_postgrest_client),
) -> dict:
    """Crea la cita. Confirmada y bloqueando el hueco, como la del bot.

    EL EMAIL YA NO SE IGNORA: si el salón tiene los recordatorios encendidos y
    quien reserva lo da, se guarda en su ficha. Es la mitad de la captación —
    la otra mitad es que a quien YA está en el CRM no se le vuelve a pedir.
    """
    salon, db = await _contexto(slug, settings, servicio)
    salon_id = salon["id"]

    # LA PÁGINA SE ABRE PERO AQUÍ NO SE ESCRIBE. La pantalla ya no enseña el
    # formulario, y esto es lo que lo impide de verdad: sin esta línea, una
    # petición a mano metería una cita en un centro al que no entra nadie a
    # atenderla. El texto no nombra el plan — la clienta no tiene por qué saber
    # nada de nuestra facturación — y el teléfono ya está en la cabecera.
    if salon.get("reservas_cerradas"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{SIN_RESERVAS_ONLINE} Llama al salón para pedir tu cita.",
        )

    telefono = a_e164(payload.telefono, REGION_TELEFONO)
    if not telefono:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ese teléfono no parece válido. Escríbelo con el prefijo si es de fuera.",
        )

    if await _pasa_del_limite(db, salon_id, telefono):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Se han hecho muchas reservas seguidas. Prueba de nuevo en un rato.",
        )

    try:
        inicio = datetime.fromisoformat(payload.inicio)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Esa hora no es válida."
        ) from None
    if inicio.tzinfo is None:
        # Una hora sin zona es una hora de pared DEL CENTRO que se reserva, no
        # la del servidor ni la de la clienta (que puede estar en Londres).
        inicio = zonas.instante_desde_reloj(inicio, zonas.zona(salon.get("zona_horaria")))

    # RE-VALIDACIÓN CONTRA LA BD. Entre que la pantalla pintó los huecos y esta
    # petición han pasado minutos, y el hueco puede haber volado.
    try:
        hueco = await hueco_concreto(
            db,
            salon_id,
            service_id=payload.service_id,
            inicio=inicio,
            employee_id=payload.employee_id,
            politica=politica_del_bot(),
        )
    except CitaNoValida as e:
        raise HTTPException(status_code=e.status, detail=_para_la_clienta(e)) from e
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudo comprobar la agenda."
        ) from e

    if hueco is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ese hueco acaba de ocuparse. Elige otra hora, por favor.",
        )

    # «Cualquiera disponible»: el primero, que es el que menos citas tiene ese
    # día. El orden lo pone el motor y es determinista.
    empleado = next((e for e in hueco.empleados if e.id == payload.employee_id), None)
    if empleado is None:
        empleado = hueco.empleados[0]

    client_id = await asegurar_cliente(
        db, salon_id, telefono_e164=telefono, nombre=payload.nombre
    )
    # EL CORREO, SI LO HA DADO Y EL SALÓN LO PIDE.
    #
    # Solo se escribe si la ficha no tenía ya uno: el del CRM lo puso el salón y
    # una página pública no puede pisarlo. Quien reserva puede equivocarse al
    # teclear, y sobrescribir dejaría al salón sin forma de escribir a ese
    # cliente por lo demás.
    if client_id and payload.email and salon.get("recordatorios_email_activo"):
        await _guardar_email_si_falta(db, client_id, payload.email)

    notas = None if client_id else f"Reserva online · tel. {telefono}"
    if not client_id:
        logger.warning("Público: cita creada sin ficha; el teléfono va en las notas.")

    try:
        cita = await db.insert(
            "appointments",
            {
                "salon_id": salon_id,
                "client_id": client_id,
                "employee_id": empleado.id,
                "service_id": payload.service_id,
                "inicio": hueco.inicio.isoformat(),
                "fin": hueco.fin.isoformat(),
                "notas": notas,
                # EL NOMBRE QUE ESCRIBIÓ QUIEN RESERVA, guardado en la CITA.
                #
                # La cita se enlaza a la ficha del teléfono —eso da continuidad
                # de CRM y no se toca— pero el nombre de la ficha NO se
                # sobrescribe: cualquiera con un teléfono podría renombrar a un
                # cliente del salón desde una página pública.
                #
                # Sin esto, una madre que reserva para su hija desde su propio
                # móvil deja al salón esperando a la madre. El salón ve los dos
                # nombres; quien reserva sigue viendo solo el suyo.
                "nombre_reserva": payload.nombre,
                # Los pone el SERVIDOR, nunca el cuerpo de la petición.
                "estado": "confirmada",
                "canal": "online",
            },
        )
    except PostgrestError as e:
        if es_violacion_de_exclusion(e):
            # Ha perdido la carrera contra otra reserva simultánea. Es el caso
            # que la comprobación de arriba no puede cubrir, porque entre mirar
            # e insertar cabe otra petición entera.
            logger.info("Público: hueco ocupado por una reserva simultánea.")
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Ese hueco acaba de ocuparse. Elige otra hora, por favor.",
            ) from e
        raise

    servicio_nombre, precio = await _servicio_legible(db, salon_id, payload.service_id)
    return {
        "id": cita["id"],
        "servicio": servicio_nombre,
        "profesional": empleado.nombre,
        "inicio": hueco.inicio.isoformat(),
        "fin": hueco.fin.isoformat(),
        "precio": precio,
        # EL NOMBRE TECLEADO, no el de la ficha. Si aquí se devolviera
        # `clients.nombre`, el widget sería un buscador de teléfono a nombre
        # sobre la clientela entera del salón.
        "nombre": payload.nombre,
    }


async def _guardar_email_si_falta(db: PostgrestClient, client_id: str, email: str) -> None:
    """Anota el correo en la ficha, pero NUNCA encima de uno que ya hubiera.

    SE VALIDA CON EL MISMO CRITERIO QUE EL DEL BOT (`core/email_valido.py`).
    Antes solo se recortaba a 120 caracteres, así que el `type="email"` del
    navegador era todo lo que separaba al CRM de guardar cualquier cosa. Y un
    correo que no existe no da un error visible: da un recordatorio que sale,
    rebota y queda anotado en `recordatorio_error` semanas después.

    UN CORREO MAL ESCRITO NO TUMBA LA RESERVA, y eso no cambia: si no valida,
    no se guarda y la cita sigue su camino. Quien reserva no puede perder una
    cita por una errata en un campo que era opcional; el aviso de que el correo
    no vale se lo da el navegador antes de enviar, que es donde puede
    corregirlo.

    Cualquier fallo se traga: la cita ya está creada y perder el correo del
    recordatorio no puede tumbar una reserva que el cliente da por hecha.
    """
    limpio = normalizar_email(email)
    if not limpio:
        logger.info("Público: el email de la reserva no es válido; no se guarda.")
        return
    try:
        await db.update(
            "clients",
            {"id": f"eq.{client_id}", "email": "is.null"},
            {"email": limpio},
        )
    except PostgrestError:
        logger.warning("Público: no se pudo guardar el email del recordatorio.")


async def _servicio_legible(
    db: PostgrestClient, salon_id: str, service_id: str
) -> tuple[str, float | None]:
    # El motor ya comprobó que el servicio es de este centro antes de crear la
    # cita. El `salon_id` va igual: esta función no puede depender de quién la
    # llame antes.
    try:
        filas = await db.select(
            "services",
            {
                "select": "nombre,precio",
                "id": f"eq.{service_id}",
                "salon_id": f"eq.{salon_id}",
                "limit": "1",
            },
        )
    except PostgrestError:
        return ("Tu cita", None)
    if not filas:
        return ("Tu cita", None)
    return (filas[0]["nombre"], filas[0].get("precio"))
