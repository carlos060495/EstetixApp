"""Custodia del access_token del salón.

El token es una credencial completa del dueño: se acuña bajo demanda, se
guarda solo en memoria y se devuelve al purgarlo para poder cerrar su sesión
en GoTrue. Nunca se persiste ni se devuelve al cliente.

Además de los casos "de libro" (acuñar, reutilizar, purgar), este fichero
cubre cuatro huecos de concurrencia, identidad y ciclo de vida encontrados en
revisión:
  - una entrada caducada debe RETIRARSE de la estructura interna, no solo
    dejar de devolverse (fuga de memoria si nadie llama a purgar_token_tenant);
  - dos peticiones concurrentes del mismo sid no deben acuñar dos veces;
  - una purga que cae justo mientras un acuñado está en vuelo no debe dejar
    un token huérfano reinsertado después en la custodia;
  - la identidad se ELIGE por `auth_user_id` (routers/impersonation.py) pero
    GoTrue la acuña por email, así que el `sub` que devuelve tiene que
    comprobarse contra el esperado antes de custodiar nada.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

import app.core.tenant_token_broker as ttb
from app.core.config import Settings
from app.core.tenant_token_broker import (
    obtener_token_tenant,
    purgar_token_tenant,
    reiniciar_custodia,
)
from tests.conftest import IMPERSONATION_SECRET_TEST

SID = "55555555-0000-0000-0000-000000000001"
EMAIL = "duena@salon.test"
AUTH_USER = "99999999-0000-0000-0000-000000000001"


class ClienteAuthFalso:
    def __init__(self) -> None:
        self.acunados = 0
        self.cerrados: list[str] = []

    async def generate_magiclink(self, email: str) -> dict:
        return {"properties": {"hashed_token": f"hash-{email}"}}

    async def verify_magiclink(self, token_hash: str) -> dict:
        self.acunados += 1
        # `user.id` es el `sub` que GoTrue acaba de acuñar: el doble lo devuelve
        # porque el broker lo compara con el `auth_user_id` esperado.
        return {"access_token": f"token-{self.acunados}", "user": {"id": AUTH_USER}}

    async def logout_local(self, access_token: str) -> None:
        self.cerrados.append(access_token)


class ClienteAuthConcurrente:
    """Como el falso de arriba, pero `verify_magiclink` se queda esperando
    una señal antes de responder, para abrir una ventana de concurrencia real
    (una corrutina sin `await` interno no cede el control al loop de eventos,
    así que sin esta señal las peticiones "concurrentes" se ejecutarían en
    la práctica una detrás de otra y no ejercitarían la carrera)."""

    def __init__(self) -> None:
        self.acunados = 0
        self.cerrados: list[str] = []
        self.en_verify = asyncio.Event()
        self._puede_continuar = asyncio.Event()

    async def generate_magiclink(self, email: str) -> dict:
        return {"properties": {"hashed_token": f"hash-{email}"}}

    async def verify_magiclink(self, token_hash: str) -> dict:
        self.acunados += 1
        mi_numero = self.acunados  # capturado ANTES de esperar: acunados es compartido
        self.en_verify.set()
        await self._puede_continuar.wait()
        return {"access_token": f"token-{mi_numero}", "user": {"id": AUTH_USER}}

    async def logout_local(self, access_token: str) -> None:
        self.cerrados.append(access_token)

    def liberar(self) -> None:
        self._puede_continuar.set()


@pytest.fixture()
def auth(monkeypatch: pytest.MonkeyPatch) -> ClienteAuthFalso:
    reiniciar_custodia()
    falso = ClienteAuthFalso()
    monkeypatch.setattr("app.core.tenant_token_broker._cliente_auth", lambda _s: falso)
    yield falso
    reiniciar_custodia()


def _settings() -> Settings:
    return Settings(jwt_impersonation_secret=IMPERSONATION_SECRET_TEST)


@pytest.mark.anyio
async def test_acuna_el_token_la_primera_vez(auth: ClienteAuthFalso) -> None:
    token = await obtener_token_tenant(
        _settings(), SID, EMAIL, tenant_auth_user_id=AUTH_USER
    )
    assert token == "token-1"
    assert auth.acunados == 1


@pytest.mark.anyio
async def test_reutiliza_el_token_en_llamadas_sucesivas(auth: ClienteAuthFalso) -> None:
    """Una petición del salón hace varias consultas: acuñar en cada una
    generaría una sesión de GoTrue por consulta."""
    settings = _settings()
    await obtener_token_tenant(settings, SID, EMAIL, tenant_auth_user_id=AUTH_USER)
    await obtener_token_tenant(settings, SID, EMAIL, tenant_auth_user_id=AUTH_USER)
    await obtener_token_tenant(settings, SID, EMAIL, tenant_auth_user_id=AUTH_USER)
    assert auth.acunados == 1


@pytest.mark.anyio
async def test_cada_sesion_tiene_su_propio_token(auth: ClienteAuthFalso) -> None:
    settings = _settings()
    primero = await obtener_token_tenant(settings, SID, EMAIL, tenant_auth_user_id=AUTH_USER)
    segundo = await obtener_token_tenant(
        settings, "otro-sid", EMAIL, tenant_auth_user_id=AUTH_USER
    )
    assert primero != segundo


@pytest.mark.anyio
async def test_purgar_devuelve_el_token_y_lo_olvida(auth: ClienteAuthFalso) -> None:
    settings = _settings()
    token = await obtener_token_tenant(settings, SID, EMAIL, tenant_auth_user_id=AUTH_USER)

    assert purgar_token_tenant(SID) == token
    assert purgar_token_tenant(SID) is None

    # Y tras purgarlo, la siguiente petición acuña uno nuevo.
    nuevo = await obtener_token_tenant(settings, SID, EMAIL, tenant_auth_user_id=AUTH_USER)
    assert nuevo == "token-2"


@pytest.mark.anyio
async def test_si_gotrue_no_devuelve_token_se_corta_con_502(
    auth: ClienteAuthFalso, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi import HTTPException

    async def verify_vacio(token_hash: str) -> dict:
        return {}

    monkeypatch.setattr(auth, "verify_magiclink", verify_vacio)

    with pytest.raises(HTTPException) as excinfo:
        await obtener_token_tenant(_settings(), SID, EMAIL, tenant_auth_user_id=AUTH_USER)
    assert excinfo.value.status_code == 502


@pytest.mark.anyio
async def test_una_entrada_caducada_se_retira_de_la_estructura_interna(
    auth: ClienteAuthFalso, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No basta con que una entrada caducada deje de devolverse: si nadie
    llama nunca a purgar_token_tenant (el admin cierra la pestaña sin pulsar
    "salir"), tiene que desaparecer sola de `_custodia` o es una fuga de
    memoria sin límite, una entrada por sesión de soporte."""
    reloj = {"ahora": datetime(2026, 1, 1, tzinfo=timezone.utc)}
    monkeypatch.setattr(ttb, "_ahora", lambda: reloj["ahora"])

    settings = _settings()
    await obtener_token_tenant(settings, SID, EMAIL, tenant_auth_user_id=AUTH_USER)
    assert SID in ttb._custodia

    # Avanzamos el reloj más allá del TTL de la sesión de soporte.
    reloj["ahora"] = reloj["ahora"] + timedelta(minutes=ttb._MINUTOS_CUSTODIA + 1)

    # Cualquier llamada —aunque sea de otro sid— barre las entradas caducadas.
    await obtener_token_tenant(
        settings, "otro-sid-para-disparar-el-barrido", EMAIL, tenant_auth_user_id=AUTH_USER
    )

    assert SID not in ttb._custodia


