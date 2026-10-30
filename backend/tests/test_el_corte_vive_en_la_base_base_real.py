"""QUE NO SE PUEDA USAR SIN PAGAR: LA FRONTERA ES LA BASE (0086), contra la base de verdad.

EL AGUJERO, medido el 13-10-2026 con la sesión real de una titular pidiéndole
los datos a la base directamente (`/rest/v1/clients`, con la clave pública que
lleva la web): una cuenta SUSPENDIDA leía sus 11 clientas y escribía una nueva;
una CANCELADA y una RECIÉN CREADA SIN TARJETA, igual. El backend no decía nada
—la cancelada y la sin tarjeta ni siquiera las cortaba— y la base, todo.

Lo que se fija aquí:

  1. EL RECORRIDO DE LAS POLÍTICAS. Todas las de `public`, leídas del catálogo:
     las que escriben preguntan a `centros_que_trabajan()`; las que leen
     también, salvo la lectura de la agenda y las salidas, que lo DICEN en un
     comentario. Ninguna `ALL`. Toda tabla que la API puede tocar tiene RLS.
     Ninguna función que se salte la RLS y pueda llamar una sesión escribe. Y la
     ficha de la clienta no se lee por ningún camino. Con sus controles: cada
     regla, rota a propósito dentro de una transacción que se deshace, en rojo.
  2. EL ESPEJO. `cuenta_trabaja` de la base y la de Python dicen lo mismo,
     estado por estado.
  3. CADA CORTE DEL BACKEND TIENE SU CORTE EN LA BASE (`CORTES_EN_LA_BASE`).
  4. LAS DOS PUERTAS, CON PETICIONES DE VERDAD: suspendida, cancelada y sin
     tarjeta reciben 403 por el backend y por la base. La agenda se ve; la
     ficha no; el fichaje no; el periodo de gracia no corta; soporte sí entra;
     los recordatorios siguen saliendo; y al volver a pagar el reloj se para.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.billing.estado_suscripcion import cuenta_trabaja
from app.core.security import CODIGO_CORTADA, CODIGO_SUSPENDIDA, CORTES_EN_LA_BASE
from app.core.tenant_token_broker import acunar_token_tenant
from app.db.postgrest_client import PostgrestClient
from app.db.supabase_auth_client import SupabaseAuthAdminClient
from app.tareas import recordatorios
from tests.lo_que_deja_una_prueba import sql
from tests.test_alta_completa_e2e import SECRET_KEY, SUPABASE_URL
from tests.test_camino_humano_dos_centros import (  # noqa: F401  (fixtures)
    anyio_backend,
    dos_centros,
)

pytestmark = [pytest.mark.base_real, pytest.mark.anyio]

# Del seed: el salón A y su administradora.
SALON_SEED = "00000000-0000-0000-0000-000000000001"
ADMIN_SEED = "10000000-0000-0000-0000-000000000001"


# ═══ el lector del catálogo ═════════════════════════════════════════════════

# LAS REGLAS, en una consulta. Devuelve una línea por política (o tabla, o
# función) que las incumple. Se ejecuta tal cual y también con los controles.
INCUMPLIMIENTOS = r"""
with roles_api as (
  select oid from pg_roles where rolname in ('anon', 'authenticated')
),
pol as (
  select c.relname as tabla, p.polname as politica,
         case p.polcmd when 'r' then 'SELECT' when 'a' then 'INSERT' when 'w' then 'UPDATE'
                       when 'd' then 'DELETE' else 'ALL' end as cmd,
         coalesce(pg_get_expr(p.polqual, p.polrelid), '') || ' ' ||
         coalesce(pg_get_expr(p.polwithcheck, p.polrelid), '') as expr,
         coalesce(obj_description(p.oid, 'pg_policy'), '') as nota
  from pg_policy p
  join pg_class c on c.oid = p.polrelid
  join pg_namespace n on n.oid = c.relnamespace
  where n.nspname = 'public'
    and (0 = any (p.polroles) or exists (select 1 from roles_api r where r.oid = any (p.polroles)))
)
select 'POLITICA|' || tabla || '.' || politica || ' (' || cmd || '): ' || motivo
from (
  select tabla, politica, cmd, case
    when cmd = 'ALL' then 'una ALL también vale para leer: pártela en INSERT, UPDATE y DELETE'
    when cmd <> 'SELECT' and expr not like '%centros_que_trabajan%' and nota not like 'SALIDA (0086)%'
      then 'escribe sin preguntar a centros_que_trabajan()'
    when cmd = 'SELECT' and expr not like '%centros_que_trabajan%'
         and nota not like 'LECTURA DE LA AGENDA (0086)%' and nota not like 'SALIDA (0086)%'
      then 'lee sin preguntar a centros_que_trabajan() ni declararse agenda o salida'
    when tabla = 'clients' and (nota like 'LECTURA DE LA AGENDA%' or nota like 'SALIDA%')
      then 'la ficha de la clienta no es agenda ni salida'
  end as motivo
  from pol
) x where motivo is not null
union all
-- Toda tabla que la API puede tocar tiene RLS.
select 'TABLA|' || c.relname || ': la API la puede tocar y no tiene RLS'
from pg_class c join pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'public' and c.relkind = 'r' and not c.relrowsecurity
  and (has_table_privilege('authenticated', c.oid, 'select,insert,update,delete')
       or has_table_privilege('anon', c.oid, 'select,insert,update,delete'))
