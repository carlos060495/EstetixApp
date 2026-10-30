"""EL CAMINO HUMANO: entrar, cambiar de centro, seguir trabajando.

Es el test que faltaba, y el incidente lo dice mejor que yo. Se verificaron con
dos centros de verdad el bot, el widget y el cron —las tres cosas que se
preguntaron— pero no lo que hace una persona: crear el segundo centro, cambiar
a él y seguir siendo administradora. Al cambiar, `/auth/me` contestaba 404
porque buscaba la ficha por el CENTRO ACTIVO, y la ficha vive en el centro 1.
Todo lo demás respondía 200, así que la sesión quedaba viva sin saber quién era
—ni que era administradora, con lo que desaparecían las pestañas admin-only— y
sin forma de volver desde la interfaz. Hubo que arreglarlo con un UPDATE a mano
en producción.

LAS DOS COSAS QUE FIJA ESTE FICHERO:

  1. LA IDENTIDAD NO ES POR CENTRO. Quién eres, tu rol y tu cuenta no cambian
     al cambiar de centro; lo único que cambia son los datos que miras.
  2. NO EXISTE UN ESTADO DEL QUE NO SE PUEDA SALIR. Con un centro guardado que
     ya no vale, la aplicación vuelve a uno utilizable Y LO DICE.

Contra la base local de verdad, porque el fallo estaba en una consulta y un
doble no tiene filas.
"""

from __future__ import annotations

import uuid

import pytest
from starlette.testclient import TestClient

from app.core.centro_activo import CABECERA_CENTRO
from app.db.postgrest_client import PostgrestClient
from app.db.supabase_auth_client import SupabaseAuthAdminClient
from app.main import app
from app.routers import auth as auth_router
from tests.lo_que_deja_una_prueba import retirar_registros
from tests.test_alta_completa_e2e import (
    SECRET_KEY,
    SUPABASE_URL,
    _current_user,
    _dep,
    _settings,
    _token_de_inquilino,
)



# `base_real` y nada más. Que el stack esté o no lo decide `tests/stack_local.py`
# UNA sola vez por tirada, esperando de verdad; antes lo preguntaba cada fichero
# por su cuenta con dos segundos de paciencia, y los tests DESAPARECÍAN. En modo
# estricto (`ESTETIX_SIN_SALTOS=1`), que no esté es un error a gritos, nunca un salto.
pytestmark = [
    pytest.mark.base_real,
]


@pytest.fixture()
def anyio_backend():
    return "asyncio"


