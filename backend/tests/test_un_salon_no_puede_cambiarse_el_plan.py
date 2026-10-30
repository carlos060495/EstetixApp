"""UN SALÓN NO PUEDE PONERSE EN ELITE ÉL SOLO. Lo dice la BASE, no el código.

POR QUÉ ESTE FICHERO. `accounts` guarda el CIF y la marca —que el perfil edita—
y también `plan`, `pending_plan`, `stripe_customer_id` y la suspensión. Llevamos
semanas diciendo que `accounts.plan` lo escribe únicamente el webhook de Stripe,
pero eso lo defiende el código, y el código no es la frontera: la frontera es la
base de datos. Esto lo comprueba.

La 0040 dejó tres murallas y la 0050 no toca ninguna:

  1. EL GRANT ES POR COLUMNA. `authenticated` ni siquiera puede nombrar `plan`
     en un update: `grant update (cif, cif_pais, logo_url, accent_color)`.
  2. EL TRIGGER `accounts_plan_service_role_only`, que revienta si
     `current_user` es `authenticated` y el plan cambia.
  3. LA POLÍTICA `accounts_update_admin`, que acota la fila a la propia cuenta.

Y SUS CONTROLES, que son la mitad del trabajo. No basta con ver el rechazo: un
test que mandara una petición mal formada también vería un rechazo y no probaría
nada. Así que aquí:

  · se comprueba que la MISMA petición, cambiando `cif` en vez de `plan`, SÍ
    funciona — o sea que el token, la fila y la forma son correctos;
  · y se PROVOCA el agujero de verdad, ampliando el grant y quitando el trigger
    contra la base local, para ver que la comprobación se pone roja. Si no se
    pusiera, este fichero no valdría para nada.

Todo lo que se toca se deja como estaba, pase lo que pase.
"""

from __future__ import annotations

import subprocess
import uuid

import httpx
import pytest

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

TRIGGER = "accounts_plan_service_role_only"


@pytest.fixture()
def anyio_backend():
    return "asyncio"


# ── hablar con Postgres directamente, para poder PROVOCAR el agujero ───────

def _contenedor() -> str:
    """El contenedor de la base del stack local, sin escribir su nombre.

    Se descubre en vez de fijarlo porque depende del `project_id` y en otra
    máquina puede no llamarse igual. Si no aparece, esto FALLA: un control que se salta a sí
    mismo cuando no encuentra la base es un verde que no ha comprobado nada.
    """
    r = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}", "--filter", "name=supabase_db"],
        capture_output=True, text=True,
    )
    nombres = [n for n in r.stdout.split() if n.startswith("supabase_db")]
    assert nombres, f"no encuentro el contenedor de la base: {r.stdout!r} {r.stderr!r}"
    return nombres[0]


def sql(consulta: str) -> str:
    r = subprocess.run(
        ["docker", "exec", "-i", _contenedor(), "psql", "-U", "postgres", "-At",
         "-v", "ON_ERROR_STOP=1"],
        input=consulta, capture_output=True, text=True, encoding="utf-8",
    )
    assert r.returncode == 0, f"psql falló: {r.stderr.strip()}\n{consulta}"
    return r.stdout.strip()


# ── una cuenta de verdad, con su admin y su token ─────────────────────────

