"""Programa de referidos: registro público y gestión desde el panel.

Dos superficies con reglas distintas y aquí se fijan las dos: una puerta
PÚBLICA que solo puede crear solicitudes pendientes, y un panel donde `viewer`
mira pero no toca.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from starlette.testclient import TestClient

from app.core.admin_deps import get_admin_db
from app.core.codigo_referido import (
    LONGITUD_CODIGO_REFERIDO,
    generar_codigo_referido,
    normalizar_codigo_referido,
)
from app.core.config import Settings, get_settings
from app.core.support_codes import ALFABETO_CODIGO
from app.main import app
from app.routers import referidos as mod
from tests.conftest import ADMIN_ID, ORIGEN_PERMITIDO, FakePostgrest, admin_cookie

PENDIENTE = "11111111-0000-0000-0000-000000000001"
APROBADO = "22222222-0000-0000-0000-000000000002"
RECHAZADO = "33333333-0000-0000-0000-000000000003"
INEXISTENTE = "99999999-0000-0000-0000-000000000009"


def _referido(rid: str, nombre: str, estado: str, **extra) -> dict:
    base = {
        "id": rid,
        "nombre": nombre,
        "email": f"{nombre.lower()}@test.es",
        "codigo": None,
        "comision_pct": None,
        "estado": estado,
        # La base lo pone por defecto (0077): todos los de antes son personas.
        "tipo": "individual",
        "created_at": "2026-08-01T09:00:00+00:00",
        "aprobado_at": None,
        "aprobado_por": None,
    }
    base.update(extra)
    return base


def _datos() -> dict[str, list[dict]]:
    return {
        "referidos": [
            _referido(PENDIENTE, "Ana", "pendiente"),
            _referido(APROBADO, "Beto", "aprobado", codigo="K7M3XQ", comision_pct=25),
            _referido(RECHAZADO, "Cris", "rechazado"),
        ],
        "plataforma_config": [
            {
                "id": True,
                "comision_referido_pct": 10,
                "actualizado_at": "2026-08-01T09:00:00+00:00",
                "actualizado_por": None,
            }
        ],
        "admin_logs": [],
    }


@pytest.fixture()
def db() -> FakePostgrest:
    falso = FakePostgrest(_datos())

    def altas_que_cuentan(_body: dict) -> list[dict]:
        """La misma regla que la función de la 0070: cuenta si la cuenta existe
        o consta como borrada. La de verdad la prueba `test_la_atribucion_base_real.py`."""
        vivas = {c["id"] for c in falso.tables.get("accounts", [])}
        borradas = {b["account_id"] for b in falso.tables.get("cuentas_borradas", [])}
        cuenta: dict[str, int] = {}
        for a in falso.tables.get("altas_atribuidas", []):
            if a["cuenta_id"] in vivas or a["cuenta_id"] in borradas:
                cuenta[a["referido_id"]] = cuenta.get(a["referido_id"], 0) + 1
        return [{"referido_id": r, "altas": n} for r, n in cuenta.items()]

    falso.rpcs["altas_que_cuentan"] = altas_que_cuentan
    # 0077: con qué % van hoy sus salones (ninguno tiene salones aquí).
    falso.rpcs["porcentajes_de_sus_salones"] = lambda _body: []
    # 0079: si su correo es el de una administradora (aquí, de nadie).
    falso.rpcs["referidos_duenos_de_salon"] = lambda _body: []
    # 0071: el enlace de acceso que se crea al aprobar. Se apunta lo que se pidió.
    falso.enlaces = []

    def referido_nuevo_enlace(body: dict) -> list[dict]:
        falso.enlaces.append(body)
        return []

    falso.rpcs["referido_nuevo_enlace"] = referido_nuevo_enlace
    return falso


@pytest.fixture(autouse=True)
def _freno_limpio() -> Iterator[None]:
    """El freno por IP vive en memoria del PROCESO: sin esto, un test se
    comería los intentos del siguiente (todos llegan desde la misma IP)."""
    mod._freno_registro._failures.clear()
    yield
    mod._freno_registro._failures.clear()


def _cliente(db: FakePostgrest, rol: str = "owner") -> TestClient:
    app.dependency_overrides[get_admin_db] = lambda: db
    c = TestClient(app)
    c.cookies.set("__Host-sa-access-token", admin_cookie(rol))
    return c


@pytest.fixture()
def client(db: FakePostgrest) -> Iterator[TestClient]:
    yield _cliente(db)
    app.dependency_overrides.clear()


# ── El código ───────────────────────────────────────────────────────────────


def test_el_codigo_no_lleva_caracteres_que_se_confunden() -> None:
    """I, L, O y U fuera: alguien va a dictar esto por teléfono."""
    for prohibido in "ILOU":
        assert prohibido not in ALFABETO_CODIGO
    codigos = {generar_codigo_referido() for _ in range(400)}
    assert all(len(c) == LONGITUD_CODIGO_REFERIDO for c in codigos)
    assert all(set(c) <= set(ALFABETO_CODIGO) for c in codigos)
    # 32^6 combinaciones: 400 tiradas no deberían repetir ni una.
    assert len(codigos) == 400


def test_normalizar_acepta_lo_que_teclea_una_persona() -> None:
    assert normalizar_codigo_referido(" k7m3-xq ") == "K7M3XQ"


# ── Registro público ────────────────────────────────────────────────────────


def _publico(db: FakePostgrest) -> TestClient:
    """El registro público construye su PostgrestClient a mano (no hay sesión,
    así que no puede usar la dependencia del panel). Se sustituye la clase por
    una que devuelve el doble."""
    app.dependency_overrides[get_settings] = lambda: Settings(
        supabase_url="http://x", supabase_secret_key="k"
    )
    return TestClient(app)


@pytest.fixture()
def publico(db: FakePostgrest, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(mod, "PostgrestClient", lambda *a, **k: db)
    yield _publico(db)
    app.dependency_overrides.clear()


def _registrar(client: TestClient, email: str = "nueva@test.es", **extra):
    cuerpo = {"nombre": "Nueva Referida", "email": email, "tipo_declarado": "persona"}
    cuerpo.update(extra)
    return client.post("/referidos/registro", json=cuerpo)


def test_el_registro_crea_una_solicitud_pendiente(
    publico: TestClient, db: FakePostgrest
) -> None:
    respuesta = _registrar(publico)
    assert respuesta.status_code == 201
    assert respuesta.json()["estado"] == "pendiente"

    nueva = next(r for r in db.tables["referidos"] if r["email"] == "nueva@test.es")
    assert nueva["estado"] == "pendiente"
    # Lo que NO debe traer todavía:
    assert nueva.get("codigo") is None
    assert nueva.get("comision_pct") is None


def test_el_registro_no_deja_elegir_estado_ni_codigo(
    publico: TestClient, db: FakePostgrest
) -> None:
    """Aunque se manden en el cuerpo. Quien llama no decide su propio nivel."""
    respuesta = publico.post(
        "/referidos/registro",
        json={
            "nombre": "Listilla",
            "email": "listilla@test.es",
            "tipo_declarado": "persona",
            "estado": "aprobado",
            "codigo": "AAAAAA",
            "comision_pct": 90,
        },
    )
    assert respuesta.status_code == 201
    fila = next(r for r in db.tables["referidos"] if r["email"] == "listilla@test.es")
    assert fila["estado"] == "pendiente"
    assert fila.get("codigo") is None
    assert fila.get("comision_pct") is None


def test_el_email_es_unico(publico: TestClient, db: FakePostgrest) -> None:
    antes = len(db.tables["referidos"])
    assert _registrar(publico, email="ana@test.es").status_code == 409
    assert len(db.tables["referidos"]) == antes


def test_el_email_es_unico_tambien_en_mayusculas(
    publico: TestClient, db: FakePostgrest
) -> None:
    """`Ana@Test.es` y `ana@test.es` son la misma persona."""
    antes = len(db.tables["referidos"])
    assert _registrar(publico, email="Ana@Test.es").status_code == 409
    assert len(db.tables["referidos"]) == antes


def test_el_email_se_guarda_en_minusculas(publico: TestClient, db: FakePostgrest) -> None:
    _registrar(publico, email="MAYUS@Test.ES")
    assert any(r["email"] == "mayus@test.es" for r in db.tables["referidos"])


def test_un_email_invalido_no_pasa(publico: TestClient) -> None:
    assert _registrar(publico, email="esto-no-es-un-correo").status_code == 422


def test_el_freno_por_ip_corta_el_spam(publico: TestClient, db: FakePostgrest) -> None:
    for i in range(mod.MAX_REGISTROS_POR_IP):
        assert _registrar(publico, email=f"bot{i}@test.es").status_code == 201
    # El siguiente ya no entra, y no crea nada.
    antes = len(db.tables["referidos"])
    respuesta = _registrar(publico, email="bot-de-mas@test.es")
    assert respuesta.status_code == 429
    assert "Retry-After" in respuesta.headers
    assert len(db.tables["referidos"]) == antes


def test_reintentar_el_mismo_correo_tambien_gasta_intentos(
    publico: TestClient, db: FakePostgrest
) -> None:
    """Si el duplicado saliera gratis, el freno no serviría de nada: bastaría
    con reenviar el mismo correo para siempre."""
    for _ in range(mod.MAX_REGISTROS_POR_IP):
        assert _registrar(publico, email="ana@test.es").status_code == 409
    assert _registrar(publico, email="otra@test.es").status_code == 429


# ── Panel: lectura y permisos ───────────────────────────────────────────────


def test_la_lista_trae_los_referidos_y_el_global(client: TestClient) -> None:
    cuerpo = client.get("/admin/referidos").json()
    assert cuerpo["total"] == 3
    assert str(cuerpo["comision_global_pct"]) in {"10", "10.0", "10.00"}


def test_la_comision_efectiva_cae_al_global_si_no_hay_propia(client: TestClient) -> None:
    items = {i["nombre"]: i for i in client.get("/admin/referidos").json()["items"]}
    # Ana no tiene pacto propio -> el global.
    assert float(items["Ana"]["comision_efectiva_pct"]) == 10.0
    # Beto sí -> el suyo.
    assert float(items["Beto"]["comision_efectiva_pct"]) == 25.0


def test_se_puede_filtrar_por_estado(client: TestClient) -> None:
    cuerpo = client.get("/admin/referidos", params={"estado": "pendiente"}).json()
    assert [i["nombre"] for i in cuerpo["items"]] == ["Ana"]


def test_un_estado_inventado_da_400(client: TestClient) -> None:
    assert client.get("/admin/referidos", params={"estado": "vip"}).status_code == 400


def test_viewer_puede_leer(db: FakePostgrest) -> None:
    client = _cliente(db, rol="viewer")
    try:
        assert client.get("/admin/referidos").status_code == 200
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize(
    "metodo,ruta,cuerpo",
    [
        ("post", f"/admin/referidos/{PENDIENTE}/aprobar", {"tipo": "individual"}),
        ("post", f"/admin/referidos/{PENDIENTE}/rechazar", {}),
        ("patch", f"/admin/referidos/{PENDIENTE}", {"comision_pct": 50}),
        ("put", "/admin/referidos/config/comision", {"comision_referido_pct": 50}),
    ],
)
def test_viewer_no_puede_tocar_nada(
    db: FakePostgrest, metodo: str, ruta: str, cuerpo: dict
) -> None:
    client = _cliente(db, rol="viewer")
    try:
        respuesta = getattr(client, metodo)(
            ruta, headers={"Origin": ORIGEN_PERMITIDO}, json=cuerpo
        )
        assert respuesta.status_code == 403
        # Y no ha cambiado nada.
        assert db.tables["referidos"][0]["estado"] == "pendiente"
        assert db.tables["plataforma_config"][0]["comision_referido_pct"] == 10
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("rol", ["owner", "support"])
def test_owner_y_support_si_pueden_aprobar(db: FakePostgrest, rol: str) -> None:
    client = _cliente(db, rol=rol)
    try:
        assert _aprobar(client, PENDIENTE).status_code == 200
    finally:
        app.dependency_overrides.clear()


# ── Aprobar ─────────────────────────────────────────────────────────────────


def _aprobar(client: TestClient, rid: str, cuerpo: dict | None = None):
    return client.post(
        f"/admin/referidos/{rid}/aprobar",
        headers={"Origin": ORIGEN_PERMITIDO},
        # El tipo se elige al aprobar (0077): sin él, 422.
        json={"tipo": "individual", **(cuerpo or {})},
    )


def test_aprobar_genera_codigo_y_pasa_a_aprobado(
    client: TestClient, db: FakePostgrest
) -> None:
    cuerpo = _aprobar(client, PENDIENTE).json()
    assert cuerpo["estado"] == "aprobado"
    assert cuerpo["codigo"] and len(cuerpo["codigo"]) == LONGITUD_CODIGO_REFERIDO
    assert set(cuerpo["codigo"]) <= set(ALFABETO_CODIGO)

    fila = next(r for r in db.tables["referidos"] if r["id"] == PENDIENTE)
    assert fila["aprobado_at"] is not None
    assert fila["aprobado_por"] == ADMIN_ID


def test_el_codigo_generado_no_choca_con_uno_existente(
    client: TestClient, db: FakePostgrest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El primer intento devuelve un código YA usado; debe reintentar."""
    salidas = iter(["K7M3XQ", "K7M3XQ", "NUEVO1"])
    monkeypatch.setattr(mod, "generar_codigo_referido", lambda: next(salidas))
    assert _aprobar(client, PENDIENTE).json()["codigo"] == "NUEVO1"


