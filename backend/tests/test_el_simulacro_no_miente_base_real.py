"""EL SIMULACRO NO MIENTE SOBRE LO QUE SE QUEDA (0084), contra la base de verdad.
Todo en transacciones que se deshacen.

«Se quedaría» decía «Nada» para un salón con una comisión y su anulación: el
paso 3 buscaba la cuenta solo en columnas llamadas `account_id`, `salon_id` o
`target_salon_id`, y las tablas de los referidos la nombran con otro nombre.

  1. LO QUE SE QUEDA SALE, sin listas: un ORÁCULO independiente recorre el
     esquema —toda columna uuid de toda tabla de `public`, por igualdad de tipo,
     no por texto— y cuenta las filas de la cuenta que el plan no se lleva. Toda
     tabla donde el oráculo encuentra algo tiene que salir en el paso 3 con ESE
     número. Una tabla nueva, con una columna que no se llama como ninguna, sale.
  2. NADA ATRIBUIDO A QUIEN NO ES: con dos salones que tienen comisiones, el
     de cada uno cuenta las suyas y no las del otro; uno sin nada, no enseña
     nada; y el simulacro nunca cuenta más que el oráculo.
  3. LO QUE NO SE PUEDE MIRAR, SE DICE: una tabla que no se deja leer (bloqueada
     por otra sesión) sale como «no_comprobada», y el panel la pinta como problema.
  CADA UNO CON SU CONTROL: el paso 3 de antes (por nombres), unas marcas que
  cazan de más.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.core.simulacro import clasificar
from tests.test_un_salon_no_puede_cambiarse_el_plan import _contenedor, sql

pytestmark = [pytest.mark.base_real]

REPO = Path(__file__).resolve().parents[2]

# Un referido; dos salones (A y B) atribuidos, con su cliente de Stripe y sus
# comisiones POR LAS FUNCIONES DE VERDAD; A con una anulación, un código que no
# valió y un cambio de %; C sin nada.
SEMBRAR = """
create temp table zz (k text primary key, v uuid) on commit drop;
do $$ declare r uuid; a uuid; b uuid; c uuid; begin
  update public.plataforma_config set comision_referido_pct = 10 where id;
  insert into public.referidos (nombre, email, codigo, estado)
    values ('zz Agencia', 'zz@simulacro.test', 'ZZSIM1', 'aprobado') returning id into r;
  insert into public.accounts (nombre, stripe_customer_id) values ('zz Salón A', 'cus_zzsima') returning id into a;
  insert into public.accounts (nombre, stripe_customer_id) values ('zz Salón B', 'cus_zzsimb') returning id into b;
  insert into public.accounts (nombre) values ('zz Salón C') returning id into c;
  insert into public.salons (account_id, nombre_comercial) values (a, 'zz Salón A'), (b, 'zz Salón B'), (c, 'zz Salón C');
  perform public.atribuir_alta(a, 'ZZNOEXISTE');
  perform public.atribuir_alta(a, 'ZZSIM1');
  perform public.atribuir_alta(b, 'ZZSIM1');
  perform public.devengar_comision('cus_zzsima', 'in_zza', 'inpay_zza', 'ch_zza', null, 'eur', 2900, 2900,
                                   clock_timestamp(), 'evt_zza');
  perform public.ajustar_comision('ch_zza', 2900, 0, clock_timestamp(), 'evt_zza2');
  perform public.devengar_comision('cus_zzsimb', 'in_zzb', 'inpay_zzb', 'ch_zzb', null, 'eur', 5000, 5000,
                                   clock_timestamp(), 'evt_zzb');
  perform public.cambiar_porcentaje_de_salones(r, array[a], 12, 'zz guarda del simulacro', gen_random_uuid(),
                                               'guarda@simulacro.test');
  insert into zz values ('A', a), ('B', b), ('C', c);
