"""EL SALÓN DE MUESTRA (0080), contra la base de verdad. Todo en transacciones
que se deshacen: al acabar, la base está como estaba.

Los guardas (07-10-2026) que viven en la base, cada uno con su
CONTROL —la base rota a propósito dentro de la transacción— que sale rojo:

  1. VOLVER ES VOLVER A LA MISMA: el mismo navegador recibe su demo viva; otro
     navegador, otra; caducada, el mismo navegador recibe una nueva.
  2. LOS TOPES (vivas a la vez y nuevas por hora) se cumplen: pasado uno, no
     se crea nada y se dice cuál.
  3. UNA DEMO NO ES UN CLIENTE: ni suscripción, ni atribución, ni comisión,
     por ningún camino (ni el alta con un código bueno).
  4. EL BARRIDO NACE APAGADO, su ENSAYO no borra nada, y RESPETA SU TOPE (y deja
     constancia de lo que borró).
  5. EL BARRIDO NO ALCANZA UN SALÓN DE VERDAD NI AUNQUE SE LE PIDA: ni pidiéndole
     una cuenta de verdad, ni colándola en `demos` como caducada.
  6. UNA DEMO NO LEE DATOS DE OTRO SALÓN: se recorren TODAS las tablas y vistas
     que una sesión de salón puede leer (de `pg_class`, sin lista), y lo que ve
     la demo tiene que ser suyo según el MISMO plan con el que se borra una
     cuenta (`plan_de_borrado`, 0068).
  7. Una demo caducada no consigue token (el gancho), y `es_demo` no cambia.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.test_comisiones_base_real import _LEER_R, _intento, _lineas, _r
from tests.test_un_salon_no_puede_cambiarse_el_plan import sql

pytestmark = [pytest.mark.base_real]

M0080 = Path(__file__).resolve().parents[2] / "supabase/migrations/0080_el_salon_de_muestra.sql"
H1, H2 = "'" + "a1" * 32 + "'", "'" + "b2" * 32 + "'"
CORREO = "'demo-prueba@demo.estetixapp.invalid'"


def _crear(h: str, correo: str = CORREO) -> str:
    return f"(select resultado || '|' || coalesce(account_id::text, '') from public.crear_demo({h}, {correo}))"


def _en_transaccion(cuerpo: str, roto: str = "") -> str:
    return sql(f"begin;\ncreate temp table zz (k text primary key, v text) on commit drop;\n{roto}\n{cuerpo}\nrollback;\n")


def _funcion(nombre: str, *cambios: tuple[str, str]) -> str:
    texto = M0080.read_text(encoding="utf-8")
    m = re.search(rf"create (or replace )?function public\.{nombre}\(.*?\n(\$\$|\$function\$);\n", texto, re.S)
    assert m, nombre
    cuerpo = re.sub(r"^create (or replace )?function", "create or replace function", m.group(0))
    for antes, despues in cambios:
        assert cuerpo.count(antes) == 1, (nombre, antes)
        cuerpo = cuerpo.replace(antes, despues)
    return cuerpo


# Envejecer una demo: nació hace 25 horas y caducó hace una.
def _envejecer(cuenta_sql: str, horas: int = 25) -> str:
    return (f"update public.demos set creada_at = now() - interval '{horas} hours', "
            f"caduca_at = now() - interval '{horas - 24} hours' where account_id = {cuenta_sql};\n")


# ═══ 1. Volver es volver a la misma ═══════════════════════════════════════

def test_volver_es_volver_a_la_misma_y_caducada_se_crea_otra():
    salida = _en_transaccion(f"""
insert into zz values ('primera', {_crear(H1)});
insert into zz values ('vuelve', {_crear(H1)});
insert into zz values ('otro_navegador', {_crear(H2)});
{_envejecer("split_part((select v from zz where k = 'primera'), '|', 2)::uuid")}
insert into zz values ('tras_caducar', {_crear(H1)});
select 'Z|' || k || '|' || v from zz order by k;
select 'N|' || count(*) from public.demos where navegador_hash = {H1};
""")
    z = dict(ln.split("|", 1) for ln in _lineas(salida, "Z"))
    primera, vuelve, otro, nueva = (z[k].split("|") for k in ("primera", "vuelve", "otro_navegador", "tras_caducar"))
    assert primera[0] == "creada" and vuelve == ["ya_tiene", primera[1]]
    assert otro[0] == "creada" and otro[1] != primera[1]
    assert nueva[0] == "creada" and nueva[1] != primera[1]
    assert _lineas(salida, "N") == ["2"]  # la caducada sigue ahí hasta el barrido


def test_una_demo_nace_con_datos_y_sin_nada_que_salga():
    salida = _en_transaccion(f"""
