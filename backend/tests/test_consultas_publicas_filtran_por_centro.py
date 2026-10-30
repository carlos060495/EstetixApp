"""Ninguna consulta pública cruza de un centro a otro sin que alguien lo decida.

EL FALLO QUE LO ORIGINA (16-09-2026). El bot, el widget y los recordatorios
entran con el token del admin de la cuenta, que ve todos sus centros. La página
pública pedía los servicios sin filtrar por centro, y Salon Centro ofrecía los
9 servicios de Salon Norte además del suyo. La frontera entre centros no puede
estar en la base —el motor de huecos necesita leer las citas de una persona en
los dos—, así que está aquí.

CÓMO FUNCIONA. `tests/consultas_del_token.py` saca del código, sin lista escrita
a mano, cada consulta que se puede hacer desde las puertas que usan ese token.
Las que filtran por centro pasan solas. Las que no, tienen que estar en
`SIN_FILTRO_DE_CENTRO`, una a una y con su motivo.

SI ESTE TEST FALLA porque has añadido una consulta:

  1. Lo primero es FILTRARLA POR CENTRO. Casi siempre es eso lo que falta.
  2. Solo si no puede llevar `salon_id` —va por el id de una fila que YA se leyó
     filtrando por este centro, o es de arranque con service_role—, apúntala
     aquí con el motivo. No copies el motivo de la de al lado: si no sabes
     escribir por qué no cruza centros, probablemente cruza.

Y si falla porque sobra una entrada: la consulta ya no existe o ya filtra por
centro. Quítala; una entrada huérfana es un permiso que nadie está usando.
"""

from __future__ import annotations

from tests.consultas_del_token import (
    alcanzables,
    consultas,
    leer,
    operaciones,
    puertas,
)

# ══════════════════════════════════════════ los motivos, escritos una vez

ARRANQUE = (
    "service_role, y es la consulta que AVERIGUA el centro (por slug, por número "
    "de Meta o la lista de centros con recordatorios): todavía no hay un "
    "salon_id con el que filtrar."
)
ADMIN_DE_LA_CUENTA = (
    "service_role: el admin de la CUENTA del centro, que es con quien se entra. "
    "Va por cuenta a propósito; por centro, el segundo centro no tenía a nadie."
)
SESION_DEL_CENTRO = (
    "Por el id de la sesión del bot, que se abrió o se leyó filtrando por este "
    "centro (`webhooks_whatsapp._sesion`)."
)
CLIENTA_DEL_CENTRO = (
    "Por el id de una clienta encontrada o creada en este centro "
    "(`buscar_por_telefono` / `asegurar_cliente`, que filtran por salon_id)."
)
ENLACE_SIN_CENTRO = (
    "`employee_services` no tiene salon_id. Los ids que recibe son de fichas y "
    "servicios leídos filtrando por este centro."
)
HORARIO_DE_FICHAS = (
    "El horario propio no tiene salon_id. Va por fichas que el motor ya leyó "
    "filtrando por este centro."
)
MISMA_PERSONA = (
    "CRUZA CENTROS A PROPÓSITO: las citas de una misma persona en todos sus "
    "centros, para no ofrecer una hora en la que ya está ocupada en el otro. "
    "Solo lee horas y fichas, nada de la clienta. Es la razón por la que esta "
    "frontera no puede estar en la base."
)
MENSAJE_DE_META = (
    "Por el wa_message_id, que Meta hace único en todo WhatsApp: solo pregunta "
    "si ese mensaje ya se guardó."
)
LATIDO = "service_role y sin centro: el latido de la tarea es de la plataforma."

