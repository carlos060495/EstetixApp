"""El simulacro del borrado, contra la base de verdad. NO SE BORRA NADA.

  1. NO PUEDE BORRAR: la función es `stable`, y el CONTROL demuestra que
     Postgres rechaza un DELETE dentro de una función `stable` (si no lo
     rechazara, el `stable` no protegería nada).
  2. NO CAMBIA NI UNA FILA: se cuentan las tablas de la cuenta antes y después.
  3. VE LO QUE SE AÑADA MAÑANA: una tabla nueva con su clave en cascada hacia
     `salons`, creada en una transacción que se deshace, aparece en el
     simulacro. Es el control de que el inventario sale del esquema y no de una
     lista.
  4. ENSEÑA LO QUE CONTRADICE LO DECIDIDO, con la cuenta de verdad.
  5. EL RELOJ DE QUIEN NUNCA PAGÓ se pone en marcha y se para de verdad.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.db.postgrest_client import PostgrestClient
from app.routers import admin_plataforma
from app.routers.billing import _campos_de_la_suscripcion, _escribir_suscripcion
from tests.test_alta_completa_e2e import SECRET_KEY, SUPABASE_URL
from tests.test_camino_humano_dos_centros import (  # noqa: F401  (fixtures)
    anyio_backend,
    dos_centros,
)
from tests.test_un_salon_no_puede_cambiarse_el_plan import sql

pytestmark = [pytest.mark.base_real, pytest.mark.anyio]

AJUSTES = Settings(supabase_url=SUPABASE_URL, supabase_secret_key=SECRET_KEY,
                   supabase_jwks_url=f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json",
                   database_url="postgresql://x", frontend_base_url="https://www.estetixapp.es")


def _db() -> PostgrestClient:
    return PostgrestClient(SUPABASE_URL, SECRET_KEY, timeout=30.0)


async def _simulacro(cuenta: str):
    return await admin_plataforma.simulacro_de_una_cuenta(cuenta, _admin=None, db=_db(), settings=AJUSTES)


# ═══ 1. No puede borrar ═══════════════════════════════════════════════════


async def test_el_simulacro_es_stable_y_postgres_no_deja_borrar_desde_una_stable():
    assert sql("select provolatile from pg_proc where proname = 'simulacro_de_borrado';") == "s"
    # EL CONTROL: una función stable con un DELETE dentro, en una transacción
    # que se deshace. Si Postgres la dejara borrar, el `stable` no valdría nada.
    salida = sql(
        "begin;\n"
        "create table public.zz_prueba_stable (id int);\n"
        "insert into public.zz_prueba_stable values (1);\n"
        "create function public.zz_borra_stable() returns int language plpgsql stable as "
        "$f$ begin delete from public.zz_prueba_stable; return 1; end $f$;\n"
        "do $$ begin perform public.zz_borra_stable(); raise notice 'BORRO'; "
        "exception when others then raise notice 'RECHAZADO: %', sqlerrm; end $$;\n"
        "select count(*) from public.zz_prueba_stable;\n"
        "rollback;\n"
    )
    # La fila sigue ahí: el DELETE de la función stable no llegó a ejecutarse.
    assert salida.splitlines()[-2] == "1", salida


async def test_solo_lo_puede_llamar_service_role():
    permisos = sql(
        "select string_agg(r, ',' order by r) from (values ('anon'), ('authenticated'), ('service_role')) v(r) "
        "where has_function_privilege(r, 'public.simulacro_de_borrado(uuid)', 'execute');"
    )
    assert permisos == "service_role"


# ═══ 2. No cambia ni una fila ═════════════════════════════════════════════


async def test_hacer_el_simulacro_no_cambia_nada(dos_centros):
    cuenta = dos_centros["account_id"]
    contar = (
        "select (select count(*) from public.salons where account_id = '{c}') || '|' || "
        "(select count(*) from public.users where account_id = '{c}') || '|' || "
        "(select count(*) from public.services s join public.salons x on x.id = s.salon_id where x.account_id = '{c}') || '|' || "
        "(select count(*) from public.accounts where id = '{c}');"
    ).format(c=cuenta)
    antes = sql(contar)
    sim = await _simulacro(cuenta)
    assert sim.filas_a_borrar > 0
    assert sql(contar) == antes


# ═══ 3. Ve lo que se añada mañana ════════════════════════════════════════


async def test_CONTROL_una_tabla_nueva_en_cascada_aparece_en_el_simulacro(dos_centros):
    salida = sql(
        "begin;\n"
        "create table public.zz_tabla_nueva (salon_id uuid references public.salons(id) on delete cascade);\n"
        f"insert into public.zz_tabla_nueva select id from public.salons where account_id = '{dos_centros['account_id']}';\n"
        f"select tabla || '|' || filas || '|' || accion from public.simulacro_de_borrado('{dos_centros['account_id']}') "
        "where tabla = 'public.zz_tabla_nueva';\n"
        "rollback;\n"
    )
    assert "public.zz_tabla_nueva|2|borra" in salida, salida


# ═══ 4. Lo que contradice lo decidido ════════════════════════════════════


async def test_la_auditoria_de_soporte_se_queda_y_la_cuenta_se_va(dos_centros):
    cuenta = dos_centros
    await _escribir_suscripcion(_db(), cuenta["account_id"], cuenta["salon_id"],
                                *_campos_de_la_suscripcion({"id": "sub_sim", "status": "active",
                                                            "items": {"data": []}}, AJUSTES), AJUSTES)
    sim = await _simulacro(cuenta["account_id"])
    problemas = {f.tabla for f in sim.filas if f.problema}
    # Hasta la 0066 la auditoría de soporte caía en cascada desde `salons`.
    # Ahora se queda, que es lo decidido.
    nuestros = {"public.impersonation_sessions", "public.support_access_codes"}
    assert nuestros & problemas == set(), problemas
    assert all(f.que_haria != "borra" for f in sim.filas if f.tabla in nuestros)
    # La cuenta y su suscripción se van (0068), sin problema.
    for tabla in ("public.accounts", "public.subscriptions", "auth.users"):
        fila = next(f for f in sim.filas if f.tabla == tabla)
        assert fila.que_haria == "borra" and fila.problema is None, fila
    assert sim.situacion == "sin_reloj"


async def test_las_correcciones_de_fichaje_se_irian_despues_de_su_centro(dos_centros):
    """Hasta la 0067 se quedaban (sin clave ajena) y su trigger hacía fallar el
    borrado: dos rojos. Ahora se van después de su centro, que es lo decidido."""
    cuenta, centro = dos_centros["account_id"], dos_centros["centro_2"]
    salida = sql(
        "begin;\n"
        "insert into public.correcciones_de_fichaje (lote, account_id, salon_id, employee_id, accion, marca_id, "
        "tipo, ts_despues, motivo, usuario_id) values (gen_random_uuid(), "
        f"'{cuenta}', '{centro}', gen_random_uuid(), 'nueva', gen_random_uuid(), 'salida', now(), "
        f"'corrección del simulacro', '{dos_centros['id']}');\n"
        f"select paso || '|' || tabla || '|' || coalesce(filas::text, '') || '|' || accion || '|' || detalle "
        f"from public.simulacro_de_borrado('{cuenta}') where tabla = 'public.correcciones_de_fichaje';\n"
        "rollback;\n"
    )
    filas = [ln for ln in salida.splitlines() if ln.startswith(("1|", "2|", "3|", "4|", "5|"))]
    assert filas == ["1|public.correcciones_de_fichaje|1|borra|después de sus centros: su trigger solo deja "
                     "borrarlo cuando el centro ya no existe"], salida


# ═══ 5. El reloj de quien nunca pagó ═════════════════════════════════════


async def test_quien_nunca_activo_la_prueba_entra_en_el_reloj_y_sale_al_activarla(dos_centros):
    cuenta = dos_centros
    sql(f"delete from public.subscriptions where account_id = '{cuenta['account_id']}';")
    sql(f"update public.accounts set created_at = now() - interval '20 days' where id = '{cuenta['account_id']}';")

    contador = await admin_plataforma.plazo_de_datos(_admin=None, db=_db(), settings=AJUSTES)
    fila = next(c for c in contador.cuentas if c.account_id == cuenta["account_id"])
    assert fila.origen == "sin_pago" and fila.dias_restantes in (83, 84, 85)

    # Activa la prueba: aparece su suscripción y sale del reloj.
    await _escribir_suscripcion(_db(), cuenta["account_id"], cuenta["salon_id"],
                                *_campos_de_la_suscripcion({"id": "sub_prueba", "status": "trialing",
                                                            "items": {"data": []}}, AJUSTES), AJUSTES)
    contador = await admin_plataforma.plazo_de_datos(_admin=None, db=_db(), settings=AJUSTES)
    assert all(c.account_id != cuenta["account_id"] for c in contador.cuentas)
