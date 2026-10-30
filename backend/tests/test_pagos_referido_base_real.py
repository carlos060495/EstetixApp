"""PAGAR A LOS REFERIDOS (0076), contra la base de verdad. Todo en
transacciones que se deshacen.

Los guardas (06-10-2026) que viven en la base, cada uno con su
CONTROL —la base rota a propósito dentro de la transacción, con el código de
la propia migración cambiado— que sale rojo:

  1. Una comisión no se paga dos veces: una orden sin resolver bloquea otra, y
     lo que se ordena tiene que ser lo que se debe en ese momento.
  2. No se paga sin registro inmutable: las órdenes y sus resultados no ganan
     INSERT, UPDATE ni DELETE, y una orden se resuelve una sola vez.
  3. Las monedas no se mezclan en un pago.
  4. Ninguna fila de comisión cambia al pagar.

Y lo que lo rodea: un pago rechazado deja de contar; uno revertido, en parte;
el mínimo, por moneda. Que el pago lo dispare solo una persona y que «¿se le
puede pagar?» se le pregunte a Stripe está en `test_pagos_referido.py`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.test_comisiones_base_real import PREP, _intento, _lineas, _r, devengar
from tests.test_un_salon_no_puede_cambiarse_el_plan import sql

pytestmark = [pytest.mark.base_real]

MIGRACION = Path(__file__).resolve().parents[2] / "supabase/migrations/0076_pagar_a_los_referidos.sql"
REF = "(select v::uuid from zz where k = 'referido')"
_LEER_R = "select 'R|' || k || '|' || v from zz_r order by k;\n"


def _funcion(nombre: str, *cambios: tuple[str, str]) -> str:
    texto = MIGRACION.read_text(encoding="utf-8")
    m = re.search(rf"create (or replace )?function public\.{nombre}\(.*?\n\$\$;\n", texto, re.S)
    assert m, nombre
    cuerpo = re.sub(r"^create (or replace )?function", "create or replace function", m.group(0))
    for antes, despues in cambios:
        assert cuerpo.count(antes) == 1, (nombre, antes)
        cuerpo = cuerpo.replace(antes, despues)
    return cuerpo


# Tres cobros de 29 € al 10 %: 8,70 € de comisión en euros; y uno en dólares.
CON_DEUDA = PREP + "".join([
    devengar("cus_zzuno", "inpay_1", "ch_zz1", 2900),
    devengar("cus_zzuno", "inpay_2", "ch_zz2", 2900),
    devengar("cus_zzdos", "inpay_3", "ch_zz3", 2900),
    "select 'D|usd|' || resultado from public.devengar_comision('cus_zzdos', 'in_zz', 'inpay_u', 'ch_zzu', null, "
    "'usd', 5000, 5000, clock_timestamp(), null);\n",
    # Para cobrarlos todos sin rozar el mínimo (25 €): bajamos el de euros a 5 €.
    "update public.minimos_de_pago set centimos = 500 where moneda = 'eur';\n",
    f"create temp table zz_hasta on commit drop as select max(id) as h from public.comisiones "
    f"where referido_id = {REF} and moneda = 'eur';\n",
])


def ordenar(clave: str, importe: int | str = 870, moneda: str = "eur", hasta: str = "(select h from zz_hasta)") -> str:
    return (f"select '{clave}|' || resultado || '|' || coalesce(pago_id::text, '') from public.ordenar_pago("
            f"{REF}, '{moneda}', {importe}, {hasta}, 'acct_zzdestino', gen_random_uuid(), 'carlos@estetixapp.test');\n")


def resolver(clave: str, resultado: str = "enviado", tr: str = "'tr_zz1'") -> str:
    return (f"select '{clave}|' || public.resolver_pago((select pago_id from public.pagos_a_referidos p "
            f"join lateral (select p.id as pago_id) x on true where p.referido_id = {REF} "
            f"order by p.ordenado_at desc limit 1), '{resultado}', {tr}, 'prueba', null);\n")


def deuda(clave: str, moneda: str = "eur") -> str:
    return (f"select '{clave}|' || neto || '|' || pagado || '|' || debe "
            f"from public.deuda_con_referido({REF}, '{moneda}', null);\n")


# ═══ 1. No se paga dos veces ═════════════════════════════════════════════


def _dos_veces(roto: str = "") -> str:
    return sql(f"""begin;
{CON_DEUDA}{roto}
{ordenar('PRIMERA')}
{ordenar('SIN_RESOLVER')}
{resolver('RESUELTA')}
{ordenar('YA_PAGADA')}
{deuda('DEUDA')}
select 'N|' || count(*) from public.pagos_a_referidos where referido_id = {REF};
rollback;
""")


def test_una_comision_no_se_paga_dos_veces():
    salida = _dos_veces()
    assert _lineas(salida, "PRIMERA")[0].startswith("ordenado|")
    # Con la primera sin resolver, otra no: puede que ese dinero ya haya salido.
    assert _lineas(salida, "SIN_RESOLVER") == ["hay_uno_pendiente|"]
    assert _lineas(salida, "RESUELTA") == ["enviado"]
    # Ya enviada, lo que se debe es 0: lo que se «confirmó» ya no es la deuda.
    assert _lineas(salida, "YA_PAGADA") == ["ha_cambiado|"]
    assert _lineas(salida, "DEUDA") == ["870|870|0"]
    assert _lineas(salida, "N") == ["1"]


def test_CONTROL_sin_las_dos_comprobaciones_se_pagaria_dos_veces():
    roto = _funcion("ordenar_pago",
                    ("if exists (select 1 from public.pagos_que_cuentan(p_referido) x\n"
                     "             where x.moneda = p_moneda and x.estado = 'pendiente') then",
                     "if false then"),
                    ("if v_deuda.debe is distinct from p_importe or v_deuda.hasta_comision_id is distinct from p_hasta then",
                     "if false then"))
    assert _lineas(_dos_veces(roto), "N") == ["3"]


def test_mientras_esta_sin_resolver_cuenta_como_pagado():
    salida = sql(f"begin;\n{CON_DEUDA}{ordenar('O')}{deuda('DEU')}rollback;\n")
    assert _lineas(salida, "DEU") == ["870|870|0"]


def test_un_rechazo_deja_de_contar_y_se_puede_volver_a_ordenar():
    salida = sql(f"""begin;
{CON_DEUDA}
{ordenar('O1')}
{resolver('RES1', 'rechazado', 'null')}
{deuda('DEU')}
{ordenar('O2')}
rollback;
""")
    assert _lineas(salida, "RES1") == ["rechazado"]
    assert _lineas(salida, "DEU") == ["870|0|870"]
    assert _lineas(salida, "O2")[0].startswith("ordenado|")


def test_una_reversion_parcial_devuelve_esa_parte_a_la_deuda_una_sola_vez():
    salida = sql(f"""begin;
{CON_DEUDA}
{ordenar('O')}
{resolver('RES')}
select 'REV1|' || public.apuntar_reversion('tr_zz1', 300, 'evt_1');
select 'REV2|' || public.apuntar_reversion('tr_zz1', 300, 'evt_1');
select 'AJENA|' || public.apuntar_reversion('tr_de_otro', 300, null);
{deuda('DEU')}
rollback;
""")
    assert _lineas(salida, "REV1") == ["revertido"] and _lineas(salida, "REV2") == ["sin_cambios"]
    assert _lineas(salida, "AJENA") == ["no_es_nuestra"]
    assert _lineas(salida, "DEU") == ["870|570|300"]


def test_una_orden_se_resuelve_una_sola_vez():
    salida = sql(f"""begin;
{CON_DEUDA}
{ordenar('O')}
{resolver('RES1')}
{resolver('RES2', 'rechazado', 'null')}
select 'F|' || count(*) from public.pagos_a_referidos_resultados x
  join public.pagos_a_referidos p on p.id = x.pago_id where p.referido_id = {REF};
