"""Un 422 se lee como una frase para la persona, nunca «Value error» ni inglés."""

import re

from fastapi.testclient import TestClient

from app.core.errores_de_formulario import mensaje_de_validacion, un_error
from app.main import app

_DE_PROGRAMADOR = re.compile(r"Value error|Assertion failed|should|valid|Field required|Input|\(\d{3}\)")


def test_los_mensajes_propios_pierden_el_prefijo():
    err = {"type": "value_error", "loc": ("body",), "msg": "Value error, Esa hora ya ha pasado. Elige otra."}
    assert un_error(err) == "Esa hora ya ha pasado. Elige otra."


def test_el_correo_y_las_restricciones_se_dicen_en_castellano():
    assert un_error({"type": "value_error", "loc": ("body", "email"),
                     "msg": "value is not a valid email address: The part after the @-sign is reserved"}
                    ).startswith("El correo no es válido")
    assert un_error({"type": "missing", "loc": ("body", "email"), "msg": "Field required"}) == "Falta el correo."
    assert un_error({"type": "string_too_short", "loc": ("body", "motivo"), "msg": "String should…",
                     "ctx": {"min_length": 10}}) == "El motivo es demasiado corto: escribe al menos 10 caracteres."
    assert un_error({"type": "greater_than_equal", "loc": ("body", "importe"), "msg": "…", "ctx": {"ge": 0}}
                    ) == "El importe tiene que ser como mínimo 0."
    # Un campo que no conocemos no enseña su nombre técnico.
    assert "provider_xyz" not in un_error({"type": "uuid_parsing", "loc": ("body", "provider_xyz"), "msg": "x"})


def test_no_se_repite_la_misma_frase():
    e = {"type": "missing", "loc": ("body", "email"), "msg": "Field required"}
    assert mensaje_de_validacion([e, e]) == "Falta el correo."


def test_un_422_de_verdad_sale_como_texto_legible():
    c = TestClient(app)
    for cuerpo in ({"email": "x@example.invalid"}, {}):
        r = c.post("/auth/recuperar", json=cuerpo)
        assert r.status_code == 422
        detalle = r.json()["detail"]
        assert isinstance(detalle, str) and not _DE_PROGRAMADOR.search(detalle), detalle
