"""Rastro de lo que hace una sesión de soporte.

admin_logs es append-only: lo que entre aquí no se puede corregir nunca. Por eso
el test más importante del fichero es el que comprueba que NO entran cuerpos de
petición ni datos personales.

El rastro vive en el doble `auditoria_de_soporte` (conftest.py), separado del
doble con los datos del salón: en producción también son dos clientes distintos
—admin_logs se escribe con la secret key del panel, nunca con el token del
inquilino—.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest
from starlette.requests import Request
from starlette.testclient import TestClient

from app.core.impersonation import COOKIE_IMPERSONACION
from app.core.impersonation_policy import LECTURAS_AUDITADAS, accion_auditable, iterar_rutas
from app.core.security import get_postgrest_client, get_service_postgrest_client
from app.main import app
from tests.conftest import ADMIN_ID, ORIGEN_PERMITIDO, SID, FakePostgrest, impersonation_cookie

SALON = "aaaaaaaa-0000-0000-0000-000000000001"
EMPLEADO = "e1111111-0000-0000-0000-000000000001"
AHORA = datetime.now(timezone.utc)


@pytest.fixture(autouse=True)
def _la_hora_de_este_test() -> None:
    """LA HORA SE TOMA AL EMPEZAR CADA TEST, no al importar el fichero. Tomada al
    importar, envejecía con la tirada: pasados 25 minutos la sesión de soporte del
    doble ya había caducado y la suite se colgaba (15-10-2026)."""
    global AHORA
    AHORA = datetime.now(timezone.utc)


def _sesion(**cambios) -> dict:
    fila = {
        "id": SID,
        "admin_id": ADMIN_ID,
        "admin_email": "owner@estetixapp.test",
        "salon_id": SALON,
        "tenant_auth_user_id": "99999999-0000-0000-0000-000000000001",
        "tenant_email": "duena@salon.test",
        "started_at": AHORA.isoformat(),
        "expires_at": (AHORA + timedelta(minutes=25)).isoformat(),
        "ended_at": None,
        "ended_reason": None,
    }
    fila.update(cambios)
    return fila


def _datos(sesion: dict | None = None) -> dict[str, list[dict]]:
    return {
        "impersonation_sessions": [sesion or _sesion()],
        "clients": [
            {
                "id": "c1",
                "salon_id": SALON,
                "nombre": "Lucía Pérez",
                "telefono": "600111222",
                "email": "lucia@correo.test",
                "notas": None,
                "vip": False,
                "total_gastado": 0,
                "visitas": 0,
                "created_at": AHORA.isoformat(),
            }
        ],
        "employees": [
            {
                "id": EMPLEADO,
                "salon_id": SALON,
                "nombre": "Marta",
                "estado": "activo",
                "pin_fichaje_hash": None,
            }
        ],
        "providers": [],
        "services": [],
    }


class PostgrestQuePoneDefectos(FakePostgrest):
    """El doble devuelve lo insertado tal cual, pero los `response_model` piden
    columnas que en la BD real rellena Postgres. Sin ellas, un POST permitido
    moriría por validación de respuesta y el test dejaría de medir la
    auditoría."""

    _DEFECTOS: dict[str, dict] = {"clients": {"vip": False, "total_gastado": 0, "visitas": 0}}

    async def insert(self, table: str, body: dict, select=None) -> dict:
        return await super().insert(
            table,
            {
                "id": f"{table}-1",
                "created_at": AHORA.isoformat(),
                **self._DEFECTOS.get(table, {}),
                **body,
            },
        )


@pytest.fixture()
def db() -> PostgrestQuePoneDefectos:
    return PostgrestQuePoneDefectos(_datos())


def _cliente_de_soporte(db: FakePostgrest) -> TestClient:
    app.dependency_overrides[get_service_postgrest_client] = lambda: db
    app.dependency_overrides[get_postgrest_client] = lambda: db
    c = TestClient(app)
    c.cookies.set(COOKIE_IMPERSONACION, impersonation_cookie(SALON))
    return c


@pytest.fixture()
def client(db: PostgrestQuePoneDefectos) -> Iterator[TestClient]:
    yield _cliente_de_soporte(db)
    app.dependency_overrides.clear()


def _logs(rastro: FakePostgrest, accion: str) -> list[dict]:
    return [l for l in rastro.tables["admin_logs"] if l["accion"] == accion]


# ── Mutaciones ──────────────────────────────────────────────────────────────


def test_una_mutacion_deja_rastro_con_ruta(
    client: TestClient, auditoria_de_soporte: FakePostgrest
) -> None:
    respuesta = client.post(
        "/providers", headers={"Origin": ORIGEN_PERMITIDO}, json={"nombre": "Proveedor"}
    )
    assert respuesta.status_code == 201

    logs = _logs(auditoria_de_soporte, "impersonation_action")
    assert len(logs) == 1
    assert logs[0]["admin_id"] == ADMIN_ID
    assert logs[0]["target_salon_id"] == SALON
    assert logs[0]["detalle"]["method"] == "POST"
    assert logs[0]["detalle"]["path"] == "/providers"
    assert logs[0]["detalle"]["sid"] == SID
    # Se apunta ANTES de hacerla (20-10-2026): el estado aún no existe...
    assert "status" not in logs[0]["detalle"]
    # ...y al terminar se completa, en su fila aparte (0091).
    (resultado,) = auditoria_de_soporte.tables["admin_logs_resultado"]
    assert resultado == {"admin_log_id": logs[0]["id"], "detalle": {"status": 201}}


def test_si_no_se_puede_completar_la_accion_se_queda_hecha_y_sin_resultado(
    client: TestClient, db: FakePostgrest, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Completar no condiciona la acción: el apunte se queda sin resultado y así
    se lee, «se intentó, no sabemos cómo acabó»."""
    from app.db.postgrest_client import PostgrestError

    class SinResultado(FakePostgrest):
        async def insert(self, table: str, body: dict, select=None) -> dict:
            if table == "admin_logs_resultado":
                raise PostgrestError(503, "caído")
            return await super().insert(table, body)

    rastro = SinResultado({"admin_logs": []})
    monkeypatch.setattr("app.core.impersonation_policy._db_auditoria", lambda _s: rastro)
    with caplog.at_level(logging.ERROR):
        respuesta = client.post("/providers", headers={"Origin": ORIGEN_PERMITIDO}, json={"nombre": "P"})

    assert respuesta.status_code == 201
    assert len(db.tables["providers"]) == 1
    assert len(_logs(rastro, "impersonation_action")) == 1
    assert "admin_logs_resultado" not in rastro.tables
    assert any("completar" in r.getMessage() for r in caplog.records if r.levelno == logging.ERROR)