SIN_FILTRO_DE_CENTRO: dict[str, str] = {
    # ── arranque, con service_role
    "routers.publico._salon_por_slug · select salons": ARRANQUE,
    "routers.webhooks_whatsapp._salon_del_numero · select salon_whatsapp_config": ARRANQUE,
    "tareas.recordatorios._salones_con_recordatorios · select_todo salons": ARRANQUE,
    "core.bot_tenant_token._identidad_del_salon · select users": ADMIN_DE_LA_CUENTA,
    "tareas.recordatorios._latir · upsert_many tareas_latido": LATIDO,
    "core.whatsapp_cloud._apuntar · rpc apuntar_envio": (
        "service_role, y la función de la base filtra por salón Y número (`p_salon`, "
        "`p_phone_number_id`): apunta el resultado de un envío en la fila de ESE número (0085)."
    ),
    # ── el mostrador (0090): `security._cliente_de` usa la custodia del bot, y
    #    con eso la sesión entera queda al alcance de este recorrido. Ninguna de
    #    estas va con el token del salón: son las que averiguan QUIÉN pide.
    "core.mostrador.dispositivo_de · select dispositivos_mostrador": (
        "service_role, por la huella de la cookie del dispositivo: es la consulta que AVERIGUA "
        "de qué centro es el mostrador. El centro sale de la fila."
    ),
    "core.mostrador.usar_la_gestion · update dispositivos_mostrador": (
        "service_role, por el id del dispositivo que se acaba de leer por su cookie: renueva la "
        "gestión de ESE dispositivo y nada más."
    ),
    "core.mostrador.apuntar_uso · update dispositivos_mostrador": (
        "service_role, por el id del dispositivo que se acaba de leer por su cookie: apunta el uso "
        "de ESE dispositivo y nada más."
    ),
    "core.mostrador.retirado · select dispositivos_mostrador": (
        "service_role, por la huella de una cookie que ya no vale: solo dice si alguien la retiró, "
        "para explicarlo en pantalla. No devuelve ni centro ni datos."
    ),
    "core.impersonation.cargar_contexto_sesion · select impersonation_sessions": (
        "service_role, por el sid de la cookie de soporte: la fila dice de qué centro es la sesión."
    ),
    "core.impersonation.cargar_contexto_sesion · update impersonation_sessions": (
        "service_role, por el sid de esa misma sesión de soporte ya leída: la cierra o la apunta."
    ),
    "core.tenant_token_broker._apuntar_la_sesion_de_soporte · update impersonation_sessions": (
        "service_role, por el sid de la sesión de soporte para la que se acaba de acuñar el token."
    ),
    # ── la conversación del bot
    "routers.webhooks_whatsapp._procesar_mensaje · select bot_messages": MENSAJE_DE_META,
    "routers.webhooks_whatsapp._procesar_mensaje · update bot_sessions": SESION_DEL_CENTRO,
    "routers.webhooks_whatsapp._avisar_de_ia · update bot_sessions": SESION_DEL_CENTRO,
    "routers.webhooks_whatsapp._historial · select bot_messages": SESION_DEL_CENTRO,
    "routers.webhooks_whatsapp._pasa_del_limite · select bot_messages": SESION_DEL_CENTRO,
    "routers.webhooks_whatsapp._ya_se_le_dijo_que_no · select bot_messages": SESION_DEL_CENTRO,
    "core.bot_herramientas._guardar_propuesta · update bot_sessions": SESION_DEL_CENTRO,
    "core.bot_herramientas._leer_propuesta · select bot_sessions": SESION_DEL_CENTRO,
    "core.bot_herramientas._olvidar_propuesta · update bot_sessions": SESION_DEL_CENTRO,
    "core.bot_herramientas._pasar_a_persona · select bot_sessions": SESION_DEL_CENTRO,
    "core.bot_herramientas._pasar_a_persona · update bot_sessions": SESION_DEL_CENTRO,
    "core.bot_herramientas._intentos_de_email · select bot_messages": SESION_DEL_CENTRO,
    # ── la clienta
    "core.bot_herramientas._decidir_el_ofrecimiento · select clients": CLIENTA_DEL_CENTRO,
    "core.bot_herramientas._guardar_email_cliente · update clients": CLIENTA_DEL_CENTRO,
    "core.bot_herramientas._no_quiere_recordatorios · update clients": CLIENTA_DEL_CENTRO,
    "routers.publico._guardar_email_si_falta · update clients": CLIENTA_DEL_CENTRO,
    "routers.publico._pasa_del_limite · select appointments": CLIENTA_DEL_CENTRO,
    # ── quién hace qué y cuándo
    "core.bot_herramientas._verificar_que_lo_realiza · select employee_services": ENLACE_SIN_CENTRO,
    "core.bot_herramientas._verificar_que_lo_realiza · select employee_services #2": ENLACE_SIN_CENTRO,
    "core.disponibilidad._empleados_que_lo_hacen · select employee_services": ENLACE_SIN_CENTRO,
    "core.horario_empleada.cargar_horarios_propios · select employee_schedule": HORARIO_DE_FICHAS,
    "core.horario_empleada.cargar_horarios_propios · select employee_schedule_exceptions": HORARIO_DE_FICHAS,
    "core.disponibilidad._citas_del_rango · select_todo appointments": MISMA_PERSONA,
    # ── los recordatorios: desde la 0086 van con la clave de servicio y cada
    # consulta lleva su centro, así que ya no necesitan ninguna excepción.
}