insert into zz values ('d', {_crear(H1)});
with c as (select split_part(v, '|', 2)::uuid as id from zz where k = 'd'),
     s as (select s.* from public.salons s, c where s.account_id = c.id)
select 'R|' || (select count(*) from public.clients where salon_id in (select id from s)) || '|'
  || (select count(*) from public.appointments where salon_id in (select id from s)) || '|'
  || (select count(*) from public.employees where salon_id in (select id from s)) || '|'
  || (select count(*) from public.products where salon_id in (select id from s)) || '|'
  || (select count(*) from public.incomes where salon_id in (select id from s)) || '|'
  || (select bool_or(s.recordatorios_email_activo or s.reservas_online_activo) from s) || '|'
  || (select es_demo from public.accounts a, c where a.id = c.id);
""")
    # LOS CONTACTOS NO SE MIRAN AQUÍ (29-10-2026): esto es solo la siembra, y el
    # backend completa la demo después (teléfono y conversaciones). Mirarlos aquí
    # seguía en verde mientras cambiaba lo que se ve. Los vigila, sobre la demo
    # TERMINADA y con la lista de lo permitido, `test_la_demo_terminada_solo_tiene_
    # contactos_inventados` (test_el_salon_de_muestra.py).
    clientas, citas, empleadas, productos, caja, sale_algo, es_demo = _lineas(salida, "R")[0].split("|")
    assert int(clientas) >= 10 and int(citas) >= 50 and int(empleadas) == 3 and int(productos) >= 5 and int(caja) >= 3
    assert sale_algo == "false" and es_demo == "true"


# ═══ 2. Los topes ═════════════════════════════════════════════════════════

def test_los_topes_se_cumplen_y_se_dice_cual():
    salida = _en_transaccion(f"""
delete from public.demos;  -- solo dentro de esta transacción
update public.topes_de_demos set vivas_max = 1, por_hora_max = 5 where unico;
insert into zz values ('una', {_crear(H1)});
insert into zz values ('vivas', {_crear(H2)});
update public.topes_de_demos set vivas_max = 50, por_hora_max = 1 where unico;
insert into zz values ('hora', {_crear(H2)});
select 'Z|' || k || '|' || split_part(v, '|', 1) from zz order by k;
""", roto="set local session_replication_role = replica;")
    z = dict(ln.split("|", 1) for ln in _lineas(salida, "Z"))
    assert z == {"una": "creada", "vivas": "sin_sitio_vivas", "hora": "sin_sitio_hora"}


def test_el_freno_por_ip_no_crea_pero_deja_volver_a_la_viva():
    """`p_puede_crear = false` (lo decide el backend con la cifra de la base):
    no se crea ninguna, pero quien ya tiene una viva vuelve a ella."""
    salida = _en_transaccion(f"""
insert into zz values ('viva', {_crear(H1)});
insert into zz values ('vuelve_frenada', (select resultado || '|' || coalesce(account_id::text, '')
  from public.crear_demo({H1}, {CORREO}, false)));
insert into zz values ('nueva_frenada', (select resultado || '|' || coalesce(account_id::text, '')
  from public.crear_demo({H2}, {CORREO}, false)));
select 'Z|' || k || '|' || split_part(v, '|', 1) from zz order by k;
select 'N|' || count(*) from public.demos where navegador_hash = {H2};
""")
    z = dict(ln.split("|", 1) for ln in _lineas(salida, "Z"))
    assert z == {"viva": "creada", "vuelve_frenada": "ya_tiene", "nueva_frenada": "sin_sitio_ip"}
    assert _lineas(salida, "N") == ["0"]


def test_CONTROL_sin_el_freno_la_ip_frenada_crearia_otra():
    roto = _funcion("crear_demo", ("if not coalesce(p_puede_crear, false) then", "if false then"))
    salida = _en_transaccion(f"""