def test_una_lectura_no_se_completa(client: TestClient, auditoria_de_soporte: FakePostgrest) -> None:
    """Las lecturas se cuentan juntas al mostrarlas (`admin_logs_vista`)."""
    assert client.get("/clients").status_code == 200
    assert len(_logs(auditoria_de_soporte, "impersonation_read")) == 1
    assert "admin_logs_resultado" not in auditoria_de_soporte.tables


def test_la_linea_entra_antes_que_la_accion(
    client: TestClient, db: FakePostgrest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Apuntado o no se hace: en el instante de escribir la fila, el proveedor
    todavía no existe en el salón."""
    vistos: list[int] = []

    class RastroQueMira(FakePostgrest):
        async def insert(self, table: str, body: dict, select=None) -> dict:
            if table == "admin_logs":
                vistos.append(len(db.tables["providers"]))
            return await super().insert(table, body)

    rastro = RastroQueMira({"admin_logs": []})
    monkeypatch.setattr("app.core.impersonation_policy._db_auditoria", lambda _s: rastro)
    respuesta = client.post("/providers", headers={"Origin": ORIGEN_PERMITIDO}, json={"nombre": "P"})
    assert respuesta.status_code == 201
    assert vistos == [0]
    assert len(db.tables["providers"]) == 1


def test_la_ruta_va_con_plantilla_no_con_el_id(
    client: TestClient, auditoria_de_soporte: FakePostgrest
) -> None:
    client.delete("/clients/c1", headers={"Origin": ORIGEN_PERMITIDO})
    logs = _logs(auditoria_de_soporte, "impersonation_action")
    assert logs[0]["detalle"]["path"] == "/clients/{client_id}"
    assert "c1" not in str(logs[0]["detalle"])


def test_no_se_registra_el_cuerpo_de_la_peticion(
    client: TestClient, auditoria_de_soporte: FakePostgrest
) -> None:
    respuesta = client.post(
        "/clients",
        headers={"Origin": ORIGEN_PERMITIDO},
        json={"nombre": "Lucía Pérez", "telefono": "600111222"},
    )
    assert respuesta.status_code == 201
    rastro = str(auditoria_de_soporte.tables["admin_logs"])
    assert "Lucía" not in rastro
    assert "600111222" not in rastro


def test_generar_un_pin_queda_registrado_sin_el_pin(
    client: TestClient, auditoria_de_soporte: FakePostgrest
) -> None:
    """Está permitido (es soporte legítimo y el PIN es de baja sensibilidad),
    pero tiene que quedar rastro de que se generó una credencial. El PIN en sí
    NO puede acabar en admin_logs: es append-only."""
    respuesta = client.post(
        f"/employees/{EMPLEADO}/pin/generar", headers={"Origin": ORIGEN_PERMITIDO}
    )
    assert respuesta.status_code == 200
    pin = respuesta.json()["pin"]

    logs = _logs(auditoria_de_soporte, "impersonation_action")
    assert logs[0]["detalle"]["path"] == "/employees/{employee_id}/pin/generar"
    assert pin not in str(auditoria_de_soporte.tables["admin_logs"])


class PostgrestQueRevientaLaRespuesta(PostgrestQuePoneDefectos):
    """Aplica la mutación y devuelve algo que el `response_model` no acepta:
    reproduce un endpoint que se cae DESPUÉS de haber tocado el salón."""

    async def insert(self, table: str, body: dict, select=None) -> dict:
        await super().insert(table, body)
        return {"esto-no-es-un-proveedor": True}


def test_una_mutacion_que_revienta_despues_deja_su_rastro(
    auditoria_de_soporte: FakePostgrest,
) -> None:
    """El caso en el que MÁS falta hace el rastro: la fila ya está en el salón
    y el endpoint se cae después. Como la línea se escribe ANTES, ya estaba."""
    roto = PostgrestQueRevientaLaRespuesta(_datos())
    app.dependency_overrides[get_service_postgrest_client] = lambda: roto
    app.dependency_overrides[get_postgrest_client] = lambda: roto
    c = TestClient(app, raise_server_exceptions=False)
    c.cookies.set(COOKIE_IMPERSONACION, impersonation_cookie(SALON))
    try:
        respuesta = c.post(
            "/providers", headers={"Origin": ORIGEN_PERMITIDO}, json={"nombre": "Proveedor"}
        )
    finally:
        app.dependency_overrides.clear()

    assert respuesta.status_code == 500
    assert roto.tables["providers"], "la fila ya estaba puesta en el salón"

    logs = _logs(auditoria_de_soporte, "impersonation_action")
    assert len(logs) == 1
    assert logs[0]["detalle"]["path"] == "/providers"
    assert auditoria_de_soporte.tables["admin_logs_resultado"][0]["detalle"] == {"status": 500}


# ── Intentos bloqueados ─────────────────────────────────────────────────────


def test_una_accion_bloqueada_deja_rastro_del_intento(
    client: TestClient, auditoria_de_soporte: FakePostgrest
) -> None:
    """Que el portazo funcione no quita valor forense al intento: querer cobrar
    con la tarjeta del dueño es justo lo que hay que poder demostrar después."""
    respuesta = client.post("/billing/checkout", headers={"Origin": ORIGEN_PERMITIDO}, json={})
    assert respuesta.status_code == 403

    logs = _logs(auditoria_de_soporte, "impersonation_action")
    assert len(logs) == 1
    assert logs[0]["target_salon_id"] == SALON
    assert logs[0]["detalle"]["path"] == "/billing/checkout"
    assert logs[0]["detalle"]["status"] == 403


def test_un_csrf_contra_la_sesion_de_soporte_deja_rastro(
    client: TestClient, db: FakePostgrest, auditoria_de_soporte: FakePostgrest
) -> None:
    """El 403 por Origin es literalmente una web ajena intentando escribir en el
    salón con la cookie de soporte que el navegador adjunta sola. Sin esta
    línea, un intento de CSRF no queda registrado en ninguna parte."""
    respuesta = client.post(
        "/providers", headers={"Origin": "https://sitio-malicioso.test"}, json={"nombre": "X"}
    )
    assert respuesta.status_code == 403
    assert db.tables["providers"] == [], "el portazo tiene que seguir cerrado"

    logs = _logs(auditoria_de_soporte, "impersonation_action")
    assert len(logs) == 1
    assert logs[0]["detalle"]["path"] == "/providers"
    assert logs[0]["detalle"]["status"] == 403
    assert logs[0]["detalle"]["motivo"] == "origen"
    # La PRIMERA señal de un CSRF se escribe siempre, sin esperar a nada.
    assert logs[0]["detalle"]["intentos"] == 1


@pytest.mark.parametrize(
    "metodo,ruta,cabeceras,cuerpo,motivo",
    [
        # Una web ajena escribiendo con la cookie que el navegador adjunta sola.
        (
            "POST",
            "/providers",
            {"Origin": "https://sitio-malicioso.test"},
            {"nombre": "X"},
            "origen",
        ),
        # El propio agente contra la lista negra.
        ("POST", "/billing/checkout", {"Origin": ORIGEN_PERMITIDO}, {}, "bloqueado"),
        # El propio agente contra un campo vetado del endpoint.
        (
            "PATCH",
            "/salon/profile",
            {"Origin": ORIGEN_PERMITIDO},
            {"email": "otro@correo.test"},
            "campo",
        ),
    ],
)
def test_el_motivo_distingue_quien_provoco_el_403(
    client: TestClient,
    auditoria_de_soporte: FakePostgrest,
    metodo: str,
    ruta: str,
    cabeceras: dict,
    cuerpo: dict,
    motivo: str,
) -> None:
    """Sin `motivo`, una fila de CSRF externo es indistinguible de una acción
    bloqueada del agente: mismo `accion`, misma forma, solo cambia `path`. Y eso
    deja que el ruido del atacante sepulte justo lo que hay que conservar."""
    respuesta = client.request(metodo, ruta, headers=cabeceras, json=cuerpo)
    assert respuesta.status_code == 403

    logs = _logs(auditoria_de_soporte, "impersonation_action")
    assert len(logs) == 1
    assert logs[0]["detalle"]["motivo"] == motivo


def test_una_accion_atendida_no_lleva_motivo(
    client: TestClient, auditoria_de_soporte: FakePostgrest
) -> None:
    """`motivo` solo existe cuando hubo portazo: es lo que hace que una fila de
    bloqueo se pueda separar del trabajo normal del agente."""
    client.post("/providers", headers={"Origin": ORIGEN_PERMITIDO}, json={"nombre": "Proveedor"})
    detalle = _logs(auditoria_de_soporte, "impersonation_action")[0]["detalle"]
    assert "motivo" not in detalle
    assert "intentos" not in detalle


def _csrf(client: TestClient) -> int:
    respuesta = client.post(
        "/providers", headers={"Origin": "https://sitio-malicioso.test"}, json={"nombre": "X"}
    )
    return respuesta.status_code


def test_los_csrf_repetidos_no_inundan_un_log_imborrable(
    client: TestClient, auditoria_de_soporte: FakePostgrest
) -> None:
    """El 403 por Origin lo dispara el ATACANTE, no el agente: sin freno, una web
    abierta en otra pestaña escribe filas sin límite en una tabla append-only."""
    for _ in range(5):
        assert _csrf(client) == 403

    logs = _logs(auditoria_de_soporte, "impersonation_action")
    assert len(logs) == 1, "dentro de la ventana solo se escribe la primera"
    assert logs[0]["detalle"]["intentos"] == 1


def test_al_pasar_la_ventana_la_siguiente_fila_lleva_la_cuenta(
    client: TestClient, auditoria_de_soporte: FakePostgrest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Los intentos callados no se pierden: se acumulan en el `detalle` de la
    siguiente fila. admin_logs no se puede corregir, así que la cuenta tiene que
    viajar hacia delante y no reescribiendo la fila anterior."""
    from app.core.impersonation_policy import _FrenoDeOrigen

    for _ in range(4):
        assert _csrf(client) == 403
    assert len(_logs(auditoria_de_soporte, "impersonation_action")) == 1

    despues = datetime.now(timezone.utc) + timedelta(minutes=6)
    monkeypatch.setattr(_FrenoDeOrigen, "_ahora", staticmethod(lambda: despues))
    assert _csrf(client) == 403

    logs = _logs(auditoria_de_soporte, "impersonation_action")
    assert len(logs) == 2
    # 3 callados + el que reabre la ventana. El primero ya fue en su propia fila.
    assert logs[1]["detalle"]["intentos"] == 4


@pytest.mark.parametrize("fallos", [1, 2])
def test_una_fila_perdida_no_calla_el_csrf_siguiente(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, fallos: int
) -> None:
    """El freno no puede darse por escrito antes de que el insert lo confirme.

    Si marcase la ventana como consumida y la escritura fallara —un corte de red
    basta, y por eso el `except` de la auditoría es ancho—, se quedaría
    convencido de que existe una fila que nunca llegó: hasta 5 minutos de un
    ataque EN CURSO sin una sola línea, que es justo lo que el freno existe para
    impedir.
    """
    from app.db.postgrest_client import PostgrestError

    class PostgrestQueFallaAlPrincipio(FakePostgrest):
        def __init__(self, cuantas: int) -> None:
            super().__init__({"admin_logs": []})
            self.pendientes = cuantas

        async def insert(self, table: str, body: dict, select=None) -> dict:
            if table == "admin_logs" and self.pendientes > 0:
                self.pendientes -= 1
                raise PostgrestError(503, "PostgREST caído")
            return await super().insert(table, body)

    rastro = PostgrestQueFallaAlPrincipio(fallos)
    monkeypatch.setattr("app.core.impersonation_policy._db_auditoria", lambda _s: rastro)

    for _ in range(fallos):
        assert _csrf(client) == 403  # sus filas se pierden por el fallo
    assert _csrf(client) == 403  # y aquí la BD ya responde

    logs = _logs(rastro, "impersonation_action")
    assert len(logs) == 1, "el intento siguiente tenía que poder escribir"
    # La cuenta no se pierde con la fila: los que no llegaron a registrarse
    # siguen contando en la primera fila que sí entra.
    assert logs[0]["detalle"]["intentos"] == fallos + 1


@pytest.mark.anyio
async def test_los_intentos_en_vuelo_no_se_pierden_al_deshacer(
    db: PostgrestQuePoneDefectos, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deshacer tiene que SUMAR sobre el estado vivo, no pisarlo.

    Entrelazado real, no simulado: mientras el insert de A está en vuelo
    (suspendido en su `await`), B y C entran, se cuentan como callados e
    incrementan la entrada del freno. Si al fallar A se sobrescribiese la
    entrada con la cuenta que A capturó, B y C desaparecerían de la cuenta sin
    que nadie se entere — un subconteo silencioso justo en el escenario que el
    freno existe para endurecer: CSRF sostenido con peticiones solapadas
    mientras `admin_logs` falla.

    Va por ASGI (`httpx.ASGITransport`) y no por `TestClient` porque este es
    síncrono: con él las cuatro peticiones se atenderían en fila y el
    solapamiento —que es justo lo que se mide— no llegaría a existir.
    """
    import asyncio

    import httpx

    from app.db.postgrest_client import PostgrestError

    en_vuelo = asyncio.Event()
    soltar = asyncio.Event()

    class PostgrestQueSeCuelgaYFalla(FakePostgrest):
        """El primer insert del rastro se queda en vuelo hasta que el test lo
        suelta, y entonces falla. Los siguientes funcionan."""

        def __init__(self) -> None:
            super().__init__({"admin_logs": []})
            self.primera = True

        async def insert(self, table: str, body: dict, select=None) -> dict:
            if table == "admin_logs" and self.primera:
                self.primera = False
                en_vuelo.set()
                await soltar.wait()
                raise PostgrestError(503, "PostgREST caído")
            return await super().insert(table, body)

    rastro = PostgrestQueSeCuelgaYFalla()
    monkeypatch.setattr("app.core.impersonation_policy._db_auditoria", lambda _s: rastro)

    app.dependency_overrides[get_service_postgrest_client] = lambda: db
    app.dependency_overrides[get_postgrest_client] = lambda: db
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            cookies={COOKIE_IMPERSONACION: impersonation_cookie(SALON)},
        ) as c:

            async def csrf() -> int:
                respuesta = await c.post(
                    "/providers",
                    headers={"Origin": "https://sitio-malicioso.test"},
                    json={"nombre": "X"},
                )
                return respuesta.status_code

            a = asyncio.create_task(csrf())
            await en_vuelo.wait()  # A está DENTRO del insert, suspendida
            assert await csrf() == 403  # B: callada, incrementa la entrada
            assert await csrf() == 403  # C: callada, incrementa la entrada
            soltar.set()
            assert await a == 403  # A: su fila se pierde
            assert await csrf() == 403  # D: con el rastro ya sano
    finally:
        app.dependency_overrides.clear()

    logs = _logs(rastro, "impersonation_action")
    assert len(logs) == 1
    assert logs[0]["detalle"]["intentos"] == 4, "A + B + C + D, sin perder a nadie"


def test_con_la_bd_sana_el_freno_sigue_dejando_una_sola_fila(
    client: TestClient, auditoria_de_soporte: FakePostgrest
) -> None:
    """El arreglo de arriba no puede reintroducir el problema contrario: cuando
    la escritura va bien, la ventana se consume igual y las repeticiones callan.
    La marca se pone bajo el candado ANTES del `await`, así que dos peticiones
    concurrentes tampoco pueden colar dos filas."""
    for _ in range(4):
        assert _csrf(client) == 403

    logs = _logs(auditoria_de_soporte, "impersonation_action")
    assert len(logs) == 1
    assert logs[0]["detalle"]["intentos"] == 1


@pytest.mark.parametrize(
    "metodo,ruta,cuerpo,motivo",
    [
        ("POST", "/billing/checkout", {}, "bloqueado"),
        ("PATCH", "/salon/profile", {"email": "otro@correo.test"}, "campo"),
    ],
)
def test_el_freno_no_toca_los_bloqueos_del_agente(
    client: TestClient,
    auditoria_de_soporte: FakePostgrest,
    metodo: str,
    ruta: str,
    cuerpo: dict,
    motivo: str,
) -> None:
    """El freno existe porque el 403 por Origin lo provoca un tercero. Lo que
    hace el agente —cobrar, renovarse el acceso, elegir un PIN, cambiar el email
    de la cuenta— es justo lo que hay que conservar entero, intento por intento:
    insistir tres veces en lo mismo es en sí un dato."""
    for _ in range(3):
        respuesta = client.request(metodo, ruta, headers={"Origin": ORIGEN_PERMITIDO}, json=cuerpo)
        assert respuesta.status_code == 403

    logs = _logs(auditoria_de_soporte, "impersonation_action")
    assert len(logs) == 3
    assert all(l["detalle"]["motivo"] == motivo for l in logs)
    assert all("intentos" not in l["detalle"] for l in logs)


def test_una_cookie_muerta_no_puede_escribir_en_el_rastro(
    auditoria_de_soporte: FakePostgrest,
) -> None:
    """El invariante que sostiene todo lo demás: solo se audita lo que se ha
    comprobado VIVO contra la BD. Si bastase con presentar una cookie, una
    sesión ya revocada podría meter filas en un log que nadie puede borrar."""
    muerta = PostgrestQuePoneDefectos(
        _datos(_sesion(ended_at=AHORA.isoformat(), ended_reason="revocada_por_salon"))
    )
    c = _cliente_de_soporte(muerta)
    try:
        respuesta = c.post("/billing/checkout", headers={"Origin": ORIGEN_PERMITIDO}, json={})
    finally:
        app.dependency_overrides.clear()

    assert respuesta.status_code == 401
    assert auditoria_de_soporte.tables["admin_logs"] == []


# ── Lecturas ────────────────────────────────────────────────────────────────


def test_leer_el_crm_deja_rastro(
    client: TestClient, auditoria_de_soporte: FakePostgrest
) -> None:
    assert client.get("/clients").status_code == 200
    logs = _logs(auditoria_de_soporte, "impersonation_read")
    assert len(logs) == 1
    assert logs[0]["detalle"]["path"] == "/clients"
    assert logs[0]["target_salon_id"] == SALON


def test_leer_la_ficha_de_un_cliente_se_registra_sin_su_id(
    client: TestClient, auditoria_de_soporte: FakePostgrest
) -> None:
    """La lectura de una ficha concreta es la más sensible de las tres, y es
    justo la que lleva un identificador de persona en la URL."""
    assert client.get("/clients/c1").status_code == 200

    logs = _logs(auditoria_de_soporte, "impersonation_read")
    assert len(logs) == 1
    assert logs[0]["detalle"]["path"] == "/clients/{client_id}"
    assert "c1" not in str(logs[0]["detalle"])


def test_una_lectura_auditada_no_se_lleva_los_datos_leidos(
    client: TestClient, auditoria_de_soporte: FakePostgrest
) -> None:
    """La auditoría de lectura existe por RGPD —dejar constancia de qué datos
    personales se consultaron—, así que copiarlos al log iría contra su propio
    motivo. Y admin_logs no se puede corregir después."""
    respuesta = client.get("/clients")
    assert "Lucía Pérez" in respuesta.text
    rastro = str(auditoria_de_soporte.tables["admin_logs"])
    assert "Lucía" not in rastro
    assert "600111222" not in rastro
    assert "lucia@correo.test" not in rastro


def test_las_lecturas_normales_no_dejan_rastro(
    client: TestClient, auditoria_de_soporte: FakePostgrest
) -> None:
    client.get("/services")
    assert auditoria_de_soporte.tables["admin_logs"] == []


def test_fuera_de_modo_soporte_no_se_registra_nada(
    db: FakePostgrest, auditoria_de_soporte: FakePostgrest
) -> None:
    from app.core.security import CurrentUser, get_current_user

    app.dependency_overrides[get_service_postgrest_client] = lambda: db
    app.dependency_overrides[get_postgrest_client] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        sub="u", salon_id=SALON, rol="admin", email="duena@salon.test"
    )
    try:
        TestClient(app).get("/clients")
        assert auditoria_de_soporte.tables["admin_logs"] == []
    finally:
        app.dependency_overrides.clear()


# ── Cuando no se puede auditar ──────────────────────────────────────────────


def _rastro_roto(fallo: str) -> FakePostgrest:
    import httpx

    from app.db.postgrest_client import PostgrestError

    error = (
        PostgrestError(503, "PostgREST caído")
        if fallo == "postgrest"
        else httpx.ConnectError("sin ruta al host")
    )

    class PostgrestRoto(FakePostgrest):
        async def insert(self, table: str, body: dict, select=None) -> dict:
            raise error

    return PostgrestRoto({})


@pytest.mark.parametrize("fallo", ["postgrest", "red"])
def test_si_no_se_puede_apuntar_la_accion_no_se_hace(
    client: TestClient,
    db: FakePostgrest,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    fallo: str,
) -> None:
    """Apuntado o no se hace (20-10-2026): 503 con el motivo, el salón
    intacto, y la línea en el log de aplicación con salón y sesión.

    Los dos fallos van juntos a propósito: un corte de red NO viaja como
    PostgrestError, y atrapar solo aquel dejaría pasar la acción sin rastro.
    """
    monkeypatch.setattr("app.core.impersonation_policy._db_auditoria", lambda _s: _rastro_roto(fallo))

    with caplog.at_level(logging.ERROR):
        respuesta = client.post(
            "/providers", headers={"Origin": ORIGEN_PERMITIDO}, json={"nombre": "Proveedor"}
        )

    assert respuesta.status_code == 503
    assert "no se ha hecho" in respuesta.json()["detail"]
    assert db.tables["providers"] == [], "sin su línea, la acción no se hace"

    errores = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert errores, "un fallo de auditoría no puede pasar en silencio"
    aviso = errores[0].getMessage()
    assert "impersonation_action" in aviso
    assert SALON in aviso


@pytest.mark.parametrize("fallo", ["postgrest", "red"])
def test_si_no_se_puede_apuntar_la_lectura_no_se_lee(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, fallo: str
) -> None:
    monkeypatch.setattr("app.core.impersonation_policy._db_auditoria", lambda _s: _rastro_roto(fallo))

    respuesta = client.get("/clients")

    assert respuesta.status_code == 503
    assert "no se ha leído" in respuesta.json()["detail"]
    assert "Lucía" not in respuesta.text


# ── Qué se audita y qué no, sin levantar la app ─────────────────────────────


def _peticion(metodo: str) -> Request:
    return Request({"type": "http", "method": metodo, "headers": [], "state": {}})


@pytest.mark.parametrize(
    "metodo,clave,esperado",
    [
        ("POST", "POST /clients", "impersonation_action"),
        ("PATCH", "PATCH /clients/{client_id}", "impersonation_action"),
        ("PUT", "PUT /employees/{employee_id}/pin", "impersonation_action"),
        ("DELETE", "DELETE /clients/{client_id}", "impersonation_action"),
        # Las tres lecturas PII-pesadas, por RGPD.
        ("GET", "GET /clients", "impersonation_read"),
        ("GET", "GET /clients/{client_id}", "impersonation_read"),
        ("GET", "GET /rgpd/documentos/{doc_id}/pdf", "impersonation_read"),
        # El resto de navegación no se audita: ahogaría el rastro útil.
        ("GET", "GET /services", None),
        ("GET", "GET /salon/profile", None),
        # Sin ruta resuelta no hay plantilla que registrar, y el id real de la
        # URL NO puede acabar en un log que no se puede corregir.
        ("GET", None, None),
        ("POST", None, None),
    ],
)
def test_la_clasificacion_de_lo_auditable(
    metodo: str, clave: str | None, esperado: str | None
) -> None:
    assert accion_auditable(_peticion(metodo), clave) == esperado


def test_las_lecturas_auditadas_existen_de_verdad() -> None:
    """El test de arriba compara una cadena consigo misma: si alguien renombra
    `{doc_id}` a `{documento_id}`, la auditoría RGPD dejaría de escribirse EN
    SILENCIO y seguiría en verde. Esto lo ata a las rutas reales de la app."""
    reales = {
        f"{metodo} {ruta.path}"
        for ruta in iterar_rutas(app.routes)
        for metodo in (getattr(ruta, "methods", None) or ())
    }
    huerfanas = LECTURAS_AUDITADAS - reales
    assert not huerfanas, (
        f"Lecturas auditadas que ya no existen: {sorted(huerfanas)}. "
        "Actualiza LECTURAS_AUDITADAS en app/core/impersonation_policy.py."
    )
