"""Teléfonos: reconciliar lo que guarda el CRM con lo que manda WhatsApp.

EL PROBLEMA, en una línea: el CRM guarda lo que teclea una persona
(«600 11 22 33», sin prefijo de país) y Meta manda E.164 sin el más
(«34600112233»). Comparar por igualdad no encuentra nada, y no encontrar al
cliente significa duplicarle la ficha en cada reserva del bot —y, en la fase de
cancelar, no encontrar sus citas—.

POR QUÉ NO SE NORMALIZA A MANO: quitar los no dígitos y comparar «parece»
suficiente hasta que se mira fuera de España. En Reino Unido el número se
escribe «020 7946 0958» y el 0 de delante es un prefijo TRONCAL que NO forma
parte del número internacional (+44 20 7946 0958): una normalización casera lo
dejaría dentro y no encontraría nunca a ese cliente. Cada país tiene sus reglas.
Por eso se usa `phonenumbers`, que las conoce todas, en vez de escribirlas aquí.

NO SE ASUME ESPAÑA. El proyecto se internacionalizará y aquí no hay ningún
«+34» salvo `REGION_POR_DEFECTO`, que es el último recurso documentado y vive en
un solo sitio. El país sale de `region_del_salon()`, que es TAMBIÉN el punto
donde mañana se leerá un campo país/zona horaria del inquilino: se cambia esa
función y nada más.
"""

from __future__ import annotations

import logging

import phonenumbers
from phonenumbers import (
    NumberParseException,
    PhoneNumberFormat,
    PhoneNumberType,
    ValidationResult,
)

logger = logging.getLogger(__name__)

# El ÚNICO país asumido en todo el proyecto, y solo cuando no hay forma de
# deducirlo. EstetixApp nació de ámbito español; en cuanto haya un salón fuera,
# lo correcto no es cambiar esta constante sino que `region_del_salon()` lea el
# país del inquilino (ver su docstring).
REGION_POR_DEFECTO = "ES"


def _solo_digitos(bruto: str) -> str:
    return "".join(c for c in bruto if c.isdigit())


def region_del_salon(telefono_del_salon: str | None) -> str:
    """El país del salón, en código ISO de dos letras («ES», «TH», «GB»).

    PUNTO ÚNICO. Todo lo que necesite saber de qué país es un salón pregunta
    aquí y no deduce nada por su cuenta.

    HOY se deduce del prefijo del PROPIO número del salón: el de su WhatsApp
    (`salon_whatsapp_config.display_phone_number`) donde lo haya, y si no el de
    su ficha (`salons.telefono`). Los dos son el mismo hecho —desde qué país
    atiende— y por eso entran por el mismo sitio; el segundo existe para todos
    los salones y el primero solo para los que tienen WhatsApp conectado, que
    es justo la diferencia entre poder deducir el país de un salón de Básico y
    no poder. Un salón que atiende desde un +66 es tailandés, y sus clientes
    escribirán con números tailandeses.

    MAÑANA vendrá de una columna país del salón, junto con su zona horaria (ver
    el backlog del spec de esta fase). Cuando exista, esta función pasa a leerla
    y el resto del código no se entera.
    """
    if not telefono_del_salon or not telefono_del_salon.strip():
        return REGION_POR_DEFECTO

    bruto = telefono_del_salon.strip()
    # Sin el más no hay prefijo de país que leer, así que se le pone: el número
    # del salón se enseña en internacional (es el que ve un cliente), y si
    # alguien lo guardó sin el más sigue siendo internacional.
    candidato = bruto if bruto.startswith("+") else "+" + _solo_digitos(bruto)
    try:
        numero = phonenumbers.parse(candidato, None)
    except NumberParseException:
        return REGION_POR_DEFECTO

    if not phonenumbers.is_valid_number(numero):
        return REGION_POR_DEFECTO
    return phonenumbers.region_code_for_number(numero) or REGION_POR_DEFECTO


def a_e164(bruto: str | None, region: str) -> str | None:
    """Un teléfono cualquiera en su forma canónica «+34600112233».

    `None` si no hay forma de leerlo como un número válido. Devolver `None` y no
    lanzar es deliberado: el CRM está lleno de teléfonos escritos por personas
    («no tengo», una extensión, un móvil a medias) y una ficha con el campo mal
    puesto no puede tumbar la respuesta del bot.

    ORDEN DE LOS INTENTOS, que importa: primero se lee como número LOCAL de la
    región, y solo si eso no da un número válido se prueba a leerlo como
    internacional. Al revés, un «600112233» español acabaría interpretado como
    un prefijo de país inventado.
    """
    if not bruto or not bruto.strip():
        return None

    texto = bruto.strip()
    if texto.startswith("+"):
        intentos = [(texto, None)]
    else:
        # Local primero, internacional después. Si el número ya trae su prefijo
        # de país (como el `wa_id` de Meta), el primer intento no dará un número
        # válido de la región y ganará el segundo.
        intentos = [(texto, region), ("+" + _solo_digitos(texto), None)]

    for candidato, region_candidata in intentos:
        try:
            numero = phonenumbers.parse(candidato, region_candidata)
        except NumberParseException:
            continue
        if phonenumbers.is_valid_number(numero):
            return phonenumbers.format_number(numero, PhoneNumberFormat.E164)
    return None


