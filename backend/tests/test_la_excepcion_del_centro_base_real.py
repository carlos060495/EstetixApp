"""LA EXCEPCIÓN DEL CENTRO (0067), contra la base de verdad.

04-10-2026, opción A: una corrección de fichaje se puede borrar SOLO
si su centro ya no existe. Lo que la hace segura es que NO SE PUEDE PEDIR:
depende de un hecho (el centro ya no está), no de quién borra ni de algo que
se pueda poner. Este guarda se pone ROJO si eso deja de ser así:

  1. LA CONDICIÓN. La función del trigger solo puede mirar `public.salons` por
     `old.salon_id`, tiene que ser SECURITY DEFINER (para que «no existe» no sea
     «la RLS no me lo enseña»), no puede mirar quién borra (rol, usuario,
     permiso) ni nada que se pueda poner (una bandera, un ajuste de sesión), y
     el DELETE de las correcciones no puede pasar por otra función.
  2. EL COMPORTAMIENTO, en una transacción que se deshace: con el centro vivo,
     ni `postgres` ni `service_role` borran una corrección; sin el centro, el
     DELETE pasa y el UPDATE y el TRUNCATE siguen rechazados.
  3. LA GRIETA DE A: borrar un centro tiene que seguir siendo solo nuestro. Ni
     la sesión ni `anon` tienen DELETE o TRUNCATE sobre `salons`, no hay
     política de DELETE, ninguna cascada llega a `salons`, ninguna función de
     la base borra centros, y una corrección huérfana solo la borra `postgres`.

Cada regla tiene su CONTROL: la misma comprobación sobre una base cambiada a
propósito (dentro de una transacción que se deshace) tiene que salir roja.
"""

from __future__ import annotations

import re

import pytest

from tests.test_un_salon_no_puede_cambiarse_el_plan import sql

pytestmark = [pytest.mark.base_real]

FUNCION = "se_borra_solo_sin_su_centro"
MOTIVO = "control de la excepción del centro"

# Lo que delataría que la excepción se puede PEDIR: quién borra, o algo que se
# pone en la sesión.
_PROHIBIDO = re.compile(
    r"current_user|session_user|current_role|\buser\b|current_setting|set_config|pg_has_role"
    r"|has_\w*privilege|auth\.|request\.|application_name|pg_backend_pid|inet_client",
)

_ORIGINAL = f"""
create or replace function public.{FUNCION}()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
  {{antes}}
  if exists (select 1 from public.salons s where s.id = old.salon_id) then
    raise exception '% no se puede borrar mientras su centro exista: es un registro', tg_table_name
      using errcode = '42501';
  end if;
  return old;
end;
$$;
"""


def _con(antes: str) -> str:
    return _ORIGINAL.replace("{antes}", antes)


def _intento(clave: str, sentencia: str) -> str:
    """Un intento dentro de una subtransacción que SIEMPRE se deshace, para que
    un intento que pasa no le quite la fila al siguiente."""
    return f"""
do $$ declare n bigint; begin
  begin
    {sentencia};
    get diagnostics n = row_count;
    raise exception using errcode = 'ZZ001', message = n::text;
  exception
    when sqlstate 'ZZ001' then insert into zz_r values ('{clave}', 'PASA:' || sqlerrm);
    when others then insert into zz_r values ('{clave}', 'RECHAZADA:' || sqlstate);
  end;
end $$;
"""


