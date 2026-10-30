"""Una respuesta 3xx de PostgREST no son datos.

EL AGUJERO, encontrado construyendo el enlace entre fichas. `_raise_for_status`
cortaba en 400, y PostgREST contesta **300 Multiple Choices** cuando un embed
es ambiguo (PGRST201: hay dos caminos entre las mismas dos tablas). Con ese
corte, el cuerpo del error -un objeto JSON con `code`, `hint` y `message`- se
devolvia al endpoint COMO SI FUERAN FILAS.

Lo que se veia desde fuera era esto:

    AttributeError: 'str' object has no attribute 'get'

...tres marcos por debajo de la consulta, iterando el "resultado" y sacando las
CLAVES del diccionario de error una a una. Y ni una linea en el registro
diciendo que la consulta no se habia ejecutado, porque el unico sitio que
registra es justo el que no llego a dispararse.

No es un fallo de una consulta concreta: cualquier consulta con un embed
ambiguo -hoy, o la que escriba alguien manana- se comporta asi. Por eso el
arreglo esta en el cliente y esta prueba vive aparte.
"""

from __future__ import annotations

import httpx
import pytest

from app.db.postgrest_client import PostgrestClient, PostgrestError

PGRST201 = (
    b'{"code":"PGRST201","message":"Could not embed because more than one '
    b'relationship was found","hint":"Try changing \'salons\' to one of the '
    b'following: \'salons!employees_salon_id_fkey\'"}'
)


def _respuesta(codigo: int, cuerpo: bytes = b"[]") -> httpx.Response:
    return httpx.Response(
        codigo,
        content=cuerpo,
        headers={"Content-Type": "application/json"},
        request=httpx.Request("GET", "http://local/rest/v1/employees"),
    )


def test_un_300_de_embed_ambiguo_NO_pasa_por_datos():
    """EL CASO. Antes esto devolvia el diccionario de error y seguia adelante."""
    with pytest.raises(PostgrestError) as e:
        PostgrestClient._raise_for_status(_respuesta(300, PGRST201))

    assert e.value.status_code == 300


def test_y_queda_escrito_por_que(caplog):
    """La otra mitad: un 3xx silencioso no dejaba NADA en el registro, que es
    lo que convertia el fallo en un rompecabezas."""
    with caplog.at_level("WARNING"), pytest.raises(PostgrestError):
        PostgrestClient._raise_for_status(_respuesta(300, PGRST201))

    assert "PGRST201" in caplog.text
    # Y el `hint` de PostgREST, que trae la solucion literal.
    assert "employees_salon_id_fkey" in caplog.text


@pytest.mark.parametrize("codigo", [301, 304, 307])
def test_ningun_3xx_es_una_respuesta_valida_aqui(codigo):
    """No se siguen redirecciones ni se hacen peticiones condicionales: si
    PostgREST contesta 3xx, algo ha salido mal, sea cual sea."""
    with pytest.raises(PostgrestError):
        PostgrestClient._raise_for_status(_respuesta(codigo))


@pytest.mark.parametrize("codigo", [200, 201, 204, 206])
def test_LO_QUE_SI_ES_UNA_RESPUESTA_no_se_rompio(codigo):
    """EL CONTROL, y no es adorno: mover el corte de `< 400` a un rango cerrado
    podia haberse llevado por delante el 201 de un insert o el 206 de un
    listado paginado, y ninguna de las dos cosas la habria notado el test de
    arriba."""
    PostgrestClient._raise_for_status(_respuesta(codigo))


@pytest.mark.parametrize("codigo", [400, 409, 502])
def test_y_los_errores_de_siempre_siguen_siendo_errores(codigo):
    with pytest.raises(PostgrestError):
        PostgrestClient._raise_for_status(_respuesta(codigo))