def test_si_no_hay_codigo_libre_no_se_inventa_uno_repetido(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mod, "generar_codigo_referido", lambda: "K7M3XQ")
    assert _aprobar(client, PENDIENTE).status_code == 502


def test_aprobar_sin_porcentaje_deja_el_global(
    client: TestClient, db: FakePostgrest
) -> None:
    """NULL, no una copia del 10 de hoy: si mañana el global sube, este
    referido tiene que subir con él."""
    cuerpo = _aprobar(client, PENDIENTE).json()
    fila = next(r for r in db.tables["referidos"] if r["id"] == PENDIENTE)
    assert fila["comision_pct"] is None
    assert float(cuerpo["comision_efectiva_pct"]) == 10.0


def test_aprobar_con_porcentaje_lo_asigna(client: TestClient, db: FakePostgrest) -> None:
    cuerpo = _aprobar(client, PENDIENTE, {"comision_pct": 17.5}).json()
    assert float(cuerpo["comision_pct"]) == 17.5
    assert float(cuerpo["comision_efectiva_pct"]) == 17.5


def test_un_porcentaje_imposible_no_pasa(client: TestClient) -> None:
    assert _aprobar(client, PENDIENTE, {"comision_pct": 150}).status_code == 422
    assert _aprobar(client, PENDIENTE, {"comision_pct": -1}).status_code == 422