end $$;
"""

# EL ORÁCULO: otro método que el del simulacro. Recorre TODAS las tablas de
# `public` y, en cada columna uuid (o uuid[]), busca por IGUALDAD los ids de la
# cuenta —ella, sus centros, sus usuarios—, quitando lo que el plan se lleva.
ORACULO = """
create temp table zz_oraculo (cuenta text, tabla text, filas bigint) on commit drop;
do $$ declare k record; t record; pred text; plan jsonb; n bigint; ids uuid[]; begin
  for k in select z.k, z.v from zz z loop
    ids := array(select k.v union select s.id from public.salons s where s.account_id = k.v
                 union select u.id from public.users u where u.account_id = k.v);
    plan := (select coalesce(jsonb_object_agg(p.tabla, p.predicado), '{}') from public.plan_de_borrado(k.v) p);
    for t in
      select c.oid::regclass::text as tabla,
             string_agg(case when a.atttypid = 'uuid'::regtype then format('x.%I = any (%L::uuid[])', a.attname, ids)
                             else format('x.%I && %L::uuid[]', a.attname, ids) end, ' or ') as cond
      from pg_class c join pg_namespace s on s.oid = c.relnamespace
      join pg_attribute a on a.attrelid = c.oid and a.attnum > 0 and not a.attisdropped
      where s.nspname = 'public' and c.relkind = 'r' and a.atttypid in ('uuid'::regtype, 'uuid[]'::regtype)
      group by 1
    loop
      pred := t.cond;
      if plan ? ('public.' || t.tabla) then
        pred := format('(%s) and not coalesce((%s), false)', pred, plan ->> ('public.' || t.tabla));
      end if;
      execute format('select count(*) from public.%s x where %s', t.tabla, pred) into n;
      if n > 0 then insert into zz_oraculo values (k.k, 'public.' || t.tabla, n); end if;
    end loop;
  end loop;
end $$;
"""

LEER = """
select 'O|' || cuenta || '|' || tabla || '|' || filas from zz_oraculo;
select 'S|' || z.k || '|' || s.tabla || '|' || coalesce(s.filas::text, '') || '|' || s.accion
  from zz z, public.simulacro_de_borrado(z.v) s where s.paso = 3;
"""


def _leer(salida: str) -> tuple[dict, dict]:
    oraculo, sim = {}, {}
    for ln in salida.splitlines():
        if ln.startswith("O|"):
            _, k, t, n = ln.split("|")
            oraculo[(k, t)] = int(n)
        elif ln.startswith("S|"):
            _, k, t, n, accion = ln.split("|")
            sim[(k, t)] = (int(n) if n else None, accion)
    return oraculo, sim


def _rojos(oraculo: dict, sim: dict) -> list[str]:
    rojos = []
    for (k, t), n in oraculo.items():
        if (k, t) not in sim:
            rojos.append(f"{k}: {t} tiene {n} filas de la cuenta y el simulacro no las enseña")
        elif sim[(k, t)][0] is not None and sim[(k, t)][0] < n:
            rojos.append(f"{k}: {t} enseña {sim[(k, t)][0]} y son {n}")
    for (k, t), (n, accion) in sim.items():
        if accion == "no_comprobada":
            rojos.append(f"{k}: {t} no se ha podido comprobar")
        elif (k, t) in oraculo and n is not None and n > oraculo[(k, t)]:
            rojos.append(f"{k}: {t} enseña {n} y son {oraculo[(k, t)]}: le atribuye filas que no son suyas")
    return rojos


def _tirada(prefijo: str = "", extra: str = "") -> str:
    return sql(f"begin;\n{SEMBRAR}\n{prefijo}\n{ORACULO}\n{LEER}\n{extra}\nrollback;\n")


# ═══ 1 y 2. Lo que se queda sale, y solo lo suyo ════════════════════════════


def test_lo_que_se_queda_sale_con_sus_numeros_y_solo_lo_suyo():
    oraculo, sim = _leer(_tirada())
    # Antivacío y lo de las comisiones, con números: A una comisión y su
    # anulación; B una; C nada.
    assert sim.get(("A", "public.comisiones")) == (2, "queda"), sim
    assert sim.get(("B", "public.comisiones")) == (1, "queda"), sim
    for t in ("public.altas_atribuidas", "public.codigos_que_no_valieron", "public.cambios_de_porcentaje"):
        assert sim.get(("A", t)) == (1, "queda"), (t, sim)
    assert not [x for x in sim if x[0] == "C"], sim
    assert _rojos(oraculo, sim) == []


def test_una_tabla_nueva_con_un_nombre_de_columna_cualquiera_sale():
    nueva = ("create table public.zz_tabla_nueva (de_quien_era uuid, nota text);\n"
             "insert into public.zz_tabla_nueva select s.id, 'apunte' from public.salons s "
             "where s.account_id = (select v from zz where k = 'A');\n")
    oraculo, sim = _leer(_tirada(nueva))
    assert oraculo.get(("A", "public.zz_tabla_nueva")) == 1, oraculo  # el oráculo la ve
    assert sim.get(("A", "public.zz_tabla_nueva")) == (1, "queda"), sim
    assert _rojos(oraculo, sim) == []


def test_dice_por_que_columna_la_ha_encontrado():
    salida = _tirada(extra="select 'D|' || s.tabla || '|' || s.detalle from public.simulacro_de_borrado("
                           "(select v from zz where k = 'A')) s where s.paso = 3;")
    detalles = dict(ln.split("|")[1:] for ln in salida.splitlines() if ln.startswith("D|"))
    assert "(por cuenta_id)" in detalles["public.comisiones"], detalles


# — Controles —


def _paso_3_de_la_0083() -> str:
    """El simulacro de la 0083 tal cual: el paso 3 por nombres de columna."""
    texto = (REPO / "supabase/migrations/0083_el_borrado_no_deja_nada_en_auth.sql").read_text(encoding="utf-8")
    i = texto.index("create or replace function public.simulacro_de_borrado")
    return texto[i:texto.index("$$;", texto.index("as $$", i)) + 3]


def test_CONTROL_el_paso_3_por_nombres_se_pone_rojo():
    oraculo, sim = _leer(_tirada(_paso_3_de_la_0083()))
    rojos = _rojos(oraculo, sim)
    assert "A: public.comisiones tiene 2 filas de la cuenta y el simulacro no las enseña" in rojos, rojos
    assert "A: public.altas_atribuidas tiene 1 filas de la cuenta y el simulacro no las enseña" in rojos, rojos


def test_CONTROL_una_tabla_nueva_con_el_paso_3_por_nombres_se_pone_roja():
    nueva = ("create table public.zz_tabla_nueva (de_quien_era uuid);\n"
             "insert into public.zz_tabla_nueva select v from zz where k = 'A';\n")
    oraculo, sim = _leer(_tirada(_paso_3_de_la_0083() + "\n" + nueva))
    assert "A: public.zz_tabla_nueva tiene 1 filas de la cuenta y el simulacro no las enseña" in _rojos(oraculo, sim)


def test_CONTROL_unas_marcas_que_cazan_de_mas_se_ponen_rojas():
    """Las marcas de A incluyen, por error, las de B: A enseña comisiones de B."""
    de_mas = """
