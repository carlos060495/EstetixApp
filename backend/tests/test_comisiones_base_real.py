"""LAS COMISIONES (0075), contra la base de verdad. Todo en transacciones que
se deshacen.

Los guardas (06-10-2026), cada uno con su CONTROL: la
misma comprobación sobre la base rota a propósito —dentro de la transacción,
con el código sacado de la propia migración y cambiado— sale roja.

  1. Una comisión no puede nacer sin un cobro real detrás.
  2. Una devolución produce su fila contraria (y el devengo no se toca).
  3. Las tablas (y el registro de cambios de %) no ganan UPDATE, DELETE ni una
     clave ajena a la cuenta.
  4. El mismo cobro no devenga dos veces.
  5. El borrado a 90 días no se lleva las comisiones.
  6. EL PORCENTAJE ES EL DE LA ATRIBUCIÓN (corrección del 06-10: se guarda
     cuando llega el salón, no en su primer pago):
     a. el devengo usa el guardado, no el vigente del referido;
     b. una atribución nueva no se escribe sin porcentaje;
     c. un cambio de porcentaje no altera lo ya devengado;
     d. no se cambia un porcentaje sin motivo escrito;
     e. el mismo día: manda el instante en que Stripe cobró;
     f. el relleno de las atribuciones que ya existían.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.test_el_borrado_a_90_dias_base_real import BORRA, ENCENDER, _base_vacia, _sembrar
from tests.test_un_salon_no_puede_cambiarse_el_plan import sql

pytestmark = [pytest.mark.base_real]

MIGRACION = Path(__file__).resolve().parents[2] / "supabase/migrations/0075_las_comisiones_de_los_referidos.sql"
TABLAS = ("public.comisiones", "public.cambios_de_porcentaje")


# ═══ Herramientas ═════════════════════════════════════════════════════════


def _funcion(nombre: str, *cambios: tuple[str, str]) -> str:
    """La función tal como está en la migración, con `cambios` aplicados, lista
    para `create or replace`. Cada cambio tiene que encontrar su trozo."""
    texto = MIGRACION.read_text(encoding="utf-8")
    m = re.search(rf"create (or replace )?function public\.{nombre}\(.*?\n\$\$;\n", texto, re.S)
    assert m, nombre
    cuerpo = re.sub(r"^create (or replace )?function", "create or replace function", m.group(0))
    for antes, despues in cambios:
        assert cuerpo.count(antes) == 1, (nombre, antes)
        cuerpo = cuerpo.replace(antes, despues)
    return cuerpo


# Un referido, dos salones atribuidos con su cliente de Stripe, y el global al 10 %.
PREP = """
create temp table zz (k text primary key, v text) on commit drop;
do $$ declare r uuid; a uuid; b uuid; begin
  update public.plataforma_config set comision_referido_pct = 10 where id;
  insert into public.referidos (nombre, email, codigo, estado)
    values ('zz Agencia', 'zz@comisiones.test', 'ZZCOM1', 'aprobado') returning id into r;
  insert into public.accounts (nombre, stripe_customer_id) values ('zz Salón Uno', 'cus_zzuno') returning id into a;
  insert into public.accounts (nombre, stripe_customer_id) values ('zz Salón Dos', 'cus_zzdos') returning id into b;
  -- Por la función de verdad: escribe el % que se le promete hoy (el global, 10).
  perform public.atribuir_alta(a, 'ZZCOM1');
  perform public.atribuir_alta(b, 'ZZCOM1');
  insert into zz values ('referido', r), ('uno', a), ('dos', b);
end $$;
"""


def devengar(cliente: str, pago: str, cobro: str, importe: int, base: int | None = None) -> str:
    base = importe if base is None else base
    return (f"select 'D|{pago}|' || resultado from public.devengar_comision("
            # La hora de ahora DE VERDAD: `now()` es el inicio de la transacción,
            # anterior a un cambio de % hecho dentro de ella.
            f"'{cliente}', 'in_zz', '{pago}', '{cobro}', null, 'eur', {importe}, {base}, clock_timestamp(), 'evt_zz');\n")


def ajustar(cobro: str, devuelto: int, reclamado: int = 0, clave: str = "A") -> str:
    return (f"select '{clave}|' || resultado from public.ajustar_comision("
            f"'{cobro}', {devuelto}, {reclamado}, now(), 'evt_zz');\n")


CAMBIAR_UNO_A_30 = (
    "select 'C|' || cuenta_nombre || '|' || pct_antes || '|' || pct_despues "
    "from public.cambiar_porcentaje_de_salones((select v::uuid from zz where k = 'referido'), "
    "array[(select v::uuid from zz where k = 'uno')], 30, 'le subimos por el volumen que trae', "
    "gen_random_uuid(), 'carlos@estetixapp.test');\n")

FILAS = ("select 'F|' || tipo || '|' || motivo || '|' || comision_centimos || '|' || pct || '|' || cuenta_nombre "
         "from public.comisiones where referido_id = (select v::uuid from zz where k = 'referido') order by id;\n")


def _intento(clave: str, sentencia: str) -> str:
    """Un intento que se DESHACE siempre (pase o no) y dice qué pasó."""
    return f"""
