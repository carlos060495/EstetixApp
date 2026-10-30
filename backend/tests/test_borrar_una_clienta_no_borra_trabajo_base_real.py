"""BORRAR UNA CLIENTA NO HACE DESAPARECER NINGUNA CITA NI NINGÚN INGRESO (0087).

Medido el 14-10-2026: ya era así. `appointments.client_id` es ON DELETE SET
NULL —sus citas se quedan como «Cliente Express», la agenda las pinta igual— y
ningún ingreso apunta a una clienta. Con ella solo se van su ficha y sus
consentimientos. Esto impide que deje de serlo:

  1. EL CATÁLOGO, sin lista: toda clave ajena que llegue a `clients` —y, en
     cadena, a lo que se borre con ella— es SET NULL, salvo las declaradas
     parte de la ficha (comentario `FICHA DE LA CLIENTA` en la clave).
  2. CON NÚMEROS: una clienta con una cita pasada, dos futuras y un ingreso
     cobrado; se borra; se cuentan TODAS las tablas de `public` antes y
     después. Solo bajan `clients` y `consents`. Las citas siguen ahí (las
     futuras, a la misma hora y sin clienta) y la caja, intacta.

CONTROL: con la clave de las citas cambiada a CASCADE (en una transacción que
se deshace), los dos guardas se ponen en rojo. Todo va dentro de transacciones
que se deshacen: no queda nada.
"""

from __future__ import annotations

import pytest

from tests.lo_que_deja_una_prueba import sql

pytestmark = [pytest.mark.base_real]

SALON = "00000000-0000-0000-0000-000000000001"

# Las claves que se llevan filas al borrar una clienta, en cadena, y si están declaradas.
CASCADA = """
with recursive cadena(tabla, clave, declarada) as (
  select (c.conrelid::regclass)::text, c.conname, coalesce(obj_description(c.oid, 'pg_constraint'), '') like 'FICHA DE LA CLIENTA%'
  from pg_constraint c where c.contype = 'f' and c.confrelid = 'public.clients'::regclass and c.confdeltype = 'c'
  union
  select c.conrelid::regclass::text, c.conname, coalesce(obj_description(c.oid, 'pg_constraint'), '') like 'FICHA DE LA CLIENTA%'
  from pg_constraint c join cadena on c.confrelid = cadena.tabla::regclass
  where c.contype = 'f' and c.confdeltype = 'c'
)
select 'public.' || replace(tabla, 'public.', '') || '.' || clave || '|' || case when declarada then 't' else 'f' end
from cadena order by 1;
"""

CONTAR = """
create or replace function pg_temp.contar() returns table (t text, n bigint) language plpgsql as $$
declare r record; begin
  for r in select c.oid::regclass::text as tabla from pg_class c join pg_namespace s on s.oid = c.relnamespace
           where s.nspname = 'public' and c.relkind = 'r' loop
    execute format('select count(*) from %s', r.tabla) into n; t := r.tabla; return next;
  end loop; end $$;
create temp table antes on commit drop as select * from pg_temp.contar();
"""

CLIENTA = f"""
insert into public.clients (id, salon_id, nombre, telefono) values ('c1a00000-0000-0000-0000-0000000000c1', '{SALON}', 'Clienta que se borra', '600999888');
insert into public.consents (salon_id, client_id, tipo) values ('{SALON}', 'c1a00000-0000-0000-0000-0000000000c1', 'rgpd');
insert into public.appointments (id, salon_id, client_id, employee_id, service_id, inicio, fin, estado)
select x.id::uuid, '{SALON}', 'c1a00000-0000-0000-0000-0000000000c1', e.id, s.id, now() + x.d, now() + x.d + interval '1 hour', x.estado::public.cita_estado
from (values ('a1a00000-0000-0000-0000-0000000000a1', interval '-10 days', 'completada'),
             ('a1a00000-0000-0000-0000-0000000000a2', interval '3 days', 'confirmada'),
             ('a1a00000-0000-0000-0000-0000000000a3', interval '9 days', 'confirmada')) x(id, d, estado),
     (select id from public.employees where salon_id = '{SALON}' and estado = 'activo' limit 1) e,
     (select id from public.services where salon_id = '{SALON}' limit 1) s;
insert into public.incomes (salon_id, concepto, importe) values ('{SALON}', 'Servicio de la clienta que se borra', 35);
"""

DESPUES = """
create temp table despues on commit drop as select * from pg_temp.contar();
select 'BAJA|' || a.t || '|' || (a.n - d.n) from antes a join despues d using (t) where d.n < a.n order by 1;
select 'CITAS|' || count(*) || '|' || count(*) filter (where client_id is null) || '|' || count(*) filter (where inicio > now())
  from public.appointments where id::text like 'a1a00000-%';
select 'CAJA|' || count(*) from public.incomes where concepto = 'Servicio de la clienta que se borra';
"""


def _borrar_la_clienta(romper: str = "") -> dict[str, list[str]]:
    salida = sql("begin;\n" + romper + CLIENTA + CONTAR + "delete from public.clients where id = "
                 "'c1a00000-0000-0000-0000-0000000000c1';\n" + DESPUES + "rollback;\n")
    filas: dict[str, list[str]] = {}
    for ln in salida.splitlines():
        if "|" in ln:
            clave, _, resto = ln.partition("|")
            filas.setdefault(clave, []).append(resto)
    return filas


A_CASCADE = ("alter table public.appointments drop constraint appointments_client_id_fkey;\n"
             "alter table public.appointments add constraint appointments_client_id_fkey foreign key (client_id) "
             "references public.clients(id) on delete cascade;\n")


def _sin_declarar(romper: str = "") -> list[str]:
    salida = sql("begin;\n" + romper + CASCADA + "rollback;\n")
    return [ln.split("|")[0] for ln in salida.splitlines() if ln.endswith("|f")]


def test_toda_clave_hacia_una_clienta_es_set_null_salvo_su_ficha():
    cadena = sql(CASCADA)
    assert "public.consents.consents_client_id_fkey|t" in cadena.splitlines(), cadena  # antivacío
    assert _sin_declarar() == [], (
        "Borrar una clienta se llevaría filas de otras tablas sin decirlo: "
        f"{_sin_declarar()}. Si son su ficha, declárala; si no, la clave tiene que ser SET NULL."
    )


def test_CONTROL_una_clave_de_citas_en_cascada_se_pone_roja():
    assert "public.appointments.appointments_client_id_fkey" in _sin_declarar(A_CASCADE)


def test_borrar_una_clienta_no_borra_ninguna_cita_ni_ningun_ingreso():
    r = _borrar_la_clienta()
    print("\nbajan:", r.get("BAJA"), "· citas (total|sin clienta|futuras):", r["CITAS"], "· caja:", r["CAJA"])
    assert {x.split("|")[0].removeprefix("public.") for x in r.get("BAJA", [])} <= {"clients", "consents"}, r
    assert r["CITAS"] == ["3|3|2"], "las tres citas siguen, sin clienta (Cliente Express), y las dos futuras también"
    assert r["CAJA"] == ["1"], "el ingreso cobrado sigue en la caja"


def test_CONTROL_con_las_citas_en_cascada_desaparecen():
    r = _borrar_la_clienta(A_CASCADE)
    assert any(x.removeprefix("public.").startswith("appointments|") for x in r.get("BAJA", [])), r
    assert r["CITAS"] == ["0|0|0"], r