insert into zz values ('nueva_frenada', (select resultado from public.crear_demo({H2}, {CORREO}, false)));
select 'Z|' || v from zz;""", roto)
    assert _lineas(salida, "Z") == ["creada"]


def test_los_topes_los_cambia_el_owner_con_motivo_y_queda_registrado():
    salida = _en_transaccion(f"""
select 'R|cambio|' || resultado || ':' || antes || '>' || despues
  from public.cambiar_tope_de_demos('vivas_max', 120, 'Una feria: vienen muchos a la vez', gen_random_uuid(), 'o@x.es');
select 'R|cambio_ip|' || resultado || ':' || antes || '>' || despues
  from public.cambiar_tope_de_demos('por_ip_hora_max', 6, 'Una feria: toda la sala comparte la wifi', gen_random_uuid(), 'o@x.es');
select 'R|registro|' || count(*) from public.cambios_de_tope_de_demos where despues = 120;
{_intento('sin_motivo', "perform public.cambiar_tope_de_demos('vivas_max', 7, 'corto', gen_random_uuid(), 'o@x.es')")}
{_intento('borrar_registro', 'delete from public.cambios_de_tope_de_demos')}
{_intento('salon_lee', 'set local role authenticated; perform 1 from public.topes_de_demos')}
{_LEER_R}""")
    r = _r(salida)
    assert r["cambio"].startswith("cambiado:") and r["cambio"].endswith(">120") and r["registro"] == "1"
    assert r["cambio_ip"] == "cambiado:3>6"
    assert r["sin_motivo"] == "RECHAZADA:22023" and r["borrar_registro"] == r["salon_lee"] == "RECHAZADA:42501"


# ═══ 3. Una demo no es un cliente ═════════════════════════════════════════

_NO_CLIENTE = {
    "suscripcion": "insert into public.subscriptions (salon_id, account_id, estado, plan) values "
                   "((select id from public.salons where account_id = (select v::uuid from zz where k = 'c')), "
                   "(select v::uuid from zz where k = 'c'), 'active', 'pro')",
    "atribucion_en_el_alta": "perform public.atribuir_alta((select v::uuid from zz where k = 'c'), 'ZZD001')",
    "atribucion_a_mano": "insert into public.altas_atribuidas (referido_id, cuenta_id, cuenta_nombre, codigo_llegado, pct, "
                         "pct_de_donde) values ((select id from public.referidos where codigo = 'ZZD001'), "
                         "(select v::uuid from zz where k = 'c'), 'x', 'ZZD001', 10, 'global')",
    # Una comisión bien formada, de un cobro de verdad: solo la para la demo.
    "comision": "insert into public.comisiones (referido_id, cuenta_id, cuenta_nombre, tipo, motivo, stripe_cobro, "
                "stripe_factura, stripe_pago, moneda, importe_cobrado_centimos, base_centimos, pct, comision_centimos, "
                "ocurrido_at) values ((select id from public.referidos where codigo = 'ZZD001'), "
                "(select v::uuid from zz where k = 'c'), 'x', 'devengo', 'cobro', 'ch_zzdemo1', 'in_zzdemo1', "
                "'inpay_zzdemo1', 'eur', 1000, 1000, 10, 100, now())",
}


def _no_cliente(roto: str = "") -> dict[str, str]:
    cuerpo = (f"insert into zz values ('c', split_part({_crear(H1)}, '|', 2));\n"
              "insert into public.referidos (nombre, email, codigo, estado, aprobado_at) "
              "values ('zz Vendedora', 'zz.v@demo.test', 'ZZD001', 'aprobado', now());\n"
              + "".join(_intento(k, s) for k, s in _NO_CLIENTE.items())
              + "select 'R|atribuidas|' || count(*) from public.altas_atribuidas "
                "where cuenta_id = (select v::uuid from zz where k = 'c');\n" + _LEER_R)
    return _r(_en_transaccion(cuerpo, roto))


def test_una_demo_no_genera_suscripcion_ni_atribucion_ni_comision():
    r = _no_cliente()
    assert {k: r[k] for k in _NO_CLIENTE} == {k: "RECHAZADA:42501" for k in _NO_CLIENTE}
    assert r["atribuidas"] == "0"


def test_CONTROL_sin_los_triggers_la_demo_seria_un_cliente():
    roto = ("drop trigger subscriptions_una_demo_no_es_un_cliente on public.subscriptions;\n"
            "drop trigger altas_atribuidas_una_demo_no_es_un_cliente on public.altas_atribuidas;\n"
            "drop trigger comisiones_una_demo_no_es_un_cliente on public.comisiones;\n")
    r = _no_cliente(roto)
    assert r["suscripcion"] == r["atribucion_a_mano"] == r["comision"] == "PASA"


# ═══ 4. El barrido: nace apagado, ensayo sin borrar, tope ═════════════════

def _cuatro_caducadas() -> str:
    hs = [f"'{c * 64}'" for c in "cdef"]
    return "".join(f"insert into zz values ('{i}', split_part({_crear(h)}, '|', 2));\n"
                   f"{_envejecer(f'(select v::uuid from zz where k = {chr(39)}{i}{chr(39)})', 25 + i)}"
                   for i, h in enumerate(hs))


def test_el_barrido_nace_apagado_y_el_ensayo_no_borra_nada():
    salida = _en_transaccion(f"""
{_cuatro_caducadas()}
select 'R|interruptor|' || encendido from public.barrido_demos_interruptor;
select 'R|ensayo|' || count(*) from public.ensayo_del_barrido() e where e.account_id in (select v::uuid from zz);
{_intento('barrer', 'perform public.barrer_demos()')}
select 'R|siguen|' || count(*) from public.accounts where id in (select v::uuid from zz);
{_LEER_R}""")
    r = _r(salida)
    assert r == {"interruptor": "false", "ensayo": "4", "barrer": "RECHAZADA:55000", "siguen": "4"}


_ENCENDER = ("update public.barrido_demos_interruptor set encendido = true, tope_por_pasada = 2, "
             "cambiado_por = 'test', motivo = 'probar el tope';\n")


def _barrer(roto: str = "") -> dict[str, str]:
    salida = _en_transaccion(f"""
{_cuatro_caducadas()}
{_ENCENDER}
select 'R|barrido|' || borradas || ':' || quedan from public.barrer_demos();
select 'R|siguen|' || count(*) from public.accounts where id in (select v::uuid from zz);
select 'R|las_viejas_fuera|' || not exists (select 1 from public.accounts where id in
  (select v::uuid from zz where k in ('2', '3')));
