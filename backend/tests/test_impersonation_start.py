"""Inicio de una sesión de soporte desde el panel.

Cada portazo de aquí es una forma distinta de que alguien entre en la cuenta de
un salón sin permiso: rol, código robado, código caducado, código ya usado,
fuerza bruta y despliegue mal configurado.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest
from starlette.testclient import TestClient

from app.core.admin_deps import get_admin_db
from app.core.config import Settings, get_settings
from app.core.impersonation import (
    AUD_IMPERSONACION,
    COOKIE_IMPERSONACION,
    ISS_IMPERSONACION,
    decodificar_token_impersonacion,
)
from app.core.support_codes import hash_codigo
from app.db.postgrest_client import PostgrestError
from app.main import app
from tests.conftest import (
    ADMIN_ID,
    IMPERSONATION_SECRET_TEST,
    ORIGEN_PERMITIDO,
    FakePostgrest,
    admin_cookie,
)

SALON = "aaaaaaaa-0000-0000-0000-000000000001"
SALON_SIN_ADMIN = "bbbbbbbb-0000-0000-0000-000000000002"
AUTH_USER = "99999999-0000-0000-0000-000000000001"
CODIGO = "A4B7K9M2"


def _ahora() -> datetime:
    """«Ahora» cuando se crea cada código, no cuando se importa el fichero: con
    la hora de la importación, una suite que tardaba más de 10 minutos en llegar
    aquí encontraba todos los códigos caducados (05-10-2026)."""
    return datetime.now(timezone.utc)

# La IP que ve el backend detrás de TestClient: la clave del limitador.
IP_DE_TEST = "testclient"


def _codigo(salon_id: str = SALON, **cambios) -> dict:
    fila = {
        "id": "cccccccc-0000-0000-0000-000000000001",
        "salon_id": salon_id,
        "code_hash": hash_codigo(CODIGO),
        "expires_at": (_ahora() + timedelta(minutes=10)).isoformat(),
        "used_at": None,
        "used_by_admin_id": None,
        "revoked_at": None,
        "intentos_fallidos": 0,
    }
    fila.update(cambios)
    return fila


def _datos(codigos: list[dict] | None = None) -> dict[str, list[dict]]:
    return {
        "salons": [
            {"zona_horaria": "Europe/Madrid", "id": SALON, "nombre_comercial": "Salón Demo", "email": "duena@salon.test"},
            {"zona_horaria": "Europe/Madrid", "id": SALON_SIN_ADMIN, "nombre_comercial": "Huérfano", "email": None},
        ],
        "users": [
            {
                "id": "77777777-0000-0000-0000-000000000001",
                "salon_id": SALON,
                "nombre": "Dueña",
                "email": "duena@salon.test",
                "rol": "admin",
                "auth_user_id": AUTH_USER,
                "created_at": "2025-01-01T09:00:00+00:00",
            }
        ],
        "support_access_codes": codigos if codigos is not None else [_codigo()],
        "impersonation_sessions": [],
        "admin_logs": [],
    }


@pytest.fixture(autouse=True)
def _limitador_limpio() -> Iterator[None]:
    """El limitador vive en memoria del módulo y solo deja 5 fallos por IP:
    sin resetear, los portazos de un test dejarían al siguiente con un 429
    fantasma en vez del código que mide."""
    from app.routers.impersonation import _limitador

    _limitador.clear(IP_DE_TEST)
    yield
    _limitador.clear(IP_DE_TEST)


@pytest.fixture(autouse=True)
def sin_correo(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """El aviso al dueño no debe salir a la red en tests."""
    enviados: list[tuple[str, str]] = []

    async def falso(settings, to_email: str, subject: str, html: str) -> None:
        enviados.append((to_email, subject))

    monkeypatch.setattr("app.routers.impersonation.enviar_email_branded", falso)
    return enviados


def _settings(secreto: str = IMPERSONATION_SECRET_TEST) -> Settings:
    return Settings(jwt_impersonation_secret=secreto)


def _cliente(db: FakePostgrest, rol: str = "owner", secreto: str = IMPERSONATION_SECRET_TEST) -> TestClient:
    app.dependency_overrides[get_admin_db] = lambda: db
    app.dependency_overrides[get_settings] = lambda: _settings(secreto)
    c = TestClient(app)
    c.cookies.set("__Host-sa-access-token", admin_cookie(rol))
    return c


@pytest.fixture()
def db() -> FakePostgrest:
    return FakePostgrest(_datos())


@pytest.fixture()
def client(db: FakePostgrest) -> Iterator[TestClient]:
    yield _cliente(db)
    app.dependency_overrides.clear()


def _entrar(client: TestClient, salon_id: str = SALON, codigo: str = CODIGO):
    return client.post(
        f"/admin/tenants/{salon_id}/impersonate",
        headers={"Origin": ORIGEN_PERMITIDO},
        json={"codigo": codigo},
    )


# ── Camino feliz ────────────────────────────────────────────────────────────


def test_con_codigo_valido_abre_la_sesion(client: TestClient, db: FakePostgrest) -> None:
    respuesta = _entrar(client)
    assert respuesta.status_code == 200

    cuerpo = respuesta.json()
    assert cuerpo["salon_nombre"] == "Salón Demo"
    assert cuerpo["expires_at"]

    sesion = db.tables["impersonation_sessions"][0]
    assert sesion["salon_id"] == SALON
    assert sesion["tenant_auth_user_id"] == AUTH_USER
    assert sesion["tenant_email"] == "duena@salon.test"
    assert sesion["ended_at"] is None


def test_deja_la_cookie_de_soporte_y_no_toca_la_del_panel(client: TestClient) -> None:
    respuesta = _entrar(client)
    galletas = respuesta.headers.get_list("set-cookie")
    de_soporte = [g for g in galletas if g.startswith(f"{COOKIE_IMPERSONACION}=")]
    assert len(de_soporte) == 1
    galleta = de_soporte[0]
    assert "HttpOnly" in galleta  # fuera del alcance de document.cookie
    assert "Secure" in galleta  # no viaja por http
    # Mismo sitio que el frontend (`core/cookies_de_sesion.py`): lo más estricta posible.
    assert "SameSite=strict" in galleta
    assert "Path=/" in galleta
    assert f"Max-Age={30 * 60}" in galleta  # muere sola a los 30 min
    # La sesión del panel sigue intacta: salir del modo soporte no debe obligar
    # a volver a hacer login con TOTP.
    assert not any(g.startswith("__Host-sa-access-token=") for g in galletas)


def test_la_cookie_lleva_nuestro_jwt_y_no_un_token_de_supabase(client: TestClient) -> None:
    """Lo que viaja al navegador lo firmamos nosotros, con emisor y audiencia
    propios: no sirve para hablar con Supabase ni vale en el panel."""
    respuesta = _entrar(client)
    galleta = [
        g for g in respuesta.headers.get_list("set-cookie")
        if g.startswith(f"{COOKIE_IMPERSONACION}=")
    ][0]
    token = galleta.split(";")[0].split("=", 1)[1]

    claims = decodificar_token_impersonacion(_settings(), token)
    assert claims["iss"] == ISS_IMPERSONACION
    assert claims["aud"] == AUD_IMPERSONACION
    assert claims["salon_id"] == SALON
    assert claims["sid"] == respuesta.json()["sid"]
    # El consentimiento concreto que abrió esta sesión viaja en el propio token.
    assert claims["code_id"] == "cccccccc-0000-0000-0000-000000000001"
    assert claims["iat"]


def test_la_respuesta_no_filtra_el_token_del_salon(client: TestClient) -> None:
    """El access_token del dueño no sale del backend: ni en el cuerpo, ni en
    la cookie (que lleva un JWT nuestro, firmado con otro secreto)."""
    respuesta = _entrar(client)
    assert set(respuesta.json()) == {"sid", "salon_id", "salon_nombre", "expires_at"}


def test_el_codigo_queda_marcado_como_usado(client: TestClient, db: FakePostgrest) -> None:
    _entrar(client)
    codigo = db.tables["support_access_codes"][0]
    assert codigo["used_at"] is not None
    assert codigo["used_by_admin_id"] == ADMIN_ID


def test_el_mismo_codigo_no_vale_dos_veces(client: TestClient) -> None:
    assert _entrar(client).status_code == 200
    assert _entrar(client).status_code == 401


def test_avisa_al_dueno_por_correo(client: TestClient, sin_correo: list) -> None:
    _entrar(client)
    assert len(sin_correo) == 1
    destino, asunto = sin_correo[0]
    assert destino == "duena@salon.test"
    assert "soporte" in asunto.lower()


def test_si_el_correo_falla_la_sesion_se_abre_igual(
    client: TestClient, db: FakePostgrest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El aviso es importante, pero no puede dejar al salón sin soporte."""

    async def revienta(settings, to_email: str, subject: str, html: str) -> None:
        raise RuntimeError("Resend caído")

    monkeypatch.setattr("app.routers.impersonation.enviar_email_branded", revienta)
    assert _entrar(client).status_code == 200
    assert db.tables["impersonation_sessions"][0]["ended_at"] is None
    # El aviso queda como resultado de la entrada (0091), no en la entrada.
    (resultado,) = db.tables["admin_logs_resultado"]
    assert resultado["admin_log_id"] == db.tables["admin_logs"][0]["id"]
    assert resultado["detalle"] == {"aviso": "RuntimeError"}