create temp table if not exists zz_r (k text, v text) on commit drop;
do $$ begin
  begin
    {sentencia};
    raise exception using errcode = 'ZZ001';
  exception
    when sqlstate 'ZZ001' then insert into zz_r values ('{clave}', 'PASA');
    when others then insert into zz_r values ('{clave}', 'RECHAZADA:' || sqlstate);
  end;
end $$;
"""


def _lineas(salida: str, prefijo: str) -> list[str]:
    return [ln[len(prefijo) + 1:] for ln in salida.splitlines() if ln.startswith(prefijo + "|")]


def _r(salida: str) -> dict[str, str]:
    return dict(ln.split("|", 2)[1:] for ln in salida.splitlines() if ln.startswith("R|"))


_LEER_R = "select 'R|' || k || '|' || v from zz_r order by k;\n"


# ═══ 1. Sin cobro real, no hay comisión ═══════════════════════════════════

_SIN_COBRO = {
    # Lo que no es un cobro de Stripe (un PaymentIntent, algo escrito a mano).
    "rpc_payment_intent": "perform public.devengar_comision('cus_zzuno', 'in_zz', 'inpay_x', 'pi_123', null, "
                          "'eur', 1000, 1000, now(), null)",
    "rpc_a_mano": "perform public.devengar_comision('cus_zzuno', 'in_zz', 'inpay_x', 'manual', null, "
                  "'eur', 1000, 1000, now(), null)",
    # Directo a la tabla, como el backend (service_role)…
    "insert_servicio": "set local role service_role; insert into public.comisiones (referido_id, cuenta_id, "
                       "cuenta_nombre, tipo, motivo, stripe_cobro, stripe_factura, stripe_pago, moneda, "
                       "importe_cobrado_centimos, base_centimos, pct, comision_centimos, ocurrido_at) values "
                       # (el referido, sin pasar por la tabla temporal, que es del dueño)
                       "((select id from public.referidos where codigo = 'ZZCOM1'), gen_random_uuid(), 'x', "
                       "'devengo', 'cobro', 'ch_1', 'in_1', 'inpay_1', 'eur', 1000, 1000, 10, 100, now())",
    # …y como el dueño, con permiso para todo: lo para la restricción.
    "insert_dueño_sin_cobro": "insert into public.comisiones (referido_id, cuenta_id, cuenta_nombre, tipo, motivo, "
                              "stripe_cobro, stripe_factura, stripe_pago, moneda, importe_cobrado_centimos, "
                              "base_centimos, pct, comision_centimos, ocurrido_at) values "
                              "((select v::uuid from zz where k = 'referido'), gen_random_uuid(), 'x', 'devengo', "
                              "'cobro', 'regalo', 'in_1', 'inpay_1', 'eur', 1000, 1000, 10, 100, now())",
    "insert_dueño_sin_dinero": "insert into public.comisiones (referido_id, cuenta_id, cuenta_nombre, tipo, motivo, "
                               "stripe_cobro, stripe_factura, stripe_pago, moneda, importe_cobrado_centimos, "
                               "base_centimos, pct, comision_centimos, ocurrido_at) values "
                               "((select v::uuid from zz where k = 'referido'), gen_random_uuid(), 'x', 'devengo', "
                               "'cobro', 'ch_2', 'in_1', 'inpay_2', 'eur', 0, 0, 10, 0, now())",
}


def _nace_sin_cobro(roto: str = "") -> dict[str, str]:
    cuerpo = "".join(_intento(k, v) for k, v in _SIN_COBRO.items())
    salida = sql(f"begin;\n{PREP}{roto}{cuerpo}"
                 f"{devengar('cus_zzuno', 'inpay_cero', 'ch_cero', 0)}{_LEER_R}rollback;\n")
    r = _r(salida)
    # Una factura de 0 €: no escribe nada, y lo dice.
    r["rpc_cero"] = "PASA" if "inpay_cero|sin_dinero" not in _lineas(salida, "D") else "RECHAZADA:sin_dinero"
    return r


def test_una_comision_no_nace_sin_un_cobro_real():
    r = _nace_sin_cobro()
    assert len(r) == len(_SIN_COBRO) + 1  # antivacío: se intentaron todas
    assert [k for k, v in r.items() if v == "PASA"] == [], r
    assert r["insert_servicio"] == "RECHAZADA:42501"  # sin permiso de escritura
    assert r["insert_dueño_sin_cobro"] == "RECHAZADA:23514"  # la restricción


def test_CONTROL_sin_la_restriccion_ni_la_comprobacion_nace_sin_cobro():
    roto = ("alter table public.comisiones drop constraint devengo_con_cobro_real;\n"
            "grant insert on public.comisiones to service_role;\n"
            + _funcion("devengar_comision", ("if p_cobro is null or p_cobro !~", "if false and p_cobro !~"),
                       ("if p_importe_cobrado is null or p_importe_cobrado <= 0", "if false")))
    r = _nace_sin_cobro(roto)
    assert {"rpc_payment_intent", "rpc_a_mano", "insert_servicio", "insert_dueño_sin_cobro"} <= {
        k for k, v in r.items() if v == "PASA"}, r


# ═══ 2. Una devolución produce su fila contraria ═════════════════════════


def _devolver(roto: str = "") -> str:
    return sql(f"""begin;
{PREP}{roto}
{devengar('cus_zzuno', 'inpay_1', 'ch_zz1', 2900)}
{ajustar('ch_zz1', 1450, clave='MITAD')}
{ajustar('ch_zz1', 1450, clave='OTRA_VEZ')}
{ajustar('ch_zz1', 2900, clave='TODO')}
{FILAS}
rollback;
""")


def test_una_devolucion_escribe_su_fila_contraria_y_no_toca_el_devengo():
    salida = _devolver()
    assert _lineas(salida, "MITAD") == ["corregida"]
    assert _lineas(salida, "OTRA_VEZ") == ["sin_cambios"]  # el mismo aviso otra vez: nada
    assert _lineas(salida, "TODO") == ["corregida"]
    assert _lineas(salida, "F") == [
        "devengo|cobro|290|10.00|zz Salón Uno",        # el devengo, intacto
        "correccion|devolucion|-145|10.00|zz Salón Uno",
        "correccion|devolucion|-145|10.00|zz Salón Uno",
    ]


def test_CONTROL_si_ajustar_no_escribe_la_contraria_se_pone_rojo():
    salida = _devolver(_funcion("ajustar_comision", ("v_delta := v_objetivo - v_ya;", "v_delta := 0;")))
    assert not [f for f in _lineas(salida, "F") if f.startswith("correccion|")]


def test_la_reclamacion_ganada_y_la_devolucion_que_falla_vuelven_a_deberse():
    salida = sql(f"""begin;
{PREP}
{devengar('cus_zzuno', 'inpay_1', 'ch_zz1', 1000)}
{ajustar('ch_zz1', 0, 1000, 'RECLAMA')}
{ajustar('ch_zz1', 0, 0, 'GANADA')}
{ajustar('ch_zz1', 400, 0, 'DEVUELVE')}
{ajustar('ch_zz1', 0, 0, 'FALLA')}
{FILAS}
rollback;
""")
    motivos = [f.split("|")[1] + "|" + f.split("|")[2] for f in _lineas(salida, "F")]
    assert motivos == ["cobro|100", "reclamacion|-100", "reclamacion_ganada|100",
                       "devolucion|-40", "devolucion_revertida|40"]


def test_corregir_un_cobro_que_aun_no_devengo_no_escribe_nada():
    salida = sql(f"begin;\n{PREP}{ajustar('ch_que_no_existe', 1000)}{FILAS}rollback;\n")
    assert _lineas(salida, "A") == ["sin_devengo"] and _lineas(salida, "F") == []


# ═══ 3. Sin UPDATE, sin DELETE, sin clave ajena a la cuenta ══════════════

_PERMISOS = """
select 'P|' || t || '|' || r || '|' || p from unnest(array['public.comisiones', 'public.cambios_de_porcentaje']) t,
  unnest(array['service_role', 'authenticated', 'anon']) r,
  unnest(array['INSERT', 'UPDATE', 'DELETE', 'TRUNCATE']) p