def _guion(mutacion: str = "") -> str:
    borra = f"delete from public.correcciones_de_fichaje where motivo = '{MOTIVO}'"
    return f"""
begin;
{mutacion}
select 'DEF|' || p.prosecdef || '|'
       || btrim(regexp_replace(regexp_replace(p.prosrc, '--[^\\n]*', '', 'g'), '\\s+', ' ', 'g'))
  from pg_proc p where p.proname = '{FUNCION}' and p.pronamespace = 'public'::regnamespace;
select 'TRG|' || coalesce(string_agg(distinct p.proname, ',' order by p.proname), '')
  from pg_trigger t join pg_proc p on p.oid = t.tgfoid
  where t.tgrelid = 'public.correcciones_de_fichaje'::regclass and not t.tgisinternal
    and (t.tgtype & 1) <> 0 and (t.tgtype & 8) <> 0;
create temp table zz_centro (cuenta uuid, centro uuid default gen_random_uuid()) on commit drop;
with c as (insert into public.accounts (nombre) values ('zz cuenta del guarda') returning id)
  insert into zz_centro (cuenta) select id from c;
insert into public.salons (id, account_id, nombre_comercial)
  select centro, cuenta, 'zz centro del guarda' from zz_centro;
insert into public.correcciones_de_fichaje
  (lote, account_id, salon_id, employee_id, accion, marca_id, tipo, ts_despues, motivo, usuario_id)
  select gen_random_uuid(), cuenta, centro, gen_random_uuid(), 'nueva', gen_random_uuid(), 'salida', now(),
         '{MOTIVO}', gen_random_uuid() from zz_centro;
create temp table zz_r (k text, v text) on commit drop;
grant insert on zz_r to service_role;
{_intento("postgres_con_centro", borra)}
set local role service_role;
{_intento("service_role_con_centro", borra)}
reset role;
delete from public.salons where id = (select centro from zz_centro);
{_intento("update_sin_centro", f"update public.correcciones_de_fichaje set motivo = motivo where motivo = '{MOTIVO}'")}
{_intento("truncate_sin_centro", "truncate public.correcciones_de_fichaje")}
{_intento("delete_sin_centro", borra)}
select 'R|' || k || '|' || v from zz_r order by k;
rollback;
"""


def _rojos(salida: str) -> list[str]:
    rojos: list[str] = []
    defs = [ln.split("|", 2) for ln in salida.splitlines() if ln.startswith("DEF|")]
    if not defs:
        return [f"no existe la función {FUNCION}"]
    _, secdef, src = defs[0]
    cuerpo = src.lower()
    if secdef != "true":
        rojos.append("la función no es SECURITY DEFINER: la RLS podría esconder el centro")
    if "old.salon_id" not in cuerpo or "public.salons" not in cuerpo or "exists" not in cuerpo:
        rojos.append("la función ya no mira si el centro existe")
    if (tablas := set(re.findall(r"public\.(\w+)", cuerpo))) != {"salons"}:
        rojos.append(f"la función mira otras tablas: {sorted(tablas - {'salons'})}")
    if m := _PROHIBIDO.findall(cuerpo):
        rojos.append(f"la función depende de quién borra o de algo que se pone: {sorted(set(m))}")
    trg = next((ln.split("|", 1)[1] for ln in salida.splitlines() if ln.startswith("TRG|")), "")
    if trg != FUNCION:
        rojos.append(f"el DELETE de las correcciones pasa por: {trg or 'nada'}")
    r = {k: v for k, v in (ln.split("|", 2)[1:] for ln in salida.splitlines() if ln.startswith("R|"))}
    for clave in ("postgres_con_centro", "service_role_con_centro", "update_sin_centro", "truncate_sin_centro"):
        if not r.get(clave, "").startswith("RECHAZADA"):
            rojos.append(f"{clave}: {r.get(clave)}")
    if r.get("delete_sin_centro") != "PASA:1":
        rojos.append(f"delete_sin_centro: {r.get('delete_sin_centro')} (la excepción no funciona)")
    return rojos


# ═══ 1 y 2. La condición y su comportamiento ═════════════════════════════


def test_la_excepcion_depende_solo_de_que_el_centro_no_exista():
    salida = sql(_guion())
    assert _rojos(salida) == [], salida
    # Antivacío: los cinco intentos se hicieron de verdad.
    assert salida.count("\nR|") == 5, salida


_CONTROLES = {
    "un_rol": _con("if current_user = 'postgres' then return old; end if;"),
    "una_bandera": _con("if current_setting('estetix.borrado', true) = 'si' then return old; end if;"),
    "un_permiso": _con("if exists (select 1 from public.admin_users) then return old; end if;"),
    "sin_condicion": f"create or replace function public.{FUNCION}() returns trigger language plpgsql "
                     "security definer set search_path = '' as $$ begin return old; end; $$;",
    "security_invoker": f"alter function public.{FUNCION}() security invoker;",
    "otra_funcion": (
        "create function public.zz_deja_borrar() returns trigger language plpgsql as $$ begin return old; end; $$;\n"
        "drop trigger correcciones_de_fichaje_se_van_con_su_centro on public.correcciones_de_fichaje;\n"
        "create trigger zz before delete on public.correcciones_de_fichaje "
        "for each row execute function public.zz_deja_borrar();"
    ),
}