def test_un_salon_sin_email_deja_dicho_que_nadie_fue_avisado(
    db: FakePostgrest, sin_correo: list
) -> None:
    """`salons.email` es nullable. Si no hay a quién avisar, el rastro tiene que
    DECIRLO: en una tabla append-only que existe para probar que el acceso fue
    consentido y notificado, una entrada idéntica a la de un aviso entregado es
    un falso positivo."""
    for fila in db.tables["salons"]:
        if fila["id"] == SALON:
            fila["email"] = None
    try:
        assert _entrar(_cliente(db)).status_code == 200
        assert sin_correo == []
        assert db.tables["admin_logs_resultado"][0]["detalle"] == {"aviso": "sin_destinatario"}
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("fallo", ["postgrest", "red"])
def test_sin_su_linea_en_admin_logs_no_se_entra(db: FakePostgrest, fallo: str, sin_correo: list) -> None:
    """Entrar en la cuenta de alguien sin rastro es peor que no poder entrar
    (20-10-2026): sin la fila, 503, sin cookie y la sesión cerrada."""
    import httpx

    error = PostgrestError(503, "caído") if fallo == "postgrest" else httpx.ConnectError("sin ruta")
    insertar = db.insert

    async def insert(table: str, body: dict, select=None) -> dict:
        if table == "admin_logs":
            raise error
        return await insertar(table, body, select)

    db.insert = insert  # type: ignore[method-assign]
    try:
        respuesta = _entrar(_cliente(db))
    finally:
        app.dependency_overrides.clear()

    assert respuesta.status_code == 503
    assert not any(
        COOKIE_IMPERSONACION in g for g in respuesta.headers.get_list("set-cookie")
    ), "sin rastro no puede salir la cookie de soporte"
    sesion = db.tables["impersonation_sessions"][0]
    assert sesion["ended_at"] is not None
    assert sesion["ended_reason"] == "sin_rastro"
    assert db.tables["admin_logs"] == []
    assert sin_correo == [], "sin entrada no se avisa al dueño de una entrada"


