"""Consultas que pueden chocar con el tope de mil filas. Ver tests/consultas_con_tope.py.

Cada consulta sin `limit` ni clave única tiene que estar resuelta (14-10-2026):
  · o se lee entera (`select_todo`, `count`) porque crece con el tiempo o con
    el tamaño del salón —esas ya no las encuentra el censo—;
  · o está en `consultas_con_tope_pendientes.json` como LÍMITE NATURAL, con su
    motivo escrito al lado («el equipo de un solo salón»).
El test falla si aparece una NUEVA sin resolver, si una apuntada desaparece
(quítala de la lista) y si alguna apuntada no dice por qué.

Los controles van primero: un censo que no viera ninguna consulta diría «todo
bien» sin mirar nada.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from tests.consultas_con_tope import (
    censo_del_repositorio,
    claves_unicas,
    consultas_sin_tope,
)

PENDIENTES = Path(__file__).with_name("consultas_con_tope_pendientes.json")

_MIGRACION = """
create table public.citas (
  id uuid primary key default gen_random_uuid(),
  salon_id uuid not null,
  a text,
  b text,
  codigo text unique,
  unique (a, b)
);
create table public.otras (id uuid, x text, y text);
create unique index otras_x on public.otras (x);
create unique index otras_y_parcial on public.otras (y) where x is not null;
"""

_CODIGO = '''
async def todas(db, salon):
    return await db.select("citas", {"select": "id", "salon_id": f"eq.{salon}"})

async def con_limite(db):
    return await db.select("citas", {"select": "id", "limit": "50"})

async def una(db, i):
    return await db.select("citas", {"select": "id", "id": f"eq.{i}"})

async def por_codigo(db, c):
    return await db.select("citas", {"select": "id", "codigo": f"eq.{c}"})

async def media_clave(db, a):
    return await db.select("citas", {"select": "id", "a": f"eq.{a}"})

async def clave_entera(db, a, b):
    return await db.select("citas", {"select": "id", "a": f"eq.{a}", "b": f"eq.{b}"})

async def por_lista(db, ids):
    return await db.select("citas", {"select": "id", "id": f"in.({ids})"})

async def indice_unico(db, x):
    return await db.select("otras", {"select": "id", "x": f"eq.{x}"})

async def indice_parcial(db, y):
    return await db.select("otras", {"select": "id", "y": f"eq.{y}"})

async def paginando(db, salon):
    return await db.select_todo("citas", {"select": "id", "salon_id": f"eq.{salon}", "order": "id.asc"})

async def contando(db, salon):
    return await db.count("citas", {"salon_id": f"eq.{salon}"})

async def params_en_variable(db, salon):
    params = {"select": "id", "salon_id": f"eq.{salon}"}
    return await db.select("citas", params)
'''


def _de_mentira() -> set[str]:
    claves = claves_unicas([("0001.sql", _MIGRACION)])
    consultas, _ = consultas_sin_tope([("app/mentira.py", _CODIGO)], {"citas", "otras"}, claves)
    return {c.funcion for c in consultas}


def test_CONTROL_encuentra_las_que_pueden_pasar_de_mil():
    assert _de_mentira() == {"todas", "media_clave", "indice_parcial", "params_en_variable"}


def test_CONTROL_lee_las_claves_unicas_de_las_migraciones():
    claves = claves_unicas([("0001.sql", _MIGRACION)])
    assert set(map(frozenset, ({"id"}, {"codigo"}, {"a", "b"}))) <= set(claves["citas"])
    assert frozenset({"x"}) in claves["otras"] and frozenset({"y"}) not in claves["otras"]


def test_CONTROL_ve_consultas_de_verdad():
    consultas, sin_leer = censo_del_repositorio()
    tablas = {c.tabla for c in consultas}
    # Las de `appointments` ya se leen enteras: el antivacío mira tablas que siguen en el censo.
    assert len(consultas) > 30 and {"employees", "time_entries", "salons"} <= tablas
    # Lo que no sabe leer, a la vista y sin crecer sin que nadie lo decida.
    assert len(sin_leer) <= 10, "\n".join(sin_leer)


def test_estadisticas_ya_no_suma_sobre_un_select_con_tope():
    consultas, _ = censo_del_repositorio()
    en_stats = {c.tabla for c in consultas if c.donde.startswith("app/routers/stats.py")}
    assert not ({"appointments", "incomes", "expenses"} & en_stats), en_stats


def _limites_naturales() -> list[dict]:
    return json.loads(PENDIENTES.read_text(encoding="utf-8"))["limite_natural"]


def test_cada_limite_natural_dice_por_que():
    """El motivo es lo que hace de la lista una decisión y no un cajón. Uno
    que no nombre de qué se trata («un solo salón», «UNA cuenta»…) no lo es."""
    sin = [e["consulta"] for e in _limites_naturales() if len((e.get("motivo") or "").split()) < 4]
    assert not sin, f"Límites naturales sin motivo (o con uno de menos de cuatro palabras): {sin}"


def test_consultas_que_pueden_chocar_con_el_tope():
    consultas, _ = censo_del_repositorio()
    hoy = Counter(c.clave for c in consultas)
    apuntadas = Counter(e["consulta"] for e in _limites_naturales())
    nuevas = sorted((hoy - apuntadas).elements())
    arregladas = sorted((apuntadas - hoy).elements())
    assert not nuevas, (
        f"Consultas nuevas que pueden devolver mil filas en silencio: {nuevas}. "
        "Ponles `limit`, filtra por una clave única o usa `count`/`select_todo`; si de verdad no pueden "
        "crecer, apúntalas como límite natural CON SU MOTIVO."
    )
    assert not arregladas, f"Ya no están: quítalas de {PENDIENTES.name}: {arregladas}"