@pytest.fixture()
async def una_cuenta():
    db = PostgrestClient(SUPABASE_URL, SECRET_KEY, timeout=30.0)
    auth = SupabaseAuthAdminClient(SUPABASE_URL, SECRET_KEY)
    email = f"plan.{uuid.uuid4().hex[:8]}@estetixapp-e2e.com"

    cuenta = await db.insert("accounts", {"nombre": "Cuenta del plan"}, select="id,plan")
    salon = await db.insert(
        "salons", {"zona_horaria": "Europe/Madrid", "nombre_comercial": "Salon del plan", "account_id": cuenta["id"]}, select="id"
    )
    usuario = await auth.create_unconfirmed_user(email, uuid.uuid4().hex + "Aa1!")
    ficha = await db.insert(
        "users",
        {"salon_id": salon["id"], "account_id": cuenta["id"], "nombre": "Admin",
         "email": email, "rol": "admin", "auth_user_id": usuario["id"]},
        select="id",
    )
    # UNA CUENTA QUE PAGA (0086): sin suscripción viva la base no le deja tocar nada,
    # y lo que se prueba aquí es el plan, no el corte.
    await db.insert("subscriptions", {"account_id": cuenta["id"], "salon_id": salon["id"], "estado": "active",
                                      "plan": "basico", "intervalo": "mensual"})
    datos = {
        "account_id": cuenta["id"],
        "salon_id": salon["id"],
        "plan_de_partida": cuenta["plan"],
        "token": _token_de_inquilino(usuario["id"], salon["id"]),
    }
    try:
        yield datos
    finally:
        try:
            await db.delete("users", {"id": f"eq.{ficha['id']}"})
            await db.delete("subscriptions", {"account_id": f"eq.{cuenta['id']}"})
            await db.delete("salons", {"id": f"eq.{salon['id']}"})
            await db.delete("accounts", {"id": f"eq.{cuenta['id']}"})
            await auth.delete_user(usuario["id"])
        except Exception:  # noqa: BLE001 — el desmontaje no tumba el test
            pass


async def _intentar(cambio: dict, cuenta: dict) -> httpx.Response:
    """El PATCH que haría un salón con su sesión normal. Sin la app de por
    medio: lo que se mide es lo que contesta la BASE."""
    token = cuenta["token"]
    async with httpx.AsyncClient(timeout=30.0) as c:
        return await c.patch(
            f"{SUPABASE_URL}/rest/v1/accounts",
            params={"id": f"eq.{cuenta['account_id']}"},
            headers={"apikey": token, "Authorization": f"Bearer {token}",
                     "Content-Type": "application/json", "Prefer": "return=representation"},
            json=cambio,
        )


def _plan_guardado(account_id: str) -> str:
    return sql(f"select plan from public.accounts where id = '{account_id}';")


# ── EL GUARDA ─────────────────────────────────────────────────────────────

async def test_una_sesion_de_salon_NO_puede_cambiar_el_plan_de_su_cuenta(una_cuenta):
    respuesta = await _intentar({"plan": "elite"}, una_cuenta)

    assert respuesta.status_code >= 400, (
        f"LA BASE DEJÓ CAMBIAR EL PLAN: {respuesta.status_code} {respuesta.text}"
    )
    # Y lo que importa de verdad: que no haya cambiado.
    assert _plan_guardado(una_cuenta["account_id"]) == una_cuenta["plan_de_partida"]


async def test_tampoco_puede_tocar_la_suspension_ni_el_cliente_de_stripe(una_cuenta):
    """Las otras columnas que deciden dinero o estado, por el mismo camino."""
    for cambio in ({"suspendido": True}, {"stripe_customer_id": "cus_del_atacante"},
                   {"pending_plan": "elite"}):
        respuesta = await _intentar(cambio, una_cuenta)
        assert respuesta.status_code >= 400, f"pasó {cambio}: {respuesta.text}"


async def test_ni_el_plan_de_OTRA_cuenta(una_cuenta):
    """La política acota la fila: ni con el grant abierto llegaría a otra."""
    otra = sql(
        "insert into public.accounts (nombre) values ('Cuenta ajena') returning id;"
    ).splitlines()[0]
    try:
        token = una_cuenta["token"]
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.patch(
                f"{SUPABASE_URL}/rest/v1/accounts",
                params={"id": f"eq.{otra}"},
                headers={"apikey": token, "Authorization": f"Bearer {token}",
                         "Content-Type": "application/json", "Prefer": "return=representation"},
                json={"cif": "B12345674"},
            )
        # Con RLS, una fila que no se ve no se actualiza: 200 con cero filas.
        assert r.status_code >= 400 or r.json() == [], f"tocó la cuenta ajena: {r.text}"
        assert sql(f"select coalesce(cif,'-') from public.accounts where id = '{otra}';") == "-"
    finally:
        sql(f"delete from public.accounts where id = '{otra}';")