select 'R|constancia|' || borradas || ':' || quedan || ':' || cardinality(cuentas)
  from public.barridos_de_demos order by id desc limit 1;
select 'R|historial|' || count(*) from public.barrido_demos_interruptor_historial where motivo = 'probar el tope';
{_LEER_R if "zz_r" in roto else ""}""", roto)
    return _r(salida)


def test_el_barrido_respeta_su_tope_borra_las_mas_viejas_y_deja_constancia():
    # Dentro de la transacción, sin otras caducadas que las cuatro de aquí.
    r = _barrer("delete from public.demos where caduca_at <= now();")
    assert r == {"barrido": "2:2", "siguen": "2", "las_viejas_fuera": "true", "constancia": "2:2:2", "historial": "1"}


def test_CONTROL_un_barrido_sin_tope_se_pone_rojo():
    roto = ("delete from public.demos where caduca_at <= now();\n"
            + _funcion("barrer_demos", ("limit v_inter.tope_por_pasada", "")))
    assert _barrer(roto)["barrido"] == "4:0"


def test_CONTROL_un_barrido_que_no_mira_el_interruptor_se_pone_rojo():
    roto = _funcion("barrer_demos", ("if not coalesce(v_inter.encendido, false) then", "if false then"))
    salida = _en_transaccion(f"{_cuatro_caducadas()}\n{_intento('barrer', 'perform public.barrer_demos()')}{_LEER_R}",
                             roto)
    assert _r(salida)["barrer"] == "PASA"


# ═══ 5. El barrido no alcanza un salón de verdad ══════════════════════════

_REAL = "(select a.id from public.accounts a where not a.es_demo order by a.created_at limit 1)"


def _contra_uno_de_verdad(roto: str = "") -> dict[str, str]:
    salida = _en_transaccion(f"""
