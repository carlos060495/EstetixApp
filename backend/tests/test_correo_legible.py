"""Cómo se LEE el correo del recordatorio. Cuatro cosas que se veían mal.

Ninguna es un fallo de lógica: la cita era correcta, la hora era correcta y el
correo salía. Lo que fallaba es lo único que la persona ve.

  A1. «Copia y pega este enlace en tu navegador: tel:+34658723149». Un `tel:`
      no se pega en ningún sitio.
  A2. El número, de corrido. Se lee separado y se marca sin espacios: son dos
      formas del mismo dato y ninguna sirve para lo de la otra.
  A3. Los datos de la cita, casi invisibles. NO ERA COSA DEL MODO OSCURO DE
      GMAIL: la `<table>` de la ficha va dentro de un `<p>`, y el analizador de
      HTML cierra el `<p>` al encontrarla. La tabla quedaba colgando del
      `<body>` y sus valores heredaban el negro por defecto sobre un panel casi
      negro. Las etiquetas se salvaban solo porque declaraban su color.
  A4. «Hola lucas». La gente escribe su nombre en minúscula y eso no se va a
      arreglar pidiéndoselo.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import Settings
from app.core.emails import construir_email_branded
from app.core.recordatorio_email import capitalizar_nombre, cuerpo_del_recordatorio


def _settings() -> Settings:
    return Settings(
        supabase_url="http://local",
        supabase_secret_key="k",
        supabase_jwks_url="http://local/jwks",
        database_url="postgresql://x",
        frontend_base_url="https://estetixapp.es",
        resend_api_key="re_x",
        email_from="hola@estetixapp.es",
    )


def _correo(**kw) -> str:
    salon = {
        "zona_horaria": "Europe/Madrid",
        "nombre_comercial": "Salón Demo",
        "direccion": "C/ Mayor 14",
        "telefono": kw.pop("telefono", "+34658723149"),
    }
    cliente = {
        "nombre": kw.pop("nombre_ficha", "lucas"),
        "recordatorios_token": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    }
    cita = {
        "inicio": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(),
        "nombre_reserva": kw.pop("nombre_reserva", None),
        "clients": cliente,
        "services": {"nombre": "Corte + barba"},
        "employees": {"nombre": kw.pop("profesional", "eva")},
    }
    return cuerpo_del_recordatorio(_settings(), salon, cita)[1]


# ═════════════════════════════════════ A1 · lo que no se puede pegar


def test_con_un_tel_NO_sale_el_bloque_de_copiar_y_pegar():
    assert "copia y pega" not in _correo()


@pytest.mark.parametrize(
    ("enlace", "sale"),
    [
        ("https://estetixapp.es/algo", True),
        ("http://estetixapp.es/algo", True),
        ("tel:+34658723149", False),
        ("mailto:hola@estetixapp.es", False),
    ],
)
def test_el_bloque_depende_del_ESQUEMA_del_enlace(enlace, sale):
    """Del esquema y no de qué correo lo use: lo que hace útil al bloque es que
    un navegador sepa abrir el enlace, no cuál de los correos lo esté mandando."""
    html = construir_email_branded(
        title="T", intro="I", cta_label="C", action_link=enlace, footer_note="F"
    )
    assert ("copia y pega" in html) is sale
    # Y el botón sigue apuntando a donde tiene que apuntar en los cuatro casos.
    assert f'href="{enlace}"' in html
    # Ningún hueco de la plantilla se queda sin rellenar.
    assert "{{" not in html


def test_los_correos_con_enlace_web_NO_pierden_su_bloque():
    """El control. La recuperación de contraseña y el alta viven de ese bloque:
    su enlace es largo, se copia a mano y a veces el botón no funciona."""
    html = construir_email_branded(
        title="T", intro="I", cta_label="C",
        action_link="https://estetixapp.es/recuperar?token=abc", footer_note="F",
    )
    assert "copia y pega" in html
    # Tres veces: el `href` del botón, el `href` del bloque y su texto visible,
    # que es justo lo que se copia a mano cuando el botón no tira.
    assert html.count("https://estetixapp.es/recuperar?token=abc") == 3


# ═════════════════════════════════════════════ A2 · el número, legible


def test_el_numero_se_lee_separado_y_se_marca_junto():
    html = _correo(telefono="+34658723149")
    assert "+34 658 72 31 49" in html
    assert 'href="tel:+34658723149"' in html


def test_un_numero_invalido_se_enseña_TAL_CUAL():
    """No se maquilla. A ese número le sobra un dígito para ser español, así que
    tampoco se puede marcar: dejarlo con pinta de correcto escondería que el
    botón de llamar de ese salón no llama a nadie."""
    html = _correo(telefono="+346587231649")
    assert "+346587231649" in html
    assert "+34 658" not in html


def test_el_agrupamiento_lo_pone_el_PAIS_del_numero():
    """Un fijo de Londres no se lee como un móvil español, y eso no se escribe
    a mano: lo sabe `phonenumbers` a partir del prefijo."""
    assert "+44 20 7946 0958" in _correo(telefono="+442079460958")
    assert "+66 81 234 5678" in _correo(telefono="+66812345678")


def test_un_numero_NACIONAL_de_fuera_de_España_se_queda_sin_formatear():
    """LO QUE HOY NO SE PUEDE, escrito para que nadie lo dé por resuelto.

    `salons.telefono` es el único dato de país que llega al correo. Si trae el
    prefijo, se agrupa bien —pero porque lo trae él—. Si está en formato
    nacional, no hay de dónde deducir el país: se cae en la región por defecto,
    el número no valida como español y se enseña tal cual.

    El día que el salón tenga su columna de país, `region_del_salon` la leerá y
    este test tendrá que cambiar. Que haya que cambiarlo es la señal.
    """
    html = _correo(telefono="020 7946 0958")
    assert "020 7946 0958" in html
    assert "+44" not in html


# ═══════════════════════════════ A3 · lo que se lee no puede ser invisible


def test_TODO_el_texto_del_correo_declara_su_color():
    """LA CAUSA, y por eso se comprueba así y no midiendo un color final.

    La `<table>` de la ficha va dentro del `<p>` de la intro, y el analizador de
    HTML cierra el `<p>` al encontrarla: la tabla acaba colgando del `<body>` y
    lo que no declare color coge el que haya por ahí —negro sobre un panel casi
    negro—. Declararlo es lo único que sobrevive a eso y a que un cliente de
    correo reescriba el documento.
    """
    html = _correo()

    # LA CELDA, Y TAMBIÉN LO QUE LLEVE DENTRO. La dirección dejó de ser texto
    # suelto el día que pasó a abrir el mapa, y con ella el recuento bajó de 8 a
    # 7 sin que nadie lo pidiera: el texto seguía ahí, dentro de un `<a>`, y
    # esta comprobación había dejado de mirarlo. Un guardián que se encoge en
    # silencio cuando el HTML cambia es peor que no tenerlo.
    celdas = re.findall(r"<td style=\"([^\"]*)\"[^>]*>(.*?)</td>", html, re.S)
    con_texto: list[tuple[str, str]] = []
    for estilo, dentro in celdas:
        enlaces = re.findall(r"<a [^>]*style=\"([^\"]*)\"[^>]*>([^<]+)</a>", dentro)
        if enlaces:
            # El color que vale es el del `<a>`: un enlace hereda el suyo del
            # cliente de correo, no de la celda que lo contiene.
            con_texto.extend(enlaces)
            continue
        plano = re.sub(r"<[^>]+>", "", dentro)
        if plano.strip():
            con_texto.append((estilo, plano))

    assert len(con_texto) >= 8, f"no se han encontrado las filas de la ficha: {con_texto}"
    for estilo, texto in con_texto:
        assert "color:" in estilo, f"sin color declarado: {texto.strip()!r}"


def test_la_direccion_abre_el_mapa_y_es_UN_ENLACE_ESCRITO():
    """LA NOCHE ANTES DE IR POR PRIMERA VEZ es cuando se mira este correo, así
    que la dirección se toca y abre el mapa.

    Y SE COMPRUEBA QUE ES NUESTRO, no del parser. Ya pasó con los teléfonos:
    algunos clientes de correo detectan un número y lo convierten en un `tel:`
    que no siempre funciona. Este `<a>` se escribe aquí, con su `href` y su
    color, y por eso se puede afirmar adónde lleva.
    """
    html = _correo()
    enlace = re.search(r'<a href="(https://www\.google\.com/maps[^"]+)"[^>]*>([^<]+)</a>', html)

    assert enlace, "la dirección ya no abre el mapa"
    assert "color:" in html[enlace.start() : enlace.end()], "el enlace del mapa no declara color"
    # Y lleva el nombre del salón además de la calle: una calle a medias cae en
    # el centro del pueblo y la clienta acaba en otro sitio.
    assert "Peluquer" in enlace.group(1) or "Sal" in enlace.group(1)


def test_el_enlace_de_baja_lleva_su_color():
    """Un `<a>` sin color se pinta con el azul por defecto del cliente, que
    sobre este fondo casi no se ve. Es el enlace que menos se puede permitir
    ser invisible: es la salida."""
    html = _correo()
    baja = re.search(r"<a[^>]*recordatorios/baja[^>]*>", html)
    assert baja and "color:#C9A84C" in baja.group(0)


def test_la_plantilla_dice_que_YA_es_oscura():
    """Sin esto, Gmail y Apple Mail dan por hecho que el correo es claro y le
    aplican su propia transformación de modo oscuro encima."""
    html = _correo()
    assert 'name="color-scheme" content="dark"' in html
    assert 'name="supported-color-schemes" content="dark"' in html


# ═══════════════════════════════════════════ A4 · el nombre, con mayúscula


@pytest.mark.parametrize(
    ("escrito", "esperado"),
    [
        ("lucas", "Lucas"),
        ("maría josé", "María José"),
        ("ángela", "Ángela"),
        ("ana-maría", "Ana-María"),
        ("o'connor", "O'Connor"),
        ("  pepe  luis  ", "Pepe Luis"),
        # YA TRAE MAYÚSCULAS: no se toca. Es lo que salva a los apellidos que
        # llevan la suya dentro y a lo que no es un nombre.
        ("McDonald", "McDonald"),
        ("DJ Ana", "DJ Ana"),
        ("LUCAS", "LUCAS"),
        # EL ERROR QUE SE ELIGE A SABIENDAS. Una lista de partículas (de, van,
        # del…) se equivoca en el sentido contrario y peor: «Van Gogh» lleva la
        # suya en mayúscula. Una mayúscula de más se lee como un estilo; una de
        # menos, en el apellido de alguien, se lee como un error sobre su nombre.
        ("van der berg", "Van Der Berg"),
        ("", ""),
        (None, ""),
    ],
)
def test_capitalizar_nombre(escrito, esperado):
    assert capitalizar_nombre(escrito) == esperado


def test_el_correo_saluda_y_nombra_con_mayuscula():
    html = _correo(nombre_ficha="lucas", profesional="eva")
    assert "Hola Lucas" in html
    assert ">Eva<" in html


def test_se_capitaliza_el_nombre_de_LA_RESERVA_si_lo_hay():
    """La cita puede ser para otra persona: se saluda a quien va."""
    assert "Hola Mariela Luna" in _correo(nombre_reserva="mariela luna")


def test_NO_se_toca_el_nombre_del_salon_ni_el_servicio():
    """Una marca puede ser minúscula a propósito, y un servicio se escribe como
    lo dio de alta el salón. Solo se capitalizan nombres de PERSONAS."""
    salon = {"zona_horaria": "Europe/Madrid", "nombre_comercial": "estetix bcn", "direccion": "C/ Mayor 14", "telefono": ""}
    cita = {
        "inicio": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(),
        "nombre_reserva": None,
        "clients": {"nombre": "lucas", "recordatorios_token": None},
        "services": {"nombre": "corte + barba"},
        "employees": {"nombre": "eva"},
    }
    asunto, html = cuerpo_del_recordatorio(_settings(), salon, cita)
    assert "estetix bcn" in html and "Estetix Bcn" not in html
    assert "estetix bcn" in asunto
    assert ">corte + barba<" in html
