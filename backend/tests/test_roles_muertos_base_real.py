"""LOS ROLES MUERTOS EN LA BASE DE VERDAD (05-10-2026).

Ninguna función de la base nombra «estilista» o «recepcionista», ni escribe
`users.pin_fichaje_hash` (el PIN del quiosco es el de `employees`). Rojo si
aparece una, con su control dentro de una transacción que se deshace.

Y la sesión ya no puede escribir `users.rol`: la 0069 le quitó el permiso de
columna (ningún código escribía `users` con la sesión). Rojo si vuelve.
"""

from __future__ import annotations

import pytest

from tests.test_un_salon_no_puede_cambiarse_el_plan import sql

pytestmark = [pytest.mark.base_real]

_FUNCIONES = r"""
select 'F|' || n.nspname || '.' || p.proname from pg_proc p join pg_namespace n on n.oid = p.pronamespace
where n.nspname in ('public', 'auth')
  and (p.prosrc ~* '\m(estilista|recepcionista)\M'
       or p.prosrc ~* '(insert\s+into|update)\s+(public\.)?users\M[^;]*pin_fichaje_hash');
"""


def _funciones(prefijo: str = "") -> list[str]:
    salida = sql(f"{prefijo}\n{_FUNCIONES}" + ("rollback;\n" if prefijo else ""))
    return [ln[2:] for ln in salida.splitlines() if ln.startswith("F|")]


def test_ninguna_funcion_de_la_base_escribe_un_rol_muerto_ni_el_pin_de_users():
    assert _funciones() == []


@pytest.mark.parametrize("cuerpo", [
    "insert into public.users (salon_id, nombre, email, rol) values (null, 'x', 'x', 'estilista');",
    "update public.users set pin_fichaje_hash = 'h' where id = null;",
], ids=["un_rol_muerto", "el_pin_de_users"])
def test_CONTROL_una_funcion_nueva_que_lo_escribe_se_pone_roja(cuerpo):
    # plpgsql y no sql: desde la 0069 una función `sql` con «estilista» ni se
    # podría crear (el tipo ya no lo admite), y el control no probaría el escáner.
    prefijo = (f"begin;\ncreate function public.zz_escribe() returns void language plpgsql "
               f"as $$ begin {cuerpo} end $$;")
    assert _funciones(prefijo) == ["public.zz_escribe"]


def test_la_sesion_no_puede_escribir_users_rol():
    """Ver la cabecera. Leer el rol, sí: lo necesita el resto de la app."""
    salida = sql(
        "select privilege_type from information_schema.column_privileges "
        "where table_schema = 'public' and table_name = 'users' and column_name = 'rol' "
        "and grantee = 'authenticated' and privilege_type in ('INSERT', 'UPDATE') order by 1;"
    )
    assert salida.splitlines() == []
    lee = sql("select has_column_privilege('authenticated', 'public.users', 'rol', 'SELECT');")
    assert lee == "t"


def test_CONTROL_devolverle_el_permiso_se_pone_rojo():
    salida = sql(
        "begin;\n"
        "grant update (rol) on public.users to authenticated;\n"
        "select privilege_type from information_schema.column_privileges "
        "where table_schema = 'public' and table_name = 'users' and column_name = 'rol' "
        "and grantee = 'authenticated' and privilege_type in ('INSERT', 'UPDATE');\n"
        "rollback;\n"
    )
    assert "UPDATE" in salida.splitlines()
