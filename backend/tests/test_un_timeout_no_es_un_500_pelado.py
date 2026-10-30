"""SI SUPABASE NO COGE EL TELÉFONO, EL SALÓN NO SE COME UN 500 PELADO.

QUÉ PASABA. Los ~95 manejadores que contestan 502 lo hacen capturando
`PostgrestError`. Pero cuando Supabase tarda en ACEPTAR la conexión, httpx no
lanza eso: lanza `httpx.ConnectTimeout`, que no es `PostgrestError` ni nada que
nadie capture. Subía entera hasta el borde de la aplicación y el salón veía un
500 sin explicación, justo en el momento en que más falta hace saber si es cosa
suya o nuestra.

Se descubrió porque ese mismo `ConnectTimeout` mataba dos tests de ráfaga: ahí
se veía la traza. En producción, con Supabase al otro lado de internet, es la
clase de fallo que aparece cuando peor viene.

QUÉ SE COMPRUEBA:
  · que cualquier fallo de transporte se convierte en el error de la casa
    —`PostgrestError` / `StorageError`— con 502 y un motivo honesto;
  · que el 502 llega al endpoint y NO se escapa un 500;
  · y el control: que un error de VERDAD de PostgREST sigue llegando con su
    propio código, que es lo que no se puede romper al meter esto.
"""

from __future__ import annotations

import httpx
import pytest

from app.db.postgrest_client import PostgrestClient, PostgrestError
from app.db.storage_client import StorageClient, StorageError

pytestmark = pytest.mark.anyio


@pytest.fixture()
def anyio_backend():
    return "asyncio"


class _NoCogeElTelefono:
    """El cliente compartido, cuando la conexión no llega a abrirse."""

    def __init__(self, error):
        self._error = error

    async def request(self, *a, **kw):
        raise self._error


FALLOS = [
    httpx.ConnectTimeout("timed out"),
    httpx.ConnectError("connection refused"),
    httpx.ReadTimeout("read timed out"),
    httpx.RemoteProtocolError("server disconnected"),
]


@pytest.mark.parametrize("fallo", FALLOS, ids=lambda f: type(f).__name__)
async def test_un_fallo_de_transporte_es_un_502_de_la_casa(monkeypatch, fallo):
    monkeypatch.setattr(
        "app.db.postgrest_client.cliente_compartido", lambda: _NoCogeElTelefono(fallo)
    )
    db = PostgrestClient("http://x", "clave")

    with pytest.raises(PostgrestError) as e:
        await db.select("employees", {"select": "id"})

    assert e.value.status_code == 502
    # Y dice algo honesto: qué pasó, sin inventarse que la consulta falló.
    assert "base de datos" in e.value.detail
    assert type(fallo).__name__ in e.value.detail


@pytest.mark.parametrize("metodo,args", [
    ("select", ("employees", {"select": "id"})),
    ("count", ("employees", {"select": "id"})),
    ("insert", ("employees", {"nombre": "x"})),
    ("update", ("employees", {"id": "eq.1"}, {"nombre": "x"})),
    ("rpc", ("una_funcion", {})),
    ("delete", ("employees", {"id": "eq.1"})),
    ("insert_many", ("employees", [{"nombre": "x"}])),
    ("upsert_many", ("employees", [{"nombre": "x"}], "id")),
])
async def test_TODOS_los_metodos_pasan_por_el_mismo_sitio(monkeypatch, metodo, args):
    """Si alguien añade un método nuevo saltándose `_peticion`, esto lo caza."""
    monkeypatch.setattr(
        "app.db.postgrest_client.cliente_compartido",
        lambda: _NoCogeElTelefono(httpx.ConnectTimeout("timed out")),
    )
    db = PostgrestClient("http://x", "clave")

    with pytest.raises(PostgrestError) as e:
        await getattr(db, metodo)(*args)

    assert e.value.status_code == 502


async def test_storage_tambien(monkeypatch):
    monkeypatch.setattr(
        "app.db.storage_client.cliente_compartido",
        lambda: _NoCogeElTelefono(httpx.ConnectTimeout("timed out")),
    )
    almacen = StorageClient("http://x", "clave")

    with pytest.raises(StorageError) as e:
        await almacen.delete("salon-assets", "a/b.png")

    assert e.value.status_code == 502
    assert "almacenamiento" in e.value.detail


# ── EL CONTROL ─────────────────────────────────────────────────────────────

async def test_EL_CONTROL_un_error_de_verdad_sigue_llegando_con_SU_codigo(monkeypatch):
    """Lo que no se puede romper: un 404 de PostgREST no puede volverse un 502.

    Sin este control, envolverlo todo en 502 pasaría los tests de arriba y se
    cargaría los caminos que distinguen «no existe» de «no contesta» — por
    ejemplo el de `_apuntar_intento`, que trata el 404 como «la 0047 todavía no
    está aplicada» y sigue por el camino antiguo.
    """
    class _ContestaUn404:
        async def request(self, metodo, url, **kw):
            return httpx.Response(
                404, text='{"code":"PGRST202","message":"no existe"}',
                request=httpx.Request(metodo, url),
            )

    monkeypatch.setattr("app.db.postgrest_client.cliente_compartido", lambda: _ContestaUn404())
    db = PostgrestClient("http://x", "clave")

    with pytest.raises(PostgrestError) as e:
        await db.rpc("pin_fichaje_intento", {})

    assert e.value.status_code == 404, "un 404 de PostgREST se ha convertido en otra cosa"