union all
-- Una función que se salta la RLS y puede llamar una sesión NO ESCRIBE.
select 'FUNCION|' || p.proname || ': se salta la RLS, la puede llamar una sesión y no es STABLE'
from pg_proc p join pg_namespace n on n.oid = p.pronamespace
where n.nspname = 'public' and p.prosecdef and p.prorettype <> 'trigger'::regtype
  and p.provolatile = 'v'
  and (has_function_privilege('authenticated', p.oid, 'execute') or has_function_privilege('anon', p.oid, 'execute'))
union all
-- Y si lee la tabla de clientas, no devuelve nada de su ficha.
select 'FICHA|' || p.proname || ': devuelve ' || col || ' de las clientas a una sesión'
from pg_proc p join pg_namespace n on n.oid = p.pronamespace,
     unnest(coalesce(p.proargnames, '{}')) col
where n.nspname = 'public' and p.prosecdef
  and p.prosrc ilike '%public.clients%'
  and has_function_privilege('authenticated', p.oid, 'execute')
  and col in ('telefono', 'email', 'notas', 'cumpleanos', 'telefono_digitos', 'recordatorios_token')
order by 1;
"""


def incumplimientos(antes: str = "") -> list[str]:
    """Lo que incumple las reglas. `antes` rompe algo dentro de una transacción
    que se deshace: así el control nunca deja nada."""
    salida = sql(f"begin;\n{antes}\n{INCUMPLIMIENTOS}\nrollback;\n")
    return [ln for ln in salida.splitlines() if "|" in ln and ln.split("|", 1)[0] in
            {"POLITICA", "TABLA", "FUNCION", "FICHA"}]


def test_todas_las_politicas_tablas_y_funciones_cumplen_el_corte():
    assert incumplimientos() == []


def test_el_recorrido_ve_las_politicas_que_se_sabe_que_existen():
    """El antivacío: sin esto, un catálogo leído mal pasaría siempre."""
    nombres = set(sql(
        "select tablename || '.' || policyname from pg_policies where schemaname = 'public';"
    ).splitlines())
    assert {"clients.clients_select", "clients.clients_insert", "appointments.appointments_update",
            "expenses.expenses_all_admin_select", "time_entries.time_entries_select"} <= nombres
    assert len(nombres) > 80


@pytest.mark.parametrize(
    "rotura,esperado",
    [
        pytest.param(
            "create table public.zz_tabla_nueva (salon_id uuid);"
            "alter table public.zz_tabla_nueva enable row level security;"
            "create policy zz_escribe on public.zz_tabla_nueva for insert "
            "with check (salon_id = any (public.salones_visibles()));",
            "POLITICA|zz_tabla_nueva.zz_escribe (INSERT): escribe sin preguntar a centros_que_trabajan()",
            id="una tabla nueva que escribe con salones_visibles",
        ),
        pytest.param(
            "create table public.zz_tabla_nueva (salon_id uuid);"
            "alter table public.zz_tabla_nueva enable row level security;"
            "create policy zz_lee on public.zz_tabla_nueva for select "
            "using (salon_id = any (public.salones_visibles()));",
            "POLITICA|zz_tabla_nueva.zz_lee (SELECT): lee sin preguntar a centros_que_trabajan() "
            "ni declararse agenda o salida",
            id="una tabla nueva que lee sin decir por qué",
        ),
        pytest.param(
            "create policy zz_todo on public.clients for all using (true);",
            "POLITICA|clients.zz_todo (ALL): una ALL también vale para leer: pártela en INSERT, UPDATE y DELETE",
            id="una ALL",
        ),
        pytest.param(
            "alter policy clients_select on public.clients using (salon_id = any (public.salones_visibles()));"
            "comment on policy clients_select on public.clients is 'LECTURA DE LA AGENDA (0086): x';",
            "POLITICA|clients.clients_select (SELECT): la ficha de la clienta no es agenda ni salida",
            id="la ficha disfrazada de agenda",
        ),
        pytest.param(
            "create table public.zz_sin_rls (x int); grant select on public.zz_sin_rls to authenticated;",
            "TABLA|zz_sin_rls: la API la puede tocar y no tiene RLS",
            id="una tabla sin RLS",
        ),
        pytest.param(
            "create function public.zz_escribe() returns void language sql security definer "
            "as 'select 1'; grant execute on function public.zz_escribe() to authenticated;",
            "FUNCION|zz_escribe: se salta la RLS, la puede llamar una sesión y no es STABLE",
            id="una función que se salta la RLS y escribe",
        ),
        pytest.param(
            "create function public.zz_fichas() returns table (id uuid, telefono text) language sql stable "
            "security definer as 'select id, telefono from public.clients';"
            "grant execute on function public.zz_fichas() to authenticated;",
            "FICHA|zz_fichas: devuelve telefono de las clientas a una sesión",
            id="una función que devuelve el teléfono",
        ),
    ],
)
def test_CONTROL_cada_regla_rota_a_proposito_se_pone_en_rojo(rotura, esperado):
    assert esperado in incumplimientos(rotura)


# ═══ las pruebas como una sesión de verdad, en SQL ═══════════════════════════

# Se entra como la administradora del seed, como la entraría PostgREST.
ENTRAR = """
create or replace function pg_temp.intentar(q text) returns text language plpgsql as $f$
declare n bigint;
begin
  execute q into n;
  return 'ok:' || coalesce(n, 0);