def test_el_aviso_sale_con_la_entrada_ya_apuntada(
    client: TestClient, db: FakePostgrest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """20-10-2026: el dueño no puede recibir el aviso de una entrada
    que no ocurrió. Cuando sale el correo, la entrada ya está en admin_logs."""
    apuntadas_al_avisar: list[int] = []

    async def mira(settings, to_email: str, subject: str, html: str) -> None:
        apuntadas_al_avisar.append(len(db.tables["admin_logs"]))

    monkeypatch.setattr("app.routers.impersonation.enviar_email_branded", mira)
    assert _entrar(client).status_code == 200
    assert apuntadas_al_avisar == [1]
    assert db.tables["admin_logs_resultado"][0]["detalle"] == {"aviso": "entregado"}


def test_deja_rastro_en_admin_logs(client: TestClient, db: FakePostgrest) -> None:
    _entrar(client)
    log = db.tables["admin_logs"][0]
    assert log["accion"] == "impersonation_start"
    assert log["target_salon_id"] == SALON
    assert log["admin_id"] == ADMIN_ID
    assert log["detalle"]["sid"]


def test_el_rastro_no_lleva_el_codigo_en_claro(client: TestClient, db: FakePostgrest) -> None:
    """admin_logs es append-only por trigger: lo que entre ahí no se borra
    jamás, así que el código dictado por teléfono no puede entrar.

    Se mira también el camino fallido, que es donde más tienta anotar qué se
    tecleó "para poder investigar": ahí quedaría escrito para siempre el código
    bueno de quien se equivoca de dígito al copiarlo.
    """
    assert _entrar(client).status_code == 200
    for _ in range(3):
        assert _entrar(client, codigo="MALO1234").status_code == 401

    rastro = str(db.tables["admin_logs"])
    assert "impersonation_start" in rastro
    assert "impersonation_denied" in rastro  # los intentos fallidos sí constan
    assert CODIGO not in rastro
    assert "MALO1234" not in rastro


# ── Portazos ────────────────────────────────────────────────────────────────


def test_viewer_no_puede_impersonar(db: FakePostgrest) -> None:
    try:
        assert _entrar(_cliente(db, rol="viewer")).status_code == 403
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("rol", ["owner", "support"])
def test_owner_y_support_si_pueden(db: FakePostgrest, rol: str) -> None:
    try:
        assert _entrar(_cliente(db, rol=rol)).status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_sin_secreto_configurado_devuelve_503(db: FakePostgrest) -> None:
    try:
        assert _entrar(_cliente(db, secreto="")).status_code == 503
    finally:
        app.dependency_overrides.clear()


def test_con_el_secreto_del_panel_devuelve_503(db: FakePostgrest) -> None:
    from tests.conftest import ADMIN_JWT_SECRET_TEST

    try:
        assert _entrar(_cliente(db, secreto=ADMIN_JWT_SECRET_TEST)).status_code == 503
    finally:
        app.dependency_overrides.clear()


def test_codigo_incorrecto_devuelve_401(client: TestClient) -> None:
    assert _entrar(client, codigo="ZZZZ-ZZZZ").status_code == 401


def test_codigo_caducado_devuelve_401(db: FakePostgrest) -> None:
    db.tables["support_access_codes"] = [
        _codigo(expires_at=(_ahora() - timedelta(minutes=1)).isoformat())
    ]
    try:
        assert _entrar(_cliente(db)).status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_codigo_revocado_devuelve_401(db: FakePostgrest) -> None:
    db.tables["support_access_codes"] = [_codigo(revoked_at=_ahora().isoformat())]
    try:
        assert _entrar(_cliente(db)).status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_el_codigo_de_otro_salon_no_sirve(client: TestClient, db: FakePostgrest) -> None:
    """El código va atado a su salón: si valiese en otro, el consentimiento de
    un dueño abriría la cuenta de otro."""
    assert _entrar(client, salon_id=SALON_SIN_ADMIN).status_code == 401


def test_tres_fallos_queman_el_codigo(client: TestClient, db: FakePostgrest) -> None:
    for _ in range(3):
        _entrar(client, codigo="ZZZZ-ZZZZ")
    assert db.tables["support_access_codes"][0]["revoked_at"] is not None
    assert _entrar(client).status_code == 401


def test_el_panel_acota_por_salon_lo_que_escribe_en_los_codigos(
    client: TestClient, db: FakePostgrest
) -> None:
    """El panel escribe con service role: no hay RLS que lo acote al inquilino,
    así que el WHERE es toda la frontera (mismo invariante que en soporte.py).

    Cubre los dos caminos de escritura: el contador de intentos fallidos y el
    marcado del código como usado. El id sale de un SELECT ya filtrado por
    salón, pero repetir `salon_id` es lo que evita que un refactor de ese SELECT
    abra un cruce entre salones sin que nadie se entere.
    """
    _entrar(client, codigo="ZZZZ-ZZZZ")  # suma un intento fallido
    _entrar(client)  # acierta: marca el código como usado

    escrituras = [params for tabla, params, _ in db.updates if tabla == "support_access_codes"]
    assert len(escrituras) == 2, f"esperaba las dos escrituras, vi {escrituras}"
    for params in escrituras:
        assert params.get("salon_id") == f"eq.{SALON}", f"escritura sin acotar por salón: {params}"


def test_al_sexto_intento_desde_la_misma_ip_responde_429(client: TestClient) -> None:
    """Ocho caracteres de un alfabeto de 32 no se adivinan con ingenio: se
    adivinan a base de intentos. El corte es por IP y no distingue si el código
    de este intento era bueno."""
    for _ in range(5):
        assert _entrar(client, codigo="ZZZZ-ZZZZ").status_code == 401
    respuesta = _entrar(client)
    assert respuesta.status_code == 429
    assert respuesta.headers["retry-after"]


class _PostgrestSinContador(FakePostgrest):
    """PostgREST que revienta justo al anotar el intento fallido."""

    async def update(self, table: str, params: dict[str, str], body: dict) -> list[dict]:
        if table == "support_access_codes" and "intentos_fallidos" in body:
            raise PostgrestError(500, "no se pudo escribir el contador")
        return await super().update(table, params, body)


def test_si_no_se_puede_contar_el_fallo_el_portazo_sigue_siendo_401() -> None:
    """Contar el intento es best-effort; cerrar la puerta no.

    Si el UPDATE del contador escapa como 500 pasan tres cosas, y la segunda es
    la grave: el 401 se convierte en 500, `add_failure` no llega a correr —la
    defensa antifuerza bruta falla ABIERTA— y el 500 solo aparece cuando el
    salón tiene código vivo, que es un oráculo.
    """
    db = _PostgrestSinContador(_datos())
    try:
        c = _cliente(db)
        for _ in range(5):
            assert _entrar(c, codigo="ZZZZ-ZZZZ").status_code == 401
        # El límite por IP sigue en pie pese a que ningún contador se guardó.
        assert _entrar(c).status_code == 429
    finally:
        app.dependency_overrides.clear()


class _PostgrestQuePierdeLaCarrera(FakePostgrest):
    """Otra petición marcó el mismo código entre nuestro SELECT y nuestro
    UPDATE: PostgREST devuelve cero filas afectadas."""

    async def update(self, table: str, params: dict[str, str], body: dict) -> list[dict]:
        if table == "support_access_codes" and params.get("used_at") == "is.null":
            return []
        return await super().update(table, params, body)


def test_quien_pierde_la_carrera_por_el_codigo_no_abre_sesion() -> None:
    """Un código, una sesión. Dos peticiones simultáneas con el mismo código
    valen lo mismo hasta el último instante: las separa el UPDATE condicional
    por `used_at is null`, y la que no afecta a ninguna fila se va con 401 sin
    haber abierto nada."""
    db = _PostgrestQuePierdeLaCarrera(_datos())
    try:
        assert _entrar(_cliente(db)).status_code == 401
        assert db.tables["impersonation_sessions"] == []
    finally:
        app.dependency_overrides.clear()


def test_con_sesion_viva_un_codigo_inventado_sigue_dando_401(
    client: TestClient, db: FakePostgrest
) -> None:
    """La razón de fondo del orden de comprobaciones: el código va PRIMERO.

    Quien no trae un código bueno no tiene por qué enterarse de si hay o no una
    sesión de soporte abierta en esa cuenta. Si el 409 se comprobase antes, el
    endpoint contaría lo que pasa dentro de un salón ajeno a cualquiera que
    llegue con un código inventado.
    """
    db.tables["impersonation_sessions"].append(
        {
            "id": "55555555-0000-0000-0000-000000000009",
            "salon_id": SALON,
            "expires_at": (_ahora() + timedelta(minutes=20)).isoformat(),
            "ended_at": None,
        }
    )
    assert _entrar(client, codigo="ZZZZ-ZZZZ").status_code == 401


def test_salon_inexistente_devuelve_404(client: TestClient) -> None:
    assert _entrar(client, salon_id="eeeeeeee-0000-0000-0000-000000000009").status_code == 404


def test_salon_sin_usuario_admin_devuelve_409(db: FakePostgrest) -> None:
    db.tables["support_access_codes"] = [_codigo(salon_id=SALON_SIN_ADMIN)]
    try:
        assert _entrar(_cliente(db), salon_id=SALON_SIN_ADMIN).status_code == 409
    finally:
        app.dependency_overrides.clear()


def test_con_sesion_ya_activa_devuelve_409(client: TestClient, db: FakePostgrest) -> None:
    db.tables["impersonation_sessions"].append(
        {
            "id": "55555555-0000-0000-0000-000000000009",
            "salon_id": SALON,
            "expires_at": (_ahora() + timedelta(minutes=20)).isoformat(),
            "ended_at": None,
        }
    )
    assert _entrar(client).status_code == 409


def test_una_sesion_ya_activa_no_quema_el_codigo(client: TestClient, db: FakePostgrest) -> None:
    """El 409 se decide antes de tocar el código: si lo consumiese, el dueño
    tendría que dictar otro por un choque que no es culpa suya."""
    db.tables["impersonation_sessions"].append(
        {
            "id": "55555555-0000-0000-0000-000000000009",
            "salon_id": SALON,
            "expires_at": (_ahora() + timedelta(minutes=20)).isoformat(),
            "ended_at": None,
        }
    )
    _entrar(client)
    assert db.tables["support_access_codes"][0]["used_at"] is None


def test_sin_origin_permitido_devuelve_403(client: TestClient) -> None:
    """CSRF: además de `SameSite=Strict`, el origen se comprueba (otro subdominio
    de estetixapp.es también sería «mismo sitio»)."""
    respuesta = client.post(
        f"/admin/tenants/{SALON}/impersonate",
        headers={"Origin": "https://sitio-malicioso.test"},
        json={"codigo": CODIGO},
    )
    assert respuesta.status_code == 403