rollback;
""")
    # La segunda no escribe: dice lo que ya estaba.
    assert _lineas(salida, "RES2") == ["enviado"] and _lineas(salida, "F") == ["1"]


# ═══ 2. Registro inmutable ═══════════════════════════════════════════════

_PERMISOS = """
select 'P|' || t || '|' || r || '|' || p
from unnest(array['public.pagos_a_referidos', 'public.pagos_a_referidos_resultados', 'public.minimos_de_pago']) t,
  unnest(array['service_role', 'authenticated', 'anon']) r,
  unnest(array['INSERT', 'UPDATE', 'DELETE', 'TRUNCATE']) p
where has_table_privilege(r, t, p);
select 'K|' || k.conrelid::regclass from pg_constraint k
where k.contype = 'f' and k.conrelid in ('public.pagos_a_referidos'::regclass, 'public.pagos_a_referidos_resultados'::regclass);
"""


def _quien_toca(roto: str = "") -> list[str]:
    intentos = "".join([
        _intento("update_orden", "update public.pagos_a_referidos set importe_centimos = 1"),
        _intento("delete_orden", "delete from public.pagos_a_referidos"),
        _intento("update_resultado", "update public.pagos_a_referidos_resultados set resultado = 'rechazado'"),
        _intento("delete_resultado", "delete from public.pagos_a_referidos_resultados"),
        _intento("orden_a_mano", "set local role service_role; insert into public.pagos_a_referidos (id, referido_id, "
                                 "referido_nombre, destino_stripe, moneda, importe_centimos, desde_comision_excluido, "
                                 "hasta_comision_id, periodo_desde, periodo_hasta, ordenado_por, ordenado_por_email) "
                                 "values (gen_random_uuid(), gen_random_uuid(), 'x', 'acct_x', 'eur', 100, 0, 1, now(), "
                                 "now(), gen_random_uuid(), 'a@b.c')"),
    ])
    salida = sql(f"begin;\n{CON_DEUDA}{ordenar('O')}{resolver('RES')}{roto}{_PERMISOS}{intentos}{_LEER_R}rollback;\n")
    assert len(_r(salida)) == 5  # antivacío
    mal = _lineas(salida, "P") + ["clave " + k for k in _lineas(salida, "K")]
    return mal + [f"{k} pasa" for k, v in _r(salida).items() if v == "PASA"]


def test_las_ordenes_y_sus_resultados_no_se_editan_ni_se_borran_ni_se_escriben_a_mano():
    assert _quien_toca() == []


@pytest.mark.parametrize("roto, esperado", [
    ("grant update on public.pagos_a_referidos to service_role;\n", "public.pagos_a_referidos|service_role|UPDATE"),
    ("grant delete on public.pagos_a_referidos_resultados to service_role;\n",
     "public.pagos_a_referidos_resultados|service_role|DELETE"),
    ("grant insert on public.pagos_a_referidos to service_role;\n", "orden_a_mano pasa"),
    ("drop trigger pagos_a_referidos_sin_update_ni_delete on public.pagos_a_referidos;\n", "update_orden pasa"),
    ("drop trigger pagos_a_referidos_resultados_sin_update_ni_delete on public.pagos_a_referidos_resultados;\n",
     "update_resultado pasa"),
    ("alter table public.pagos_a_referidos add constraint zz foreign key (referido_id) "
     "references public.referidos (id) not valid;\n", "clave pagos_a_referidos"),
], ids=["update_orden", "delete_resultado", "insert_a_mano", "sin_trigger_orden", "sin_trigger_resultado", "clave"])
def test_CONTROL_ganar_update_delete_o_una_clave_se_pone_rojo(roto, esperado):
    assert esperado in _quien_toca(roto)


# ═══ 3. Las monedas no se mezclan ════════════════════════════════════════


def _monedas(roto: str = "") -> str:
    return sql(f"""begin;
{CON_DEUDA}{roto}
{deuda('EUR')}
{deuda('USD', 'usd')}
-- Lo de las dos monedas junto no se puede ordenar como un pago en euros.
{ordenar('JUNTAS', 870 + 500)}
{ordenar('SOLO_EUR')}
select 'M|' || string_agg(distinct moneda, ',') from public.pagos_a_referidos where referido_id = {REF};
rollback;
""")


def test_las_monedas_no_se_mezclan_en_un_pago():
    salida = _monedas()
    assert _lineas(salida, "EUR") == ["870|0|870"] and _lineas(salida, "USD") == ["500|0|500"]
    assert _lineas(salida, "JUNTAS") == ["ha_cambiado|"]
    assert _lineas(salida, "SOLO_EUR")[0].startswith("ordenado|")
    assert _lineas(salida, "M") == ["eur"]


def test_CONTROL_si_la_deuda_no_mirara_la_moneda_se_mezclarian():
    roto = _funcion("deuda_con_referido",
                    ("where referido_id = p_referido and moneda = p_moneda and (p_hasta is null or id <= p_hasta)",
                     "where referido_id = p_referido and (p_hasta is null or id <= p_hasta)"))
    salida = _monedas(roto)
    assert _lineas(salida, "EUR") == ["1370|0|1370"]


def test_una_moneda_sin_minimo_no_se_paga_y_por_debajo_tampoco():
    salida = sql(f"""begin;
{CON_DEUDA}
create temp table zz_hu on commit drop as select max(id) as h from public.comisiones
  where referido_id = {REF} and moneda = 'usd';
{ordenar('USD', 500, 'usd', '(select h from zz_hu)')}
update public.minimos_de_pago set centimos = 2500 where moneda = 'eur';
{ordenar('POCO')}
rollback;
""")
    assert _lineas(salida, "USD") == ["sin_minimo|"] and _lineas(salida, "POCO") == ["por_debajo_del_minimo|"]


# ═══ 4. Ninguna comisión cambia al pagar ═════════════════════════════════

_HUELLA = (f"select 'H|' || md5(string_agg(c::text, ';' order by c.id)) from public.comisiones c "
           f"where c.referido_id = {REF};\n")


def _huellas(roto: str = "") -> list[str]:
    salida = sql(f"""begin;
{CON_DEUDA}{roto}
{_HUELLA}
{ordenar('O')}
{resolver('RES')}
select 'REV|' || public.apuntar_reversion('tr_zz1', 100, null);
{_HUELLA}
rollback;
""")
    return _lineas(salida, "H")


def test_ninguna_fila_de_comision_cambia_al_pagar():
    antes, despues = _huellas()
    assert antes == despues


def test_CONTROL_si_pagar_tocara_las_comisiones_se_pone_rojo():
    roto = ("alter table public.comisiones disable trigger comisiones_sin_update_ni_delete;\n"
            + _funcion("resolver_pago", ("  return (select resultado from public.pagos_a_referidos_resultados",
                                         "  update public.comisiones set stripe_evento = 'pagado' "
                                         "where referido_id = (select referido_id from public.pagos_a_referidos "
                                         "where id = p_pago);\n"
                                         "  return (select resultado from public.pagos_a_referidos_resultados")))
    antes, despues = _huellas(roto)
    assert antes != despues