def telefono_legible(bruto: str | None, region: str) -> str | None:
    """El mismo número, escrito para que lo lea una persona: «+34 658 72 31 49».

    PARA ENSEÑAR, NUNCA PARA MARCAR. Lo que va en un `href="tel:"` es el número
    sin espacios; esto es solo lo que se ve. Son dos cosas distintas y por eso
    son dos valores distintos en el sitio que lo pinta.

    EL AGRUPAMIENTO NO SE ELIGE AQUÍ, lo pone `phonenumbers` según la región, y
    es el motivo de que esto no sea un `re.sub` de tres en tres: un móvil
    español se lee «658 72 31 49», un fijo de Londres «20 7946 0958» y uno de
    Bangkok «81 234 5678». Escribir la regla a mano habría funcionado en España
    y habría partido por sitios sin sentido en cuanto hubiera un salón fuera.

    SI EL NÚMERO NO ES VÁLIDO, devuelve `None` y quien llama enseña lo que haya
    guardado, tal cual. Es deliberado no maquillarlo: un número al que le sobra
    o le falta un dígito tampoco se puede marcar, y dejarlo con pinta de
    correcto esconde que el botón de llamar de ese salón no llama a nadie.
    """
    e164 = a_e164(bruto, region)
    if not e164:
        return None
    try:
        numero = phonenumbers.parse(e164, None)
    except NumberParseException:
        return None
    return phonenumbers.format_number(numero, PhoneNumberFormat.INTERNATIONAL)


class TelefonoNoValido(ValueError):
    """Un teléfono que no se puede marcar, con el motivo ya escrito.

    El motivo va en el mensaje y va en castellano llano a propósito: lo lee la
    persona que acaba de teclear el número en Ajustes, y «campo inválido» no le
    dice qué corregir. Que le sobre un dígito y que no exista ese prefijo son
    dos problemas distintos con dos arreglos distintos.
    """


def _ejemplo_de(region: str) -> str:
    """Un número de ejemplo de ESA región, para no dar por hecho España."""
    try:
        ejemplo = phonenumbers.example_number_for_type(region, PhoneNumberType.MOBILE)
        if ejemplo is not None:
            return phonenumbers.format_number(ejemplo, PhoneNumberFormat.INTERNATIONAL)
    except Exception:  # noqa: BLE001 — un ejemplo que falla no puede tumbar un guardado
        pass
    return "+34 612 34 56 78"


def revisar_telefono(bruto: str | None, region: str) -> str | None:
    """El teléfono en E.164, o `TelefonoNoValido` con el porqué.

    POR QUÉ EXISTE. El teléfono de Salón Demo tenía diez dígitos nacionales —uno
    de más para España— y se guardó sin que nada chistara. El botón «Llamar al
    salón» de todos sus recordatorios llevaba muerto desde el primer correo, y
    no se supo hasta que alguien fue a formatear ese número para otra cosa. Un
    campo que solo se comprueba cuando se usa es un campo sin comprobar.

    `None` (vacío) es válido: el teléfono es opcional. Lo que no es válido es
    tener algo que parece un teléfono y no lo es.

    DEVUELVE E.164, y quien llama decide si guardar eso o lo tecleado. Ver el
    router de Ajustes.
    """
    texto = (bruto or "").strip()
    if not texto:
        return None

    # Mismo orden de intentos que `a_e164`: primero como número local de la
    # región, y solo si eso no cuaja como internacional. Al revés, un
    # «600112233» español acabaría leído como un prefijo de país inventado.
    intentos = [(texto, None)] if texto.startswith("+") else [(texto, region), (texto, None)]

    ultimo: phonenumbers.PhoneNumber | None = None
    fallo_al_leer: int | None = None
    for candidato, region_candidata in intentos:
        try:
            numero = phonenumbers.parse(candidato, region_candidata)
        except NumberParseException as e:
            # Se guarda el motivo del PRIMER intento: cuando ni siquiera se
            # puede leer, la librería ya sabe por qué —un prefijo de país que no
            # existe no es lo mismo que una cadena con letras— y esa distinción
            # es justo la que le sirve a quien lo está escribiendo.
            fallo_al_leer = e.error_type if fallo_al_leer is None else fallo_al_leer
            continue
        if phonenumbers.is_valid_number(numero):
            return phonenumbers.format_number(numero, PhoneNumberFormat.E164)
        ultimo = ultimo or numero

    raise TelefonoNoValido(_por_que_no_vale(ultimo, fallo_al_leer, region))