where has_table_privilege(r, t, p);
select 'K|' || k.conrelid::regclass || '|' || k.confrelid::regclass from pg_constraint k
where k.contype = 'f' and k.conrelid in ('public.comisiones'::regclass, 'public.cambios_de_porcentaje'::regclass)
  and k.confrelid in ('public.accounts'::regclass, 'public.salons'::regclass, 'public.users'::regclass);
"""


def _quien_toca(roto: str = "") -> list[str]:
    intentos = "".join([
        _intento("update_dueño", "update public.comisiones set comision_centimos = comision_centimos * 2"),
        _intento("delete_dueño", "delete from public.comisiones"),
        _intento("update_cambio", "update public.cambios_de_porcentaje set pct_despues = 50"),
        _intento("delete_cambio", "delete from public.cambios_de_porcentaje"),
    ])
    # Con una fila en cada tabla: un UPDATE sobre cero filas «pasaría» sin probar nada.
    salida = sql(f"begin;\n{PREP}{devengar('cus_zzuno', 'inpay_1', 'ch_zz1', 1000)}{CAMBIAR_UNO_A_30}"
                 f"{roto}{_PERMISOS}"
                 f"{intentos}{_LEER_R}rollback;\n")
    mal = _lineas(salida, "P") + ["clave " + k for k in _lineas(salida, "K")]
    mal += [f"{k} pasa" for k, v in _r(salida).items() if v == "PASA"]
    assert len(_r(salida)) == 4  # antivacío
    return mal


def test_las_comisiones_no_se_editan_ni_se_borran_ni_cuelgan_de_la_cuenta():
    assert _quien_toca() == []


@pytest.mark.parametrize("roto, esperado", [
    ("grant update on public.comisiones to service_role;\n", "public.comisiones|service_role|UPDATE"),
    ("grant delete on public.cambios_de_porcentaje to authenticated;\n", "public.cambios_de_porcentaje|authenticated|DELETE"),
    ("grant update on public.cambios_de_porcentaje to service_role;\n", "public.cambios_de_porcentaje|service_role|UPDATE"),
    ("drop trigger cambios_de_porcentaje_sin_update_ni_delete on public.cambios_de_porcentaje;\n", "update_cambio pasa"),
    ("alter table public.comisiones add constraint zz foreign key (cuenta_id) references public.accounts (id) "
     "on delete cascade not valid;\n", "clave comisiones|accounts"),
    ("drop trigger comisiones_sin_update_ni_delete on public.comisiones;\n", "update_dueño pasa"),
], ids=["update", "delete_registro", "update_registro", "sin_trigger_registro", "clave_a_la_cuenta", "sin_trigger"])
def test_CONTROL_ganar_update_delete_o_clave_se_pone_rojo(roto, esperado):
    assert esperado in _quien_toca(roto)


# ═══ 4. El mismo cobro no devenga dos veces ═══════════════════════════════


def _dos_veces(roto: str = "") -> str:
    return sql(f"""begin;
{PREP}{roto}
{devengar('cus_zzuno', 'inpay_1', 'ch_zz1', 1000)}
{devengar('cus_zzuno', 'inpay_1', 'ch_zz1', 1000)}
{devengar('cus_zzuno', 'inpay_otro', 'ch_zz1', 1000)}
select 'N|' || count(*) from public.comisiones where stripe_cobro = 'ch_zz1';
rollback;
""")


def test_el_mismo_cobro_no_devenga_dos_veces():
    salida = _dos_veces()
    assert _lineas(salida, "D") == ["inpay_1|devengada", "inpay_1|ya_estaba", "inpay_otro|ya_estaba"]
    assert _lineas(salida, "N") == ["1"]


def test_y_aunque_alguien_se_saltara_la_funcion_lo_para_la_base():
    salida = sql(f"""begin;
{PREP}{devengar('cus_zzuno', 'inpay_1', 'ch_zz1', 1000)}
{_intento('copia', "insert into public.comisiones (referido_id, cuenta_id, cuenta_nombre, tipo, motivo, "
                   "stripe_cobro, stripe_factura, stripe_pago, moneda, importe_cobrado_centimos, base_centimos, "
                   "pct, comision_centimos, ocurrido_at) select referido_id, cuenta_id, cuenta_nombre, tipo, "
                   "motivo, stripe_cobro, stripe_factura, 'inpay_otro', moneda, importe_cobrado_centimos, "
                   "base_centimos, pct, comision_centimos, ocurrido_at from public.comisiones "
                   "where stripe_cobro = 'ch_zz1'")}
{_LEER_R}rollback;
""")
    assert _r(salida) == {"copia": "RECHAZADA:23505"}  # el índice único del cobro


def test_CONTROL_sin_los_indices_ni_la_comprobacion_devenga_dos_veces():
    roto = ("drop index public.comisiones_un_devengo_por_pago;\n"
            "drop index public.comisiones_un_devengo_por_cobro;\n"
            + _funcion("devengar_comision", ("where c.tipo = 'devengo' and (c.stripe_pago = p_pago",
                                             "where false and (c.stripe_pago = p_pago")))
    assert _lineas(_dos_veces(roto), "N") == ["3"]


# ═══ 5. El borrado a 90 días no se las lleva ═════════════════════════════


def _tras_el_borrado(roto: str = "") -> list[str]:
    _base_vacia()
    salida = sql(f"""begin;
{_sembrar(1)}
do $$ declare r uuid; c uuid := (select cuenta from zz_cuentas); begin
  insert into public.referidos (nombre, email, codigo, estado)
    values ('zz Agencia', 'zz@comisiones.test', 'ZZCOM9', 'aprobado') returning id into r;
  update public.accounts set stripe_customer_id = 'cus_zzborrado' where id = c;
  perform public.atribuir_alta(c, 'ZZCOM9');