@pytest.fixture()
async def dos_centros(monkeypatch):
    """Una cuenta Elite con dos centros y una administradora, creada de verdad.

    El segundo centro se abre con el endpoint real —no con un INSERT— para que
    el decorado recorra el mismo camino que recorrería una persona.
    """
    # MÁS PACIENTE QUE EN PRODUCCIÓN, a propósito. Estos ficheros hacen decenas
    # de altas completas seguidas contra el Supabase local, y el montaje y el
    # desmontaje empezaron a dar `httpx.ReadTimeout` —en un test distinto cada
    # vez, que es la firma de la saturación y no la de un fallo—. El techo de 5 s
    # de producción se queda como está.
    db = PostgrestClient(SUPABASE_URL, SECRET_KEY, timeout=30.0)
    # Y EL BACKEND QUE SE PRUEBA, IGUAL DE PACIENTE (29-10-2026). Sus clientes de
    # servicio nacen con el plazo de producción (5 s), y en una tirada cargada
    # una petición del quiosco se perdía por ese plazo: el quinto PIN malo era en
    # realidad el cuarto contado, y `test_fichaje_sujeta` salía rojo sin que
    # fallara nada de lo que mide. Es para lo que existe `TIMEOUT_POR_DEFECTO`.
    monkeypatch.setattr(PostgrestClient, "TIMEOUT_POR_DEFECTO", 30.0)
    email = f"e2e.{uuid.uuid4().hex[:10]}@estetixapp-e2e.com"
    creado: dict = {}

    async def _no_manda_correo(**kw):
        return None

    monkeypatch.setattr(auth_router, "_send_signup_confirmation_email", _no_manda_correo)

    # EL LIMITADOR DE INTENTOS ES GLOBAL AL PROCESO, y cada test de aqui hace
    # un alta de verdad. Sin esto, la decimotercera contesta 429 y —peor— el
    # estado se queda puesto: los tests de `/auth/signup` que corran despues
    # fallan por un limite que agoto OTRO fichero. Se limpia al entrar y no al
    # salir para que un fallo a mitad no deje el veneno servido.
    auth_router.signup_rate_limiter._by_ip.clear()
    auth_router.signup_rate_limiter._by_email.clear()

    # Y EL GOTRUE DE VERDAD, puesto a mano. Varios ficheros de tests hacen
    # `auth._auth_admin = lambda ...` DIRECTAMENTE, sin `monkeypatch`, asi que
    # su doble no se deshace nunca y se queda para el resto de la sesion. Este
    # fichero corre despues por orden alfabetico y el alta acababa hablando con
    # un doble ajeno: 502 «No se pudo generar el enlace» y `user_id=auth-user-1`
    # en el registro. Un fixture que necesita el stack REAL lo dice, en vez de
    # confiar en que nadie haya ensuciado.
    monkeypatch.setattr(
        auth_router,
        "_auth_admin",
        lambda ajustes: SupabaseAuthAdminClient(SUPABASE_URL, SECRET_KEY),
    )

    app.dependency_overrides.clear()
    ajustes = _settings()
    app.dependency_overrides[_dep(ajustes)] = lambda: ajustes
    cliente = TestClient(app)

    respuesta = cliente.post(
        "/auth/signup",
        json={
            "email": email,
            "password": "UnaClaveBastanteLarga123!",
            "salon_nombre": "Centro Uno",
            "direccion": "Calle Mayor 1, Donostia",
            "telefono": "600 11 22 33",
            "admin_nombre": "Dueña de Dos Centros",
            "plan": "elite",
            "cif": "B12345674",
        },
    )
    assert respuesta.status_code == 202, respuesta.text

    ficha = (
        await db.select(
            "users", {"select": "id,salon_id,account_id,auth_user_id", "email": f"eq.{email}"}
        )
    )[0]
    creado.update(ficha)

    # El plan lo escribe Stripe; aquí se pone a mano para poder abrir el segundo.
    await db.update("accounts", {"id": f"eq.{ficha['account_id']}"}, {"plan": "elite"})
    # Y LA SUSCRIPCIÓN VIVA, que también la escribe Stripe (0086): sin ella la
    # cuenta está cortada y la base no le deja abrir nada.
    await db.insert("subscriptions", {"account_id": ficha["account_id"], "salon_id": ficha["salon_id"],
                                      "estado": "active", "plan": "elite", "intervalo": "mensual"})

    token = _token_de_inquilino(ficha["auth_user_id"], ficha["salon_id"])
    monkeypatch.setattr(
        "app.core.security._usuario_desde_bearer",
        lambda _t, _s: _current_user(ficha["auth_user_id"], ficha["salon_id"]),
    )
    from app.core.security import get_postgrest_client

    app.dependency_overrides[get_postgrest_client] = lambda: PostgrestClient(
        SUPABASE_URL, token, timeout=30.0
    )

    segundo = cliente.post(
        "/salon/centros",
        # CON SU DIRECCIÓN Y SU TELÉFONO, que desde el 21-09-2026 son
        # obligatorios en TODOS los centros — el segundo también, y sobre todo:
        # es el local que todavía no conoce nadie y tiene su propia página, su
        # propio QR y su propio número.
        json={
            "nombre_comercial": f"Centro Dos {uuid.uuid4().hex[:6]}",
            "direccion": "Avenida del Norte 4, Donostia",
            "telefono": "600 44 55 66",
        },
        headers={"Authorization": "Bearer e2e"},
    )
    assert segundo.status_code == 201, segundo.text
    creado["centro_2"] = segundo.json()["id"]
    creado["cliente"] = cliente

    yield creado

    for salon in (creado["salon_id"], creado["centro_2"]):
        await db.delete("users", {"salon_id": f"eq.{salon}"})
        await db.delete("services", {"salon_id": f"eq.{salon}"})
        await db.delete("salons", {"id": f"eq.{salon}"})
    await db.delete("accounts", {"id": f"eq.{ficha['account_id']}"})
    # Los registros que se conservan a propósito (fichaje, descargas, dinero)
    # sobreviven a la cuenta: en la base local son restos de la prueba.
    retirar_registros([ficha["account_id"]])
    try:
        await SupabaseAuthAdminClient(SUPABASE_URL, SECRET_KEY).delete_user(
            ficha["auth_user_id"]
        )
    except Exception:
        pass
    app.dependency_overrides.clear()


def _cabeceras(centro: str | None = None) -> dict:
    h = {"Authorization": "Bearer e2e"}
    if centro:
        h[CABECERA_CENTRO] = centro
    return h