def _por_que_no_vale(
    numero: "phonenumbers.PhoneNumber | None", fallo_al_leer: int | None, region: str
) -> str:
    """Qué le pasa a ese número, dicho para quien lo acaba de escribir."""
    ejemplo = f"Por ejemplo: {_ejemplo_de(region)}."
    if numero is None:
        if fallo_al_leer == NumberParseException.INVALID_COUNTRY_CODE:
            return f"Ese prefijo de país no existe. {ejemplo}"
        return f"Eso no se parece a un teléfono. Escríbelo solo con números. {ejemplo}"

    razon = phonenumbers.is_possible_number_with_reason(numero)
    if razon == ValidationResult.TOO_LONG:
        return f"Ese número tiene dígitos de más. Comprueba que no te sobre ninguno. {ejemplo}"
    if razon == ValidationResult.TOO_SHORT:
        return f"Ese número tiene dígitos de menos. {ejemplo}"
    if razon == ValidationResult.INVALID_COUNTRY_CODE:
        return f"Ese prefijo de país no existe. {ejemplo}"
    if razon == ValidationResult.INVALID_LENGTH:
        return f"Ese número no tiene una longitud que exista. {ejemplo}"
    # Longitud correcta pero ningún operador usa ese comienzo. Es el caso de un
    # número inventado: «+34 111 11 11 11» tiene nueve dígitos y no es de nadie.
    return f"Ese número tiene los dígitos que toca, pero no existe. {ejemplo}"


def de_whatsapp(wa_id: str | None, region: str) -> str | None:
    """El teléfono del cliente a partir del `wa_id` que manda Meta.

    Meta entrega SIEMPRE E.164 sin el más («34600112233»), así que este número
    se resuelve sin depender de ningún país: la región solo se usa como red por
    si algún día llegara en otro formato.
    """
    if not wa_id or not wa_id.strip():
        return None
    directo = a_e164("+" + _solo_digitos(wa_id.strip()), region)
    return directo or a_e164(wa_id, region)


def variantes_en_digitos(e164: str | None) -> list[str]:
    """Las formas EN DÍGITOS con que ese número puede estar guardado en el CRM.

    Es lo que se busca contra `clients.telefono_digitos` (columna generada en la
    migración 0030, que solo quita el ruido de formato). Para un +34600112233:

        34600112233   como lo manda Meta, o un CRM que guardó el internacional
        600112233     el formato local español, que es como lo teclea la gente

    Y para un +442079460958 hay una tercera, la que se escribe con el prefijo
    troncal: 02079460958. De ahí que esto no se pueda hacer «quitando el prefijo
    de país» a mano.

    Las variantes salen del número DEL CLIENTE, no del país del salón, así que
    un cliente extranjero de un salón español también se encuentra.
    """
    if not e164:
        return []
    try:
        numero = phonenumbers.parse(e164, None)
    except NumberParseException:
        return []
    if not phonenumbers.is_valid_number(numero):
        return []

    formas = [
        _solo_digitos(phonenumbers.format_number(numero, PhoneNumberFormat.E164)),
        phonenumbers.national_significant_number(numero),
        _solo_digitos(phonenumbers.format_number(numero, PhoneNumberFormat.NATIONAL)),
    ]

    # dict.fromkeys y no set(): el orden importa poco para el resultado, pero
    # tenerlo estable hace que los tests y los logs sean legibles.
    return [f for f in dict.fromkeys(formas) if f]


def mismo_numero(uno: str | None, otro: str | None, region: str) -> bool:
    """¿Son el mismo teléfono, aunque estén escritos de formas distintas?

    Se comparan sus E.164, así que «600112233» y «+34 600 112 233» son el mismo
    número, y «+34600112233» y «+66600112233» no lo son aunque compartan los
    nueve dígitos finales.

    Si alguno no es un número legible, la respuesta es `False`: no se sabe que
    sean el mismo, y dar por bueno un empate a ciegas significaría enlazar la
    cita a la ficha de otra persona.
    """
    a = a_e164(uno, region)
    b = a_e164(otro, region)
    return bool(a) and a == b
