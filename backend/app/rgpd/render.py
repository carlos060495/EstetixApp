"""Renderizado de las plantillas RGPD a PDF con fpdf2 (pura-Python).

fpdf2 no arrastra dependencias de sistema (a diferencia de weasyprint),
así que el build de Railway no cambia. Las fuentes core son latin-1: todo
texto variable pasa por _sanear_campo antes de pintarse.
"""

from __future__ import annotations

import unicodedata
from datetime import datetime, timezone

from app.core import zona as zonas

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from app.rgpd.plantillas import Bloque, PlantillaRgpd

# multi_cell en fpdf2 deja el cursor a la DERECHA del bloque por defecto;
# para texto en flujo vertical hay que volver siempre al margen izquierdo.
_SALTO = {"new_x": XPos.LMARGIN, "new_y": YPos.NEXT}

DESCARGO = (
    "Plantilla orientativa generada automáticamente; no constituye asesoría "
    "legal. Revísela con un profesional antes de su uso."
)

_GRIS_TEXTO = 45
_GRIS_SUAVE = 120
_ALTO_LINEA = 5.2


def _sanear_campo(valor: str | None, max_len: int = 200) -> str:
    """Normaliza un campo del salón antes de meterlo en el PDF.

    Elimina caracteres de control (saltos de línea incluidos: un campo nunca
    debe romper el layout), colapsa espacios y proyecta a latin-1 (repertorio
    de las fuentes core de fpdf2; lo no representable se sustituye por '?').
    """
    if not valor:
        return ""
    sin_control = "".join(ch for ch in valor if unicodedata.category(ch)[0] != "C")
    plano = " ".join(sin_control.split())
    latin1 = plano.encode("latin-1", "replace").decode("latin-1")
    return latin1[:max_len].strip()


class _PdfRgpd(FPDF):
    def __init__(self, fecha_generacion: str):
        super().__init__(orientation="portrait", format="A4")
        self._fecha = fecha_generacion
        self.set_margins(20, 18)
        # Margen inferior amplio para que el pie (descargo + paginación)
        # nunca se solape con el cuerpo.
        self.set_auto_page_break(auto=True, margin=30)
        self.alias_nb_pages()

    def footer(self) -> None:
        self.set_y(-24)
        self.set_draw_color(190)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.set_y(-21)
        self.set_font("helvetica", "I", 7.5)
        self.set_text_color(_GRIS_SUAVE)
        self.multi_cell(0, 3.8, DESCARGO, align="C", **_SALTO)
        self.set_y(-12)
        self.set_font("helvetica", "", 7.5)
        self.cell(
            0,
            4,
            f"Documento generado el {self._fecha} · Página {self.page_no()} de {{nb}}",
            align="C",
        )


def _linea_en_blanco(pdf: _PdfRgpd) -> None:
    """Línea rellenable a mano hasta el margen derecho (nunca 'None')."""
    x, y = pdf.get_x(), pdf.get_y()
    pdf.set_draw_color(_GRIS_TEXTO)
    pdf.line(x + 1, y + _ALTO_LINEA, pdf.w - pdf.r_margin, y + _ALTO_LINEA)
    pdf.ln(7)


def _campo(pdf: _PdfRgpd, label: str, valor: str, negrita_label: bool) -> None:
    pdf.set_font("helvetica", "B" if negrita_label else "", 10)
    pdf.set_text_color(_GRIS_TEXTO)
    ancho_label = pdf.get_string_width(f"{label}: ") + 2
    pdf.cell(ancho_label, 7, f"{label}: ")
    if valor:
        pdf.set_font("helvetica", "", 10)
        pdf.multi_cell(0, 7, valor, **_SALTO)
    else:
        _linea_en_blanco(pdf)


def _titulo_seccion(pdf: _PdfRgpd, texto: str) -> None:
    pdf.ln(2.5)
    pdf.set_font("helvetica", "B", 11)
    pdf.set_text_color(_GRIS_TEXTO)
    pdf.multi_cell(0, 6.5, texto, **_SALTO)
    pdf.ln(0.5)


def _parrafo(pdf: _PdfRgpd, texto: str) -> None:
    pdf.set_font("helvetica", "", 10)
    pdf.set_text_color(_GRIS_TEXTO)
    pdf.multi_cell(0, _ALTO_LINEA, texto, align="J", **_SALTO)
    pdf.ln(1.6)


def _casilla(pdf: _PdfRgpd, texto: str) -> None:
    pdf.set_font("helvetica", "", 10)
    pdf.set_text_color(_GRIS_TEXTO)
    pdf.multi_cell(0, _ALTO_LINEA, f"[   ]  {texto}", **_SALTO)
    pdf.ln(1.6)