def test_aprobar_dos_veces_da_409(client: TestClient) -> None:
    assert _aprobar(client, APROBADO).status_code == 409


def test_un_rechazado_se_puede_aprobar_y_conserva_su_codigo(
    client: TestClient, db: FakePostgrest
) -> None:
    """Reemitir el código dejaría circulando uno viejo que ya no apunta a
    nadie."""
    fila = next(r for r in db.tables["referidos"] if r["id"] == RECHAZADO)
    fila["codigo"] = "VIEJO1"
    assert _aprobar(client, RECHAZADO).json()["codigo"] == "VIEJO1"


def test_referido_inexistente_da_404(client: TestClient) -> None:
    assert _aprobar(client, INEXISTENTE).status_code == 404


def test_aprobar_queda_en_admin_logs(client: TestClient, db: FakePostgrest) -> None:
    _aprobar(client, PENDIENTE, {"comision_pct": 12})
    log = db.tables["admin_logs"][-1]
    assert log["accion"] == "aprobar_referido"
    assert log["admin_id"] == ADMIN_ID
    assert log["detalle"]["referido_id"] == PENDIENTE
    assert log["detalle"]["comision_pct"] == "12"


# ── Rechazar ────────────────────────────────────────────────────────────────


def _rechazar(client: TestClient, rid: str, cuerpo: dict | None = None):
    return client.post(
        f"/admin/referidos/{rid}/rechazar",
        headers={"Origin": ORIGEN_PERMITIDO},
        json=cuerpo or {},
    )