# ══════════════════════════════════════════════════════════ 1. la frontera


def test_toda_consulta_publica_filtra_por_centro_o_esta_apuntada_con_su_motivo():
    nuevas = [
        c for c in consultas() if not c.filtra_por_centro and c.clave not in SIN_FILTRO_DE_CENTRO
    ]
    assert not nuevas, (
        "Consultas que se pueden hacer con el token de la cuenta SIN filtrar por "
        "centro:\n"
        + "\n".join(f"  · {c.clave}   ({c.donde})" for c in nuevas)
        + "\n\nEse token ve TODOS los centros de la cuenta. Filtra por salon_id; "
        "solo si no puede ser, apúntala en SIN_FILTRO_DE_CENTRO con el motivo "
        "(lee la cabecera de este fichero antes)."
    )


def test_no_hay_apuntadas_que_ya_no_existan_o_ya_filtren():
    """Al revés: una entrada que sobra suele ser una consulta renombrada o movida,
    y su permiso seguiría ahí esperando a que otra ocupe el nombre."""
    sin_filtro = {c.clave for c in consultas() if not c.filtra_por_centro}
    sobran = sorted(set(SIN_FILTRO_DE_CENTRO) - sin_filtro)
    assert not sobran, f"Entradas de SIN_FILTRO_DE_CENTRO que ya no hacen falta: {sobran}"


def test_cada_entrada_tiene_un_motivo_de_verdad():
    for clave, motivo in SIN_FILTRO_DE_CENTRO.items():
        assert len(motivo.split()) >= 8, f"«{clave}» necesita un motivo, no una etiqueta"


# ═══════════════════════════════════ 2. el control: que no pase en vacío


def test_las_tres_puertas_se_encuentran_y_el_panel_no_es_una():
    """Si el lector dejara de reconocer el import del token, no habría puertas,
    no habría consultas, y los dos tests de arriba pasarían sin mirar nada."""
    encontradas = puertas(leer())
    assert {
        "routers.publico",
        "routers.webhooks_whatsapp",
        "tareas.recordatorios",
    } <= encontradas
    # El panel entra con la sesión de quien lo usa, no con este token.
    assert "routers.appointments" not in encontradas


def test_el_recorrido_llega_hasta_el_fondo():
    """Funciones que solo se alcanzan cruzando módulos o por la tabla de
    despacho de las herramientas del bot. Si el recorrido se quedara en las
    puertas, estas consultas no se verían."""
    llegan = alcanzables(leer())
    for esperada in [
        ("core.bot_herramientas", "_resolver_servicio"),
        ("core.bot_herramientas", "_ejecutar_cancelacion"),
        ("core.disponibilidad", "_citas_del_rango"),
        ("core.horario_empleada", "cargar_horarios_propios"),
        ("core.bot_conocimiento", "cargar_conocimiento"),
        ("core.bot_tenant_token", "_identidad_del_salon"),
        ("core.clientes", "buscar_por_telefono"),
    ]:
        assert esperada in llegan, f"el recorrido no llega a {esperada}"


def test_el_detector_conoce_todas_las_formas_de_hablar_con_la_base():
    assert {"select", "insert", "insert_many", "upsert_many", "update", "delete", "rpc"} <= operaciones()


