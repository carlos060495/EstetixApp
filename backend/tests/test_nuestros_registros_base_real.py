"""NUESTROS REGISTROS NO CUELGAN DE LO QUE SE BORRA (0066), contra la base de verdad.

04-10-2026: los registros de dinero y de descargas, la auditoría de
soporte y el registro del panel SE QUEDAN cuando se borra un salón. (La cuenta y
su suscripción se quedaban hasta la 0068; ahora se van con el borrado.) El simulacro (0065) enseñó que la suscripción y la auditoría de
soporte caían en cascada desde `salons`.

EL GUARDA lee del esquema TODAS las claves ajenas de esas tablas y se pone rojo
si alguna apunta a algo que no sea también nuestro (otra tabla que se queda, o
los administradores de la plataforma): con CASCADE se borrarían con el salón;
con SET NULL perderían de quién eran. La lista de lo que se queda es la misma
que usa el simulacro (`core/simulacro.SE_QUEDA`).

SUS CONTROLES, dentro de transacciones que se deshacen: volver a colgar del
salón la suscripción, las visitas de soporte o el creador de un código.
"""

from __future__ import annotations

import pytest

from app.core.simulacro import SE_QUEDA
from tests.test_camino_humano_dos_centros import (  # noqa: F401  (fixtures)
    anyio_backend,
    dos_centros,
)
from tests.test_un_salon_no_puede_cambiarse_el_plan import sql

pytestmark = [pytest.mark.base_real, pytest.mark.anyio]

# Lo que es NUESTRO y puede ser apuntado sin riesgo: lo que se queda, y los
# administradores de la plataforma (no son de ningún salón).
NUESTRO = set(SE_QUEDA) | {"public.admin_users"}

_CLAVES = """
select k.conrelid::regclass::text || '|' || k.conname || '|' || k.confrelid::regclass::text || '|' || k.confdeltype::text
from pg_constraint k
where k.contype = 'f' and k.conrelid::regclass::text = any(string_to_array('{tablas}', ','))
order by 1;
"""


def _con_public(t: str) -> str:
    return t if "." in t else f"public.{t}"


def _claves(salida: str) -> list[tuple[str, str, str, str]]:
    filas = []
    for linea in salida.splitlines():
        if linea.count("|") == 3:
            tabla, nombre, destino, tipo = linea.split("|")
            filas.append((_con_public(tabla), nombre, _con_public(destino), tipo))
    return filas


def _mal(claves) -> list[str]:
    return [f"{t}.{n} → {d} ({tipo})" for t, n, d, tipo in claves if d not in NUESTRO]


def _consulta() -> str:
    return _CLAVES.format(tablas=",".join(t.removeprefix("public.") for t in SE_QUEDA))


async def test_lo_que_se_queda_no_apunta_a_nada_que_se_borre():
    claves = _claves(sql(_consulta()))
    # Antivacío: si no viera las claves que SÍ existen, el verde no valdría nada.
    assert ("public.impersonation_sessions", "impersonation_sessions_admin_id_fkey", "public.admin_users", "a") in claves
    assert _mal(claves) == []


@pytest.mark.parametrize("control", [
    ("decisiones_de_dinero", "add constraint zz foreign key (account_id) references public.accounts(id) on delete cascade not valid"),
    ("impersonation_sessions", "add constraint zz foreign key (salon_id) references public.salons(id) on delete cascade"),
    ("support_access_codes", "add constraint zz foreign key (created_by_user_id) references public.users(id) on delete set null"),
], ids=["dinero_de_la_cuenta", "visitas_del_salon", "creador_del_codigo"])
async def test_CONTROL_volver_a_colgarlo_se_pone_rojo(control):
    tabla, ddl = control
    salida = sql(f"begin;\nalter table public.{tabla} {ddl};\n{_consulta()}rollback;\n")
    assert any(m.startswith(f"public.{tabla}.zz") for m in _mal(_claves(salida))), salida


async def test_el_simulacro_de_una_cuenta_ya_no_borra_nada_de_lo_nuestro(dos_centros):
    salida = sql(
        f"select tabla from public.simulacro_de_borrado('{dos_centros['account_id']}') where paso = 1;"
    ).splitlines()
    assert {_con_public(t) for t in salida} & set(SE_QUEDA) == set(), salida
    # Y la ficha de la cliente sí se va (0068): la cuenta, su suscripción y su PIN.
    assert {"public.accounts", "public.subscriptions", "public.pin_gestion"} <= set(salida), salida


async def test_los_registros_guardan_los_nombres(dos_centros):
    centro = sql(f"select nombre_comercial from public.salons where id = '{dos_centros['salon_id']}';")
    salida = sql(
        "begin;\n"
        "insert into public.support_access_codes (salon_id, code_hash, created_by_user_id, expires_at) "
        f"values ('{dos_centros['salon_id']}', 'zz-nombres', '{dos_centros['id']}', now() + interval '1 hour');\n"
        "select centro_nombre || '|' || coalesce(creado_por_nombre, '') || '|' || (creado_por_email is not null) "
        "from public.support_access_codes where code_hash = 'zz-nombres';\n"
        "rollback;\n"
    )
    assert f"{centro}|Dueña de Dos Centros|true" in salida, salida
