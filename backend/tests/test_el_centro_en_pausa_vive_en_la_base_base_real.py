"""EL CENTRO EN PAUSA VIVE EN LA BASE (0088), contra la base de verdad.

Lo que se fija aquí:

  1. EL ESPEJO DEL REPARTO. `centros_en_pausa()` de la base y `lo_que_incluye`
     de Python pausan los mismos centros, caso por caso; y `centros_del_plan()`
     dice lo mismo que `CENTROS_POR_PLAN`. Con sus controles: cambiar una sola
     de las dos mitades lo pone en rojo.
  2. LA VÍA DIRECTA, con la sesión real de la titular contra un centro en pausa:
     sus clientas y su caja vuelven vacías, escribir una cita da 403 y su agenda
     se lee. Con el control: en Elite, las mismas consultas devuelven filas.
  3. EL BACKEND: la agenda del centro en pausa se abre en solo lectura y nada
     escribe con él en la cabecera.
  4. NADA SE BORRA: sus citas futuras siguen ahí, y al subir de plan el centro
     vuelve entero sin que nadie lo reactive.
  5. EL FICHAJE: en un centro en pausa no se entra, pero se sale.
  6. LA REGLA 8: si el plan baja sin elección (Stripe), se queda el más antiguo;
     subir a un plan que los cubre todos borra la elección vieja.
  7. UNA VEZ CADA 30 DÍAS, comprobado en el servidor, y el registro inmutable.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.billing import lo_que_incluye_el_plan
from app.billing.lo_que_incluye_el_plan import lo_que_incluye
from app.billing.plans import CENTROS_POR_PLAN
from app.core.centro_activo import CABECERA_CENTRO
from app.core.tenant_token_broker import acunar_token_tenant
from app.db.postgrest_client import PostgrestClient
from app.db.supabase_auth_client import SupabaseAuthAdminClient
from app.routers import billing
from tests.lo_que_deja_una_prueba import sql
from tests.test_alta_completa_e2e import SECRET_KEY, SUPABASE_URL
from tests.test_camino_humano_dos_centros import (  # noqa: F401  (fixtures)
    anyio_backend,
    dos_centros,
)
from tests.test_el_corte_vive_en_la_base_base_real import ENTRAR, PAUSAR_EL_DEL_SEED, SALON_SEED

pytestmark = [pytest.mark.base_real, pytest.mark.anyio]


# ═══ 1. el espejo del reparto ═══════════════════════════════════════════════

# `accounts.plan` es un enum NOT NULL: en la base no hay plan desconocido. Que
# uno desconocido no pause nada lo fija `centros_del_plan` (abajo).
PLANES = ("basico", "pro", "elite")


def _casos() -> list[dict]:
    """Cuentas de 1 a 3 centros, con y sin marca, con fechas repetidas (para que
    decida el id) y con cada plan."""
    casos = []
    fechas = ("2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00", "2025-06-01T00:00:00+00:00")
    for plan in PLANES:
        for n in (1, 2, 3):
            for marca in (None, *range(n)):
                casos.append({"plan": plan, "n": n, "marca": marca, "fechas": fechas[:n]})
    return casos


def diferencias(antes: str = "", reparto=lo_que_incluye) -> list:
    """Los casos en los que la base y `reparto` no pausan lo mismo. `antes` corre
    dentro de la transacción (que se deshace): así se rompe la base a propósito."""
    casos = _casos()
    cuerpo = ["begin;", antes]
    for i, c in enumerate(casos):
        cuenta = f"7e5a0000-0000-0000-0000-{i:012d}"
        cuerpo.append(f"insert into public.accounts (id, nombre, plan) values ('{cuenta}', 'Reparto {i}', '{c['plan']}');")
        for j in range(c["n"]):
            centro = f"7e5a{j:04d}-0000-0000-0000-{i:012d}"
            cuerpo.append(
                f"insert into public.salons (id, nombre_comercial, account_id, created_at, sigue_al_bajar) values "
                f"('{centro}', 'R{i}-{j}', '{cuenta}', '{c['fechas'][j]}', {str(c['marca'] == j).lower()});"
            )
        cuerpo.append(f"select 'R|{i}|' || array_to_string(public.centros_en_pausa('{cuenta}'), ',');")
    cuerpo.append("rollback;")
    salida = sql("\n".join(cuerpo))
    base = {int(ln.split("|")[1]): ln.split("|")[2] for ln in salida.splitlines() if ln.startswith("R|")}
    assert len(base) == len(casos), "la base no contestó a todos los casos"

    fuera = []
    for i, c in enumerate(casos):
        centros = [
            {"id": f"7e5a{j:04d}-0000-0000-0000-{i:012d}", "created_at": c["fechas"][j],
             "sigue_al_bajar": c["marca"] == j}
            for j in range(c["n"])
        ]
        python = ",".join(reparto(c["plan"], centros).centros_en_pausa)
        if python != base[i]:
            fuera.append((c, {"base": base[i], "python": python}))
    return fuera


def test_el_reparto_de_la_base_y_el_de_python_pausan_los_mismos_centros():
    assert diferencias() == []


def test_centros_del_plan_es_el_espejo_de_CENTROS_POR_PLAN():
    filas = sql("select string_agg(p || '=' || coalesce(public.centros_del_plan(p)::text, 'null'), ',') "
                "from unnest(array['basico','pro','elite','platino']) p;")
    base = dict(x.split("=") for x in filas.split(","))
    assert {k: int(v) for k, v in base.items() if k in CENTROS_POR_PLAN} == CENTROS_POR_PLAN
    assert base["platino"] == "null", "un plan que no conocemos no pausa nada"


def test_CONTROL_si_cambia_la_base_y_no_python_se_pone_rojo():
    rota = ("create or replace function public.centros_del_plan(p_plan text) returns int language sql "
            "immutable set search_path = '' as $$ select case p_plan when 'basico' then 1 when 'pro' then 2 "
            "when 'elite' then 2 end $$;")
    assert diferencias(rota), "la base deja Pro con dos centros y el guarda no lo ve"


def test_CONTROL_si_cambia_python_y_no_la_base_se_pone_rojo(monkeypatch):
    # El reparto en Python deja de mirar la marca: el más antiguo siempre.
    monkeypatch.setattr(lo_que_incluye_el_plan, "_clave",
                        lambda c: (0, str(c.get("created_at") or ""), str(c.get("id") or "")))
    assert diferencias(), "Python ignora la marca y el guarda no lo ve"


def test_CONTROL_si_cambia_el_tope_en_python_se_pone_rojo(monkeypatch):
    monkeypatch.setitem(lo_que_incluye_el_plan.CENTROS_POR_PLAN, "pro", 2)
    assert diferencias()


# ═══ 2–4. la vía directa, el backend y que nada se borra ═══════════════════

def _db() -> PostgrestClient:
    return PostgrestClient(SUPABASE_URL, SECRET_KEY, timeout=30.0)


def _directo(token: str) -> dict:
    return {"apikey": token, "Authorization": f"Bearer {token}"}


def _cab(centro: str | None = None) -> dict:
    h = {"Authorization": "Bearer e2e"}
    if centro:
        h[CABECERA_CENTRO] = centro
    return h


@pytest.fixture()
async def en_pausa(dos_centros):
    """La cuenta de dos centros con datos en el SEGUNDO (una clienta, un
    ingreso, una cita futura) y la sesión real de su titular. El test decide
    cuándo baja a Pro; al salir vuelve a Elite y se lleva lo sembrado."""
    db = _db()
    c2 = dos_centros["centro_2"]
    clienta = await db.insert("clients", {"salon_id": c2, "nombre": "Clienta Del Dos", "telefono": "+34600999888",
                                          "alergias": "Níquel"}, select="id")
    ingreso = await db.insert("incomes", {"salon_id": c2, "concepto": "Caja del dos", "importe": 25}, select="id")
    empleada = await db.insert("employees", {"salon_id": c2, "nombre": "Ficha Del Dos"}, select="id")
    manana = datetime.now(timezone.utc) + timedelta(days=3)
    cita = await db.insert("appointments", {
        "salon_id": c2, "client_id": clienta["id"], "employee_id": empleada["id"],
        "inicio": manana.isoformat(), "fin": (manana + timedelta(minutes=30)).isoformat(), "estado": "confirmada",
    }, select="id")
    email = (await db.select("users", {"select": "email", "id": f"eq.{dos_centros['id']}"}))[0]["email"]
    token = await acunar_token_tenant(SupabaseAuthAdminClient(SUPABASE_URL, SECRET_KEY), email,
                                      auth_user_id=dos_centros["auth_user_id"])
    yield {**dos_centros, "token": token, "clienta": clienta["id"], "cita": cita["id"],
           "ingreso": ingreso["id"], "empleada": empleada["id"]}
    await db.update("accounts", {"id": f"eq.{dos_centros['account_id']}"}, {"plan": "elite"})
    await db.delete("time_entries", {"salon_id": f"eq.{c2}"})
    await db.delete("appointments", {"salon_id": f"in.({dos_centros['salon_id']},{c2})"})
    await db.delete("incomes", {"id": f"eq.{ingreso['id']}"})
    await db.delete("employees", {"id": f"eq.{empleada['id']}"})
    await db.delete("clients", {"salon_id": f"in.({dos_centros['salon_id']},{c2})"})


async def _plan(cuenta: str, plan: str) -> None:
    await _db().update("accounts", {"id": f"eq.{cuenta}"}, {"plan": plan})


def _leer(token: str, tabla: str, centro: str) -> httpx.Response:
    return httpx.get(f"{SUPABASE_URL}/rest/v1/{tabla}", params={"select": "id", "salon_id": f"eq.{centro}"},
                     headers=_directo(token))


async def test_por_la_via_directa_el_centro_en_pausa_no_se_lee_ni_se_escribe_y_su_agenda_si(en_pausa):
    token, c2, cuenta = en_pausa["token"], en_pausa["centro_2"], en_pausa["account_id"]

    # EL CONTROL: en Elite los dos centros trabajan y hay filas que cortar.
    assert len(_leer(token, "clients", c2).json()) == 1
    assert len(_leer(token, "incomes", c2).json()) == 1

    await _plan(cuenta, "pro")  # el segundo (el más nuevo) queda en pausa

    clientas = _leer(token, "clients", c2)
    caja = _leer(token, "incomes", c2)
    assert (clientas.status_code, clientas.json()) == (200, []), clientas.text
    assert (caja.status_code, caja.json()) == (200, []), caja.text

    escribir = httpx.post(f"{SUPABASE_URL}/rest/v1/appointments", headers=_directo(token), json={
        "salon_id": c2, "employee_id": en_pausa["empleada"], "estado": "confirmada",
        "inicio": (datetime.now(timezone.utc) + timedelta(days=5)).isoformat(),
        "fin": (datetime.now(timezone.utc) + timedelta(days=5, minutes=30)).isoformat()})
    assert escribir.status_code == 403, escribir.text

    agenda = _leer(token, "appointments", c2)
    assert agenda.status_code == 200 and [c["id"] for c in agenda.json()] == [en_pausa["cita"]]
    # Y lo que protege a quien se sienta en la silla, también del centro en pausa.
    protege = httpx.post(f"{SUPABASE_URL}/rest/v1/rpc/clientas_de_la_agenda", headers=_directo(token),
                         json={"p_ids": [en_pausa["clienta"]]})
    assert protege.json() == [{"id": en_pausa["clienta"], "nombre": "Clienta Del Dos", "alergias": "Níquel"}]

    # El centro activo sigue trabajando con normalidad.
    assert _leer(token, "clients", en_pausa["salon_id"]).status_code == 200


async def test_el_backend_abre_su_agenda_en_solo_lectura_y_no_escribe_con_el(en_pausa):
    cliente, c2, cuenta = en_pausa["cliente"], en_pausa["centro_2"], en_pausa["account_id"]
    await _plan(cuenta, "pro")
    hoy = datetime.now(timezone.utc)

    yo = cliente.get("/auth/me", headers=_cab(c2)).json()
    assert yo["viendo_centro_en_pausa"] is True and yo["centro_activo"] == c2

    citas = cliente.get("/appointments", params={"desde": hoy.isoformat(),
                                                 "hasta": (hoy + timedelta(days=7)).isoformat()},
                        headers=_cab(c2))
    assert citas.status_code == 200, citas.text
    cita = next(c for c in citas.json() if c["id"] == en_pausa["cita"])
    assert cita["clients"] == {"nombre": "Clienta Del Dos", "alergias": "Níquel"}
    assert "+34600999888" not in citas.text

    # Escribir con el centro en pausa en la cabecera: 403, ni en él ni en otro.
    mover = cliente.patch(f"/appointments/{en_pausa['cita']}", json={"notas": "movida"}, headers=_cab(c2))
    assert mover.status_code == 403 and mover.json()["detail"]["codigo"] == "solo_lectura_centro_en_pausa"
    # Lo que no es agenda no se abre en el pausado: va al activo, como siempre.
    clientas = cliente.get("/clients", headers=_cab(c2))
    assert "Clienta Del Dos" not in clientas.text

    # EL CONTROL: sin cabecera se trabaja en el activo y no se está «viendo» nada.
    assert cliente.get("/auth/me", headers=_cab()).json()["viendo_centro_en_pausa"] is False


async def test_nada_se_borra_y_al_subir_vuelve_entero_sin_reactivarlo(en_pausa):
    token, c2, cuenta, cliente = en_pausa["token"], en_pausa["centro_2"], en_pausa["account_id"], en_pausa["cliente"]
    filtro = {"salon_id": f"eq.{c2}", "inicio": f"gte.{datetime.now(timezone.utc).isoformat()}"}
    antes = await _db().count("appointments", filtro)

    await _plan(cuenta, "pro")
    assert await _db().count("appointments", filtro) == antes == 1, "la pausa no puede llevarse citas"

    await _plan(cuenta, "elite")  # sube de plan; nadie toca nada más
    assert [c["id"] for c in _leer(token, "clients", c2).json()] == [en_pausa["clienta"]]
    assert [c["id"] for c in _leer(token, "incomes", c2).json()] == [en_pausa["ingreso"]]
    yo = cliente.get("/auth/me", headers=_cab(c2)).json()
    assert yo["centro_activo"] == c2 and yo["viendo_centro_en_pausa"] is False


# ═══ 5. el fichaje ══════════════════════════════════════════════════════════

def test_en_un_centro_en_pausa_no_se_entra_pero_se_sale():
    empleada = f"(select id from public.employees where salon_id = '{SALON_SEED}' limit 1)"

    def fichar(tipo: str, pausar: bool) -> str:
        return sql(
            "begin;\n" + ENTRAR + (PAUSAR_EL_DEL_SEED if pausar else "") +
            f"select 'R|' || pg_temp.intentar($q$with x as (insert into public.time_entries (salon_id, employee_id, "
            f"tipo) select '{SALON_SEED}', {empleada}, '{tipo}' returning 1) select count(*) from x$q$);\n"
            "rollback;\n"
        )

    assert "error:42501:centro_en_pausa" in fichar("entrada", pausar=True)
    assert "R|ok:1" in fichar("salida", pausar=True), "quien estaba dentro cierra su jornada"
    assert "R|ok:1" in fichar("pausa", pausar=True)
    assert "R|ok:1" in fichar("entrada", pausar=False), "control: sin pausa se entra"


# ═══ 6. la regla 8: sin elección, el más antiguo ════════════════════════════

def _pausados(cuenta: str) -> list[str]:
    return [x for x in sql(f"select array_to_string(public.centros_en_pausa('{cuenta}'), ',');").split(",") if x]


async def _stripe_escribe(cuenta: str, plan: str) -> None:
    """Lo que hace el webhook cuando Stripe (o alguien desde su panel) cambia el plan."""
    await billing._escribir_suscripcion(_db(), cuenta, None, {"estado": "active", "plan": plan}, "active", plan)


async def test_si_el_plan_baja_sin_eleccion_se_queda_el_mas_antiguo(en_pausa):
    cuenta, uno, dos = en_pausa["account_id"], en_pausa["salon_id"], en_pausa["centro_2"]
    await _stripe_escribe(cuenta, "pro")
    assert _pausados(cuenta) == [dos]
    # Y el registro no se queda sin saber qué pasó: lo apunta el webhook con
    # los centros que de verdad quedaron en pausa (lo comprueba la unidad del
    # webhook; aquí, que la base y el backend dicen lo mismo).
    yo = en_pausa["cliente"].get("/salon/centros", headers=_cab()).json()
    assert yo["sigue_elegido"] is False and yo["activo"] == uno
    assert [c["id"] for c in yo["centros"] if c["en_pausa"]] == [dos]


async def test_subir_a_un_plan_que_cubre_todos_borra_la_eleccion_vieja(en_pausa):
    cuenta, uno, dos = en_pausa["account_id"], en_pausa["salon_id"], en_pausa["centro_2"]
    # Eligió el segundo en una bajada anterior…
    await _db().update("salons", {"id": f"eq.{dos}"}, {"sigue_al_bajar": True})
    await _stripe_escribe(cuenta, "pro")
    assert _pausados(cuenta) == [uno], "con marca, se queda el marcado"
    # …sube a Elite (la marca se va)…
    await _stripe_escribe(cuenta, "elite")
    assert not await _db().select("salons", {"select": "id", "account_id": f"eq.{cuenta}",
                                              "sigue_al_bajar": "is.true"})
    # …y vuelve a bajar SIN elegir: el más antiguo, no la elección de entonces.
    await _stripe_escribe(cuenta, "pro")
    assert _pausados(cuenta) == [dos]


# ═══ 7. una vez cada 30 días ════════════════════════════════════════════════

async def test_cambiar_el_centro_que_se_queda_una_vez_cada_30_dias(en_pausa):
    cliente, cuenta, uno, dos = en_pausa["cliente"], en_pausa["account_id"], en_pausa["salon_id"], en_pausa["centro_2"]

    # En Elite no hay nada que elegir.
    nada = cliente.put("/salon/centros/el-que-sigue", json={"centro_que_sigue": dos}, headers=_cab())
    assert nada.status_code == 409 and nada.json()["detail"]["codigo"] == "nada_que_elegir"

    await _plan(cuenta, "pro")
    # Confirmar el que ya se queda no gasta el cambio ni se apunta.
    confirma = cliente.put("/salon/centros/el-que-sigue", json={"centro_que_sigue": uno}, headers=_cab())
    assert confirma.status_code == 200 and confirma.json()["se_puede_cambiar_desde"] is None

    primero = cliente.put("/salon/centros/el-que-sigue", json={"centro_que_sigue": dos}, headers=_cab())
    assert primero.status_code == 200, primero.text
    hasta = datetime.fromisoformat(primero.json()["se_puede_cambiar_desde"])
    assert timedelta(days=29, hours=23) < hasta - datetime.now(timezone.utc) <= timedelta(days=30)
    assert _pausados(cuenta) == [uno]

    segundo = cliente.put("/salon/centros/el-que-sigue", json={"centro_que_sigue": uno}, headers=_cab())
    assert segundo.status_code == 409
    detalle = segundo.json()["detail"]
    assert detalle["codigo"] == "cambio_de_centro_reciente"
    assert "Podrás volver a cambiarlo el " in detalle["mensaje"]
    assert _pausados(cuenta) == [uno], "el segundo intento no movió nada"

    # El registro: una fila, con el centro que quedó en pausa, y no se puede tocar.
    filas = sql(f"select suceso || '|' || array_to_string(centros_en_pausa, ',') from public.decisiones_de_dinero "
                f"where account_id = '{cuenta}' and suceso = 'elige_centro';").splitlines()
    assert filas == [f"elige_centro|{uno}"]
    with pytest.raises(AssertionError, match="es un registro"):
        sql(f"update public.decisiones_de_dinero set motivo = 'x' where account_id = '{cuenta}';")

    lista = cliente.get("/salon/centros", headers=_cab()).json()
    assert lista["sigue_elegido"] is True and lista["cambio_de_centro_desde"]


def test_el_corte_no_le_quita_a_soporte_lo_que_veia():
    """Soporte ve todo, también lo que está en pausa (14-10-2026)."""
    import uuid

    sesion = str(uuid.uuid4())
    from tests.test_el_corte_vive_en_la_base_base_real import ADMIN_SEED, _como_la_titular

    preparar = PAUSAR_EL_DEL_SEED + f"""
      insert into public.admin_users (id, email, nombre, password_hash)
      values ('ad000000-0000-0000-0000-000000000002', 'soporte2@prueba.local', 'Soporte', 'x');
      insert into public.support_access_codes (id, salon_id, code_hash, expires_at)
      values ('c0de0000-0000-0000-0000-000000000002', '{SALON_SEED}', 'x', now() + interval '1 hour');
      insert into public.impersonation_sessions (admin_id, admin_email, salon_id, code_id, tenant_auth_user_id,
                                                 tenant_email, expires_at, auth_session_id)
      values ('ad000000-0000-0000-0000-000000000002', 'soporte2@prueba.local', '{SALON_SEED}',
              'c0de0000-0000-0000-0000-000000000002', '{ADMIN_SEED}', 'x', now() + interval '30 minutes', '{sesion}');
    """
    leer = {"fichas": f"select count(*) from public.clients where salon_id = '{SALON_SEED}'"}
    assert _como_la_titular(preparar, leer, session_id=sesion)["fichas"] != "ok:0"
    assert _como_la_titular(preparar, leer)["fichas"] == "ok:0", "la titular, sin soporte, no lee nada del pausado"


