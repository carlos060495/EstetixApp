"""EL ENSAYO DE LA 0069, con lo que había en producción (05-10-2026).

La base local ya tiene la 0069 aplicada. Aquí, dentro de UNA transacción que
se deshace al final, se vuelve a montar lo de antes (el tipo con los tres
roles, `users.pin_fichaje_hash`, `user_salons`) con los datos que se
consultaron en producción:

  · 5 filas con rol muerto (4 estilistas, 1 recepcionista), ninguna con
    usuario de Auth;
  · 3 de ellas con una ficha viva y activa enlazada, las tres en el mismo
    centro; las otras 2, sin ficha;
  · 0 conversaciones del bot atendidas por ellas.

Y se ejecuta la migración TAL CUAL está en el repositorio, dos veces:

  1. la primera desenlaza las 3 fichas (y lo dice), borra las 5 filas y deja
     el tipo con «admin» solo; las fichas siguen activas y con su PIN;
  2. la segunda no hace nada (y dice «0 fichas»).

Con dos controles: lo que no se previó (una clave ajena nueva hacia `users`, o
una conversación atendida por un resto) la para sin tocar nada.
"""

from __future__ import annotations

import pathlib
import subprocess

import pytest

from tests.test_un_salon_no_puede_cambiarse_el_plan import _contenedor

pytestmark = [pytest.mark.base_real]

MIGRACION = (pathlib.Path(__file__).resolve().parents[2]
             / "supabase/migrations/0069_solo_queda_la_administradora.sql")

PIN = "$argon2id$v=19$m=65536,t=3,p=4$ensayo$0069"

# Lo de antes de la 0069, y los datos de producción. Ids fijos: todo se deshace.
_ANTES = f"""
alter type public.user_rol rename to user_rol_ensayo;
create type public.user_rol as enum ('admin', 'estilista', 'recepcionista');
alter table public.users alter column rol drop default;
alter table public.users alter column rol type public.user_rol using rol::text::public.user_rol;
alter table public.users alter column rol set default 'admin';
drop type public.user_rol_ensayo;
alter table public.users add column pin_fichaje_hash text;
create table public.user_salons (
  user_id uuid not null references public.users(id) on delete cascade,
  salon_id uuid not null references public.salons(id) on delete cascade,
  primary key (user_id, salon_id));

insert into public.accounts (id, nombre) values ('00690000-0000-0000-0000-00000000000a', 'Ensayo 0069');
insert into public.salons (id, account_id, nombre_comercial, zona_horaria)
values ('00690000-0000-0000-0000-00000000000c', '00690000-0000-0000-0000-00000000000a', 'Demo', 'Europe/Madrid');
insert into public.users (id, salon_id, account_id, nombre, email, rol, pin_fichaje_hash) values
  ('00690000-0000-0000-0000-000000000001', '00690000-0000-0000-0000-00000000000c', '00690000-0000-0000-0000-00000000000a', 'Admin', 'a@ensayo', 'admin', null),
  ('00690000-0000-0000-0000-000000000011', '00690000-0000-0000-0000-00000000000c', '00690000-0000-0000-0000-00000000000a', 'R1', 'r1@ensayo', 'recepcionista', 'h'),
  ('00690000-0000-0000-0000-000000000012', '00690000-0000-0000-0000-00000000000c', '00690000-0000-0000-0000-00000000000a', 'E1', 'e1@ensayo', 'estilista', 'h'),
  ('00690000-0000-0000-0000-000000000013', '00690000-0000-0000-0000-00000000000c', '00690000-0000-0000-0000-00000000000a', 'E2', 'e2@ensayo', 'estilista', 'h'),
  ('00690000-0000-0000-0000-000000000014', '00690000-0000-0000-0000-00000000000c', '00690000-0000-0000-0000-00000000000a', 'E3', 'e3@ensayo', 'estilista', null),
  ('00690000-0000-0000-0000-000000000015', '00690000-0000-0000-0000-00000000000c', '00690000-0000-0000-0000-00000000000a', 'E4', 'e4@ensayo', 'estilista', null);
insert into public.user_salons (user_id, salon_id)
select id, salon_id from public.users where email like '%@ensayo';
insert into public.employees (id, salon_id, nombre, user_id, estado, pin_fichaje_hash) values
  ('00690000-0000-0000-0000-000000000021', '00690000-0000-0000-0000-00000000000c', 'F1', '00690000-0000-0000-0000-000000000011', 'activo', '{PIN}'),
  ('00690000-0000-0000-0000-000000000022', '00690000-0000-0000-0000-00000000000c', 'F2', '00690000-0000-0000-0000-000000000012', 'activo', '{PIN}'),
  ('00690000-0000-0000-0000-000000000023', '00690000-0000-0000-0000-00000000000c', 'F3', '00690000-0000-0000-0000-000000000013', 'activo', '{PIN}');
"""