def test_rechazar_cambia_el_estado(client: TestClient, db: FakePostgrest) -> None:
    assert _rechazar(client, PENDIENTE).json()["estado"] == "rechazado"


def test_rechazar_NO_libera_el_codigo(client: TestClient, db: FakePostgrest) -> None:
    """Si se liberase, podría reasignarse a otra persona y los enlaces
    antiguos apuntarían al referido equivocado."""
    _rechazar(client, APROBADO)
    fila = next(r for r in db.tables["referidos"] if r["id"] == APROBADO)
    assert fila["codigo"] == "K7M3XQ"


def test_rechazar_dos_veces_da_409(client: TestClient) -> None:
    assert _rechazar(client, RECHAZADO).status_code == 409


def test_rechazar_queda_en_admin_logs_con_el_motivo(
    client: TestClient, db: FakePostgrest
) -> None:
    _rechazar(client, PENDIENTE, {"motivo": "No cumple requisitos"})
    log = db.tables["admin_logs"][-1]
    assert log["accion"] == "rechazar_referido"
    assert log["detalle"]["motivo"] == "No cumple requisitos"


# ── Editar ──────────────────────────────────────────────────────────────────


def _editar(client: TestClient, rid: str, cuerpo: dict):
    return client.patch(
        f"/admin/referidos/{rid}", headers={"Origin": ORIGEN_PERMITIDO}, json=cuerpo
    )


