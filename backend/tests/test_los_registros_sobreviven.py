"""LOS REGISTROS SOBREVIVEN A LO QUE NOMBRAN (0057), contra la base de verdad.

Un registro de auditoría no puede depender de que siga existiendo lo que
describe. Aquí se fija:

  1. EL GUARDA. Descubre los registros por su trigger —toda tabla con un trigger
     cuya función se llama `*_no_se_tocan` es un registro que no se puede tocar—
     y se pone ROJO si alguno tiene una clave ajena. El control: una tabla nueva
     con ese trigger y una clave ajena, creada dentro de una transacción que se
     deshace, la descubre y la señala.
  2. EL BORRADO DE VERDAD. Una cuenta con dos centros, una empleada y filas en
     los DOS registros (dinero y descargas) escritas con el código de la app.
     Se borra la empleada, luego un centro, y luego la cuenta entera con su
     administradora. Las filas siguen ahí y dicen quién fue, en qué centro y de
     qué cuenta.
  3. EL CONTROL DEL BORRADO: con la clave ajena de la 0054 puesta otra vez,
     borrar a la administradora FALLA. Sin él, el punto 2 pasaría igual de
     verde si la base no hubiera cambiado.
"""

from __future__ import annotations

import uuid

import pytest

from app.billing.rastro import apuntar_decision
from app.core.security import CurrentUser
from app.db.postgrest_client import PostgrestClient
from app.db.supabase_auth_client import SupabaseAuthAdminClient
from app.routers.datos import apuntar_descarga
from app.routers.employees import apuntar_correcciones
from tests.test_alta_completa_e2e import SECRET_KEY, SUPABASE_URL
from tests.test_camino_humano_dos_centros import (  # noqa: F401  (fixtures)
    anyio_backend,
    dos_centros,
)
from tests.test_un_salon_no_puede_cambiarse_el_plan import sql

pytestmark = [pytest.mark.base_real, pytest.mark.anyio]

REGISTROS = {"correcciones_de_fichaje", "decisiones_de_dinero", "descargas_de_datos"}

# Las tablas que son registros: las que tienen un trigger cuya función acaba en
# `_no_se_tocan`. Y cada clave ajena que salga de ellas.
_DESCUBRIR = """
with registros as (
  select distinct c.oid, c.relname
  from pg_trigger t
  join pg_class c on c.oid = t.tgrelid
  join pg_namespace n on n.oid = c.relnamespace
  join pg_proc p on p.oid = t.tgfoid
  where n.nspname = 'public' and not t.tgisinternal and p.proname like '%\\_no\\_se\\_tocan'
)
select r.relname || '|' || coalesce(string_agg(k.conname || '->' || k.confrelid::regclass::text, ','
                                   order by k.conname), '')
from registros r
left join pg_constraint k on k.conrelid = r.oid and k.contype = 'f'
group by r.relname order by r.relname;
"""


def _registros() -> dict[str, list[str]]:
    out = {}
    for linea in sql(_DESCUBRIR).splitlines():
        tabla, claves = linea.split("|", 1)
        out[tabla] = [c for c in claves.split(",") if c]
    return out


# ═══ 1. El guarda ═════════════════════════════════════════════════════════


async def test_ningun_registro_tiene_claves_ajenas():
    registros = _registros()
    # Antivacío: si no viera los dos que existen, el verde no valdría nada.
    assert REGISTROS <= set(registros), registros
    assert {t: k for t, k in registros.items() if k} == {}


async def test_EL_CONTROL_un_registro_nuevo_atado_con_una_clave_ajena_se_detecta():
    salida = sql(f"""
begin;
create table public.zz_registro_de_control (id bigint primary key, usuario_id uuid references public.users (id));
create function public.zz_registro_de_control_no_se_tocan() returns trigger language plpgsql as $$
begin raise exception 'no'; end; $$;
create trigger zz_sin_update before update or delete on public.zz_registro_de_control
  for each row execute function public.zz_registro_de_control_no_se_tocan();
{_DESCUBRIR}
rollback;
""")
    assert "zz_registro_de_control|zz_registro_de_control_usuario_id_fkey->users" in salida, salida
    assert "zz_registro_de_control" not in sql("select to_regclass('public.zz_registro_de_control');")


# ═══ 2 y 3. El borrado de verdad ══════════════════════════════════════════


@pytest.fixture()
async def con_registros(dos_centros):  # noqa: F811  (la fixture importada)
    db = PostgrestClient(SUPABASE_URL, SECRET_KEY, timeout=30.0)
    cuenta, centro_2 = dos_centros["account_id"], dos_centros["centro_2"]
    admin = (await db.select("users", {"select": "id,nombre,email", "auth_user_id": f"eq.{dos_centros['auth_user_id']}"}))[0]
    empleada = await db.insert("employees", {"salon_id": centro_2, "nombre": "Begoña la del Norte"}, select="id")
    centro_2_nombre = (await db.select("salons", {"select": "nombre_comercial", "id": f"eq.{centro_2}"}))[0]["nombre_comercial"]

    # Una fila en cada registro, con el código de la app, nombrando a la
    # administradora, al centro 2 y a la cuenta.
    await apuntar_decision(db, account_id=cuenta, salon_id=centro_2, suceso="baja", origen="app",
                           usuario_id=admin["id"], motivo="prueba de borrado")
    await apuntar_descarga(db, CurrentUser(sub=dos_centros["auth_user_id"], salon_id=centro_2, rol="admin"),
                           cuenta, "entregada", centros=2, filas=10, bytes=1000)
    await apuntar_correcciones(
        db, salon_id=centro_2, usuario_id=admin["id"], empleada_nombre="Begoña la del Norte", sobre_si_misma=False,
        cambios=[{"lote": str(uuid.uuid4()), "salon_id": centro_2, "employee_id": empleada["id"], "accion": "nueva",
                  "marca_id": str(uuid.uuid4()), "tipo": "salida", "ts_antes": None,
                  "ts_despues": "2026-07-13T19:00:00+00:00", "motivo": "prueba de borrado del registro"}],
    )
    yield {**dos_centros, "db": db, "admin": admin, "empleada": empleada["id"], "centro_2_nombre": centro_2_nombre}