def test_el_detector_distingue_en_el_codigo_real_lo_que_filtra_de_lo_que_no():
    """Cinco consultas que se sabe cómo son, en los dos sentidos y por las
    tres formas de escribir el filtro: en el sitio, en una variable y devuelto
    por una función."""
    por_clave = {c.clave: c.filtra_por_centro for c in consultas()}
    esperado = {
        # El arreglo del 16-09: en el sitio.
        "routers.publico.pagina · select services": True,
        # En una variable (`params`).
        "core.disponibilidad._empleados_que_lo_hacen · select employees": True,
        # Devuelto por `_solo_esta_cita`.
        "core.bot_herramientas._ejecutar_cancelacion · update appointments": True,
        # La tabla `salons` se filtra por su `id`.
        "core.bot_conocimiento.cargar_conocimiento · select salons": True,
        # Y la que cruza centros a propósito, que NO filtra.
        "core.disponibilidad._citas_del_rango · select_todo appointments": False,
    }
    for clave, filtra in esperado.items():
        assert clave in por_clave, f"el detector no encuentra «{clave}»"
        assert por_clave[clave] is filtra, f"«{clave}» mal clasificada"


# ═══════════════════ 3. y que de verdad se pone rojo al añadir una nueva

_TOKEN = "async def cliente_del_salon(settings, servicio, salon_id):\n    return None\n"


def test_el_fallo_del_16_de_septiembre_se_habria_visto():
    """La consulta tal y como estaba, en una puerta cualquiera."""
    fuentes = {
        "core.bot_tenant_token": _TOKEN,
        "routers.publico": '''
from app.core.bot_tenant_token import cliente_del_salon

async def pagina(slug, settings, servicio, salon):
    db = await cliente_del_salon(settings, servicio, salon["id"])
    filas = await db.select(
        "services",
        {"select": "id,nombre,precio,duracion_min", "visible_online": "eq.true", "order": "nombre.asc"},
    )
    return filas
''',
    }
    [consulta] = consultas(leer(fuentes))
    assert consulta.clave == "routers.publico.pagina · select services"
    assert consulta.filtra_por_centro is False


def test_una_consulta_nueva_en_un_modulo_nuevo_tambien_se_ve():
    """Nada de lista de ficheros: una puerta nueva que llama a un ayudante nuevo,
    que despacha por una tabla, con el filtro en el sitio, en una variable o
    sin él. Las tres se encuentran y solo la última queda sin filtro."""
    fuentes = {
        "core.bot_tenant_token": _TOKEN,
        "routers.puerta_nueva": '''
from app.core.bot_tenant_token import cliente_del_salon
from app.core.ayudante_nuevo import listar

async def entrar(salon_id):
    db = await cliente_del_salon(None, None, salon_id)
    return await listar(db, salon_id)
''',
        "core.ayudante_nuevo": '''
async def _empleadas(db, salon_id):
    return await db.select("employees", {"select": "id", "salon_id": f"eq.{salon_id}"})

async def _citas(db, salon_id):
    params = {"select": "id"}
    params["salon_id"] = f"eq.{salon_id}"
    return await db.select("appointments", params)

async def _productos(db, salon_id):
    return await db.select("products", {"select": "id,nombre"})

_DESPACHO = {"empleadas": _empleadas, "citas": _citas, "productos": _productos}

async def listar(db, salon_id):
    return [await f(db, salon_id) for f in _DESPACHO.values()]
''',
    }
    clasificadas = {c.clave: c.filtra_por_centro for c in consultas(leer(fuentes))}
    assert clasificadas == {
        "core.ayudante_nuevo._empleadas · select employees": True,
        "core.ayudante_nuevo._citas · select appointments": True,
        "core.ayudante_nuevo._productos · select products": False,
    }


def test_una_segunda_consulta_igual_en_la_misma_funcion_no_se_esconde():
    """Si la primera ya estaba apuntada, la segunda tiene que pedir su sitio."""
    fuentes = {
        "core.bot_tenant_token": _TOKEN,
        "routers.puerta": '''
from app.core.bot_tenant_token import cliente_del_salon

async def entrar(sesion_id):
    db = await cliente_del_salon(None, None, "x")
    await db.select("bot_sessions", {"id": f"eq.{sesion_id}"})
    await db.select("bot_sessions", {"customer_phone": "eq.600"})
''',
    }
    claves = [c.clave for c in consultas(leer(fuentes))]
    assert claves == [
        "routers.puerta.entrar · select bot_sessions",
        "routers.puerta.entrar · select bot_sessions #2",
    ]