exception when others then
  return 'error:' || sqlstate || ':' || sqlerrm;
end $f$;
"""


def _como_la_titular(preparar: str, consultas: dict[str, str], *, session_id: str | None = None,
                     sub: str = ADMIN_SEED, salon: str = SALON_SEED) -> dict[str, str]:
    """`preparar` corre como postgres; cada consulta, como la titular (la del
    seed si no se dice otra). Todo dentro de una transacción que se deshace."""
    claims = {"sub": sub, "salon_id": salon, "rol": "admin", "role": "authenticated"}
    if session_id:
        claims["session_id"] = session_id
    import json

    lineas = "\n".join(
        f"select 'R|{nombre}|' || pg_temp.intentar($q${q}$q$);" for nombre, q in consultas.items()
    )
    salida = sql(
        "begin;\n" + ENTRAR + preparar + "\n"
        f"select set_config('request.jwt.claims', '{json.dumps(claims)}', true);\n"
        "set local role authenticated;\n" + lineas + "\nrollback;\n"
    )
    return {ln.split("|")[1]: ln.split("|", 2)[2] for ln in salida.splitlines() if ln.startswith("R|")}


DEMO_TITULAR = "de000000-0000-0000-0000-0000000000d1"
DEMO_SALON = "de000000-0000-0000-0000-0000000000d2"
DEMO_CADUCADA = f"""
insert into auth.users (id, instance_id, aud, role, email)
select '{DEMO_TITULAR}', instance_id, aud, role, 'demo.corte@prueba.local' from auth.users limit 1;
insert into public.accounts (id, nombre, es_demo) values ('de000000-0000-0000-0000-0000000000d3', 'Demo', true);
insert into public.salons (id, nombre_comercial, account_id)
values ('{DEMO_SALON}', 'Demo', 'de000000-0000-0000-0000-0000000000d3');
insert into public.users (salon_id, account_id, nombre, email, rol, auth_user_id)
values ('{DEMO_SALON}', 'de000000-0000-0000-0000-0000000000d3', 'Demo', 'demo.corte@prueba.local', 'admin', '{DEMO_TITULAR}');
insert into public.demos (account_id, navegador_hash, creada_at, caduca_at)
values ('de000000-0000-0000-0000-0000000000d3', encode(sha256('corte'::bytea), 'hex'), now() - interval '2 days',
        now() - interval '1 hour');
"""

PAUSAR_EL_DEL_SEED = f"""
insert into public.salons (id, nombre_comercial, account_id, created_at)
select '5a1e0000-0000-0000-0000-0000000000a1', 'Más antiguo', s.account_id, '2000-01-01'
from public.salons s where s.id = '{SALON_SEED}';
"""

CANCELAR = f"""
update public.subscriptions set estado = 'canceled'
 where account_id = (select account_id from public.salons where id = '{SALON_SEED}');
