"""El correo del recordatorio: qué dice y qué no.

QUÉ DICE: servicio, cuándo, con quién y dónde. Lo justo para que alguien sepa
si le viene bien y sepa llegar.

QUÉ NO DICE, y es la mitad del diseño:

  · NADA DE PROMOCIONES. Este correo se manda a gente que NO ha dado su
    consentimiento para recibir publicidad: se le escribe porque tiene una cita
    que ella misma pidió, y eso lo convierte en un mensaje de servicio. Meter
    aquí una oferta —aunque sea pequeña, aunque sea del propio salón— lo
    convertiría en marketing sin permiso.
  · NADA DE OTRAS CITAS, ni el historial, ni cuánto ha gastado. Un correo se
    reenvía, se archiva y acaba en sitios que nadie eligió.

Y DICE POR QUÉ LO RECIBE, con la baja al lado. Un correo automático sin salida
no es aceptable, y menos cuando el fundamento no es un consentimiento explícito:
si alguien no lo quiere, tiene que poder decirlo sin llamar a nadie.
"""

from __future__ import annotations

import html
from zoneinfo import ZoneInfo

from app.core.config import Settings
from app.core.emails import construir_email_branded
from app.core import zona as zonas
from app.core.mapa import enlace_de_mapa
from app.core.telefonos import region_del_salon, telefono_legible

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


# Dentro de una palabra, lo que abre una parte nueva: «ana-maría» son dos, y
# «o'connor» también. El apóstrofe tipográfico va porque los teclados de móvil
# lo ponen solos.
SEPARADORES_INTERNOS = "-'’"


def capitalizar_nombre(bruto: str | None) -> str:
    """«lucas» → «Lucas». SOLO PARA ENSEÑAR: el CRM guarda lo que se tecleó.

    POR QUÉ EXISTE: la gente escribe su nombre en minúscula al reservar, por el
    widget y por WhatsApp, y eso no se va a arreglar pidiéndoselo. Pero el
    saludo de un correo del salón no puede decir «Hola lucas». Lo que se
    escribe en la base es dato de la persona y no se toca; lo que se pinta es
    decisión nuestra.

    LA REGLA ES DELIBERADAMENTE TONTA: primera letra de cada palabra, y una
    palabra que YA traiga alguna mayúscula se deja intacta. Eso deja
    «McDonald», «O'Connor» y «DJ» como estaban, y a cambio convierte
    «van der berg» en «Van Der Berg», que no es como se escribe.

    Es el error que se elige a sabiendas. La alternativa —una lista de
    partículas (de, del, van, bin, y…)— se equivoca en el sentido contrario y
    peor: «Van Gogh» es un apellido en el que «Van» va en mayúscula, «Ana de
    la O» lleva la O suelta, y hay apellidos que son la partícula entera. Una
    mayúscula de más se lee como un estilo; una de menos, en el apellido de
    alguien, se lee como un error sobre su nombre.

    Y NO SE TOCA NADA MÁS que los nombres de personas: ni el del salón —una
    marca puede ser minúscula a propósito— ni el servicio ni la dirección.
    """
    if not bruto:
        return ""
    return " ".join(_capitalizar_palabra(p) for p in bruto.strip().split())


def _capitalizar_palabra(palabra: str) -> str:
    """Una palabra suelta. Si ya trae mayúsculas, se devuelve tal cual."""
    if any(c.isupper() for c in palabra):
        return palabra

    fuera: list[str] = []
    empieza_parte = True
    for c in palabra:
        fuera.append(c.upper() if empieza_parte and c.isalpha() else c)
        if c.isalpha():
            empieza_parte = False
        elif c in SEPARADORES_INTERNOS:
            empieza_parte = True
    return "".join(fuera)


def _cuando(iso: str | None, z: ZoneInfo) -> str:
    """«mañana martes 3 de septiembre a las 12:15».

    En la zona del salón, no en UTC: el correo lo lee una persona que va a
    presentarse a una hora, y esa hora es la del reloj de la puerta.
    """
    if not iso:
        return "la hora indicada"
    try:
        dia = zonas.fecha_local(iso, z)
    except ValueError:
        # Ilegible o sin zona: mejor no decir hora que decir una equivocada.
        return "la hora indicada"
    return f"{DIAS[dia.weekday()]} {dia.day} de {MESES[dia.month - 1]} a las {zonas.hhmm(iso, z)}"


