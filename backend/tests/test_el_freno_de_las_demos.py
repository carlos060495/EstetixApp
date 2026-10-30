"""EL FRENO DE LAS DEMOS (0082): la portada decía «no hay sitio» sin que
ningún tope estuviera lleno. Reproducido el 08-10-2026 en producción y aquí.

La cadena: la contraseña de muestra de la 0081 (`muestra-xxxxxx-xxxxxx`) no
lleva mayúscula, y el GoTrue de producción (v2.197) aplica la política de
contraseñas TAMBIÉN al crear un usuario por la API de administración (el local,
v2.192, no: por eso aquí pasaba). Cada alta fallaba, la demo se deshacía sin
dejar rastro (0 creadas en el panel)… y el freno por IP ya la había contado. Al
tercer clic, «no hay sitio».

Lo que vigila este fichero, todo por la app de verdad contra el stack local:

  · la contraseña de muestra pasa la política MÁS ESTRICTA de Supabase (lo que
    hace el GoTrue de producción se emula aquí: el local no lo hace);
  · el freno por IP cuenta SOLO las demos entregadas: un alta que falla no gasta;
  · si la IP no se sabe con seguridad (sin cabecera del proxy, o la del proxy es
    interna), el freno por IP NO se aplica: los otros dos topes siguen;
  · la IP es la que pone el proxy (la última de `X-Forwarded-For`), no la que
    escriba quien llama;
  · la ventana de 60 minutos caduca de verdad;
  · el panel enseña cuántas veces se rechazó abrir una demo en la última hora y
    por cuál de los tres topes, y cuántas altas fallaron.
"""

from __future__ import annotations

import pathlib
import re
from datetime import timedelta

import httpx
import pytest

from app.core.config import get_settings
from app.db.supabase_auth_client import SupabaseAuthAdminClient, SupabaseAuthError
from app.main import app
from tests.conftest import ORIGEN_PERMITIDO, admin_cookie
from tests.test_el_salon_de_muestra import AJUSTES, _demos, _limpiar
from tests.test_un_salon_no_puede_cambiarse_el_plan import sql

pytestmark = [pytest.mark.base_real, pytest.mark.anyio]

CONFIG = pathlib.Path(__file__).resolve().parents[2] / "supabase" / "config.toml"

# La política MÁS ESTRICTA que ofrece Supabase («lower_upper_letters_digits_symbols»),
# con los mismos conjuntos que GoTrue (`GOTRUE_PASSWORD_REQUIRED_CHARACTERS`).
_CONJUNTOS = (
    "abcdefghijklmnopqrstuvwxyz",
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "0123456789",
    "!@#$%^&*()_+-=[]{};'\\:\"|<>?,./`~",
)


def _longitud_minima() -> int:
    """La de la configuración del proyecto, descubierta (no escrita a mano)."""
    m = re.search(r"^minimum_password_length\s*=\s*(\d+)", CONFIG.read_text(encoding="utf-8"), re.M)
    assert m, "supabase/config.toml sin minimum_password_length"
    return int(m.group(1))


def _debil(password: str) -> str | None:
    if len(password) < _longitud_minima():
        return "corta"
    for conjunto in _CONJUNTOS:
        if not any(c in conjunto for c in password):
            return f"le falta uno de {conjunto[:6]}…"
    return None


@pytest.fixture()
def anyio_backend():
    return "asyncio"


def _vaciar_rechazos() -> None:
    # Con el código de antes de la 0082 la tabla no existe: el guarda debe poder
    # ponerse en rojo por lo que vigila, no por el arreglo de la prueba.
    sql("do $$ begin if to_regclass('public.rechazos_de_demos') is not null then "
        "delete from public.rechazos_de_demos; end if; end $$;")


@pytest.fixture()
def ajustes():
    from app.routers import demo as router_demo

    router_demo._freno_ip._failures.clear()
    _limpiar()
    _vaciar_rechazos()
    antes = sql("select vivas_max || ',' || por_hora_max || ',' || por_ip_hora_max from public.topes_de_demos;")
    app.dependency_overrides[get_settings] = lambda: AJUSTES
    yield AJUSTES
    app.dependency_overrides.clear()
    v, h, i = antes.split(",")
    sql(f"update public.topes_de_demos set vivas_max = {v}, por_hora_max = {h}, por_ip_hora_max = {i};")
    _vaciar_rechazos()
    router_demo._freno_ip._failures.clear()
    _limpiar()


@pytest.fixture()
def gotrue_de_produccion(monkeypatch):
    """Lo que hace el GoTrue de producción (v2.197, `adminUserCreate` llama a
    `checkPasswordStrength`) y el local no: rechaza la contraseña débil."""
    original = SupabaseAuthAdminClient.create_confirmed_user
    vistas: list[str] = []

    async def con_politica(self, email, password, app_metadata):
        vistas.append(password)
        motivo = _debil(password)
        if motivo:
            raise SupabaseAuthError(422, f"weak_password: {motivo}")
        return await original(self, email, password, app_metadata)

    monkeypatch.setattr(SupabaseAuthAdminClient, "create_confirmed_user", con_politica)
    return vistas


def _navegador(xff: str | None = "81.0.0.1") -> httpx.AsyncClient:
    cabeceras = {"Origin": ORIGEN_PERMITIDO}
    if xff is not None:
        cabeceras["X-Forwarded-For"] = xff
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://localhost", headers=cabeceras)


async def _abrir(xff: str | None = "81.0.0.1") -> dict:
    async with _navegador(xff) as c:  # un navegador nuevo cada vez: sin cookie
        r = await c.post("/demo")
    return {"http": r.status_code, **r.json()}


