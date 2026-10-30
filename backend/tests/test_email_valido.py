"""El criterio de «esto es un correo», que ahora usan las DOS puertas.

Existe por el bot: lo que llega a `guardar_email_cliente` es lo que un modelo de
lenguaje ha entendido de una conversación, y si la validación viviera en el
prompt, «juan@gmail» sería un correo el día que al modelo le pareciera que sí.

El widget lo usa por el mismo motivo con otro origen: antes solo recortaba a 120
caracteres, así que el `type="email"` del navegador era toda la defensa que
había entre el CRM y cualquier cadena.

LO QUE SE PIERDE SI ESTO SE ROMPE no es un error visible: es un recordatorio que
sale, rebota, y se ve semanas después en `recordatorio_error` — si alguien mira.
"""

from __future__ import annotations

import pytest

from app.core.email_valido import MAX_EMAIL, normalizar_email


@pytest.mark.parametrize(
    ("escrito", "guardado"),
    [
        ("juan@gmail.com", "juan@gmail.com"),
        # LOS ESPACIOS SE QUITAN, no se rechazan: quien pega un correo desde el
        # teclado de un móvil los arrastra casi siempre, y hacerle repetir un
        # correo que estaba bien escrito es lo peor de los dos mundos.
        ("  juan@gmail.com  ", "juan@gmail.com"),
        # El DOMINIO a minúsculas —no distingue mayúsculas— y la parte de
        # delante de la arroba INTACTA: quien decide si esa sí las distingue es
        # el servidor de correo, no nosotros.
        ("Juan.Perez@Gmail.COM", "Juan.Perez@gmail.com"),
        ("juan+cita@gmail.com", "juan+cita@gmail.com"),
        ("a@b.co", "a@b.co"),
    ],
)
def test_lo_que_es_un_correo_se_normaliza(escrito, guardado):
    assert normalizar_email(escrito) == guardado


@pytest.mark.parametrize(
    "escrito",
    [
        None,
        "",
        "   ",
        "no",
        "juan arroba gmail punto com",
        "juan@gmail",          # sin punto en el dominio
        "juan@.com",
        "juan@gmail..com",
        "juan@@gmail.com",
        "juan gomez@gmail.com",
    ],
)
def test_lo_que_no_lo_es_devuelve_None(escrito):
    assert normalizar_email(escrito) is None


def test_los_dominios_RESERVADOS_no_valen():
    """`.test`, `.local`, `.invalid` y `.example` no reciben correo nunca.

    Es lo que una expresión regular casera daría por bueno —tienen su punto y
    sus letras— y es justo el caso que aparece cuando alguien copia un ejemplo.
    """
    assert normalizar_email("maria@correo.test") is None
    assert normalizar_email("maria@ejemplo.invalid") is None


def test_no_cabe_mas_de_lo_que_cabe_en_la_columna():
    largo = "a" * MAX_EMAIL + "@gmail.com"
    assert normalizar_email(largo) is None