@pytest.mark.parametrize("mutacion", _CONTROLES.values(), ids=_CONTROLES.keys())
def test_CONTROL_si_la_excepcion_se_puede_pedir_se_pone_rojo(mutacion):
    salida = sql(_guion(mutacion))
    assert _rojos(salida), salida


def test_el_control_original_es_la_funcion_de_verdad():
    """Los controles parten de una copia de la función: si la de la base cambia,
    la copia tiene que cambiar con ella, o los controles probarían otra cosa."""
    def cuerpo(s: str) -> str:
        return next(ln.split("|", 2)[2] for ln in s.splitlines() if ln.startswith("DEF|"))
    assert cuerpo(sql(_guion())) == cuerpo(sql(_guion(_con(""))))


# ═══ 3. La grieta de A: borrar un centro sigue siendo solo nuestro ═════════

_CAMINOS = r"""
select 'GRANT|' || grantee || '|' || privilege_type from information_schema.role_table_grants
  where table_schema = 'public' and table_name = 'salons' and privilege_type in ('DELETE', 'TRUNCATE');
select 'POLICY|' || policyname from pg_policies
  where schemaname = 'public' and tablename = 'salons' and cmd in ('DELETE', 'ALL');
select 'CASCADA|' || conname from pg_constraint
  where contype = 'f' and conrelid = 'public.salons'::regclass and confdeltype = 'c';
select 'FUNCION|' || n.nspname || '.' || p.proname from pg_proc p join pg_namespace n on n.oid = p.pronamespace
  where n.nspname not in ('pg_catalog', 'information_schema')
    and p.prosrc ~* '(delete\s+from|truncate)\s+(table\s+)?(only\s+)?(public\.)?"?salons"?\M';
select 'HUERFANA|' || grantee from information_schema.role_table_grants
  where table_schema = 'public' and table_name = 'correcciones_de_fichaje' and privilege_type = 'DELETE';
"""

_NUESTRO_DELETE = {"postgres", "service_role"}


def _caminos_abiertos(salida: str) -> list[str]:
    rojos = []
    for ln in salida.splitlines():
        partes = ln.split("|")
        if len(partes) < 2:
            continue  # las etiquetas de psql (BEGIN, GRANT…)
        if partes[0] == "GRANT":
            _, quien, que = partes
            if quien not in ({"postgres"} if que == "TRUNCATE" else _NUESTRO_DELETE):
                rojos.append(f"{quien} tiene {que} sobre salons")
        elif partes[0] == "POLICY":
            rojos.append(f"política que deja borrar centros: {partes[1]}")
        elif partes[0] == "CASCADA":
            rojos.append(f"una clave de salons hace que se borre en cascada con otra tabla: {partes[1]}")
        elif partes[0] == "FUNCION":
            rojos.append(f"una función de la base borra centros: {partes[1]}")
        elif partes[0] == "HUERFANA" and partes[1] != "postgres":
            rojos.append(f"{partes[1]} puede borrar correcciones (huérfanas, en cuanto falte su centro)")
    return rojos


def test_borrar_un_centro_es_solo_nuestro():
    salida = sql(_CAMINOS)
    assert _caminos_abiertos(salida) == [], salida
    # Antivacío: ve los permisos que SÍ existen.
    assert "GRANT|service_role|DELETE" in salida and "HUERFANA|postgres" in salida, salida


_CONTROLES_CAMINOS = {
    "la_sesion_con_delete": "grant delete on public.salons to authenticated;",
    "anon_con_truncate": "grant truncate on public.salons to anon;",
    "una_politica_de_delete": "create policy zz on public.salons for delete using (true);",
    "una_cascada_hasta_salons": "alter table public.salons add constraint zz foreign key (account_id) "
                                "references public.accounts (id) on delete cascade;",
    "una_funcion_que_borra": "create function public.zz_cierra(p uuid) returns void language sql "
                             "as $$ delete from public.salons where id = p $$;",
    "service_role_borra_correcciones": "grant delete on public.correcciones_de_fichaje to service_role;",
}


@pytest.mark.parametrize("mutacion", _CONTROLES_CAMINOS.values(), ids=_CONTROLES_CAMINOS.keys())
def test_CONTROL_un_camino_nuevo_para_borrar_centros_se_pone_rojo(mutacion):
    salida = sql(f"begin;\n{mutacion}\n{_CAMINOS}rollback;\n")
    assert _caminos_abiertos(salida), salida