"""


def test_el_espejo_python_y_la_base_dicen_lo_mismo_estado_por_estado():
    casos = []
    for estados in ([], ["active"], ["trialing"], ["past_due"], ["canceled"], ["unpaid"], ["incomplete"],
                    ["incomplete_expired"], ["paused"]):
        for suspendida in (False, True):
            casos.append((estados, suspendida, False, False))
    for vigente in (True, False):
        casos.append(([], False, True, vigente))

    cuerpo = ["begin;"]
    # Una suscripción por cuenta (la base no admite dos), y `es_demo` se decide
    # al crear la cuenta: no se puede cambiar después.
    for i, (estados, suspendida, es_demo, vigente) in enumerate(casos):
        salon = f"7e570000-0000-0000-0000-{i:012d}"
        cuenta_id = f"7e57cccc-0000-0000-0000-{i:012d}"
        cuerpo.append(f"insert into public.accounts (id, nombre, es_demo, suspendido) values "
                      f"('{cuenta_id}', 'Espejo {i}', {str(es_demo).lower()}, {str(suspendida).lower()});")
        cuerpo.append(f"insert into public.salons (id, nombre_comercial, account_id) "
                      f"values ('{salon}', 'Espejo {i}', '{cuenta_id}');")
        cuenta = f"'{cuenta_id}'::uuid"
        for e in estados:
            cuerpo.append(f"insert into public.subscriptions (account_id, salon_id, estado) "
                          f"values ({cuenta}, '{salon}', '{e}');")
        if es_demo:
            caduca = "now() + interval '1 day'" if vigente else "now() - interval '1 day'"
            cuerpo.append(f"insert into public.demos (account_id, navegador_hash, creada_at, caduca_at) "
                          f"select {cuenta}, encode(sha256('espejo{i}'::bytea), 'hex'), "
                          f"now() - interval '2 days', {caduca};")
        cuerpo.append(f"select 'R|{i}|' || public.cuenta_trabaja({cuenta});")
    cuerpo.append("rollback;")
    salida = sql("\n".join(cuerpo))
    base = {int(ln.split("|")[1]): ln.split("|")[2] == "true" for ln in salida.splitlines() if ln.startswith("R|")}

    assert len(base) == len(casos)
    distintos = [
        (casos[i], base[i]) for i in range(len(casos))
        if cuenta_trabaja(casos[i][0], suspendida=casos[i][1], es_demo=casos[i][2], demo_vigente=casos[i][3])
        != base[i]
    ]
    assert distintos == [], f"La base y Python no dicen lo mismo: {distintos}"


def test_cada_corte_del_backend_tiene_su_funcion_en_la_base_y_corta():
    funciones = set(sql("select proname from pg_proc p join pg_namespace n on n.oid = p.pronamespace "
                        "where n.nspname = 'public';").splitlines())
    for codigo, funcion in CORTES_EN_LA_BASE.items():
        if funcion is not None:
            assert funcion in funciones, f"«{codigo}» dice que lo corta {funcion}(), y no existe"

    leer = {"fichas": "select count(*) from public.clients"}
    estados = {
        "cuenta_suspendida": f"update public.accounts set suspendido = true where id = "
                             f"(select account_id from public.salons where id = '{SALON_SEED}');",
        "cuenta_cortada": CANCELAR,
        "demo_terminada": DEMO_CADUCADA,
        "sesion_revocada": f"update public.users set acceso_retirado_el = now() where auth_user_id = '{ADMIN_SEED}';",
        # Un centro MÁS ANTIGUO en la cuenta del seed (Pro, un centro): el del
        # seed queda en pausa (0088), y con él todas sus fichas.
        "centro_en_pausa": PAUSAR_EL_DEL_SEED,
        "solo_lectura_centro_en_pausa": PAUSAR_EL_DEL_SEED,
    }
    assert set(estados) == {c for c, f in CORTES_EN_LA_BASE.items() if f is not None}
    for codigo, preparar in estados.items():
        quien = {"sub": DEMO_TITULAR, "salon": DEMO_SALON} if codigo == "demo_terminada" else {}
        r = _como_la_titular(preparar, leer, **quien)["fichas"]
        if codigo in ("sesion_revocada", "centro_en_pausa", "solo_lectura_centro_en_pausa"):
            # Sin su fila no ve ningún centro; con el centro en pausa, ninguna
            # fila de él (0088: leer da vacío, escribir da 403). Cero filas.
            assert r == "ok:0", (codigo, r)
        else:
            assert r.startswith("error:42501:cuenta_cortada"), (codigo, r)
    # EL CONTROL: con la cuenta al día, las mismas fichas se leen; y la demo
    # SIN caducar, también.
    assert _como_la_titular("", leer)["fichas"].startswith("ok:")
    vigente = DEMO_CADUCADA.replace("now() - interval '1 hour'", "now() + interval '1 hour'")
    assert _como_la_titular(vigente, leer, sub=DEMO_TITULAR, salon=DEMO_SALON)["fichas"].startswith("ok:")


# UNA TABLA VACÍA NO PRUEBA NADA: con 0 filas Postgres no llega a evaluar la
# política y contesta `ok:0`, cortada o no. La caja y el stock del seed están
# vacíos, así que se siembra una fila de cada uno (dentro de la transacción
# que se deshace: no queda nada). Lo mismo que /employees el 12-10.
SEMBRAR_CAJA_Y_STOCK = f"""
insert into public.incomes (salon_id, concepto, importe) values ('{SALON_SEED}', 'Prueba del corte', 10);
insert into public.products (salon_id, nombre) values ('{SALON_SEED}', 'Prueba del corte');
"""

CERRADO_A_LA_CORTADA = {
    "la_ficha": "select count(*) from public.clients",
    "la_ficha_colgada_de_la_cita": "select count(*) from public.appointments a join public.clients c "
                                   "on c.id = a.client_id",
    "escribir_una_cita": "with x as (update public.appointments set notas = notas returning 1) "
                         "select count(*) from x",
    "la_caja": "select count(*) from public.incomes",
    "el_stock": "select count(*) from public.products",
}


def test_CONTROL_con_la_cuenta_al_dia_cada_cerrojo_tiene_filas_que_cerrar():
    """El antivacío del de abajo: al día, cada consulta que la cortada no puede
    hacer devuelve filas. Si alguna saliera `ok:0`, su 42501 de abajo no
    probaría nada (Postgres no evalúa la política sin filas)."""
    r = _como_la_titular(SEMBRAR_CAJA_Y_STOCK, CERRADO_A_LA_CORTADA)
    for nombre, valor in r.items():
        assert valor.startswith("ok:") and int(valor.split(":")[1]) > 0, (nombre, valor)


def test_cortada_ve_su_agenda_entera_y_nada_de_la_ficha():
    r = _como_la_titular(SEMBRAR_CAJA_Y_STOCK + CANCELAR, {
        "citas": "select count(*) from public.appointments",
        "fichas_de_empleada": "select count(*) from public.employees",
        "servicios": "select count(*) from public.services",
        "horario": "select count(*) from public.salon_schedule",
        "lo_que_protege": "select count(*) from public.clientas_de_la_agenda("
                          "array(select client_id from public.appointments where client_id is not null))",
        **CERRADO_A_LA_CORTADA,
    })
    for leer in ("citas", "fichas_de_empleada", "servicios", "horario", "lo_que_protege"):
        assert r[leer].startswith("ok:"), (leer, r[leer])
    assert int(r["citas"].split(":")[1]) > 0, "el seed tiene citas: sin ellas esto no prueba nada"
    for cerrado in CERRADO_A_LA_CORTADA:
        assert r[cerrado].startswith("error:42501:cuenta_cortada"), (cerrado, r[cerrado])


def test_lo_que_protege_a_la_clienta_es_su_nombre_y_sus_alergias_y_nada_mas():
    columnas = sql("select array_to_string(proargnames, ',') from pg_proc where proname = 'clientas_de_la_agenda';")
    assert columnas.split(",")[1:] == ["id", "nombre", "alergias"]


def test_el_periodo_de_gracia_no_corta():
    r = _como_la_titular(CANCELAR.replace("'canceled'", "'past_due'"),
                         {"fichas": "select count(*) from public.clients"})
    assert r["fichas"].startswith("ok:")


def test_soporte_si_entra_en_una_cuenta_cortada_y_nadie_mas_con_su_misma_identidad():
    sesion = str(uuid.uuid4())
    preparar = CANCELAR + f"""
      insert into public.admin_users (id, email, nombre, password_hash)
      values ('ad000000-0000-0000-0000-000000000001', 'soporte@prueba.local', 'Soporte', 'x');
      insert into public.support_access_codes (id, salon_id, code_hash, expires_at)
      values ('c0de0000-0000-0000-0000-000000000001', '{SALON_SEED}', 'x', now() + interval '1 hour');
      insert into public.impersonation_sessions (admin_id, admin_email, salon_id, code_id, tenant_auth_user_id,
                                                 tenant_email, expires_at, auth_session_id)
      values ('ad000000-0000-0000-0000-000000000001', 'soporte@prueba.local', '{SALON_SEED}',
              'c0de0000-0000-0000-0000-000000000001', '{ADMIN_SEED}', 'x', now() + interval '30 minutes', '{sesion}');
    """
    leer = {"fichas": "select count(*) from public.clients"}
    assert _como_la_titular(preparar, leer, session_id=sesion)["fichas"].startswith("ok:")
    # Su misma identidad con OTRA sesión —la de la titular— sigue cortada.
    assert _como_la_titular(preparar, leer, session_id=str(uuid.uuid4()))["fichas"].startswith("error:42501")


def test_con_la_cuenta_cortada_no_se_ficha_por_ningun_camino():
    """El fichaje lo escribe service_role: lo corta el trigger, no la RLS."""
    empleada = f"(select id from public.employees where salon_id = '{SALON_SEED}' limit 1)"
    salida = sql(
        "begin;\n" + ENTRAR + CANCELAR +
        f"select 'R|' || pg_temp.intentar($q$with x as (insert into public.time_entries (salon_id, employee_id, tipo) "
        f"select '{SALON_SEED}', {empleada}, 'entrada' returning 1) select count(*) from x$q$);\n"
        "rollback;\n"
    )
    assert "error:42501:cuenta_cortada" in salida
    control = sql(
        "begin;\n" + ENTRAR +
        f"select 'R|' || pg_temp.intentar($q$with x as (insert into public.time_entries (salon_id, employee_id, tipo) "
        f"select '{SALON_SEED}', {empleada}, 'entrada' returning 1) select count(*) from x$q$);\n"
        "rollback;\n"
    )
    assert "R|ok:1" in control


def test_el_reloj_empieza_el_dia_del_primer_impago_y_se_para_al_volver():
    cuenta = f"(select account_id from public.salons where id = '{SALON_SEED}')"
    hace_diez = "now() - interval '10 days'"
    salida = sql(f"""