_FICHAS = "'00690000-0000-0000-0000-000000000021', '00690000-0000-0000-0000-000000000022', '00690000-0000-0000-0000-000000000023'"

_DESPUES = f"""
select 'R|roles|' || enum_range(null::public.user_rol)::text;
select 'R|restos|' || count(*) from public.users where email like '%@ensayo' and rol::text <> 'admin';
select 'R|admin|' || count(*) from public.users where email = 'a@ensayo';
select 'R|fichas|' || count(*) from public.employees
  where id in ({_FICHAS}) and estado::text = 'activo' and user_id is null and pin_fichaje_hash = '{PIN}';
select 'R|user_salons|' || coalesce(to_regclass('public.user_salons')::text, 'no');
select 'R|pin_users|' || count(*) from information_schema.columns
  where table_schema = 'public' and table_name = 'users' and column_name = 'pin_fichaje_hash';
"""


def _cuerpo() -> str:
    """La migración sin su `begin;`/`commit;`: el ensayo es su propia transacción."""
    lineas = MIGRACION.read_text(encoding="utf-8").splitlines()
    fuera = [ln for ln in lineas if ln.strip() in ("begin;", "commit;")]
    assert len(fuera) == 2, fuera
    return "\n".join(ln for ln in lineas if ln.strip() not in ("begin;", "commit;"))


def _psql(guion: str) -> tuple[int, str, str]:
    r = subprocess.run(
        ["docker", "exec", "-i", _contenedor(), "psql", "-U", "postgres", "-At", "-v", "ON_ERROR_STOP=1"],
        input=guion, capture_output=True, text=True, encoding="utf-8",
    )
    return r.returncode, r.stdout, r.stderr


def _resultados(salida: str) -> dict[str, str]:
    return {p[1]: p[2] for p in (ln.split("|", 2) for ln in salida.splitlines() if ln.startswith("R|"))}


def test_dos_veces_con_lo_que_habia_en_produccion():
    cuerpo = _cuerpo()
    codigo, salida, avisos = _psql(
        "begin;\n" + _ANTES
        + f"select 'R|enlazadas|' || count(*) from public.employees where id in ({_FICHAS}) and user_id is not null;\n"
        + cuerpo + "\n" + _DESPUES.replace("R|", "R|1.")
        + cuerpo + "\n" + _DESPUES.replace("R|", "R|2.")
        + "rollback;\n"
    )
    assert codigo == 0, avisos
    r = _resultados(salida)
    assert r["enlazadas"] == "3"  # el montaje es el de producción
    for vuelta in ("1.", "2."):
        assert r[vuelta + "roles"] == "{admin}"
        assert r[vuelta + "restos"] == "0"
        assert r[vuelta + "admin"] == "1"
        assert r[vuelta + "fichas"] == "3", "las fichas tienen que seguir activas, desenlazadas y con su PIN"
        assert r[vuelta + "user_salons"] == "no"
        assert r[vuelta + "pin_users"] == "0"
    # Lo dice, las dos veces: 3 y después 0.
    dichas = [ln for ln in avisos.splitlines() if "fichas desenlazadas" in ln]
    assert len(dichas) == 2, avisos
    assert "0069: 3 fichas desenlazadas" in dichas[0]
    assert "0069: 0 fichas desenlazadas" in dichas[1]


@pytest.mark.parametrize("imprevisto, se_queja_de", [
    ("create table public.zz_imprevista (quien uuid references public.users(id) on delete set null);\n"
     "insert into public.zz_imprevista values ('00690000-0000-0000-0000-000000000014');",
     "filas de zz_imprevista.quien apuntan a usuarios con un rol muerto"),
    ("insert into public.bot_sessions (salon_id, customer_phone, atendida_por) values "
     "('00690000-0000-0000-0000-00000000000c', '+34600000069', '00690000-0000-0000-0000-000000000015');",
     "conversaciones las atendió un usuario con un rol muerto"),
], ids=["una_clave_ajena_que_no_se_miro", "una_conversacion_atendida"])
def test_CONTROL_lo_que_no_se_previo_la_para_sin_tocar_nada(imprevisto, se_queja_de):
    codigo, salida, avisos = _psql(
        "begin;\n" + _ANTES + imprevisto + "\n" + _cuerpo() + "\nrollback;\n"
    )
    assert codigo != 0
    assert se_queja_de in avisos, avisos
    # Se paró en la comprobación 0: ni una ficha llegó a desenlazarse.
    assert "fichas desenlazadas" not in avisos