end $$;
select 'D|' || resultado from public.devengar_comision('cus_zzborrado', 'in_zz', 'inpay_b', 'ch_zzb', null,
  'eur', 2900, 2900, now(), null);
select 'D|' || resultado from public.ajustar_comision('ch_zzb', 2900, 0, now(), null);
select 'D|cambio' from public.cambiar_porcentaje_de_salones(
  (select id from public.referidos where codigo = 'ZZCOM9'), null, 30, 'prueba del borrado a 90 días',
  gen_random_uuid(), 'guarda@estetixapp.test');
{roto}
{ENCENDER}
select 'B|' || count(*) from public.{BORRA}(array(select cuenta from zz_cuentas)) where error is null;
select 'Q|' || (select count(*) from public.comisiones c join zz_cuentas z on z.cuenta = c.cuenta_id)
  || ',' || (select count(*) from public.cambios_de_porcentaje c join zz_cuentas z on z.cuenta = c.cuenta_id)
  || ',' || (select count(*) from public.accounts a join zz_cuentas z on z.cuenta = a.id);
rollback;
""")
    assert _lineas(salida, "D") == ["devengada", "corregida", "cambio"], salida
    return _lineas(salida, "B") + _lineas(salida, "Q")


def test_el_borrado_a_90_dias_se_lleva_el_salon_y_deja_sus_comisiones():
    # Una cuenta borrada; sus dos filas de comisión y su cambio de %, en su sitio.
    assert _tras_el_borrado() == ["1", "2,1,0"]


def test_CONTROL_si_colgaran_de_la_cuenta_el_borrado_se_las_llevaria():
    roto = ("alter table public.comisiones disable trigger comisiones_sin_update_ni_delete;\n"
            "alter table public.cambios_de_porcentaje disable trigger cambios_de_porcentaje_sin_update_ni_delete;\n"
            "alter table public.comisiones add constraint zz1 foreign key (cuenta_id) "
            "references public.accounts (id) on delete cascade;\n"
            "alter table public.cambios_de_porcentaje add constraint zz2 foreign key (cuenta_id) "
            "references public.accounts (id) on delete cascade;\n")
    assert _tras_el_borrado(roto) == ["1", "0,0,0"]


# ═══ 6. El porcentaje es el de la atribución ══════════════════════════════

# — a. el devengo usa el guardado, no el vigente del referido —


def _porcentajes(roto: str = "") -> list[str]:
    salida = sql(f"""begin;
{PREP}{roto}
-- Los dos salones llegaron con el global de entonces (10). Después se le sube
-- el % al referido y al global…
update public.referidos set comision_pct = 25 where id = (select v::uuid from zz where k = 'referido');
update public.plataforma_config set comision_referido_pct = 30 where id;
-- …y llega un tercer salón; y pagan los tres.
do $$ declare c uuid; begin
  insert into public.accounts (nombre, stripe_customer_id) values ('zz Salón Tres', 'cus_zztres') returning id into c;
  perform public.atribuir_alta(c, 'ZZCOM1');