def _filas(tabla: str, cuenta: str) -> list[dict]:
    import json

    return json.loads(sql(
        f"select coalesce(json_agg(r), '[]') from (select account_id, salon_id, usuario_id, cuenta_nombre, "
        f"centro_nombre, usuario_nombre, usuario_email from public.{tabla} where account_id = '{cuenta}') r;"
    ))


def _intentar_borrar(usuario_id: str, *, con_clave_ajena: bool) -> str:
    """Intenta borrar a la persona dentro de una transacción que se DESHACE
    siempre, y dice qué pasó: 'BORRADO' o 'BLOQUEADO'."""
    clave = ("alter table public.decisiones_de_dinero add constraint zz_control_fk "
             "foreign key (usuario_id) references public.users (id) not valid;" if con_clave_ajena else "")
    return sql(f"""
begin;
{clave}
create temp table zz_resultado (r text);
do $$ begin
  delete from public.users where id = '{usuario_id}';
  insert into zz_resultado values ('BORRADO');
exception when foreign_key_violation then
  insert into zz_resultado values ('BLOQUEADO');
end $$;
select 'RESULTADO=' || r from zz_resultado;
rollback;
""")


async def test_EL_CONTROL_con_la_clave_ajena_de_la_0054_borrar_a_la_administradora_falla(con_registros):
    """Las dos mitades: con la clave ajena de antes, BLOQUEADO; sin ella, BORRADO."""
    admin = con_registros["admin"]["id"]
    assert "RESULTADO=BLOQUEADO" in _intentar_borrar(admin, con_clave_ajena=True)
    assert "RESULTADO=BORRADO" in _intentar_borrar(admin, con_clave_ajena=False)
    # Y todo se deshizo: la persona sigue y la clave de control no quedó puesta.
    assert sql(f"select count(*) from public.users where id = '{admin}';") == "1"
    assert "zz_control_fk" not in (sql(
        "select coalesce(string_agg(conname, ','), '') from pg_constraint "
        "where conrelid = 'public.decisiones_de_dinero'::regclass;") or "")


async def test_se_borran_una_empleada_un_centro_y_la_cuenta_y_los_registros_siguen_diciendo_quien(con_registros):
    c = con_registros
    db, cuenta, centro_2 = c["db"], c["account_id"], c["centro_2"]

    # 1. La empleada.
    await db.delete("employees", {"id": f"eq.{c['empleada']}"})
    assert await db.select("employees", {"select": "id", "id": f"eq.{c['empleada']}"}) == []

    # 2. El centro 2, que es el que nombran las dos filas.
    await db.delete("services", {"salon_id": f"eq.{centro_2}"})
    await db.delete("salons", {"id": f"eq.{centro_2}"})
    assert await db.select("salons", {"select": "id", "id": f"eq.{centro_2}"}) == []

    # 3. La cuenta entera: su administradora (la que nombran las filas), su
    #    otro centro, la cuenta y la identidad de acceso.
    for salon in (c["salon_id"],):
        await db.delete("users", {"salon_id": f"eq.{salon}"})
        await db.delete("services", {"salon_id": f"eq.{salon}"})
        await db.delete("salons", {"id": f"eq.{salon}"})
    await db.delete("accounts", {"id": f"eq.{cuenta}"})
    await SupabaseAuthAdminClient(SUPABASE_URL, SECRET_KEY).delete_user(c["auth_user_id"])
    assert await db.select("accounts", {"select": "id", "id": f"eq.{cuenta}"}) == []
    assert await db.select("users", {"select": "id", "id": f"eq.{c['admin']['id']}"}) == []

    # Y las filas siguen ahí, con los ids sueltos y los nombres de entonces.
    esperado = {
        "account_id": cuenta, "salon_id": centro_2, "usuario_id": c["admin"]["id"],
        "cuenta_nombre": "Centro Uno", "centro_nombre": c["centro_2_nombre"],
        "usuario_nombre": c["admin"]["nombre"], "usuario_email": c["admin"]["email"],
    }
    for tabla in sorted(REGISTROS):
        (fila,) = _filas(tabla, cuenta)
        print(f"TRAS EL BORRADO · {tabla}: {fila}")  # la prueba, legible con -s
        assert {k: fila[k] for k in esperado if k != "cuenta_nombre"} == {
            k: v for k, v in esperado.items() if k != "cuenta_nombre"}, tabla
        assert fila["cuenta_nombre"], tabla  # el titular de la cuenta, el que tuviera