begin;
update public.subscriptions set estado = 'past_due', primer_impago_at = {hace_diez} where account_id = {cuenta};
select 'R|impago|' || origen || '|' || (terminada_at::date = ({hace_diez})::date)
  from public.relojes_del_plazo(now(), {cuenta});
update public.subscriptions set estado = 'canceled', terminada_at = now() where account_id = {cuenta};
select 'R|cancelada_tras_impago|' || origen || '|' || (terminada_at::date = ({hace_diez})::date)
  from public.relojes_del_plazo(now(), {cuenta});
-- VUELVE A PAGAR, aunque el webhook no hubiera borrado las fechas: no hay reloj.
update public.subscriptions set estado = 'active' where account_id = {cuenta};
select 'R|vuelve|' || count(*) from public.relojes_del_plazo(now(), {cuenta});
rollback;
""")
    r = {ln.split("|")[1]: ln.split("|", 2)[2] for ln in salida.splitlines() if ln.startswith("R|")}
    assert r == {"impago": "impago|true", "cancelada_tras_impago": "impago|true", "vuelve": "0"}


# ═══ las dos puertas, con peticiones de verdad ══════════════════════════════

ADM = {"apikey": SECRET_KEY, "Authorization": f"Bearer {SECRET_KEY}"}


def _db() -> PostgrestClient:
    return PostgrestClient(SUPABASE_URL, SECRET_KEY, timeout=30.0)


def _directo(token: str) -> dict:
    # La clave que lleva la web es pública; la base decide con el token.
    return {"apikey": token, "Authorization": f"Bearer {token}"}


@pytest.fixture()
async def titular(dos_centros):
    """La cuenta de `dos_centros` (paga), con una clienta con correo, una cita
    mañana, y la sesión REAL de su titular."""
    db = _db()
    clienta = await db.insert("clients", {"salon_id": dos_centros["salon_id"], "nombre": "Clienta Del Corte",
                                          "telefono": "+34600111222", "email": "clienta.corte@prueba.local",
                                          "alergias": "Látex"}, select="id")
    # CON DATOS EN CADA COSA QUE LA AGENDA PINTA: una tabla vacía no tiene filas
    # que la base pueda cortar, y un guarda sobre tablas vacías pasa en vacío
    # (le pasó a este con el puesto de las fichas).
    puesto = await db.insert("puestos", {"salon_id": dos_centros["salon_id"], "nombre": "Estilista"}, select="id")
    empleada = await db.insert("employees", {"salon_id": dos_centros["salon_id"], "nombre": "Ficha Corte",
                                             "puesto_id": puesto["id"]}, select="id")
    manana = datetime.now(timezone.utc) + timedelta(hours=20)
    cita = await db.insert("appointments", {
        "salon_id": dos_centros["salon_id"], "client_id": clienta["id"], "employee_id": empleada["id"],
        "inicio": manana.isoformat(), "fin": (manana + timedelta(minutes=30)).isoformat(), "estado": "confirmada",
    }, select="id")
    email = (await db.select("users", {"select": "email", "id": f"eq.{dos_centros['id']}"}))[0]["email"]
    token = await acunar_token_tenant(SupabaseAuthAdminClient(SUPABASE_URL, SECRET_KEY), email,
                                      auth_user_id=dos_centros["auth_user_id"])
    yield {**dos_centros, "token": token, "clienta": clienta["id"], "cita": cita["id"]}
    await db.delete("appointments", {"id": f"eq.{cita['id']}"})
    await db.delete("employees", {"id": f"eq.{empleada['id']}"})
    await db.delete("puestos", {"id": f"eq.{puesto['id']}"})
    await db.delete("clients", {"salon_id": f"in.({dos_centros['salon_id']},{dos_centros['centro_2']})"})


async def _poner(cuenta: str, caso: str) -> None:
    db = _db()
    await db.update("accounts", {"id": f"eq.{cuenta}"}, {"suspendido": caso == "suspendida"})
    if caso == "sin_tarjeta":
        await db.delete("subscriptions", {"account_id": f"eq.{cuenta}"})
        return
    estado = {"suspendida": "active", "cancelada": "canceled", "al_dia": "active", "gracia": "past_due"}[caso]
    if await db.select("subscriptions", {"select": "id", "account_id": f"eq.{cuenta}"}):
        await db.update("subscriptions", {"account_id": f"eq.{cuenta}"}, {"estado": estado})
    else:
        centro = (await db.select("salons", {"select": "id", "account_id": f"eq.{cuenta}",
                                             "order": "created_at.asc", "limit": "1"}))[0]["id"]
        await db.insert("subscriptions", {"account_id": cuenta, "salon_id": centro, "estado": estado,
                                          "plan": "elite", "intervalo": "mensual"})


@pytest.mark.parametrize("caso,codigo", [
    ("suspendida", CODIGO_SUSPENDIDA),
    ("cancelada", CODIGO_CORTADA),
    ("sin_tarjeta", CODIGO_CORTADA),
])
async def test_EL_AGUJERO_cerrado_por_las_dos_puertas(titular, caso, codigo):
    """La prueba que importa: lo mismo que se midió el 13-10-2026, ahora 403 por
    el backend y por la base, al leer y al escribir."""
    cliente, token, cuenta = titular["cliente"], titular["token"], titular["account_id"]
    try:
        await _poner(cuenta, caso)

        backend = cliente.get("/clients", headers={"Authorization": "Bearer e2e"})
        assert backend.status_code == 403, backend.text
        assert backend.json()["detail"]["codigo"] == codigo

        leer = httpx.get(f"{SUPABASE_URL}/rest/v1/clients", params={"select": "nombre,telefono"},
                         headers=_directo(token))
        assert leer.status_code == 403, leer.text
        escribir = httpx.post(f"{SUPABASE_URL}/rest/v1/clients", headers=_directo(token),
                              json={"salon_id": titular["salon_id"], "nombre": "Escrita por la vía directa"})
        assert escribir.status_code == 403, escribir.text
        assert not await _db().select("clients", {"select": "id", "nombre": "eq.Escrita por la vía directa"})
    finally:
        await _poner(cuenta, "al_dia")

    # EL CONTROL: al día, las dos puertas abren.
    assert cliente.get("/clients", headers={"Authorization": "Bearer e2e"}).status_code == 200
    assert httpx.get(f"{SUPABASE_URL}/rest/v1/clients", params={"select": "id"},
                     headers=_directo(token)).status_code == 200


async def test_cortada_ve_la_agenda_por_las_dos_puertas_con_nombre_y_alergias(titular):
    cliente, token, cuenta = titular["cliente"], titular["token"], titular["account_id"]
    desde = datetime.now(timezone.utc).isoformat()
    hasta = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    try:
        await _poner(cuenta, "cancelada")
        r = cliente.get("/appointments", params={"desde": desde, "hasta": hasta},
                        headers={"Authorization": "Bearer e2e"})
        assert r.status_code == 200, r.text
        cita = next(c for c in r.json() if c["id"] == titular["cita"])
        assert cita["clients"] == {"nombre": "Clienta Del Corte", "alergias": "Látex"}
        assert "+34600111222" not in r.text and "clienta.corte@prueba.local" not in r.text

        directo = httpx.get(f"{SUPABASE_URL}/rest/v1/appointments", params={"select": "id"},
                            headers=_directo(token))
        assert directo.status_code == 200 and directo.json()
        # La ficha colgada de la cita, por la vía directa: 403.
        con_ficha = httpx.get(f"{SUPABASE_URL}/rest/v1/appointments",
                              params={"select": "id,clients(telefono,email)"}, headers=_directo(token))
        assert con_ficha.status_code == 403, con_ficha.text
        # Y nada de escribir en la agenda.
        mover = cliente.patch(f"/appointments/{titular['cita']}", json={"notas": "movida"},
                              headers={"Authorization": "Bearer e2e"})
        assert mover.status_code == 403
    finally:
        await _poner(cuenta, "al_dia")


async def test_los_recordatorios_de_lo_ya_reservado_siguen_saliendo(titular):
    """La clienta no tiene nada que ver con la factura del salón."""
    servicio = _db()
    salon = {"id": titular["salon_id"], "recordatorio_horas": 24}
    try:
        await _poner(titular["account_id"], "cancelada")
        citas = await recordatorios._citas_a_avisar(servicio, salon, datetime.now(timezone.utc))
        cita = next(c for c in citas if c["id"] == titular["cita"])
        assert cita["clients"]["email"] == "clienta.corte@prueba.local"
        assert await recordatorios._reclamar(servicio, titular["salon_id"], titular["cita"]) is True
    finally:
        await _poner(titular["account_id"], "al_dia")


async def test_el_periodo_de_gracia_no_corta_por_ninguna_puerta(titular):
    try:
        await _poner(titular["account_id"], "gracia")
        assert titular["cliente"].get("/clients", headers={"Authorization": "Bearer e2e"}).status_code == 200
        assert httpx.get(f"{SUPABASE_URL}/rest/v1/clients", params={"select": "id"},
                         headers=_directo(titular["token"])).status_code == 200
    finally:
        await _poner(titular["account_id"], "al_dia")


async def test_cortada_se_lleva_sus_datos_enteros(titular):
    """Descargar sus datos es una de las salidas: lee con la clave de servicio,
    acotado a su cuenta, y trae su ficha entera aunque esté cortada."""
    from app.routers import datos

    servicio = _db()
    try:
        await _poner(titular["account_id"], "cancelada")
        d = await datos.leer_datos(servicio, titular["account_id"], servicio)
        assert any(c.get("telefono") == "+34600111222" for c in d.clientas)
        assert {c["id"] for c in d.centros} == {titular["salon_id"], titular["centro_2"]}
    finally:
        await _poner(titular["account_id"], "al_dia")


def _parametros(ruta) -> dict | None:
    """Los parámetros obligatorios de una ruta, rellenados por su nombre. `None`
    si hay uno que el guarda no sabe rellenar: entonces se pone rojo y lo dice."""
    hoy = datetime.now(timezone.utc)
    conocidos = {"desde": hoy.isoformat(), "hasta": (hoy + timedelta(days=1)).isoformat(),
                 "fecha": hoy.date().isoformat()}
    fuera = {}
    for p in ruta.dependant.query_params:
        if p.field_info.is_required():
            if p.name not in conocidos:
                return None
            fuera[p.name] = conocidos[p.name]
    return fuera


async def test_TODAS_las_rutas_de_la_agenda_contestan_a_una_cuenta_cortada(titular):
    """Recorriendo las marcadas «agenda», sin lista: si una lee algo que la base
    corta (lo que pasó con el puesto de las fichas), aquí sale un 403 o un 500."""
    from app.core.impersonation_policy import iterar_rutas
    from app.core.security import motivo_para_cortada
    from app.main import app as la_app
    from fastapi.routing import APIRoute

    rutas = [r for r in iterar_rutas(la_app.routes)
             if isinstance(r, APIRoute) and motivo_para_cortada(r) == "agenda"]
    assert len(rutas) >= 10, "el recorrido no encuentra las rutas de la agenda"
    malas = []
    try:
        await _poner(titular["account_id"], "cancelada")
        for r in rutas:
            params = _parametros(r)
            if params is None:
                malas.append(f"GET {r.path}: tiene un parámetro obligatorio que el guarda no sabe rellenar")
                continue
            resp = titular["cliente"].get(r.path, params=params, headers={"Authorization": "Bearer e2e"})
            if resp.status_code != 200:
                malas.append(f"GET {r.path}: {resp.status_code} {resp.text[:120]}")
    finally:
        await _poner(titular["account_id"], "al_dia")
    assert malas == []
