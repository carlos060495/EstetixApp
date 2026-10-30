"""LA CONEXIÓN SE COMPARTE. EL TOKEN NO.

POR QUÉ EXISTE ESTE FICHERO. Todo el proceso comparte una sola conexión a la
base, en vez de abrir una por llamada y tirarla. Eso
arregla una lentitud grande, y abre de par en par la puerta al peor fallo que
puede tener esta aplicación: si el token de un salón acabara guardado EN el
cliente compartido en vez de viajar en cada llamada, dos peticiones simultáneas
de dos cuentas distintas se cruzarían, y un salón vería los datos de otro.

No es una preocupación teórica. Antes, `SupabaseAuthAdminClient`
pegaba sus cabeceras al cliente (`httpx.AsyncClient(headers=...)`). Con clientes
de usar y tirar eso no podía cruzarse con nadie; compartido, sí.

LO QUE SE COMPRUEBA, contra PostgREST de verdad y con RLS de verdad:

  1. Dos cuentas distintas, con dos tokens distintos, lanzando peticiones A LA
     VEZ por la misma conexión: ninguna ve nada de la otra.
  2. EL CONTROL, que es la mitad del trabajo: el mismo cruce, provocado a
     propósito pegando el token al cliente, SÍ lo detecta esta comprobación. Sin
     este control, el test de arriba pasaría aunque no mirara nada.
  3. Que el cliente compartido no guarda nada de nadie: ni cabeceras, ni `auth`,
     ni `base_url`, ni galletas.
  4. Y que de verdad se está compartiendo: muchas peticiones, pocas conexiones.

Se mira al nivel de `PostgrestClient` y no a través de los endpoints a
propósito: es exactamente ahí donde viaja el token, y así el test falla por lo
que tiene que fallar y no por el montaje de una sesión.
"""

from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest

from app.db.conexiones import cliente_compartido
from app.db.postgrest_client import PostgrestClient
from app.db.supabase_auth_client import SupabaseAuthAdminClient
from tests.test_alta_completa_e2e import (
    SECRET_KEY,
    SUPABASE_URL,
    _token_de_inquilino,
)

pytestmark = [
    pytest.mark.base_real,
    pytest.mark.anyio,
]

# Cuántas peticiones simultáneas se lanzan mezclando las dos cuentas. No es un
# número mágico: es bastante más que la piscina de PostgREST (10) para que haya
# cola de verdad y las peticiones de las dos cuentas convivan en el mismo
# instante sobre las mismas conexiones.
A_LA_VEZ = 40


@pytest.fixture()
def anyio_backend():
    return "asyncio"


async def _crear_cuenta(db: PostgrestClient, auth: SupabaseAuthAdminClient, etiqueta: str) -> dict:
    """Una cuenta con su salón, su usuario de GoTrue y su ficha de admin."""
    email = f"cruce.{etiqueta}.{uuid.uuid4().hex[:8]}@estetixapp-e2e.com"
    cuenta = await db.insert("accounts", {"nombre": f"Cuenta {etiqueta}"}, select="id")
    salon = await db.insert(
        "salons",
        {"zona_horaria": "Europe/Madrid", "nombre_comercial": f"Salon {etiqueta}", "account_id": cuenta["id"]},
        select="id,nombre_comercial",
    )
    usuario_auth = await auth.create_unconfirmed_user(email, uuid.uuid4().hex + "Aa1!")
    ficha = await db.insert(
        "users",
        {
            "salon_id": salon["id"],
            "account_id": cuenta["id"],
            "nombre": f"Admin {etiqueta}",
            "email": email,
            "rol": "admin",
            "auth_user_id": usuario_auth["id"],
        },
        select="id",
    )
    return {
        "account_id": cuenta["id"],
        "salon_id": salon["id"],
        "nombre": salon["nombre_comercial"],
        "user_id": ficha["id"],
        "auth_user_id": usuario_auth["id"],
        "token": _token_de_inquilino(usuario_auth["id"], salon["id"]),
    }


@pytest.fixture()
async def dos_cuentas():
    """Dos cuentas ajenas entre sí. Nada las relaciona salvo el proceso."""
    db = PostgrestClient(SUPABASE_URL, SECRET_KEY, timeout=30.0)
    auth = SupabaseAuthAdminClient(SUPABASE_URL, SECRET_KEY)
    cuentas = [await _crear_cuenta(db, auth, "A"), await _crear_cuenta(db, auth, "B")]
    try:
        yield cuentas
    finally:
        for c in cuentas:
            try:
                await db.delete("users", {"id": f"eq.{c['user_id']}"})
                await db.delete("salons", {"id": f"eq.{c['salon_id']}"})
                await db.delete("accounts", {"id": f"eq.{c['account_id']}"})
                await auth.delete_user(c["auth_user_id"])
            except Exception:  # noqa: BLE001 — el desmontaje no puede tumbar el test
                pass


def _nadie_ve_lo_ajeno(resultados: list[tuple[dict, list[dict]]], cuentas: list[dict]) -> list[str]:
    """LA COMPROBACIÓN, en un sitio, para poder probarla con el control.

    Devuelve la lista de cruces encontrados. Vacía = nadie vio nada ajeno.
    """
    ajenos = {c["salon_id"]: [o["salon_id"] for o in cuentas if o is not c] for c in cuentas}
    cruces = []
    for cuenta, filas in resultados:
        vistos = {f["id"] for f in filas}
        for id_ajeno in ajenos[cuenta["salon_id"]]:
            if id_ajeno in vistos:
                cruces.append(f"{cuenta['nombre']} vio el salón {id_ajeno}")
        if vistos != {cuenta["salon_id"]}:
            cruces.append(f"{cuenta['nombre']} vio {sorted(vistos)} en vez de solo el suyo")
    return cruces