@pytest.mark.anyio
async def test_dos_peticiones_concurrentes_del_mismo_sid_solo_acunan_una_vez(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reiniciar_custodia()
    falso = ClienteAuthConcurrente()
    monkeypatch.setattr("app.core.tenant_token_broker._cliente_auth", lambda _s: falso)
    settings = _settings()

    tarea1 = asyncio.create_task(
        obtener_token_tenant(settings, SID, EMAIL, tenant_auth_user_id=AUTH_USER)
    )
    await falso.en_verify.wait()  # la primera ya está "en vuelo", esperando a GoTrue

    tarea2 = asyncio.create_task(
        obtener_token_tenant(settings, SID, EMAIL, tenant_auth_user_id=AUTH_USER)
    )
    await asyncio.sleep(0)  # deja que la segunda intente entrar y quede bloqueada en el candado

    falso.liberar()
    resultado1, resultado2 = await asyncio.gather(tarea1, tarea2)

    assert resultado1 == resultado2 == "token-1"
    assert falso.acunados == 1

    reiniciar_custodia()


@pytest.mark.anyio
async def test_sesiones_distintas_no_se_bloquean_entre_si(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El candado es por sid, NO un candado único de módulo: dos sesiones de
    soporte distintas deben poder acuñar en paralelo sin esperarse la una a
    la otra."""
    reiniciar_custodia()
    falso = ClienteAuthConcurrente()
    monkeypatch.setattr("app.core.tenant_token_broker._cliente_auth", lambda _s: falso)
    settings = _settings()

    tarea1 = asyncio.create_task(
        obtener_token_tenant(settings, "sid-a", EMAIL, tenant_auth_user_id=AUTH_USER)
    )
    await falso.en_verify.wait()  # sid-a ya está esperando en verify_magiclink
    falso.en_verify.clear()

    # Si el candado fuera único de módulo, esta segunda petición (sid distinto)
    # se quedaría esperando a que sid-a suelte el candado —que no ocurrirá
    # hasta que liberemos el evento— y jamás llegaría a golpear GoTrue. Con
    # `wait_for` acotamos la espera para que un candado global cuelgue el
    # test con un fallo claro en vez de bloquearlo para siempre.
    tarea2 = asyncio.create_task(
        obtener_token_tenant(settings, "sid-b", EMAIL, tenant_auth_user_id=AUTH_USER)
    )
    await asyncio.wait_for(falso.en_verify.wait(), timeout=2)

    assert falso.acunados == 2  # ambas llegaron a GoTrue sin esperarse la una a la otra

    falso.liberar()
    resultado1, resultado2 = await asyncio.gather(tarea1, tarea2)
    assert resultado1 != resultado2

    reiniciar_custodia()


@pytest.mark.anyio
async def test_purga_en_vuelo_durante_un_acunado_no_deja_el_token_huerfano(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reproduce la carrera reportada: purgar_token_tenant cae justo cuando el
    acuñado ya golpeó a GoTrue y espera respuesta. El pop no encuentra nada
    (todavía no se ha guardado nada), pero el acuñado en vuelo NO debe
    reinsertar el token después: quedaría un access_token vivo que ya nadie
    purgará. En vez de eso, la limpieza anti-resurrección debe cerrarlo en
    GoTrue con logout_local."""
    reiniciar_custodia()
    falso = ClienteAuthConcurrente()
    monkeypatch.setattr("app.core.tenant_token_broker._cliente_auth", lambda _s: falso)
    settings = _settings()

    tarea = asyncio.create_task(
        obtener_token_tenant(settings, SID, EMAIL, tenant_auth_user_id=AUTH_USER)
    )
    await falso.en_verify.wait()  # el acuñado ya está esperando la respuesta de GoTrue

    # El admin cierra la sesión de soporte justo en ese hueco.
    purgado_antes_de_tiempo = purgar_token_tenant(SID)
    assert purgado_antes_de_tiempo is None  # nada que purgar todavía: la carrera es justo esta

    falso.liberar()
    token = await tarea

    assert token == "token-1"  # la petición ya en vuelo se sirve igual: ya se pagó el viaje
    assert SID not in ttb._custodia  # pero no queda reinsertado, huérfano, en la custodia
    assert purgar_token_tenant(SID) is None  # y no hay nada más que purgar
    assert falso.cerrados == ["token-1"]  # anti-resurrección: se cerró en GoTrue igualmente

    reiniciar_custodia()


@pytest.mark.anyio
async def test_si_el_logout_local_de_limpieza_falla_no_propaga(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El logout_local de la limpieza anti-resurrección es best-effort: la
    sesión ya se considera cerrada (no está en la custodia ni hay endpoint
    que la use), así que un fallo de red al avisar a GoTrue no debe romper
    la petición que ya tenía su token en la mano."""
    reiniciar_custodia()
    falso = ClienteAuthConcurrente()

    async def logout_que_falla(access_token: str) -> None:
        raise RuntimeError("GoTrue no responde")

    falso.logout_local = logout_que_falla  # type: ignore[method-assign]
    monkeypatch.setattr("app.core.tenant_token_broker._cliente_auth", lambda _s: falso)
    settings = _settings()

    tarea = asyncio.create_task(
        obtener_token_tenant(settings, SID, EMAIL, tenant_auth_user_id=AUTH_USER)
    )
    await falso.en_verify.wait()

    purgar_token_tenant(SID)  # dispara la limpieza anti-resurrección al terminar el acuñado

    falso.liberar()
    token = await tarea  # no debe lanzar, pese a que logout_local falla

    assert token == "token-1"
    assert SID not in ttb._custodia

    reiniciar_custodia()


# --- La identidad acuñada tiene que ser la elegida --------------------------
#
# `routers/impersonation.py` ELIGE al usuario por `auth_user_id` y lo guarda en
# la fila de la sesión, pero GoTrue lo acuña por EMAIL. Si `public.users.email`
# divergiera del email real en GoTrue, `generate_link(magiclink)` puede crear un
# usuario nuevo (sesión de soporte que no ve nada, más un usuario espurio) o,
# con datos corruptos, acuñar la identidad de OTRO salón: FastAPI creería
# `salon_id = A` por la fila de sesión mientras PostgREST devolvería los datos
# de B. Hoy no es alcanzable por la API —ningún endpoint escribe `users.email`—,
# así que estos casos fijan el cierre para que siga cerrado.

OTRO_AUTH_USER = "88888888-0000-0000-0000-000000000002"


class _AuthQueAcunaOtraIdentidad(ClienteAuthFalso):
    async def verify_magiclink(self, token_hash: str) -> dict:
        self.acunados += 1
        return {"access_token": f"token-{self.acunados}", "user": {"id": OTRO_AUTH_USER}}


@pytest.mark.anyio
async def test_si_gotrue_acuna_otra_identidad_no_se_custodia_y_corta_con_502(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import HTTPException

    reiniciar_custodia()
    falso = _AuthQueAcunaOtraIdentidad()
    monkeypatch.setattr("app.core.tenant_token_broker._cliente_auth", lambda _s: falso)
    try:
        with pytest.raises(HTTPException) as excinfo:
            await obtener_token_tenant(_settings(), SID, EMAIL, tenant_auth_user_id=AUTH_USER)

        assert excinfo.value.status_code == 502
        assert SID not in ttb._custodia  # no se custodia una credencial que no es la pedida
        assert falso.cerrados == ["token-1"]  # y se cierra en GoTrue lo que se acuñó de más
    finally:
        reiniciar_custodia()


@pytest.mark.anyio
async def test_el_error_de_identidad_no_filtra_el_token_ni_el_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El `detail` viaja al cliente y acaba en los logs de acceso: no puede
    llevar ni la credencial acuñada ni la dirección del dueño, ni tampoco el
    `sub` que devolvió GoTrue."""
    from fastapi import HTTPException

    reiniciar_custodia()
    falso = _AuthQueAcunaOtraIdentidad()
    monkeypatch.setattr("app.core.tenant_token_broker._cliente_auth", lambda _s: falso)
    try:
        with pytest.raises(HTTPException) as excinfo:
            await obtener_token_tenant(_settings(), SID, EMAIL, tenant_auth_user_id=AUTH_USER)

        texto = f"{excinfo.value.detail} {excinfo.value!r}"
        assert "token-1" not in texto
        assert EMAIL not in texto
        assert OTRO_AUTH_USER not in texto
        assert excinfo.value.detail == "No se pudo abrir la sesión de soporte."
    finally:
        reiniciar_custodia()


@pytest.mark.anyio
async def test_si_gotrue_no_dice_a_quien_acuno_tampoco_se_custodia(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sin `user.id` no hay forma de saber a quién pertenece el token: eso es
    un "no" igual que un `sub` distinto, nunca un "adelante por defecto"."""
    from fastapi import HTTPException

    reiniciar_custodia()
    falso = ClienteAuthFalso()

    async def verify_sin_usuario(token_hash: str) -> dict:
        return {"access_token": "token-sin-dueno"}

    falso.verify_magiclink = verify_sin_usuario  # type: ignore[method-assign]
    monkeypatch.setattr("app.core.tenant_token_broker._cliente_auth", lambda _s: falso)
    try:
        with pytest.raises(HTTPException) as excinfo:
            await obtener_token_tenant(_settings(), SID, EMAIL, tenant_auth_user_id=AUTH_USER)

        assert excinfo.value.status_code == 502
        assert SID not in ttb._custodia
        assert falso.cerrados == ["token-sin-dueno"]
    finally:
        reiniciar_custodia()


@pytest.mark.anyio
async def test_si_el_cierre_del_token_ajeno_falla_igual_se_corta_con_502(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El `logout_local` de esa credencial ajena es best-effort: que GoTrue no
    responda no puede convertir el 502 en un 500 —ni, mucho peor, dejar pasar
    la petición—."""
    from fastapi import HTTPException

    reiniciar_custodia()
    falso = _AuthQueAcunaOtraIdentidad()

    async def logout_que_falla(access_token: str) -> None:
        raise RuntimeError("GoTrue no responde")

    falso.logout_local = logout_que_falla  # type: ignore[method-assign]
    monkeypatch.setattr("app.core.tenant_token_broker._cliente_auth", lambda _s: falso)
    try:
        with pytest.raises(HTTPException) as excinfo:
            await obtener_token_tenant(_settings(), SID, EMAIL, tenant_auth_user_id=AUTH_USER)

        assert excinfo.value.status_code == 502
        assert SID not in ttb._custodia
    finally:
        reiniciar_custodia()