def _bloque(pdf: _PdfRgpd, bloque: Bloque, datos: dict[str, str]) -> None:
    if bloque.titulo:
        _titulo_seccion(pdf, bloque.titulo)
    for parrafo in bloque.parrafos:
        _parrafo(pdf, parrafo.format(**datos))
    for casilla in bloque.casillas:
        _casilla(pdf, casilla)
    for etiqueta in bloque.lineas:
        _campo(pdf, etiqueta, "", negrita_label=False)


def _bloque_firma(pdf: _PdfRgpd) -> None:
    pdf.ln(4)
    pdf.set_font("helvetica", "", 10)
    pdf.set_text_color(_GRIS_TEXTO)
    pdf.multi_cell(
        0,
        7,
        "En ____________________________, a _______ de ____________________ "
        "de 20_____.",
        **_SALTO,
    )
    pdf.ln(10)
    ancho_util = pdf.w - pdf.l_margin - pdf.r_margin
    ancho_col = (ancho_util - 10) / 2
    y_linea = pdf.get_y()
    pdf.set_draw_color(_GRIS_TEXTO)
    pdf.line(pdf.l_margin, y_linea, pdf.l_margin + ancho_col, y_linea)
    pdf.line(pdf.l_margin + ancho_col + 10, y_linea, pdf.l_margin + ancho_util, y_linea)
    pdf.set_y(y_linea + 1.5)
    pdf.set_font("helvetica", "", 9)
    pdf.set_text_color(_GRIS_SUAVE)
    pdf.cell(ancho_col, 5, "Firma del cliente", align="C")
    pdf.cell(10, 5, "")
    pdf.cell(ancho_col, 5, "Firma y sello del responsable", align="C")


def render_pdf(plantilla: PlantillaRgpd, salon: dict) -> bytes:
    """Genera el PDF de una plantilla con los datos del salón ya en BD.

    Los campos del salón se sanean SIEMPRE aquí (defensa en profundidad:
    da igual quién llame). Los campos del cliente se dejan como líneas en
    blanco para rellenar a mano.
    """
    datos = {
        "nombre_comercial": _sanear_campo(salon.get("nombre_comercial")) or "el responsable",
        # El día del CENTRO: en UTC, un PDF de las 00:30 de Madrid salía con la
        # fecha de ayer.
        "fecha": zonas.fecha_dd_mm_aaaa(datetime.now(timezone.utc), zonas.zona(salon.get("zona_horaria"))),
    }
    cif = _sanear_campo(salon.get("cif"), 40)
    direccion = _sanear_campo(salon.get("direccion"))

    pdf = _PdfRgpd(datos["fecha"])
    pdf.add_page()

    # Cabecera del documento.
    pdf.set_font("helvetica", "B", 15)
    pdf.set_text_color(20)
    pdf.multi_cell(0, 7.5, plantilla.titulo, **_SALTO)
    pdf.set_font("helvetica", "", 8.5)
    pdf.set_text_color(_GRIS_SUAVE)
    pdf.multi_cell(0, 5, "Referencia normativa: RGPD (UE) 2016/679 · LOPDGDD 3/2018", **_SALTO)
    pdf.set_draw_color(190)
    pdf.line(pdf.l_margin, pdf.get_y() + 1.5, pdf.w - pdf.r_margin, pdf.get_y() + 1.5)
    pdf.ln(5)

    # Bloque estándar con los datos del responsable (los del salón, del JWT).
    _titulo_seccion(pdf, "Responsable del tratamiento")
    _campo(pdf, "Nombre comercial", datos["nombre_comercial"], negrita_label=True)
    # UN NOMBRE GENÉRICO, EL MISMO PARA LOS DOCE PAÍSES. Decía «CIF / NIF» con
    # cualquier país: un salón francés descargaba un documento legal con
    # «CIF / NIF: FR40303265045». Se descartó la etiqueta de cada país a
    # propósito: en un documento legal vale más un nombre correcto para todos
    # que doce que hay que mantener. Lo vigila `test_textos_sin_cif.py`.
    #
    # SIN DATO, LA LÍNEA NO SE IMPRIME. Es obligatoria al darse de alta y al
    # guardarla, pero hay cuentas de antes sin ella, y dejar la etiqueta con un
    # hueco sería un documento legal con una línea a medias.
    # Tampoco se rellena con «No consta»: el RGPD identifica al responsable por
    # su nombre y su dirección, que siguen ahí, y un número escrito a mano en el
    # papel acabaría siendo distinto del de la cuenta.
    if cif:
        _campo(pdf, "Número de identificación fiscal", cif, negrita_label=True)
    _campo(pdf, "Dirección", direccion, negrita_label=True)

    for bloque in plantilla.bloques:
        _bloque(pdf, bloque, datos)

    if plantilla.con_firma:
        _bloque_firma(pdf)

    return bytes(pdf.output())