end $$;
{devengar('cus_zzuno', 'inpay_1', 'ch_zz1', 1000)}
{devengar('cus_zzdos', 'inpay_2', 'ch_zz2', 1000)}
{devengar('cus_zztres', 'inpay_3', 'ch_zz3', 1000)}
{FILAS}
select 'AT|' || cuenta_nombre || '|' || pct || '|' || pct_de_donde from public.altas_atribuidas
  where referido_id = (select v::uuid from zz where k = 'referido') order by 1;
rollback;
""")
    return [f.split("|")[3] + " " + f.split("|")[4] for f in _lineas(salida, "F")] + _lineas(salida, "AT")


def test_el_devengo_usa_el_porcentaje_de_la_atribucion_y_no_el_vigente():
    assert _porcentajes() == [
        "10.00 zz Salón Uno",   # llegó al 10: paga después del cambio y sigue al 10
        "10.00 zz Salón Dos",
        "25.00 zz Salón Tres",  # llegó después: con el % de entonces, su propio 25
        "zz Salón Dos|10.00|global", "zz Salón Tres|25.00|propio", "zz Salón Uno|10.00|global",
    ]


def test_CONTROL_si_el_devengo_leyera_el_vigente_cambiaria_el_de_los_que_ya_llegaron():
    roto = _funcion("devengar_comision", (
        "v_pct := public.porcentaje_del_salon(v_cuenta, coalesce(p_pagado_at, now()));",
        "v_pct := coalesce((select r.comision_pct from public.referidos r where r.id = v_alta.referido_id), "
        "(select c.comision_referido_pct from public.plataforma_config c where c.id));"))
    assert _porcentajes(roto)[0] == "25.00 zz Salón Uno"


_LEE_EL_VIGENTE = re.compile(r"comision_pct|comision_referido_pct|plataforma_config")


def test_ninguna_funcion_del_devengo_nombra_el_porcentaje_vigente():
    """Y escrito: ni el devengo ni `porcentaje_del_salon` nombran el % del
    referido ni el global. Leído de la base, no del fichero."""
    salida = sql("select 'S|' || proname || '|' || replace(prosrc, chr(10), ' ') from pg_proc "
                 "where proname in ('devengar_comision', 'porcentaje_del_salon', 'ajustar_comision');")
    cuerpos = {ln.split("|")[1]: ln.split("|", 2)[2] for ln in salida.splitlines() if ln.startswith("S|")}
    assert len(cuerpos) == 3  # antivacío
    assert [n for n, c in cuerpos.items() if _LEE_EL_VIGENTE.search(c)] == []
    # Control: el cuerpo de la función rota del control de arriba sí se ve.
    assert _LEE_EL_VIGENTE.search("v_pct := coalesce((select r.comision_pct from public.referidos r ...")


# — b. ninguna atribución nueva sin porcentaje —


def _atribuciones_sin_pct(roto: str = "") -> list[str]:
    intento = _intento("a_mano_sin_pct", "insert into public.altas_atribuidas (referido_id, cuenta_id, "
                                         "cuenta_nombre, codigo_llegado) values ((select id from public.referidos "
                                         "where codigo = 'ZZCOM1'), gen_random_uuid(), 'x', 'ZZCOM1')")
    salida = sql(f"""begin;
{roto}{PREP}{intento}
select 'N|' || count(*) from public.altas_atribuidas
  where referido_id = (select v::uuid from zz where k = 'referido') and (pct is null or pct_de_donde is null);