# ══════════════════════════ 1. la identidad no es por centro

@pytest.mark.anyio
async def test_al_cambiar_de_centro_sigues_siendo_quien_eres(dos_centros):
    """EL FALLO DE PRODUCCIÓN, tal cual: `/auth/me` daba 404 en el centro 2."""
    cliente = dos_centros["cliente"]
    centro_2 = dos_centros["centro_2"]

    en_el_uno = cliente.get("/auth/me", headers=_cabeceras())
    assert en_el_uno.status_code == 200, en_el_uno.text

    en_el_dos = cliente.get("/auth/me", headers=_cabeceras(centro_2))

    assert en_el_dos.status_code == 200, (
        f"/auth/me responde {en_el_dos.status_code} con el centro 2 activo: "
        f"{en_el_dos.text[:120]}. La sesión queda viva sin saber quién es."
    )
    yo_uno, yo_dos = en_el_uno.json(), en_el_dos.json()

    # Lo que NO cambia al cambiar de centro.
    for campo in ("sub", "rol", "email", "nombre_usuario", "plan"):
        assert yo_dos[campo] == yo_uno[campo], f"«{campo}» cambió al cambiar de centro"
    assert yo_dos["rol"] == "admin", "dejó de ser administradora en el otro centro"

    # Y lo que SÍ cambia: dónde estás.
    assert yo_dos["centro_activo"] == centro_2
    assert yo_dos["salon_id"] == centro_2
    assert yo_uno["centro_activo"] == dos_centros["salon_id"]


@pytest.mark.anyio
async def test_el_selector_tambien_te_reconoce_en_el_otro_centro(dos_centros):
    """La cascada del incidente: sin `/auth/me` el frontend no sabía que era
    administradora y desaparecían las pestañas admin-only. Se comprueba que la
    otra respuesta que orienta a la pantalla también es coherente."""
    cliente = dos_centros["cliente"]
    centro_2 = dos_centros["centro_2"]

    r = cliente.get("/salon/centros", headers=_cabeceras(centro_2))

    assert r.status_code == 200, r.text
    datos = r.json()
    assert datos["activo"] == centro_2
    assert {c["id"] for c in datos["centros"]} == {dos_centros["salon_id"], centro_2}
    assert datos["centro_rechazado"] is False


# ══════════════════════════ 2. no hay estado del que no se salga

@pytest.mark.anyio
async def test_un_centro_guardado_que_ya_no_vale_no_deja_la_sesion_muerta(dos_centros):
    """EL REQUISITO DE FONDO.

    Aunque algún día un centro activo sea inválido por otro motivo —se cerró,
    se retiró el permiso—, la aplicación tiene que volver a uno utilizable y
    decirlo. Que la única salida sea un UPDATE en la base es el fallo de
    verdad; el 404 solo fue cómo se manifestó.
    """
    cliente = dos_centros["cliente"]
    inventado = "cccccccc-0000-0000-0000-00000000000c"

    r = cliente.get("/auth/me", headers=_cabeceras(inventado))

    assert r.status_code == 200, (
        f"/auth/me responde {r.status_code} con un centro que no vale: no habría "
        "forma de volver desde la interfaz."
    )
    yo = r.json()
    assert yo["centro_activo"] == dos_centros["salon_id"], "no volvió a un centro utilizable"
    assert yo["centro_rechazado"] is True, "volvió atrás EN SILENCIO"
    assert yo["rol"] == "admin"


@pytest.mark.anyio
async def test_y_la_vuelta_atras_no_es_silenciosa_en_el_selector(dos_centros):
    cliente = dos_centros["cliente"]

    r = cliente.get(
        "/salon/centros", headers=_cabeceras("cccccccc-0000-0000-0000-00000000000c")
    )

    assert r.status_code == 200
    assert r.json()["centro_rechazado"] is True
    assert r.json()["activo"] == dos_centros["salon_id"]


@pytest.mark.anyio
async def test_seguir_trabajando_en_el_centro_2(dos_centros):
    """Y que después de cambiar la app siga sirviendo: los datos que se ven son
    los del centro 2, no los del 1."""
    cliente = dos_centros["cliente"]
    centro_2 = dos_centros["centro_2"]

    perfil = cliente.get("/salon/profile", headers=_cabeceras(centro_2))

    assert perfil.status_code == 200, perfil.text
    assert perfil.json()["nombre_comercial"].startswith("Centro Dos")
