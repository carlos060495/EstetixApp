"""LO QUE UNA PRUEBA DEJA EN LA BASE LOCAL, SE LO LLEVA (12-10-2026).

Una tirada completa dejaba 410 filas más de las que encontraba; 212 cuentas de
restos acabaron rompiendo una pantalla que no tenía nada que ver con ellas.

  · `retirar_registros(cuentas)`: los registros que se conservan A PROPÓSITO
    —correcciones de fichaje, descargas, decisiones de dinero— sobreviven al
    borrado de la cuenta, y sus triggers no dejan borrarlos. En producción eso
    es lo que se quiere; en la base local son restos de la prueba. Se buscan
    por la columna `account_id` en todas las tablas de `public` (sin lista) y
    se retiran saltando SUS triggers solo dentro de esta transacción. Se llama
    DESPUÉS de borrar la cuenta: no toca nada de una cuenta que exista.
  · `registro_de_auth`: GoTrue apunta en `auth.audit_log_entries` cada alta,
    entrada y borrado que hacen las pruebas por su API. Lo que apuntó mientras
    corría un fichero de base real se borra al acabar ese fichero.
  · `el_latido_queda_como_estaba(tarea)`: una tarea que corre de verdad deja su
    latido en `tareas_latido` (una fila por tarea, con la hora de la pasada). No
    añade filas, así que contarlas no lo veía: cambiaba la que había (15-10-2026).
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from contextlib import contextmanager

import pytest


def sql(consulta: str) -> str:
    """psql en el contenedor de la base local (como `test_un_salon_no_puede_cambiarse_el_plan.sql`,
    sin importar ficheros de pruebas desde `conftest`)."""
    nombres = subprocess.run(["docker", "ps", "--format", "{{.Names}}", "--filter", "name=supabase_db"],
                             capture_output=True, text=True).stdout.split()
    assert nombres, "no encuentro el contenedor de la base local"
    r = subprocess.run(["docker", "exec", "-i", nombres[0], "psql", "-U", "postgres", "-qAt", "-v", "ON_ERROR_STOP=1"],
                       input=consulta, capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, f"psql falló: {r.stderr.strip()}"
    return r.stdout.strip()


def retirar_registros(cuentas: list[str]) -> None:
    cuentas = [c for c in cuentas if c]
    if not cuentas:
        return
    lista = "{" + ",".join(cuentas) + "}"
    sql(f"""
begin;
do $$ declare t record; n bigint; begin
  -- Solo las de cuentas que YA NO EXISTEN: esto es para restos, nunca para datos vivos.
  if exists (select 1 from public.accounts where id = any ('{lista}'::uuid[])) then
    raise exception 'retirar_registros: la cuenta sigue existiendo; bórrala antes';
  end if;
  for t in
    select c.oid::regclass::text as tabla
    from pg_class c join pg_namespace s on s.oid = c.relnamespace
    join pg_attribute a on a.attrelid = c.oid and a.attname = 'account_id' and not a.attisdropped
    where s.nspname = 'public' and c.relkind = 'r'
  loop
    execute format('select count(*) from %s where account_id = any (%L::uuid[])', t.tabla, '{lista}') into n;
    if n > 0 then
      execute format('alter table %s disable trigger user', t.tabla);
      execute format('delete from %s where account_id = any (%L::uuid[])', t.tabla, '{lista}');
      execute format('alter table %s enable trigger user', t.tabla);
    end if;
  end loop;
end $$;
commit;
""")


@pytest.fixture(scope="module", autouse=True)
def registro_de_auth(request):
    """Lo que GoTrue apuntó mientras corría este fichero, fuera al terminar.
    Solo en los ficheros con pruebas de base real: los demás no tienen stack."""
    de_base_real = any(i.get_closest_marker("base_real") for i in request.session.items
                       if i.module is request.module)
    if not de_base_real:
        yield
        return
    desde = sql("select clock_timestamp();")
    yield
    sql(f"delete from auth.audit_log_entries where created_at >= '{desde}'::timestamptz;")


# ═══ EL GUARDA: UNA TIRADA NO PUEDE DEJAR MÁS FILAS DE LAS QUE ENCONTRÓ ═══
# Cuenta las filas de todas las tablas de `public`, `auth` y `storage` (sin
# lista) antes de la primera prueba y después de la última. Si sobra alguna, la
# tirada sale en ROJO diciendo dónde. Solo cuando corren pruebas de base real.
# `cron` no entra: pg_cron apunta sus ejecuciones por su cuenta.

_CONTAR = r"""
select n.nspname || '.' || c.relname || '|' ||
       (xpath('/row/n/text()', query_to_xml(format('select count(*) as n from %I.%I', n.nspname, c.relname),
                                            false, true, '')))[1]::text
from pg_class c join pg_namespace n on n.oid = c.relnamespace
where c.relkind = 'r' and n.nspname in ('public', 'auth', 'storage')
order by 1;
"""


def contar_filas() -> dict[str, int]:
    return {t: int(n) for t, n in (ln.split("|") for ln in sql(_CONTAR).splitlines())}


def lo_que_sobra(antes: dict[str, int], despues: dict[str, int]) -> list[str]:
    return [f"{t}: +{despues[t] - antes.get(t, 0)}" for t in sorted(despues) if despues[t] > antes.get(t, 0)]


_ANTES: dict[str, int] | None = None


def pytest_collection_finish(session):
    global _ANTES
    if any(i.get_closest_marker("base_real") for i in session.items):
        try:
            _ANTES = contar_filas()
        except AssertionError:
            _ANTES = None  # sin stack: las de base real se saltan o dan su propio error


def comprobar_al_terminar(session) -> None:
    """Lo llama el `pytest_sessionfinish` de conftest (que también es el de `sin_saltos`)."""
    if _ANTES is None:
        return
    sobra = lo_que_sobra(_ANTES, contar_filas())
    if sobra:
        rep = session.config.pluginmanager.get_plugin("terminalreporter")
        if rep:
            rep.write_sep("=", "LA TIRADA DEJA FILAS EN LA BASE LOCAL", red=True)
            for linea in sobra:
                rep.write_line(f"  {linea}")
        session.exitstatus = 1


@contextmanager
def el_latido_queda_como_estaba(tarea: str) -> Iterator[None]:
    """La fila de `tareas_latido` de esa tarea vuelve a ser la de antes al salir.

    La tarea corre de verdad y escribe su latido como siempre; lo que se deshace
    es el rastro en la base local. Si no había fila, se borra la que deje.
    """
    antes = sql(f"select row_to_json(t)::text from public.tareas_latido t where t.tarea = '{tarea}';")
    try:
        yield
    finally:
        volver = (
            f"insert into public.tareas_latido select * from json_populate_record(null::public.tareas_latido, $j${antes}$j$);"
            if antes else ""
        )
        sql(f"begin;\ndelete from public.tareas_latido where tarea = '{tarea}';\n{volver}\ncommit;")