insert into zz values ('real', {_REAL});
{_intento('pedirselo', "perform public.borrar_una_demo((select v::uuid from zz where k = 'real'))")}
-- Colarlo en `demos` como si fuera una caducada, y encender.
insert into public.demos (account_id, navegador_hash, creada_at, caduca_at)
  values ((select v::uuid from zz where k = 'real'), {H2}, now() - interval '30 hours', now() - interval '6 hours');
{_ENCENDER}
select 'R|colado|' || borradas from public.barrer_demos();
select 'R|sigue|' || exists (select 1 from public.accounts where id = (select v::uuid from zz where k = 'real'));
select 'R|centros|' || count(*) from public.salons where account_id = (select v::uuid from zz where k = 'real');
{_LEER_R}""", roto)
    return _r(salida)


def test_el_barrido_no_alcanza_un_salon_de_verdad_ni_aunque_se_le_pida():
    r = _contra_uno_de_verdad()
    assert r["pedirselo"] == "RECHAZADA:42501"
    assert r["sigue"] == "true" and int(r["centros"]) >= 1


def test_CONTROL_sin_sus_dos_comprobaciones_el_barrido_borraria_uno_de_verdad():
    roto = (_funcion("borrar_una_demo", ("where a.id = p_cuenta and a.es_demo for update", "where a.id = p_cuenta for update"))
            + _funcion("demos_caducadas", ("join public.accounts a on a.id = d.account_id and a.es_demo",
                                           "join public.accounts a on a.id = d.account_id")))
    r = _contra_uno_de_verdad(roto)
    assert r["sigue"] == "false"


# ═══ 6. Una demo no lee datos de otro salón ═══════════════════════════════

# Todo lo que una sesión de salón puede leer (tablas y vistas de `public`), y,
# para cada cosa, qué es de la demo: el predicado del plan de borrado de SU
# cuenta, o su centro/cuenta si la tabla no está en el plan. Lo que ve tiene
# que ser todo suyo.
_AISLAMIENTO = r"""
insert into auth.users (id, instance_id, aud, role, email)
select '40000000-0000-0000-0000-0000000000d1', u.instance_id, u.aud, u.role, 'demo-aisla@demo.estetixapp.invalid'
from auth.users u limit 1;
insert into zz values ('c', split_part((select resultado || '|' || account_id
  from public.crear_demo(repeat('9', 64), 'demo-aisla@demo.estetixapp.invalid')), '|', 2));
select public.enlazar_demo((select v::uuid from zz where k = 'c'), '40000000-0000-0000-0000-0000000000d1');
create temp table zz_plan as select tabla, predicado from public.plan_de_borrado((select v::uuid from zz where k = 'c'));
create temp table zz_salones as select id from public.salons where account_id = (select v::uuid from zz where k = 'c');
create temp table zz_cosas as
  select format('%I.%I', n.nspname, c.relname) as tabla,
         coalesce((select p.predicado from zz_plan p where p.tabla = format('%I.%I', n.nspname, c.relname)),
                  case when exists (select 1 from pg_attribute a where a.attrelid = c.oid and a.attname = 'salon_id' and not a.attisdropped)
                         then format('salon_id in (%s)', (select coalesce(string_agg(quote_literal(id), ','), 'null') from zz_salones))
                       when exists (select 1 from pg_attribute a where a.attrelid = c.oid and a.attname = 'account_id' and not a.attisdropped)
                         then format('account_id = %L', (select v from zz where k = 'c'))
                       else 'false' end) as predicado
  from pg_class c join pg_namespace n on n.oid = c.relnamespace
  where n.nspname = 'public' and c.relkind in ('r', 'v', 'm')
    and has_any_column_privilege('authenticated', c.oid, 'SELECT');
grant select on zz_cosas to authenticated;
create temp table zz_vistas (tabla text, ve bigint, suyas bigint);
grant insert on zz_vistas to authenticated;
select set_config('request.jwt.claims', json_build_object('sub', '40000000-0000-0000-0000-0000000000d1',
  'salon_id', (select id from zz_salones limit 1), 'rol', 'admin', 'role', 'authenticated')::text, true);