def test_editar_cambia_el_porcentaje(client: TestClient, db: FakePostgrest) -> None:
    assert float(_editar(client, APROBADO, {"comision_pct": 30}).json()["comision_pct"]) == 30.0


def test_poner_el_porcentaje_a_null_devuelve_al_global(
    client: TestClient, db: FakePostgrest
) -> None:
    """Sin esto no habría forma de devolver a alguien al global una vez fijado
    un pacto propio."""
    cuerpo = _editar(client, APROBADO, {"comision_pct": None}).json()
    assert cuerpo["comision_pct"] is None
    assert float(cuerpo["comision_efectiva_pct"]) == 10.0


def test_lo_que_no_se_manda_no_se_toca(client: TestClient, db: FakePostgrest) -> None:
    _editar(client, APROBADO, {"nombre": "Beto Nuevo"})
    fila = next(r for r in db.tables["referidos"] if r["id"] == APROBADO)
    assert fila["nombre"] == "Beto Nuevo"
    # El porcentaje sigue donde estaba, no se ha puesto a NULL.
    assert fila["comision_pct"] == 25


def test_editar_sin_nada_da_400(client: TestClient) -> None:
    assert _editar(client, APROBADO, {}).status_code == 400


def test_un_iban_no_entra_ni_por_el_alta_ni_por_el_panel(
    publico: TestClient, client: TestClient, db: FakePostgrest
) -> None:
    """El IBAN se fue (0072/0073): cobran por Stripe Connect. Un formulario
    viejo que todavía lo mande da de alta igual, sin guardarlo; y el panel ya no
    tiene por dónde escribirlo."""
    assert _registrar(publico, datos_pago="ES91 2100 0418 4502 0005 1332").status_code == 201
    nueva = next(r for r in db.tables["referidos"] if r["email"] == "nueva@test.es")
    assert "datos_pago" not in nueva and "2100" not in str(nueva)
    r = _editar(client, APROBADO, {"datos_pago": "ES91 2100 0418 4502 0005 1332"})
    assert r.status_code == 400  # «No hay nada que cambiar»: el campo no existe
    assert "2100" not in str(db.tables["referidos"]) + str(db.tables["admin_logs"])