# ── LOS CONTROLES ─────────────────────────────────────────────────────────

async def test_EL_CONTROL_la_misma_peticion_cambiando_el_CIF_SI_funciona(una_cuenta):
    """Antivacío. Sin esto, una petición mal formada daría el mismo rechazo de
    arriba y este fichero estaría midiendo su propio error."""
    respuesta = await _intentar({"cif": "B12345674", "cif_pais": "ES"}, una_cuenta)

    assert respuesta.status_code == 200, f"{respuesta.status_code} {respuesta.text}"
    assert sql(
        f"select cif from public.accounts where id = '{una_cuenta['account_id']}';"
    ) == "B12345674"


async def test_EL_CONTROL_si_alguien_amplia_el_grant_SIGUE_bloqueado_por_el_trigger(una_cuenta):
    """Se provoca el primer agujero de verdad: `grant update (plan)`.

    Lo que tiene que pasar es que NO baste: ahí está el trigger para eso.
    """
    sql("grant update (plan) on public.accounts to authenticated;")
    try:
        respuesta = await _intentar({"plan": "elite"}, una_cuenta)

        assert respuesta.status_code >= 400, (
            "CON EL GRANT ABIERTO EL PLAN CAMBIÓ: el trigger no está haciendo su trabajo"
        )
        assert _plan_guardado(una_cuenta["account_id"]) == una_cuenta["plan_de_partida"]
    finally:
        sql("revoke update (plan) on public.accounts from authenticated;")


async def test_EL_CONTROL_con_el_grant_abierto_Y_SIN_trigger_el_plan_SI_cambia(una_cuenta):
    """El control que le da dientes a todo lo demás.

    Se quitan las DOS murallas y se comprueba que entonces el plan SÍ cambia. Si
    esto no pasara, el rechazo de los tests de arriba podría venir de cualquier
    otra cosa —un token malo, una fila que no existe— y no probaría nada.
    """
    sql("grant update (plan) on public.accounts to authenticated;")
    sql(f"alter table public.accounts disable trigger {TRIGGER};")
    try:
        respuesta = await _intentar({"plan": "elite"}, una_cuenta)

        assert respuesta.status_code == 200, f"{respuesta.status_code} {respuesta.text}"
        assert _plan_guardado(una_cuenta["account_id"]) == "elite", (
            "ni quitando el grant y el trigger cambia: la comprobación no mide lo que cree"
        )
    finally:
        sql(f"alter table public.accounts enable trigger {TRIGGER};")
        sql("revoke update (plan) on public.accounts from authenticated;")
        sql(
            f"update public.accounts set plan = '{una_cuenta['plan_de_partida']}' "
            f"where id = '{una_cuenta['account_id']}';"
        )


async def test_y_todo_queda_como_estaba(una_cuenta):
    """Que los controles de arriba no dejen el veneno servido.

    Corre después por orden alfabético... que no es una garantía, así que
    comprueba el estado ACTUAL en vez de confiar en el orden: el grant de `plan`
    no existe y el trigger está activo.
    """
    puede = sql(
        "select count(*) from information_schema.column_privileges "
        "where table_name = 'accounts' and grantee = 'authenticated' "
        "and column_name = 'plan' and privilege_type = 'UPDATE';"
    )
    assert puede == "0", "quedó un `grant update (plan)` suelto en la base local"

    activo = sql(
        f"select tgenabled from pg_trigger where tgname = '{TRIGGER}';"
    )
    assert activo == "O", f"el trigger {TRIGGER} quedó desactivado (tgenabled={activo!r})"

    # Y el permiso que SÍ se añade, que es de lo que va todo esto.
    tiene_cif_pais = sql(
        "select count(*) from information_schema.column_privileges "
        "where table_name = 'accounts' and grantee = 'authenticated' "
        "and column_name = 'cif_pais' and privilege_type = 'UPDATE';"
    )
    assert tiene_cif_pais == "1", "falta el grant de `cif_pais`: ¿se aplicó la 0050?"
