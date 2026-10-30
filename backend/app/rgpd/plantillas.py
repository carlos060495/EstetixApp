"""Textos de las plantillas legales RGPD (texto fijo con placeholders).

El cuerpo legal es SIEMPRE fijo (vive en el repo, sin input de usuario):
los únicos valores interpolados son los datos del salón leídos de la BD
por el salon_id del JWT ({nombre_comercial}) y la fecha de generación
({fecha}); el CIF y la dirección solo se pintan en el bloque "Responsable
del tratamiento" (con línea en blanco si faltan). Los campos del CLIENTE
se dejan como líneas rellenables a mano.

Restricción de fpdf2: las fuentes core son latin-1, así que estos textos
evitan caracteres fuera de ese repertorio (sin em-dash, comillas curvas
ni símbolo euro).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Bloque:
    """Sección del documento: título + párrafos y/o elementos rellenables."""

    titulo: str | None = None
    parrafos: tuple[str, ...] = ()
    # Etiquetas que se pintan con una línea en blanco para rellenar a mano.
    lineas: tuple[str, ...] = ()
    # Opciones con casilla "[   ]" para marcar a mano.
    casillas: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlantillaRgpd:
    doc_id: str
    titulo: str
    filename: str
    bloques: tuple[Bloque, ...] = field(default_factory=tuple)
    con_firma: bool = True


_DERECHOS = (
    "Puede ejercer sus derechos de acceso, rectificación, supresión, oposición, "
    "limitación del tratamiento y portabilidad dirigiéndose al responsable en la "
    "dirección indicada en este documento. Si considera que el tratamiento no se "
    "ajusta a la normativa, puede presentar una reclamación ante la Agencia "
    "Española de Protección de Datos (www.aepd.es)."
)


TRATAMIENTO = PlantillaRgpd(
    doc_id="tratamiento",
    titulo="Consentimiento para el tratamiento de datos personales",
    filename="rgpd-consentimiento-tratamiento-datos.pdf",
    bloques=(
        Bloque(
            parrafos=(
                "De conformidad con el Reglamento (UE) 2016/679 (RGPD) y la Ley "
                "Orgánica 3/2018 (LOPDGDD), se informa a la persona abajo firmante "
                "de que sus datos personales serán tratados por {nombre_comercial} "
                "como responsable del tratamiento, en los términos que se detallan "
                "a continuación.",
            ),
        ),
        Bloque(
            titulo="Finalidades del tratamiento",
            parrafos=(
                "1. Gestión de la agenda y de las citas: alta como cliente, "
                "programación de servicios, avisos y recordatorios de cita.",
                "2. Ficha de cliente e historial: registro de los servicios y "
                "tratamientos realizados para asegurar su correcta prestación y "
                "seguimiento.",
                "3. Datos de salud declarados por el cliente (alergias, "
                "sensibilidades o condiciones relevantes para los tratamientos): se "
                "tratan únicamente para prevenir reacciones adversas y adaptar el "
                "servicio, y requieren su consentimiento explícito (art. 9.2.a "
                "RGPD).",
                "4. Facturación y obligaciones legales derivadas de la prestación "
                "del servicio.",
            ),
        ),
        Bloque(
            titulo="Legitimación",
            parrafos=(
                "La base jurídica del tratamiento es la ejecución de la relación de "
                "servicios solicitada (art. 6.1.b RGPD), el consentimiento del "
                "interesado para las finalidades que lo requieren (arts. 6.1.a y "
                "9.2.a RGPD) y el cumplimiento de obligaciones legales aplicables "
                "(art. 6.1.c RGPD).",
            ),
        ),
        Bloque(
            titulo="Conservación",
            parrafos=(
                "Los datos se conservarán mientras se mantenga la relación con el "
                "cliente y, una vez finalizada, bloqueados durante los plazos de "
                "prescripción legal aplicables (en particular, los derivados de "
                "obligaciones fiscales y de facturación).",
            ),
        ),
        Bloque(
            titulo="Destinatarios",
            parrafos=(
                "No se cederán datos a terceros salvo obligación legal. Podrán "
                "acceder a los datos los proveedores tecnológicos que prestan "
                "servicios al responsable como encargados del tratamiento, con "
                "contrato conforme al art. 28 RGPD.",
            ),
        ),
        Bloque(
            titulo="Derechos",
            parrafos=(_DERECHOS,),
        ),
        Bloque(
            titulo="Consentimientos otorgados (marque lo que proceda)",
            casillas=(
                "CONSIENTO el tratamiento de mis datos identificativos y de "
                "contacto para la gestión de citas y de mi ficha de cliente.",
                "CONSIENTO de forma explícita el tratamiento de los datos de salud "
                "que yo declare (alergias, sensibilidades) para la seguridad de los "
                "tratamientos.",
                "CONSIENTO recibir comunicaciones comerciales y promociones del "
                "salón (opcional; puede retirarse en cualquier momento).",
            ),
        ),
        Bloque(
            titulo="Datos del cliente",
            lineas=(
                "Nombre y apellidos",
                "DNI / NIE",
                "Teléfono",
                "Correo electrónico",
            ),
        ),
    ),
)


PRIVACIDAD = PlantillaRgpd(
    doc_id="privacidad",
    titulo="Cláusula informativa de privacidad",
    filename="rgpd-clausula-informativa-privacidad.pdf",
    bloques=(
        Bloque(
            parrafos=(
                "En cumplimiento del deber de información de los arts. 13 y 14 del "
                "Reglamento (UE) 2016/679 (RGPD) y del art. 11 de la LOPDGDD, se "
                "facilita la siguiente información básica sobre protección de datos "
                "aplicable a los clientes de {nombre_comercial}.",
            ),
        ),
        Bloque(
            titulo="Finalidad",
            parrafos=(
                "Gestión de citas y de la relación comercial, mantenimiento de la "
                "ficha de cliente con el historial de servicios y, solo si se ha "
                "consentido, envío de comunicaciones comerciales.",
            ),
        ),
        Bloque(
            titulo="Legitimación",
            parrafos=(
                "Ejecución de la relación de servicios, consentimiento del "
                "interesado y cumplimiento de obligaciones legales.",
            ),
        ),
        Bloque(
            titulo="Destinatarios",
            parrafos=(
                "No se ceden datos a terceros salvo obligación legal. Acceden a los "
                "datos los proveedores tecnológicos del responsable en calidad de "
                "encargados del tratamiento (art. 28 RGPD).",
            ),
        ),
        Bloque(
            titulo="Conservación",
            parrafos=(
                "Durante la vigencia de la relación con el cliente y, después, "
                "bloqueados durante los plazos de prescripción legal.",
            ),
        ),
        Bloque(
            titulo="Derechos",
            parrafos=(_DERECHOS,),
        ),
        Bloque(
            titulo="Recepción de la información (a rellenar por el cliente)",
            lineas=(
                "Nombre y apellidos",
                "DNI / NIE",
            ),
        ),
    ),
)


IMAGEN = PlantillaRgpd(
    doc_id="imagen",
    titulo="Consentimiento para el uso de imagen",
    filename="rgpd-consentimiento-imagen.pdf",
    bloques=(
        Bloque(
            parrafos=(
                "El derecho a la propia imagen está protegido por el art. 18.1 de "
                "la Constitución Española y regulado por la Ley Orgánica 1/1982; su "
                "tratamiento como dato personal se rige por el Reglamento (UE) "
                "2016/679 (RGPD) y la LOPDGDD.",
                "La persona abajo firmante AUTORIZA a {nombre_comercial} a captar "
                "fotografías y/o vídeos de los resultados de sus servicios y "
                "tratamientos (imágenes del antes y el después) y a publicarlos, "
                "sin contraprestación económica y sin límite temporal salvo "
                "revocación, en los canales marcados a continuación.",
            ),
        ),
        Bloque(
            titulo="Canales autorizados (marque lo que proceda)",
            casillas=(
                "Redes sociales del salón (Instagram, Facebook, TikTok u otras).",
                "Página web y perfiles de negocio del salón.",
                "Material promocional impreso o digital del salón.",
            ),
        ),
        Bloque(
            titulo="Condiciones y revocación",
            parrafos=(
                "Las imágenes se utilizarán exclusivamente para mostrar el trabajo "
                "del salón, sin asociarse a datos de salud y evitando cualquier uso "
                "que pueda perjudicar la dignidad de la persona. Este consentimiento "
                "puede revocarse en cualquier momento dirigiéndose al responsable, "
                "sin efectos retroactivos sobre los usos ya realizados.",
                _DERECHOS,
            ),
        ),
        Bloque(
            titulo="Datos del cliente",
            lineas=(
                "Nombre y apellidos",
                "DNI / NIE",
            ),
        ),
    ),
)


BAJA = PlantillaRgpd(
    doc_id="baja",
    titulo="Solicitud de baja y supresión de datos",
    filename="rgpd-solicitud-baja-supresion.pdf",
    bloques=(
        Bloque(
            parrafos=(
                "Mediante el presente escrito, la persona abajo identificada "
                "ejerce ante {nombre_comercial}, como responsable del tratamiento, "
                "el derecho de supresión reconocido en el art. 17 del Reglamento "
                "(UE) 2016/679 (RGPD) y en el art. 15 de la LOPDGDD.",
            ),
        ),
        Bloque(
            titulo="Datos del solicitante",
            lineas=(
                "Nombre y apellidos",
                "DNI / NIE",
                "Teléfono",
                "Correo electrónico",
            ),
        ),
        Bloque(
            titulo="Alcance de la solicitud (marque lo que proceda)",
            casillas=(
                "BAJA TOTAL: supresión de todos mis datos personales de los "
                "ficheros del responsable.",
                "BAJA DE COMUNICACIONES: dejar de recibir comunicaciones "
                "comerciales, manteniendo mi ficha de cliente.",
            ),
        ),
        Bloque(
            titulo="Tramitación",
            parrafos=(
                "El responsable atenderá esta solicitud a la mayor brevedad y, en "
                "todo caso, en el plazo máximo de un mes desde su recepción (art. "
                "12.3 RGPD). Los datos necesarios para el cumplimiento de "
                "obligaciones legales (por ejemplo, de facturación) se conservarán "
                "bloqueados durante los plazos de prescripción correspondientes.",
                "Si la solicitud no fuera atendida en plazo, el solicitante puede "
                "presentar una reclamación ante la Agencia Española de Protección "
                "de Datos (www.aepd.es).",
            ),
        ),
    ),
)


PLANTILLAS: dict[str, PlantillaRgpd] = {
    p.doc_id: p for p in (TRATAMIENTO, PRIVACIDAD, IMAGEN, BAJA)
}