select 'T|' || count(*) from public.altas_atribuidas where referido_id = (select v::uuid from zz where k = 'referido');
{_LEER_R}rollback;
""")
    assert _lineas(salida, "T")[0] >= "2"  # antivacío: las dos de PREP, por la función
    fuera = [f"{n} sin %" for n in _lineas(salida, "N") if n != "0"]
    fuera += [f"{k} pasa" for k, v in _r(salida).items() if v == "PASA"]
    return fuera


def test_una_atribucion_nueva_no_se_escribe_sin_porcentaje():
    assert _atribuciones_sin_pct() == []


def test_CONTROL_sin_la_columna_obligatoria_ni_la_funcion_que_la_escribe_se_pone_rojo():
    roto = ("alter table public.altas_atribuidas alter column pct drop not null, "
            "alter column pct_de_donde drop not null;\n"
            + _funcion("atribuir_alta",
                       ("(referido_id, cuenta_id, cuenta_nombre, codigo_llegado, pct, pct_de_donde)",
                        "(referido_id, cuenta_id, cuenta_nombre, codigo_llegado)"),
                       ("""  values (v_referido.id, p_cuenta, v_nombre, v_llegado,
          coalesce(v_referido.comision_pct, v_global),
          case when v_referido.comision_pct is null then 'global' else 'propio' end)""",
                        "  values (v_referido.id, p_cuenta, v_nombre, v_llegado)")))
    assert set(_atribuciones_sin_pct(roto)) == {"2 sin %", "a_mano_sin_pct pasa"}


# — c. un cambio no altera lo ya devengado —


def _cambio_y_devolucion(roto: str = "") -> list[str]:
    salida = sql(f"""begin;
{PREP}{roto}
{devengar('cus_zzuno', 'inpay_1', 'ch_zz1', 2900)}
{CAMBIAR_UNO_A_30}
{devengar('cus_zzuno', 'inpay_2', 'ch_zz2', 2900)}
-- Y DESPUÉS del cambio, se devuelve el primer cobro (el de antes).
{ajustar('ch_zz1', 2900)}
{FILAS}
rollback;
""")
    assert _lineas(salida, "C") == ["zz Salón Uno|10.00|30.00"], salida
    return _lineas(salida, "F")


def test_un_cambio_de_porcentaje_no_altera_lo_ya_devengado():
    assert _cambio_y_devolucion() == [
        "devengo|cobro|290|10.00|zz Salón Uno",        # el de antes, intacto
        "devengo|cobro|870|30.00|zz Salón Uno",        # lo nuevo, al 30
        "correccion|devolucion|-290|10.00|zz Salón Uno",  # la devolución del de antes, a SU 10
    ]


def test_CONTROL_si_la_devolucion_usara_el_porcentaje_de_hoy_alteraria_lo_devengado():
    roto = _funcion("ajustar_comision", (
        "v_objetivo := round(v_devengo.comision_centimos::numeric * v_no_debido / v_devengo.importe_cobrado_centimos);",
        "v_objetivo := round(v_devengo.base_centimos::numeric * public.porcentaje_del_salon(v_devengo.cuenta_id, clock_timestamp()) "
        "/ 100 * v_no_debido / v_devengo.importe_cobrado_centimos);"))
    assert "correccion|devolucion|-870|10.00|zz Salón Uno" in _cambio_y_devolucion(roto)


# — d. sin motivo escrito no hay cambio —

_SIN_MOTIVO = {
    "rpc_sin_motivo": "perform public.cambiar_porcentaje_de_salones((select v::uuid from zz where k = 'referido'), "
                      "null, 30, null, gen_random_uuid(), 'a@b.c')",
    "rpc_en_blanco": "perform public.cambiar_porcentaje_de_salones((select v::uuid from zz where k = 'referido'), "
                     "null, 30, '          ', gen_random_uuid(), 'a@b.c')",
    "rpc_una_palabra": "perform public.cambiar_porcentaje_de_salones((select v::uuid from zz where k = 'referido'), "
                       "null, 30, 'porque', gen_random_uuid(), 'a@b.c')",
    "a_mano_servicio": "set local role service_role; insert into public.cambios_de_porcentaje (lote, referido_id, "
                       "cuenta_id, cuenta_nombre, pct_antes, pct_despues, motivo, hecho_por, hecho_por_email, "
                       "hecho_at) values (gen_random_uuid(), gen_random_uuid(), gen_random_uuid(), 'x', 10, 30, "
                       "'un motivo largo de verdad', gen_random_uuid(), 'a@b.c', now())",
    "a_mano_dueño_sin_motivo": "insert into public.cambios_de_porcentaje (lote, referido_id, cuenta_id, "
                               "cuenta_nombre, pct_antes, pct_despues, motivo, hecho_por, hecho_por_email, "
                               "hecho_at) values (gen_random_uuid(), gen_random_uuid(), gen_random_uuid(), 'x', "
                               "10, 30, ' ', gen_random_uuid(), 'a@b.c', now())",
}


def _cambia_sin_motivo(roto: str = "") -> dict[str, str]:
    cuerpo = "".join(_intento(k, v) for k, v in _SIN_MOTIVO.items())
    return _r(sql(f"begin;\n{PREP}{roto}{cuerpo}{_LEER_R}rollback;\n"))


def test_no_se_cambia_un_porcentaje_sin_motivo_escrito():
    r = _cambia_sin_motivo()
    assert len(r) == len(_SIN_MOTIVO)  # antivacío
    assert [k for k, v in r.items() if v == "PASA"] == [], r
    assert r["a_mano_servicio"] == "RECHAZADA:42501" and r["a_mano_dueño_sin_motivo"] == "RECHAZADA:23514"


def test_CONTROL_sin_la_comprobacion_ni_la_restriccion_se_cambia_sin_motivo():
    roto = ("alter table public.cambios_de_porcentaje drop constraint cambios_de_porcentaje_motivo_check;\n"
            "alter table public.cambios_de_porcentaje alter column motivo drop not null;\n"
            + _funcion("cambiar_porcentaje_de_salones",
                       ("if p_motivo is null or char_length(btrim(p_motivo)) < 10 then", "if false then"),
                       ("btrim(p_motivo),\n", "p_motivo,\n")))
    r = _cambia_sin_motivo(roto)
    assert {"rpc_sin_motivo", "rpc_en_blanco", "rpc_una_palabra", "a_mano_dueño_sin_motivo"} <= {
        k for k, v in r.items() if v == "PASA"}, r


# — e. el mismo día: manda el instante del cobro —


def test_el_mismo_dia_manda_el_instante_en_que_stripe_cobro():
    """Un cambio a las T, y dos pagos del mismo salón: uno cobrado un segundo
    antes y otro un segundo después. Llegan los dos DESPUÉS del cambio (el
    aviso de Stripe se retrasa), y aun así el de antes va al % de antes."""
    salida = sql(f"""begin;
{PREP}
{CAMBIAR_UNO_A_30}
create temp table zz_t on commit drop as
  select hecho_at as t from public.cambios_de_porcentaje
  where cuenta_id = (select v::uuid from zz where k = 'uno');