# ── El % global ─────────────────────────────────────────────────────────────


def test_se_edita_el_global(client: TestClient, db: FakePostgrest) -> None:
    respuesta = client.put(
        "/admin/referidos/config/comision",
        headers={"Origin": ORIGEN_PERMITIDO},
        json={"comision_referido_pct": 15},
    )
    assert respuesta.status_code == 200
    assert float(respuesta.json()["comision_referido_pct"]) == 15.0
    assert db.tables["plataforma_config"][0]["comision_referido_pct"] == 15.0


def test_cambiar_el_global_arrastra_a_quien_no_tiene_pacto_propio(
    client: TestClient,
) -> None:
    """Es la razón de guardar NULL en vez de copiar el global."""
    client.put(
        "/admin/referidos/config/comision",
        headers={"Origin": ORIGEN_PERMITIDO},
        json={"comision_referido_pct": 20},
    )
    items = {i["nombre"]: i for i in client.get("/admin/referidos").json()["items"]}
    assert float(items["Ana"]["comision_efectiva_pct"]) == 20.0   # sin pacto -> sube
    assert float(items["Beto"]["comision_efectiva_pct"]) == 25.0  # con pacto -> intacto


def test_un_global_imposible_no_pasa(client: TestClient) -> None:
    for valor in (101, -1):
        respuesta = client.put(
            "/admin/referidos/config/comision",
            headers={"Origin": ORIGEN_PERMITIDO},
            json={"comision_referido_pct": valor},
        )
        assert respuesta.status_code == 422


def test_cambiar_el_global_queda_en_admin_logs(
    client: TestClient, db: FakePostgrest
) -> None:
    client.put(
        "/admin/referidos/config/comision",
        headers={"Origin": ORIGEN_PERMITIDO},
        json={"comision_referido_pct": 18},
    )
    log = db.tables["admin_logs"][-1]
    assert log["accion"] == "cambiar_comision_global"
    assert log["detalle"]["comision_referido_pct"] == "18"


# ── 0071: el acceso al portal ──────────────────────────────────────────────

_O = {"Origin": ORIGEN_PERMITIDO}


def test_aprobar_crea_su_enlace_de_bienvenida_y_dice_si_salio_el_correo(client: TestClient, db: FakePostgrest) -> None:
    r = client.post(f"/admin/referidos/{PENDIENTE}/aprobar", headers=_O, json={"tipo": "individual"})
    assert r.status_code == 200, r.text
    assert [e["p_para"] for e in db.enlaces] == ["bienvenida"]
    assert db.enlaces[0]["p_referido"] == PENDIENTE
    assert db.enlaces[0]["p_minutos"] == 72 * 60
    # Sin Resend configurado en el test, el correo no sale: se dice, y la
    # aprobación no se deshace.
    assert r.json()["correo_de_acceso"] == "no_enviado"
    assert r.json()["estado"] == "aprobado"
    # El token NUNCA viaja en la respuesta ni se guarda en claro.
    assert "token" not in r.text and len(db.enlaces[0]["p_token_hash"]) == 64


def test_reenviar_el_acceso_solo_a_un_aprobado(client: TestClient, db: FakePostgrest) -> None:
    assert client.post(f"/admin/referidos/{PENDIENTE}/reenviar-acceso", headers=_O).status_code == 409
    client.post(f"/admin/referidos/{PENDIENTE}/aprobar", headers=_O, json={"tipo": "individual"})
    r = client.post(f"/admin/referidos/{PENDIENTE}/reenviar-acceso", headers=_O)
    assert r.status_code == 200, r.text
    assert [e["p_para"] for e in db.enlaces] == ["bienvenida", "bienvenida"]


def test_la_lista_no_devuelve_la_contrasena(client: TestClient) -> None:
    texto = client.get("/admin/referidos").text
    assert "password" not in texto and "argon2" not in texto