async def _panel() -> dict:
    async with _navegador() as c:
        r = await c.get("/admin/demos", cookies={"__Host-sa-access-token": admin_cookie("owner")})
    assert r.status_code == 200, r.text
    return r.json()["estado"]


# ── 1. La contraseña de muestra pasa la política de producción ───────────────


async def test_con_la_politica_de_produccion_la_demo_se_abre(ajustes, gotrue_de_produccion):
    r = await _abrir()
    assert gotrue_de_produccion, "no se llegó a crear el usuario"
    assert r.get("estado") == "creada", (r, [_debil(p) for p in gotrue_de_produccion])


# ── 2. Un alta que falla no gasta el freno ──────────────────────────────────


async def test_tres_altas_fallidas_no_llenan_el_freno(ajustes, monkeypatch):
    sql("update public.topes_de_demos set por_ip_hora_max = 3;")
    original = SupabaseAuthAdminClient.create_confirmed_user

    async def falla(self, *a, **k):
        raise SupabaseAuthError(500, "GoTrue caído")

    monkeypatch.setattr(SupabaseAuthAdminClient, "create_confirmed_user", falla)
    fallidas = [await _abrir() for _ in range(3)]
    assert [f["http"] for f in fallidas] == [502, 502, 502] and _demos() == 0
    monkeypatch.setattr(SupabaseAuthAdminClient, "create_confirmed_user", original)
    cuarta = await _abrir()
    assert cuarta.get("estado") == "creada", cuarta


# ── 3. Si la IP no se sabe, el freno por IP no se aplica ─────────────────────


@pytest.mark.parametrize("xff", [None, "", "10.1.2.3", "100.64.0.9", "127.0.0.1", "no-es-una-ip"],
                         ids=["sin_cabecera", "vacia", "privada", "interna_cgnat", "loopback", "basura"])
async def test_sin_ip_segura_el_freno_por_ip_no_frena(ajustes, xff):
    sql("update public.topes_de_demos set por_ip_hora_max = 1;")
    r = [await _abrir(xff) for _ in range(3)]
    assert [x["estado"] for x in r] == ["creada", "creada", "creada"], r


async def test_sin_ip_segura_los_otros_topes_siguen(ajustes):
    sql("update public.topes_de_demos set por_ip_hora_max = 1, vivas_max = 2;")
    r = [await _abrir(None) for _ in range(3)]
    assert [x["estado"] for x in r] == ["creada", "creada", "sin_sitio"], r


# ── 4. La IP es la del proxy, no la que escriba quien llama ──────────────────


async def test_la_ip_es_la_que_pone_el_proxy(ajustes):
    """Railway pone la IP real al final de `X-Forwarded-For`; lo de delante lo
    puede escribir cualquiera. Cambiar lo de delante no da otra conexión."""
    sql("update public.topes_de_demos set por_ip_hora_max = 1;")
    primera = await _abrir("1.1.1.1, 81.0.0.9")
    disfrazada = await _abrir("2.2.2.2, 81.0.0.9")
    otra_de_verdad = await _abrir("2.2.2.2, 81.0.0.10")
    assert (primera["estado"], disfrazada["estado"], otra_de_verdad["estado"]) == ("creada", "sin_sitio", "creada")


# ── 5. La ventana de 60 minutos caduca ──────────────────────────────────────


async def test_la_ventana_de_una_hora_caduca(ajustes, monkeypatch):
    from app.routers import demo as router_demo

    sql("update public.topes_de_demos set por_ip_hora_max = 1;")
    assert (await _abrir())["estado"] == "creada"
    assert (await _abrir())["estado"] == "sin_sitio"
    ahora = router_demo._freno_ip._now()
    monkeypatch.setattr(router_demo._freno_ip, "_now", lambda: ahora + timedelta(minutes=61))
    assert (await _abrir())["estado"] == "creada"


# ── 6. El panel cuenta los rechazos de la última hora, por tope ──────────────


async def test_el_panel_cuenta_los_rechazos_por_tope_y_los_fallos(ajustes, monkeypatch):
    # Uno viejo: fuera de la última hora, no cuenta.
    sql("insert into public.rechazos_de_demos (minuto, motivo, veces) "
        "values (date_trunc('minute', now() - interval '2 hours'), 'sin_sitio_ip', 50);")
    sql("update public.topes_de_demos set por_ip_hora_max = 1;")
    await _abrir("81.0.0.1")
    await _abrir("81.0.0.1")                      # por la IP
    await _abrir("81.0.0.1")                      # por la IP
    sql("update public.topes_de_demos set por_ip_hora_max = 50, vivas_max = 1;")
    await _abrir("81.0.0.2")                      # por las vivas
    sql("update public.topes_de_demos set vivas_max = 200, por_hora_max = 1;")
    await _abrir("81.0.0.3")                      # por la hora

    async def falla(self, *a, **k):
        raise SupabaseAuthError(500, "GoTrue caído")

    sql("update public.topes_de_demos set por_hora_max = 40;")
    monkeypatch.setattr(SupabaseAuthAdminClient, "create_confirmed_user", falla)
    await _abrir("81.0.0.4")                      # alta fallida

    estado = await _panel()
    assert {k: estado.get(k) for k in ("rechazos_vivas", "rechazos_hora", "rechazos_ip", "fallos_al_crear")} == {
        "rechazos_vivas": 1, "rechazos_hora": 1, "rechazos_ip": 2, "fallos_al_crear": 1}