select 'D|' || resultado from public.devengar_comision('cus_zzuno', 'in_zz', 'inpay_antes', 'ch_antes', null, 'eur',
  1000, 1000, (select t from zz_t) - interval '1 second', null);
select 'D|' || resultado from public.devengar_comision('cus_zzuno', 'in_zz', 'inpay_despues', 'ch_despues', null, 'eur',
  1000, 1000, (select t from zz_t) + interval '1 second', null);
select 'X|' || stripe_cobro || '|' || pct from public.comisiones
  where referido_id = (select v::uuid from zz where k = 'referido') order by stripe_cobro;
rollback;
""")
    assert _lineas(salida, "X") == ["ch_antes|10.00", "ch_despues|30.00"]


# — f. el relleno de las atribuciones que ya existían —


def _bloque_del_relleno() -> str:
    texto = MIGRACION.read_text(encoding="utf-8")
    i = texto.index("alter table public.altas_atribuidas\n  add column pct")
    j = texto.index("comment on column public.altas_atribuidas.pct")
    return texto[i:j]


def test_las_atribuciones_que_ya_existian_se_rellenan_con_el_porcentaje_de_hoy():
    """Con la tabla como estaba antes de la 0075 (sin las columnas), dos
    atribuciones viejas —una de un referido con pacto propio y otra con el
    global—, y el bloque de la migración tal cual."""
    salida = sql(f"""begin;