async def _rafaga(clientes: list[tuple[dict, PostgrestClient]]) -> list[tuple[dict, list[dict]]]:
    """`A_LA_VEZ` peticiones alternando las dos cuentas, todas a la vez."""
    async def una(cuenta, db):
        return cuenta, await db.select("salons", {"select": "id,nombre_comercial"})

    tareas = [una(*clientes[i % len(clientes)]) for i in range(A_LA_VEZ)]
    return list(await asyncio.gather(*tareas))


# ── 1 · EL GUARDA ──────────────────────────────────────────────────────────

async def test_dos_cuentas_a_la_vez_por_la_misma_conexion_no_se_cruzan(dos_cuentas):
    """Lo que no puede pasar nunca: un salón viendo los datos de otro."""
    clientes = [(c, PostgrestClient(SUPABASE_URL, SECRET_KEY, user_jwt=c["token"], timeout=30.0))
                for c in dos_cuentas]

    resultados = await _rafaga(clientes)

    assert len(resultados) == A_LA_VEZ
    # Y que la RLS estaba puesta de verdad: si devolviera vacío, no se estaría
    # comprobando nada.
    assert all(filas for _, filas in resultados), "alguna petición no devolvió ninguna fila"
    assert _nadie_ve_lo_ajeno(resultados, dos_cuentas) == []


# ── 2 · EL CONTROL: que el guarda detecte el cruce de verdad ───────────────

async def test_EL_CONTROL_si_el_token_se_pegara_al_cliente_esto_LO_CAZA(dos_cuentas):
    """El fallo, provocado a propósito, con la MISMA comprobación de arriba.

    Se reproduce el patrón que había en `SupabaseAuthAdminClient`: las cabeceras
    puestas en el cliente compartido en vez de en cada llamada. Entonces las
    peticiones de B salen con el token de A, y B ve el salón de A.
    """
    a, b = dos_cuentas
    envenenado = httpx.AsyncClient(
        timeout=30.0,
        headers={"apikey": SECRET_KEY, "Authorization": f"Bearer {a['token']}"},
    )
    try:
        async def una(cuenta):
            resp = await envenenado.get(
                f"{SUPABASE_URL}/rest/v1/salons", params={"select": "id,nombre_comercial"}
            )
            resp.raise_for_status()
            return cuenta, resp.json()

        resultados = list(await asyncio.gather(*[una(dos_cuentas[i % 2]) for i in range(A_LA_VEZ)]))
    finally:
        await envenenado.aclose()

    cruces = _nadie_ve_lo_ajeno(resultados, dos_cuentas)
    assert cruces, "EL CONTROL NO VALE: se pegó el token al cliente y esto no lo detectó"
    assert any(b["nombre"] in c for c in cruces)


# ── 3 · El cliente compartido no guarda nada de nadie ──────────────────────

async def test_el_cliente_compartido_no_guarda_nada_de_nadie():
    """Las cuatro puertas por las que se colaría estado de un inquilino."""
    cliente = cliente_compartido()

    assert dict(cliente.headers) == {
        k: v for k, v in cliente.headers.items()
        if k.lower() in {"accept", "accept-encoding", "connection", "user-agent"}
    }, f"el cliente compartido lleva cabeceras propias: {dict(cliente.headers)}"
    for prohibida in ("authorization", "apikey", "cookie"):
        assert prohibida not in cliente.headers, f"el cliente compartido lleva {prohibida}"
    assert cliente.auth is None, "el cliente compartido lleva `auth`, que se aplica a todas"
    assert str(cliente.base_url) == "", "el cliente compartido tiene `base_url`"


async def test_el_cliente_compartido_NO_recuerda_galletas():
    """httpx guarda por defecto las cookies de las respuestas y las reenvía.

    En un cliente compartido eso es memoria entre inquilinos. Aquí el tarro no
    guarda nada, y esto lo comprueba metiéndole una a mano.
    """
    cliente = cliente_compartido()
    respuesta = httpx.Response(
        200,
        headers={"set-cookie": "sesion=de-otro-salon; Path=/"},
        request=httpx.Request("GET", "http://127.0.0.1:54321/rest/v1/"),
    )
    cliente.cookies.extract_cookies(respuesta)

    assert len(cliente.cookies) == 0, f"el cliente compartido guardó galletas: {dict(cliente.cookies)}"


# ── 4 · Y que de verdad se comparte ────────────────────────────────────────

async def test_muchas_peticiones_POCAS_conexiones(dos_cuentas):
    """Si esto falla, el arreglo no está haciendo nada: se volvió a abrir una
    conexión por llamada, que es justo lo que costaba 4 s por ráfaga."""
    import httpcore._backends.anyio as backend

    original = backend.AnyIOBackend.connect_tcp
    abiertas = []

    async def contando(self, host, port, *a, **kw):
        if port == 54321:
            abiertas.append(1)
        return await original(self, host, port, *a, **kw)

    backend.AnyIOBackend.connect_tcp = contando
    try:
        clientes = [(c, PostgrestClient(SUPABASE_URL, SECRET_KEY, user_jwt=c["token"], timeout=30.0))
                    for c in dos_cuentas]
        await _rafaga(clientes)          # calienta las conexiones
        abiertas.clear()
        await _rafaga(clientes)          # y ahora la que se mide
    finally:
        backend.AnyIOBackend.connect_tcp = original

    assert len(abiertas) < A_LA_VEZ / 2, (
        f"{A_LA_VEZ} peticiones abrieron {len(abiertas)} conexiones nuevas: no se está reutilizando"
    )


async def test_es_el_MISMO_cliente_en_todo_el_proceso():
    assert cliente_compartido() is cliente_compartido()