set local role authenticated;
do $$ declare r record; ve bigint; suyas bigint; begin
  for r in select * from zz_cosas loop
    begin
      execute format('select count(*) from %s', r.tabla) into ve;
      execute format('select count(*) from %s where %s', r.tabla, r.predicado) into suyas;
    exception when insufficient_privilege then ve := 0; suyas := 0;
    end;
    insert into zz_vistas values (r.tabla, ve, suyas);
  end loop;
end $$;
reset role;
select 'V|' || tabla || '|' || ve || '|' || suyas from zz_vistas order by tabla;
"""


def _aislamiento(roto: str = "") -> dict[str, tuple[int, int]]:
    salida = _en_transaccion(_AISLAMIENTO, roto)
    return {t: (int(v), int(s)) for t, v, s in (ln.split("|") for ln in _lineas(salida, "V"))}


def test_una_demo_no_lee_ni_una_fila_de_otro_salon():
    vistas = _aislamiento()
    ajenas = {t: v - s for t, (v, s) in vistas.items() if v != s}
    assert ajenas == {}, f"La demo ve filas que no son suyas: {ajenas}"
    # Antivacío: se recorrieron muchas cosas, y la demo SÍ ve lo suyo.
    assert len(vistas) >= 20
    assert vistas["public.clients"][0] >= 10 and vistas["public.appointments"][0] >= 50


def test_CONTROL_una_politica_abierta_se_pone_roja():
    roto = "create policy zz_abierta on public.clients for select to authenticated using (true);"
    vistas = _aislamiento(roto)
    v, s = vistas["public.clients"]
    assert v > s


# ═══ 7. El gancho del token y `es_demo` ═══════════════════════════════════

def test_una_demo_caducada_no_consigue_token_y_es_demo_no_cambia():
    salida = _en_transaccion(f"""
insert into auth.users (id, instance_id, aud, role, email)
select '40000000-0000-0000-0000-0000000000d2', u.instance_id, u.aud, u.role, 'demo-hook@demo.estetixapp.invalid'
from auth.users u limit 1;
insert into zz values ('c', split_part({_crear(H1, "'demo-hook@demo.estetixapp.invalid'")}, '|', 2));
select public.enlazar_demo((select v::uuid from zz where k = 'c'), '40000000-0000-0000-0000-0000000000d2');
select 'R|viva|' || coalesce(public.custom_access_token_hook(jsonb_build_object(
  'user_id', '40000000-0000-0000-0000-0000000000d2', 'claims', '{{}}'::jsonb)) -> 'claims' ->> 'salon_id', 'sin centro');
{_envejecer("(select v::uuid from zz where k = 'c')")}
select 'R|caducada|' || coalesce(public.custom_access_token_hook(jsonb_build_object(
  'user_id', '40000000-0000-0000-0000-0000000000d2', 'claims', '{{}}'::jsonb)) -> 'error' ->> 'http_code', 'con token');
{_intento('a_cliente', "update public.accounts set es_demo = false where id = (select v::uuid from zz where k = 'c')")}
{_intento('a_demo', f"update public.accounts set es_demo = true where id = {_REAL}")}
{_LEER_R}""")
    r = _r(salida)
    assert r["viva"] not in ("sin centro", "") and r["caducada"] == "403"
    assert r["a_cliente"] == r["a_demo"] == "RECHAZADA:42501"


def test_el_gancho_es_el_de_la_0069_mas_la_guarda():
    """El cuerpo del gancho es el de la 0069 letra por letra, salvo el bloque marcado."""
    m69 = (M0080.parent / "0069_solo_queda_la_administradora.sql").read_text(encoding="utf-8")
    patron = r"create or replace function public\.custom_access_token_hook\(event jsonb\).*?\n\$function\$;\n"
    viejo = re.search(patron, m69, re.S).group(0)
    nuevo = re.search(patron, M0080.read_text(encoding="utf-8"), re.S).group(0)
    sin_guarda = re.sub(r"\n  -- ── 0080 ──.*?-- ── fin 0080 ──\n", "", nuevo, flags=re.S)
    assert sin_guarda == viejo