do $$ declare d text; begin
  d := pg_get_functiondef('public.marcas_de_la_cuenta(uuid)'::regprocedure);
  execute replace(d, 'union select u.id::text from public.users u where u.account_id = p_cuenta',
                  'union select u.id::text from public.users u where u.account_id = p_cuenta '
                  'union select a.id::text from public.accounts a where a.nombre = ''zz Salón B''');
end $$;
"""
    oraculo, sim = _leer(_tirada(de_mas))
    assert "A: public.comisiones enseña 3 y son 2: le atribuye filas que no son suyas" in _rojos(oraculo, sim)


# ═══ 3. Lo que no se puede mirar, se dice ═══════════════════════════════════


def test_una_tabla_que_no_se_puede_mirar_sale_como_no_comprobada_y_es_un_problema():
    """Una tabla que no se deja leer —aquí, bloqueada por otra sesión, como la
    dejaría una migración a medias— no puede salir como «nada»."""
    sql("create table if not exists public.zz_bloqueada (nota text);")
    otra = subprocess.Popen(
        ["docker", "exec", "-i", _contenedor(), "psql", "-U", "postgres", "-qAt", "-v", "ON_ERROR_STOP=1"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        otra.stdin.write("begin;\nlock table public.zz_bloqueada in access exclusive mode;\nselect 'BLOQUEADA';\n")
        otra.stdin.flush()
        assert otra.stdout.readline().strip() == "BLOQUEADA"
        salida = sql(f"begin;\n{SEMBRAR}\nset local lock_timeout = '300ms';\n"
                     "select 'N|' || s.paso || '|' || s.tabla || '|' || coalesce(s.filas::text, '') || '|' || s.accion "
                     "|| '|' || s.detalle from public.simulacro_de_borrado((select v from zz where k = 'C')) s;\n"
                     "rollback;\n")
    finally:
        otra.stdin.write("rollback;\n")
        otra.stdin.close()
        otra.wait(timeout=30)
        sql("drop table if exists public.zz_bloqueada;")
    filas = [ln.split("|", 5)[1:] for ln in salida.splitlines() if ln.startswith("N|")]
    mia = [f for f in filas if f[1] == "public.zz_bloqueada"]
    assert mia and mia[0][3] == "no_comprobada" and "lock timeout" in mia[0][4], filas
    # Y en el panel es un problema, no un «Nada».
    sim = clasificar([{"paso": int(p), "tabla": t, "filas": int(n) if n else None, "accion": a, "detalle": d}
                      for p, t, n, a, d in filas])
    assert [f.tabla for f in sim.problemas] == ["public.zz_bloqueada"], sim.problemas


def test_una_columna_llamada_x_no_impide_mirar_la_tabla():
    """El alias de la fila no puede chocar con una columna (pasó escribiendo la 0084)."""
    salida = _tirada("create table public.zz_con_x (x uuid);\n"
                     "insert into public.zz_con_x select v from zz where k = 'A';\n")
    oraculo, sim = _leer(salida)
    assert sim.get(("A", "public.zz_con_x")) == (1, "queda"), sim