alter table public.altas_atribuidas drop column pct, drop column pct_de_donde;
update public.plataforma_config set comision_referido_pct = 12 where id;
do $$ declare r1 uuid; r2 uuid; begin
  insert into public.referidos (nombre, email, codigo, estado, comision_pct)
    values ('zz Con pacto', 'zz1@relleno.test', 'ZZREL1', 'aprobado', 20) returning id into r1;
  insert into public.referidos (nombre, email, codigo, estado)
    values ('zz Sin pacto', 'zz2@relleno.test', 'ZZREL2', 'aprobado') returning id into r2;
  insert into public.altas_atribuidas (referido_id, cuenta_id, cuenta_nombre, codigo_llegado)
    values (r1, gen_random_uuid(), 'zz vieja con pacto', 'ZZREL1'), (r2, gen_random_uuid(), 'zz vieja global', 'ZZREL2');
end $$;
{_bloque_del_relleno()}
select 'V|' || cuenta_nombre || '|' || pct || '|' || pct_de_donde from public.altas_atribuidas
  where cuenta_nombre like 'zz vieja%' order by 1;
select 'NN|' || string_agg(column_name || '=' || is_nullable, ',' order by column_name) from information_schema.columns
  where table_name = 'altas_atribuidas' and column_name in ('pct', 'pct_de_donde');
-- Y el trigger de la atribución sigue puesto después del relleno.
select 'TR|' || tgenabled::text from pg_trigger where tgname = 'altas_atribuidas_sin_update_ni_delete';
rollback;
""")
    assert _lineas(salida, "V") == ["zz vieja con pacto|20.00|propio", "zz vieja global|12.00|global"]
    assert _lineas(salida, "NN") == ["pct=NO,pct_de_donde=NO"]
    assert _lineas(salida, "TR") == ["O"]


def test_en_local_no_habia_ninguna_atribucion_que_rellenar():
    """Lo que tocó la migración en esta base: el informe lo cuenta."""
    assert sql("select count(*) from public.altas_atribuidas;") == "0"


def test_un_alta_deshecha_no_es_un_salon_ni_se_le_cambia_el_porcentaje():
    """Su atribución se queda (es inmutable), pero la cuenta ya no existe: ni
    sale en sus salones ni se le puede aplicar un cambio de %."""
    salida = sql(f"""begin;
{PREP}
delete from public.accounts where id = (select v::uuid from zz where k = 'dos');
select 'S|' || cuenta_nombre from public.salones_de_un_referido((select v::uuid from zz where k = 'referido'));
{_intento('deshecha', "perform public.cambiar_porcentaje_de_salones((select v::uuid from zz where k = 'referido'), "
                      "array[(select v::uuid from zz where k = 'dos')], 30, 'un motivo suficiente', "
                      "gen_random_uuid(), 'a@b.c')")}
select 'C|' || count(*) from public.cambiar_porcentaje_de_salones((select v::uuid from zz where k = 'referido'),
  null, 30, 'un motivo suficiente', gen_random_uuid(), 'a@b.c');
{_LEER_R}rollback;
""")
    assert _lineas(salida, "S") == ["zz Salón Uno"]
    assert _r(salida) == {"deshecha": "RECHAZADA:22023"}
    assert _lineas(salida, "C") == ["1"]  # «todos los suyos» = solo los que cuentan
