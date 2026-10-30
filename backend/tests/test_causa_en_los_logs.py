"""Que el motivo de un fallo llegue al log, y que los datos de nadie no.

EL FALLO QUE ORIGINÓ ESTO: `GET /fichaje/estado` devolvía 502 en producción sin
una línea en el log. PostgREST decía exactamente qué pasaba —«column
employees.rol does not exist»— y ese mensaje moría en un `except PostgrestError`
dos marcos más arriba. Un 502 mudo se parece a un cuelgue, así que se buscó
donde no era.

Había 113 manejadores que respondían con un error sin registrar la causa. La
mayoría (~95) capturan `PostgrestError`, y esa excepción NACE EN UN SOLO SITIO:
`PostgrestClient._raise_for_status`. Registrar ahí los cubre todos, cubre también
los ~79 que se la tragan en silencio, y —lo que importa a un año vista— cubre el
código que todavía no existe.

Estos tests fijan las dos mitades, y la segunda no es un extra:

  1. Que el motivo esté. Sin esto, el arreglo se deshace en el primer refactor.
  2. Que los DATOS DE FILA no estén. PostgREST manda el valor que provocó un
     conflicto de unique en el campo `details` —«Key (salon_id,
     customer_phone)=(…, 34600111222) already exists»—, o sea el teléfono de una
     persona. Un log se retiene, se replica y lo lee gente que no debería verlo,
     y ese error concreto es de los más frecuentes que hay aquí: cada reentrega
     de un mensaje de Meta produce uno.

La forma más fácil de «arreglar» el punto 2 sería dejar de registrar nada, y por
eso el 1 se comprueba en el mismo test.
"""

from __future__ import annotations

import logging

import httpx
import pytest

from app.db.postgrest_client import PostgrestClient, PostgrestError
from app.db.storage_client import StorageClient, StorageError

# El teléfono que PostgREST devolvería dentro de `details` en un conflicto. Si
# aparece en el log, se ha filtrado un dato de una persona.
TELEFONO = "34600111222"

CUERPO_COLUMNA = (
    '{"code":"42703","details":null,"hint":null,'
    '"message":"column employees.rol does not exist"}'
)
CUERPO_DUPLICADO = (
    '{"code":"23505",'
    f'"details":"Key (salon_id, customer_phone)=(abc, {TELEFONO}) already exists.",'
    '"hint":null,'
    '"message":"duplicate key value violates unique constraint \\"bot_sessions_salon_id_customer_phone_key\\""}'
)


def _respuesta(codigo: int, cuerpo: str, metodo: str = "GET", url: str = "http://x/rest/v1/employees") -> httpx.Response:
    """Una respuesta como la que da httpx, con su petición colgada.

    Se construye de verdad y no con un doble: lo que se está probando lee
    `resp.request.method` y `resp.request.url`, y un doble que no los tuviera
    haría pasar el test sobre código que reventaría en producción.
    """
    return httpx.Response(codigo, text=cuerpo, request=httpx.Request(metodo, url))


# ═══════════════════════════════════ PostgREST


def test_el_motivo_de_postgrest_acaba_en_el_log(caplog):
    """La línea que faltaba el día del 502 del fichaje."""
    with caplog.at_level(logging.WARNING, logger="estetix.postgrest"):
        with pytest.raises(PostgrestError):
            PostgrestClient._raise_for_status(_respuesta(400, CUERPO_COLUMNA))

    registrado = caplog.text
    assert "column employees.rol does not exist" in registrado
    assert "42703" in registrado
    # Y con qué operación pasó, o habría que adivinarlo.
    assert "employees" in registrado
    assert "400" in registrado


def test_el_dato_de_la_fila_no_acaba_en_el_log(caplog):
    """El conflicto de unique trae un teléfono dentro. No puede salir."""
    with caplog.at_level(logging.WARNING, logger="estetix.postgrest"):
        with pytest.raises(PostgrestError):
            PostgrestClient._raise_for_status(_respuesta(409, CUERPO_DUPLICADO, metodo="POST"))

    registrado = caplog.text
    assert TELEFONO not in registrado
    assert "already exists" not in registrado  # el campo `details` entero, fuera
    # Pero la línea sigue sirviendo: se sabe qué pasó y contra qué.
    assert "23505" in registrado
    assert "duplicate key value" in registrado


def test_una_respuesta_buena_no_registra_nada(caplog):
    with caplog.at_level(logging.WARNING, logger="estetix.postgrest"):
        PostgrestClient._raise_for_status(_respuesta(200, "[]"))
    assert caplog.text == ""


def test_un_error_que_no_es_json_tambien_deja_traza(caplog):
    """Un 502 del proxy delante de PostgREST no viene en JSON. Antes de este
    arreglo tampoco dejaba nada; ahora deja el cuerpo, acotado."""
    with caplog.at_level(logging.WARNING, logger="estetix.postgrest"):
        with pytest.raises(PostgrestError):
            PostgrestClient._raise_for_status(_respuesta(502, "<html>Bad Gateway</html>"))
    assert "Bad Gateway" in caplog.text


@pytest.mark.anyio
async def test_el_registro_esta_conectado_a_las_consultas_de_verdad(caplog, monkeypatch):
    """No basta con que `_raise_for_status` registre: hay que comprobar que las
    consultas pasan por ahí. Si algún método dejara de llamarlo, los tests de
    arriba seguirían verdes sobre código que ya no se ejecuta."""

    class _RespuestaFalsa:
        status_code = 400
        text = CUERPO_COLUMNA
        request = httpx.Request("GET", "http://x/rest/v1/employees")

        def json(self):
            import json

            return json.loads(CUERPO_COLUMNA)

    class _ClienteFalso:
        """El cliente COMPARTIDO, doblado.

        Antes se doblaba `httpx.AsyncClient` porque `PostgrestClient` se
        fabricaba uno por llamada. Ahora todas las peticiones van por el
        compartido de `app/db/conexiones.py`, así que el doble se pone ahí — y
        con `request()`, que es por donde pasan todas desde `_peticion`.
        """

        async def request(self, metodo, url, **kw):
            return _RespuestaFalsa()

    monkeypatch.setattr(
        "app.db.postgrest_client.cliente_compartido", lambda: _ClienteFalso()
    )

    db = PostgrestClient("http://x", "clave")
    with caplog.at_level(logging.WARNING, logger="estetix.postgrest"):
        with pytest.raises(PostgrestError):
            await db.select("employees", {"select": "id,nombre,rol"})

    assert "column employees.rol does not exist" in caplog.text


# ═══════════════════════════════════ Storage


def test_el_motivo_de_storage_acaba_en_el_log(caplog):
    """Mismo patrón y mismo motivo: `StorageError` también nace en un solo sitio,
    así que «No se pudo subir el logo» deja de ser todo lo que queda."""
    resp = _respuesta(413, "Payload too large", metodo="POST", url="http://x/storage/v1/object/salon-assets/abc/logo.png")
    with caplog.at_level(logging.WARNING, logger="estetix.storage"):
        with pytest.raises(StorageError):
            StorageClient._raise_for_status(resp)

    assert "Payload too large" in caplog.text
    assert "413" in caplog.text
    # La ruta sí: es `<salon_id>/logo.png`, dice a qué salón le pasó y no lleva
    # nada de ninguna persona.
    assert "logo.png" in caplog.text