def _url_baja(settings: Settings, token: str | None) -> str | None:
    if not token:
        return None
    return f"{settings.frontend_base_url.rstrip('/')}/recordatorios/baja/{token}"


# Los dos colores de la ficha, y el orden importa: el VALOR es lo que la
# persona necesita leer —qué servicio, qué día, con quién— y la etiqueta solo
# dice qué es. Sobre el panel #141416 el valor da 17:1 de contraste y la
# etiqueta 7,3:1, así que la jerarquía se ve pero nada queda por debajo de lo
# legible. Antes la etiqueta era #7a7568 (4,1:1) y el valor NO TENÍA COLOR.
COLOR_ETIQUETA = "#A1A1AA"
COLOR_VALOR = "#F4F4F5"
COLOR_SECUNDARIO = "#9A9AA3"


def _fila(etiqueta: str, valor: str | None, enlace: str | None = None) -> str:
    """Una línea de la ficha. Se omite entera si no hay valor.

    Enseñar «Dónde: —» es peor que no enseñar nada: quien lo lee se queda igual
    y encima con la sensación de que falta algo.

    EL VALOR LLEVA SU COLOR ESCRITO, y ese es el arreglo del modo oscuro. Antes
    no lo llevaba: lo heredaba del `<p>` de la plantilla. Un cliente de correo
    no respeta esa herencia como un navegador —Gmail reescribe el HTML y
    remapea colores para su tema oscuro—, y lo que no declara color es
    exactamente lo que acaba con el que el cliente decida. El resultado era que
    los datos de la cita se veían más apagados que sus etiquetas, que sí lo
    declaraban: al revés de la jerarquía que se había diseñado.
    """
    if not valor:
        return ""
    texto = html.escape(valor)
    if enlace:
        # EL ENLACE LLEVA SU COLOR Y SU SUBRAYADO ESCRITOS, igual que el de la
        # baja y por el mismo motivo: un `<a>` sin color se pinta con el azul
        # por defecto del cliente de correo, que sobre este fondo casi no se ve.
        #
        # Y ES DELIBERADO: se escribe aquí, no lo fabrica el parser del cliente
        # de correo. Ya pasó con los teléfonos —salían como enlaces que no
        # llevaban a ningún sitio—, así que este se comprueba en un buzón.
        texto = (
            f'<a href="{html.escape(enlace)}" style="color:{COLOR_VALOR};'
            f' text-decoration:underline">{texto}</a>'
        )
    return (
        '<tr>'
        f'<td style="padding:4px 0;color:{COLOR_ETIQUETA};font-size:13px">'
        f'{html.escape(etiqueta)}</td>'
        f'<td style="padding:4px 0;text-align:right;font-weight:600;font-size:13px;'
        f'color:{COLOR_VALOR}">{texto}</td>'
        "</tr>"
    )


def cuerpo_del_recordatorio(
    settings: Settings, salon: dict, cita: dict
) -> tuple[str, str]:
    """El asunto y el HTML. Devuelve `(asunto, html)`.

    Se reutiliza `construir_email_branded`, la misma plantilla del aviso de
    handoff, para que un correo del salón se parezca a otro correo del salón.

    EL CTA ES UNA LLAMADA, y no es un apaño: la plantilla exige un enlace de
    acción, y hoy cancelar o cambiar la cita solo se puede hacer hablando con el
    salón —ni el bot ni el widget cancelan todavía—. Un botón `tel:` es
    exactamente la acción que sí existe. Si el salón no tiene teléfono, el
    enlace cae a su página pública de reservas.
    """
    cliente = cita.get("clients") or {}
    servicio = (cita.get("services") or {}).get("nombre")
    # Los DOS nombres de persona del correo, y los únicos que se capitalizan.
    profesional = capitalizar_nombre((cita.get("employees") or {}).get("nombre")) or None
    nombre_salon = salon.get("nombre_comercial") or "tu salón"
    telefono = (salon.get("telefono") or "").strip()
    direccion = (salon.get("direccion") or "").strip() or None

    # A quién se saluda: el nombre con el que se RESERVÓ si lo hay —puede que
    # la cita sea para otra persona— y si no, el de la ficha.
    saludo = capitalizar_nombre(cita.get("nombre_reserva") or cliente.get("nombre"))

    # En la zona DEL CENTRO, que viaja en la fila del salón (0060).
    cuando = _cuando(cita.get("inicio"), zonas.zona(salon.get("zona_horaria")))
    intro = (
        f"{'Hola ' + html.escape(saludo) + ': t' if saludo else 'T'}e recordamos tu cita "
        f"en <strong>{html.escape(nombre_salon)}</strong> el {html.escape(cuando)}. "
        "No hace falta que confirmes nada."
        "<table style=\"width:100%;margin-top:16px;border-collapse:collapse\">"
        + _fila("Servicio", servicio)
        + _fila("Cuándo", cuando.capitalize())
        + _fila("Con", profesional)
        # LA NOCHE ANTES DE IR POR PRIMERA VEZ es cuando se mira esto, así que
        # la dirección se toca y abre el mapa. Sin dirección la fila no sale —
        # `_fila` no pinta lo que no hay—, que es mejor que un «Dónde: —».
        + _fila("Dónde", direccion, enlace_de_mapa(direccion, nombre_salon))
        + "</table>"
        f"<p style=\"margin-top:16px;font-size:13px;color:{COLOR_SECUNDARIO}\">"
        "¿Necesitas cambiarla o cancelarla? Llámanos y lo vemos."
        "</p>"
    )

    baja = _url_baja(settings, cliente.get("recordatorios_token"))
    pie = f"Recibes este aviso porque tienes una cita en {nombre_salon}."
    if baja:
        # La baja va en el pie y con su enlace, no escondida tras «responde a
        # este correo». Y se dice qué apaga exactamente, para que nadie tema
        # perder también el resto de avisos de su cita.
        #
        # CON SU COLOR PUESTO: un `<a>` sin color se pinta con el azul por
        # defecto del cliente de correo, que sobre este fondo casi no se ve. Es
        # el enlace que menos se puede permitir ser invisible.
        pie += (
            f' <a href="{html.escape(baja)}" style="color:#C9A84C;'
            ' text-decoration:underline;">No quiero recibir recordatorios</a>.'
        )

    # EL NÚMERO, LEGIBLE EN EL BOTÓN Y MARCABLE EN EL ENLACE. Son dos formas del
    # mismo dato y ninguna sirve para lo de la otra: «+34 658 72 31 49» no se
    # marca y «+34658723149» no se lee.
    #
    # LA REGIÓN SALE DE `region_del_salon`, que es el punto único del proyecto
    # para eso. Y HAY QUE SER HONESTO CON LO QUE ESO DA HOY: `salons.telefono`
    # es el único dato de país que llega hasta aquí, así que
    #
    #   · si el número trae su prefijo (+44 20 7946 0958), se agrupa bien —pero
    #     porque lo trae él, no porque se haya deducido nada—;
    #   · si está guardado en formato nacional (020 7946 0958), no hay de dónde
    #     sacar el país: se cae en `REGION_POR_DEFECTO` y el número se enseña
    #     tal cual, sin formatear.
    #
    # O sea que esto NO resuelve la internacionalización: la deja preparada. El
    # día que el salón tenga su columna de país, `region_del_salon` la lee y
    # este código no se entera — que es justo para lo que existe esa función.
    #
    # Y si el número guardado no es válido se enseña TAL CUAL, sin maquillar: un
    # número al que le sobra un dígito tampoco se puede marcar, y dejarlo con
    # pinta de correcto esconde que ese botón no llama a nadie.
    legible = telefono_legible(telefono, region_del_salon(telefono)) or telefono

    html_cuerpo = construir_email_branded(
        title="Mañana tienes cita" if "mañana" in cuando else "Tienes una cita",
        intro=intro,
        cta_label=f"Llamar al salón · {legible}" if telefono else "Ver el salón",
        action_link=(
            f"tel:{telefono.replace(' ', '')}"
            if telefono
            else settings.frontend_base_url
        ),
        footer_note=pie,
    )
    return (f"Recordatorio de tu cita en {nombre_salon}", html_cuerpo)
