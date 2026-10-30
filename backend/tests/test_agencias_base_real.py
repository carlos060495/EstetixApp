"""LAS AGENCIAS (0077), contra la base de verdad. Todo en transacciones que se
deshacen.

Los guardas (06-10-2026) que viven en la base, cada uno con su
CONTROL —la base rota a propósito dentro de la transacción— que sale rojo:

  1. NINGÚN TRABAJADOR PUEDE COBRAR. Se recorre el esquema: toda tabla de
     trabajadores (por su nombre) y sus columnas; rojo si alguna gana una
     cuenta de Stripe, un IBAN, un porcentaje o una contraseña. Y ninguna tabla
     que lleve un destino de dinero (una cuenta de Stripe, un IBAN) puede
     apuntar a un trabajador.
  2. UNA ATRIBUCIÓN CON EL CÓDIGO DE UN TRABAJADOR ES DE SU AGENCIA. Y,
     recorriendo TODAS las atribuciones, ninguna apunta a un trabajador.

Y lo que lo rodea: la baja, el código único entre las dos tablas, que cada
agencia toque solo lo suyo, el enlace de seguimiento, el desglose que suma el
total, el mínimo con su registro y pagar por debajo de él.
Que un alta con un código que no vale entra igual (también el de un trabajador
de baja) está en `test_un_codigo_que_no_vale_no_para_el_alta.py`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.test_comisiones_base_real import _intento, _lineas, _r
from tests.test_un_salon_no_puede_cambiarse_el_plan import sql

pytestmark = [pytest.mark.base_real]

MIGRACION = Path(__file__).resolve().parents[2] / "supabase/migrations/0077_las_agencias.sql"

AG = "(select v::uuid from zz where k = 'agencia')"
OTRA = "(select v::uuid from zz where k = 'otra')"
IND = "(select v::uuid from zz where k = 'individual')"
ANA = "(select id from public.trabajadores_de_agencia where codigo = 'ZZTA01')"


def _cuenta(k: str) -> str:
    return f"(select v::uuid from zz where k = '{k}')"


# Una agencia al 15 %, otra agencia, una persona; tres cuentas; Ana, de la
# primera agencia, con su código.
PREP = """
create temp table zz (k text primary key, v text) on commit drop;
do $$ declare ag uuid; otra uuid; ind uuid; a uuid; b uuid; c uuid; begin
  update public.plataforma_config set comision_referido_pct = 10 where id;
  insert into public.referidos (nombre, email, codigo, estado, tipo, comision_pct, aprobado_at)
    values ('zz Agencia Norte', 'zz.norte@agencias.test', 'ZZAN01', 'aprobado', 'agencia', 15, now()) returning id into ag;
  insert into public.referidos (nombre, email, codigo, estado, tipo, aprobado_at)
    values ('zz Agencia Sur', 'zz.sur@agencias.test', 'ZZAS01', 'aprobado', 'agencia', now()) returning id into otra;
  insert into public.referidos (nombre, email, codigo, estado)
    values ('zz Persona', 'zz.persona@agencias.test', 'ZZIN01', 'aprobado') returning id into ind;
  insert into public.accounts (nombre, stripe_customer_id) values ('zz Salón Uno', 'cus_zzag1') returning id into a;
  insert into public.salons (account_id, nombre_comercial) values (a, 'zz Salón Uno');
  insert into public.accounts (nombre, stripe_customer_id) values ('zz Salón Dos', 'cus_zzag2') returning id into b;
  insert into public.accounts (nombre, stripe_customer_id) values ('zz Salón Tres', 'cus_zzag3') returning id into c;
  insert into zz values ('agencia', ag), ('otra', otra), ('individual', ind), ('uno', a), ('dos', b), ('tres', c);
  perform public.alta_de_trabajador(ag, 'Ana Comercial', 'ZZTA01');
end $$;
"""


def _funcion(nombre: str, *cambios: tuple[str, str]) -> str:
    texto = MIGRACION.read_text(encoding="utf-8")
    m = re.search(rf"create (or replace )?function public\.{nombre}\(.*?\n\$\$;\n", texto, re.S)
    assert m, nombre
    cuerpo = re.sub(r"^create (or replace )?function", "create or replace function", m.group(0))
    for antes, despues in cambios:
        assert cuerpo.count(antes) == 1, (nombre, antes)
        cuerpo = cuerpo.replace(antes, despues)
    return cuerpo


# ═══ 1. Ningún trabajador puede cobrar ═══════════════════════════════════

# Lo que convierte a alguien en alguien a quien se le paga, o que entra.
COBRAR = re.compile(r"stripe|acct|iban|bic|swift|cuenta_de_cobro|banco|pct|porcentaje|percent|comision|"
                    r"password|contrasena|passwd|clave", re.I)
# Una tabla que lleva un destino de dinero.
DESTINO = re.compile(r"stripe|acct|iban|destino", re.I)

_ESQUEMA = """
select 'C|' || c.table_name || '|' || c.column_name
from information_schema.columns c
join information_schema.tables t on t.table_schema = c.table_schema and t.table_name = c.table_name
where c.table_schema = 'public' and t.table_type = 'BASE TABLE'
order by 1;
"""


def _columnas(roto: str = "") -> dict[str, list[str]]:
    salida = sql(f"begin;\n{roto}\n{_ESQUEMA}rollback;\n")
    tablas: dict[str, list[str]] = {}
    for ln in _lineas(salida, "C"):
        tabla, columna = ln.split("|")
        tablas.setdefault(tabla, []).append(columna)
    return tablas


def cobradores(tablas: dict[str, list[str]]) -> tuple[list[str], list[str]]:
    """(las tablas de trabajadores, lo que les dejaría cobrar o entrar)."""
    de_trabajadores = sorted(t for t in tablas if "trabajador" in t)
    mal = [f"{t}.{c}" for t in de_trabajadores for c in tablas[t] if COBRAR.search(c)]
    # Y una tabla con un destino de dinero que apunta a un trabajador.
    mal += [f"{t}.{c}" for t, cols in tablas.items() if t not in de_trabajadores
            and any(DESTINO.search(x) for x in cols) for c in cols if "trabajador" in c]
    return de_trabajadores, sorted(mal)


def test_ningun_trabajador_puede_cobrar():
    de_trabajadores, mal = cobradores(_columnas())
    assert "trabajadores_de_agencia" in de_trabajadores  # antivacío: el recorrido la ve
    assert mal == []


@pytest.mark.parametrize("roto,esperado", [
    ("alter table public.trabajadores_de_agencia add column pct numeric;", "trabajadores_de_agencia.pct"),
    ("alter table public.trabajadores_de_agencia add column cuenta_stripe text;",
     "trabajadores_de_agencia.cuenta_stripe"),
    ("alter table public.trabajadores_de_agencia add column iban text;", "trabajadores_de_agencia.iban"),
    ("alter table public.trabajadores_de_agencia add column password_hash text;",
     "trabajadores_de_agencia.password_hash"),
    # Una tabla de trabajadores NUEVA: el recorrido la encuentra sin que nadie la apunte.
    ("create table public.zz_trabajadores_extra (id int, comision_pct numeric);", "zz_trabajadores_extra.comision_pct"),
    # Un trabajador como destinatario de un pago.
    ("alter table public.pagos_a_referidos add column trabajador_id uuid;", "pagos_a_referidos.trabajador_id"),
], ids=["pct", "stripe", "iban", "contrasena", "tabla_nueva", "destinatario"])
def test_CONTROL_un_trabajador_que_gana_algo_de_cobrar_se_pone_rojo(roto, esperado):
    assert esperado in cobradores(_columnas(roto))[1]


def test_un_trabajador_no_tiene_ni_un_permiso_de_escritura():
    """Se escribe solo con las funciones de aquí, que comprueban que sea de la agencia."""
    permisos = sql("select string_agg(privilege_type, ',' order by privilege_type) from information_schema.role_table_grants "
                   "where table_schema = 'public' and table_name = 'trabajadores_de_agencia' "
                   "and grantee in ('anon', 'authenticated', 'service_role');")
    assert permisos == "SELECT"


# ═══ 2. La atribución es de la agencia ═══════════════════════════════════

# En TODA la tabla: ninguna atribución apunta a algo que no sea un referido, ni a un trabajador.
_NINGUNA_A_UN_TRABAJADOR = """
select 'MAL|' || count(*) from public.altas_atribuidas a
where not exists (select 1 from public.referidos r where r.id = a.referido_id)
   or exists (select 1 from public.trabajadores_de_agencia t where t.id = a.referido_id);
"""


def _atribuir(roto: str = "") -> str:
    return sql(f"""begin;
{PREP}{roto}
create temp table if not exists zz_r (k text, v text) on commit drop;
do $$ begin
  insert into zz_r values ('ATR', public.atribuir_alta({_cuenta('uno')}, '  zzta01 '));
exception when others then insert into zz_r values ('ATR', 'FALLA:' || sqlstate);
end $$;
select 'R|' || k || '|' || v from zz_r;
select 'FILA|' || (referido_id = {AG}) || '|' || pct || '|' || pct_de_donde || '|' || coalesce(trabajador_nombre, '-')
       || '|' || (trabajador_id = {ANA})
from public.altas_atribuidas where cuenta_id = {_cuenta('uno')};
{_NINGUNA_A_UN_TRABAJADOR}rollback;
""")


def test_una_alta_con_el_codigo_de_un_trabajador_es_de_su_agencia():
    salida = _atribuir()
    assert _r(salida) == {"ATR": "atribuida"}
    # De la agencia, con SU porcentaje (15, propio), y con el trabajador dentro.
    assert _lineas(salida, "FILA") == ["true|15.00|propio|Ana Comercial|true"]
    assert _lineas(salida, "MAL") == ["0"]


def test_CONTROL_atribuir_al_trabajador_se_pone_rojo():
    """La atribución escrita a nombre del trabajador: la clave a `referidos` la
    rechaza, y el guarda lo ve (no hay fila de la agencia)."""
    roto = _funcion("atribuir_alta", ("values (v_referido.id, p_cuenta, v_nombre, v_llegado,",
                                      "values (coalesce(v_trabajador.id, v_referido.id), p_cuenta, v_nombre, v_llegado,"))
    salida = _atribuir(roto)
    assert _r(salida)["ATR"].startswith("FALLA:") or _lineas(salida, "FILA") != ["true|15.00|propio|Ana Comercial|true"]


def test_CONTROL_sin_la_clave_a_referidos_el_recorrido_lo_ve():
    roto = (_funcion("atribuir_alta", ("values (v_referido.id, p_cuenta, v_nombre, v_llegado,",
                                       "values (coalesce(v_trabajador.id, v_referido.id), p_cuenta, v_nombre, v_llegado,"))
            + "alter table public.altas_atribuidas drop constraint altas_atribuidas_referido_id_fkey;\n")
    assert _lineas(_atribuir(roto), "MAL") == ["1"]


# ═══ La baja, el código, lo de cada agencia ══════════════════════════════


def test_dado_de_baja_conserva_sus_salones_y_su_codigo_deja_de_atribuir():
    salida = sql(f"""begin;
{PREP}
select 'A1|' || public.atribuir_alta({_cuenta('uno')}, 'ZZTA01');
select 'BAJA|' || public.baja_de_trabajador({AG}, {ANA});
select 'BAJA2|' || public.baja_de_trabajador({AG}, {ANA});
select 'A2|' || public.atribuir_alta({_cuenta('dos')}, 'ZZTA01');
select 'VALE|' || public.codigo_de_alta_vale('ZZTA01');
select 'SUYOS|' || salones from public.trabajadores_de_una_agencia({AG});
select 'RASTRO|' || motivo || '|' || cuenta_nombre from public.codigos_que_no_valieron
  where cuenta_id = {_cuenta('dos')};
rollback;
""")
    assert _lineas(salida, "A1") == ["atribuida"]
    assert _lineas(salida, "BAJA") == ["de_baja"] and _lineas(salida, "BAJA2") == ["ya_estaba"]
    assert _lineas(salida, "A2") == ["trabajador_de_baja"]
    assert _lineas(salida, "VALE") == ["false"]
    assert _lineas(salida, "SUYOS") == ["1"]  # el que ya trajo, sigue siendo suyo
    assert _lineas(salida, "RASTRO") == ["trabajador_de_baja|zz Salón Dos"]


def test_un_codigo_es_de_uno_solo_entre_referidos_y_trabajadores():
    salida = sql(f"""begin;
{PREP}
select 'T|' || resultado from public.alta_de_trabajador({AG}, 'Choca', 'ZZAS01');
{_intento('R', "update public.referidos set codigo = 'ZZTA01' where id = (select v::uuid from zz where k = 'individual')")}
select 'R|' || k || '|' || v from zz_r;
rollback;
""")
    assert _lineas(salida, "T") == ["codigo_ocupado"]
    assert _r(salida) == {"R": "RECHAZADA:23505"}


def test_una_persona_no_tiene_trabajadores_y_una_agencia_con_ellos_no_deja_de_serlo():
    salida = sql(f"""begin;
{PREP}
select 'IND|' || resultado from public.alta_de_trabajador({IND}, 'Nadie', 'ZZTI01');
{_intento('TIPO', "update public.referidos set tipo = 'individual' where id = (select v::uuid from zz where k = 'agencia')")}
{_intento('VACIA', "update public.referidos set tipo = 'individual' where id = (select v::uuid from zz where k = 'otra')")}
select 'R|' || k || '|' || v from zz_r;
rollback;
""")
    assert _lineas(salida, "IND") == ["no_es_una_agencia"]
    # Con trabajadores, no; sin ellos, se puede (aún no los tiene).
    assert _r(salida) == {"TIPO": "RECHAZADA:23514", "VACIA": "PASA"}


def test_cada_agencia_toca_solo_a_los_suyos():
    salida = sql(f"""begin;
{PREP}
select 'BAJA|' || public.baja_de_trabajador({OTRA}, {ANA});
select 'SEG|' || public.poner_seguimiento({OTRA}, {ANA}, repeat('b', 64));
select 'SIGUE|' || (de_baja_at is null) || '|' || (seguimiento_hash is null) from public.trabajadores_de_agencia
  where id = {ANA};
select 'VE|' || count(*) from public.trabajadores_de_una_agencia({OTRA});
rollback;
""")
    assert _lineas(salida, "BAJA") == ["no_es_suyo"] and _lineas(salida, "SEG") == ["no_es_suyo"]
    assert _lineas(salida, "SIGUE") == ["true|true"]
    assert _lineas(salida, "VE") == ["0"]


def test_la_comprobacion_del_formulario_dice_lo_mismo_que_el_alta():
    """`codigo_de_alta_vale` y `atribuir_alta` no pueden separarse: para cada
    código, vale ⇔ se atribuye. Cada intento en su propia transacción."""
    for codigo in (" zzta01 ", "ZZTA01", "zzan01", "zzin01 ", "NOEXISTE", "", "ZZ TA01"):
        lit = codigo.replace("'", "''")
        salida = sql(f"""begin;
{PREP}
select 'V|' || public.codigo_de_alta_vale('{lit}');
select 'A|' || public.atribuir_alta({_cuenta('tres')}, '{lit}');
rollback;
""")
        vale = _lineas(salida, "V") == ["true"]
        assert vale == (_lineas(salida, "A") == ["atribuida"]), (codigo, salida)


# ═══ El enlace de seguimiento ════════════════════════════════════════════


def test_el_seguimiento_abre_con_su_enlace_y_deja_de_abrir_al_revocarlo_o_cambiarlo():
    h1, h2 = "a" * 64, "c" * 64
    salida = sql(f"""begin;
{PREP}
select public.atribuir_alta({_cuenta('uno')}, 'ZZTA01');
insert into public.subscriptions (account_id, salon_id, estado) values ({_cuenta('uno')}, (select id from public.salons where account_id = {_cuenta('uno')} limit 1), 'trialing');
select public.poner_seguimiento({AG}, {ANA}, '{h1}');
select 'ABRE|' || trabajador || '|' || agencia || '|' || salon || '|' || plan || '|' || estado
  from public.seguimiento_de_un_trabajador('{h1}');
select public.poner_seguimiento({AG}, {ANA}, '{h2}');
select 'VIEJO|' || count(*) from public.seguimiento_de_un_trabajador('{h1}');
select 'NUEVO|' || count(*) from public.seguimiento_de_un_trabajador('{h2}');
select public.poner_seguimiento({AG}, {ANA}, null);
select 'REVOCADO|' || count(*) from public.seguimiento_de_un_trabajador('{h2}');
select public.poner_seguimiento({AG}, {ANA}, '{h1}');
update public.referidos set estado = 'rechazado' where id = {AG};
select 'RECHAZADA|' || count(*) from public.seguimiento_de_un_trabajador('{h1}');
select 'BASURA|' || count(*) from public.seguimiento_de_un_trabajador('nada');
rollback;
""")
    assert _lineas(salida, "ABRE") == ["Ana Comercial|zz Agencia Norte|zz Salón Uno|basico|en_prueba"]
    assert _lineas(salida, "VIEJO") == ["0"] and _lineas(salida, "NUEVO") == ["1"]
    assert _lineas(salida, "REVOCADO") == ["0"]
    assert _lineas(salida, "RECHAZADA") == ["0"] and _lineas(salida, "BASURA") == ["0"]


def test_el_seguimiento_no_devuelve_ni_una_columna_de_dinero():
    """Las columnas que devuelve CADA función de seguimiento de la base,
    recorridas (no una lista): ninguna de dinero ni de porcentaje."""
    from tests.test_seguimiento_sin_dinero import DINERO

    filas = sql("select p.proname || '|' || pg_get_function_result(p.oid) from pg_proc p "
                "join pg_namespace n on n.oid = p.pronamespace where n.nspname = 'public' and p.proname like '%seguimiento%';")
    funciones = dict(ln.split("|", 1) for ln in filas.splitlines())
    assert "seguimiento_de_un_trabajador" in funciones  # antivacío
    for nombre, resultado in funciones.items():
        columnas = re.findall(r"(\w+) \w+", resultado.removeprefix("TABLE(").removesuffix(")"))
        assert not [c for c in columnas if DINERO.search(c)], (nombre, resultado)


# ═══ El desglose suma el total ═══════════════════════════════════════════


def test_el_desglose_por_trabajador_suma_el_total_de_la_agencia():
    salida = sql(f"""begin;
{PREP}
select public.atribuir_alta({_cuenta('uno')}, 'ZZTA01');
select public.atribuir_alta({_cuenta('dos')}, 'ZZAN01');
insert into public.subscriptions (account_id, salon_id, estado) values ({_cuenta('uno')}, (select id from public.salons where account_id = {_cuenta('uno')} limit 1), 'active');
select public.devengar_comision('cus_zzag1', 'in_a', 'inpay_a1', 'ch_a1', null, 'eur', 2900, 2900, clock_timestamp(), null);
select public.devengar_comision('cus_zzag1', 'in_a', 'inpay_a2', 'ch_a2', null, 'eur', 4900, 4900, clock_timestamp(), null);
select public.devengar_comision('cus_zzag2', 'in_b', 'inpay_b1', 'ch_b1', null, 'eur', 2900, 2900, clock_timestamp(), null);
select public.ajustar_comision('ch_a2', 4900, 0, now(), null);
select 'POR|' || coalesce(t.nombre, 'directos') || '|' || d.neto from public.devengado_por_trabajador({AG}) d
  left join public.trabajadores_de_agencia t on t.id = d.trabajador_id order by 1;
select 'TOTAL|' || sum(comision_centimos) from public.comisiones where referido_id = {AG};
select 'SUMA|' || sum(neto) from public.devengado_por_trabajador({AG});
select 'ANA|' || salones || '|' || pagando from public.trabajadores_de_una_agencia({AG});
rollback;
""")
    # Ana: 435 (2900 al 15 %), más 735 devengado y anulado; directos: 435.
    assert _lineas(salida, "POR") == ["Ana Comercial|435", "directos|435"]
    assert _lineas(salida, "TOTAL") == _lineas(salida, "SUMA") == ["870"]
    assert _lineas(salida, "ANA") == ["1|1"]


# ═══ El mínimo, desde el panel ═══════════════════════════════════════════


def test_cambiar_el_minimo_pide_motivo_y_queda_registrado_sin_poder_tocarse():
    salida = sql(f"""begin;
{_intento('SIN_MOTIVO', "perform public.cambiar_minimo_de_pago('eur', 1000, 'corto', gen_random_uuid(), 'c@t.t')")}
select 'CAMBIO|' || coalesce(centimos_antes::text, '-') || '|' || centimos_despues
  from public.cambiar_minimo_de_pago('eur', 1000, 'bajamos para liquidar a los pequeños', gen_random_uuid(), 'carlos@t.t');
select 'NUEVA|' || coalesce(centimos_antes::text, '-') || '|' || centimos_despues
  from public.cambiar_minimo_de_pago('usd', 3000, 'fijamos el de dólares por primera vez', gen_random_uuid(), 'carlos@t.t');
{_intento('IGUAL', "perform public.cambiar_minimo_de_pago('usd', 3000, 'otra vez lo mismo, no cambia', gen_random_uuid(), 'c@t.t')")}
select 'VIGENTE|' || moneda || '|' || centimos from public.minimos_de_pago_vigentes();
select 'REG|' || moneda || '|' || coalesce(centimos_antes::text, '-') || '|' || centimos_despues || '|' || hecho_por_email
  from public.cambios_de_minimo order by id;
{_intento('UPDATE', "update public.cambios_de_minimo set centimos_despues = 1")}
{_intento('DELETE', "delete from public.cambios_de_minimo")}
select 'R|' || k || '|' || v from zz_r;
rollback;
""")
    assert _lineas(salida, "CAMBIO") == ["2500|1000"]
    assert _lineas(salida, "NUEVA") == ["-|3000"]
    assert _lineas(salida, "VIGENTE") == ["eur|1000", "usd|3000"]
    assert _lineas(salida, "REG")[-2:] == ["eur|2500|1000|carlos@t.t", "usd|-|3000|carlos@t.t"]
    assert _r(salida) == {"SIN_MOTIVO": "RECHAZADA:22023", "IGUAL": "RECHAZADA:22023",
                          "UPDATE": "RECHAZADA:42501", "DELETE": "RECHAZADA:42501"}


def test_pagar_por_debajo_del_minimo_necesita_su_motivo_y_queda_en_la_orden():
    hasta = f"(select max(id) from public.comisiones where referido_id = {AG})"
    salida = sql(f"""begin;
{PREP}
select public.atribuir_alta({_cuenta('uno')}, 'ZZTA01');
select public.devengar_comision('cus_zzag1', 'in_a', 'inpay_a1', 'ch_a1', null, 'eur', 2900, 2900, clock_timestamp(), null);
select 'NORMAL|' || resultado from public.ordenar_pago({AG}, 'eur', 435, {hasta}, 'acct_zz', gen_random_uuid(), 'c@t.t');
{_intento('CORTO', f"perform public.ordenar_pago_o_saltar_el_minimo({AG}, 'eur', 435, {hasta}, 'acct_zz', gen_random_uuid(), 'c@t.t', 'se va')")}
select 'SALTA|' || resultado from public.ordenar_pago_o_saltar_el_minimo({AG}, 'eur', 435, {hasta}, 'acct_zz',
  gen_random_uuid(), 'c@t.t', 'se va del programa con este saldo');
select 'ORDEN|' || importe_centimos || '|' || minimo_saltado_centimos || '|' || minimo_saltado_motivo
  from public.pagos_a_referidos where referido_id = {AG};
select 'R|' || k || '|' || v from zz_r;
rollback;
""")
    assert _lineas(salida, "NORMAL") == ["por_debajo_del_minimo"]
    assert _r(salida) == {"CORTO": "RECHAZADA:22023"}
    assert _lineas(salida, "SALTA") == ["ordenado"]
    assert _lineas(salida, "ORDEN") == ["435|2500|se va del programa con este saldo"]


# ═══ Cierre (06-10-2026): la baja revoca el enlace ═══════════════
#
# Dos barreras, y cada una con su control, porque una tapa a la otra: si solo
# se mirara «no abre», romper la revocación seguiría en verde gracias al
# filtro de la página, y al revés.
#   1. Dar de baja BORRA el hash en el mismo UPDATE (y no se puede poner otro).
#   2. La página no abre para un trabajador de baja aunque tuviera un hash.

H_VIEJO = "d" * 64


def _baja(roto: str = "") -> str:
    return sql(f"""begin;
{PREP}{roto}
select public.atribuir_alta({_cuenta('uno')}, 'ZZTA01');
select public.poner_seguimiento({AG}, {ANA}, '{H_VIEJO}');
select 'ANTES|' || count(*) from public.seguimiento_de_un_trabajador('{H_VIEJO}');
select 'BAJA|' || public.baja_de_trabajador({AG}, {ANA});
select 'HASH|' || (seguimiento_hash is null) || '|' || (seguimiento_creado_at is null)
  from public.trabajadores_de_agencia where id = {ANA};
select 'ABRE|' || count(*) from public.seguimiento_de_un_trabajador('{H_VIEJO}');
select 'OTRO|' || public.poner_seguimiento({AG}, {ANA}, repeat('e', 64));
-- Darlo de alta otra vez es OTRO trabajador, con otro código: el viejo no revive.
select 'REALTA|' || resultado from public.alta_de_trabajador({AG}, 'Ana Comercial', 'ZZTA02');
select 'REVIVE|' || count(*) from public.seguimiento_de_un_trabajador('{H_VIEJO}');
-- Un hash puesto por otra vía en uno de baja: la página tampoco abre.
update public.trabajadores_de_agencia set seguimiento_hash = '{H_VIEJO}', seguimiento_creado_at = now()
  where id = {ANA};
select 'FORZADO|' || count(*) from public.seguimiento_de_un_trabajador('{H_VIEJO}');
rollback;
""")


def test_dar_de_baja_revoca_el_enlace_y_un_trabajador_de_baja_no_abre():
    salida = _baja()
    assert _lineas(salida, "ANTES") == ["1"]  # antivacío: el enlace abría
    assert _lineas(salida, "BAJA") == ["de_baja"]
    assert _lineas(salida, "HASH") == ["true|true"]       # 1. revocado en el mismo acto
    assert _lineas(salida, "ABRE") == ["0"]
    assert _lineas(salida, "OTRO") == ["de_baja"]          # 1. y no se le pone otro
    assert _lineas(salida, "REALTA") == ["creado"]
    assert _lineas(salida, "REVIVE") == ["0"]
    assert _lineas(salida, "FORZADO") == ["0"]             # 2. la página no abre


_SIN_REVOCAR = ("  set de_baja_at = now(), seguimiento_hash = null, seguimiento_creado_at = null\n",
                "  set de_baja_at = now()\n")
_SIN_FILTRO = ("t.seguimiento_hash = p_hash and t.de_baja_at is null", "t.seguimiento_hash = p_hash")
_ENLACE_A_UNO_DE_BAJA = ("and agencia_id = p_agencia and (p_hash is null or de_baja_at is null);",
                         "and agencia_id = p_agencia;")


def test_CONTROL_la_baja_que_no_revoca_se_pone_roja():
    salida = _baja(_funcion("baja_de_trabajador", _SIN_REVOCAR))
    assert _lineas(salida, "HASH") == ["false|false"]


def test_CONTROL_la_pagina_que_abre_a_uno_de_baja_se_pone_roja():
    salida = _baja(_funcion("seguimiento_de_un_trabajador", _SIN_FILTRO))
    assert _lineas(salida, "FORZADO") == ["1"]


def test_CONTROL_sin_las_dos_barreras_el_que_se_fue_se_lleva_la_cartera():
    salida = _baja(_funcion("baja_de_trabajador", _SIN_REVOCAR) + _funcion("seguimiento_de_un_trabajador", _SIN_FILTRO))
    assert _lineas(salida, "ABRE") == ["1"]


def test_CONTROL_ponerle_un_enlace_nuevo_a_uno_de_baja_se_pone_rojo():
    salida = _baja(_funcion("poner_seguimiento", _ENLACE_A_UNO_DE_BAJA))
    assert _lineas(salida, "OTRO") == ["hecho"]


# ═══ Cierre: el plan con el que llegó, solo si no es el de hoy ═══════════


def _planes(roto: str = "") -> str:
    h = "f" * 64
    return sql(f"""begin;
{PREP}{roto}
update public.accounts set pending_plan = 'pro' where id = {_cuenta('uno')};
update public.accounts set pending_plan = 'elite' where id = {_cuenta('dos')};
select public.atribuir_alta({_cuenta('uno')}, 'ZZTA01');
select public.atribuir_alta({_cuenta('dos')}, 'ZZTA01');
-- Hoy: Uno sigue en Pro (el que eligió); Dos pasó de Elite a Básico.
update public.accounts set plan = 'pro' where id = {_cuenta('uno')};
update public.accounts set plan = 'basico' where id = {_cuenta('dos')};
select 'FILA|' || cuenta_nombre || '|' || plan_al_llegar from public.altas_atribuidas
  where cuenta_id in ({_cuenta('uno')}, {_cuenta('dos')}) order by cuenta_nombre;
select public.poner_seguimiento({AG}, {ANA}, '{h}');
select 'VE|' || salon || '|' || plan || '|' || coalesce(plan_al_llegar, '-')
  from public.seguimiento_de_un_trabajador('{h}') order by salon;
rollback;
""")


def test_el_plan_del_alta_queda_en_la_atribucion_y_se_ve_solo_si_no_es_el_de_hoy():
    salida = _planes()
    assert _lineas(salida, "FILA") == ["zz Salón Dos|elite", "zz Salón Uno|pro"]
    assert _lineas(salida, "VE") == ["zz Salón Dos|basico|elite", "zz Salón Uno|pro|-"]


def test_CONTROL_enseñar_el_plan_del_alta_siempre_se_pone_rojo():
    roto = _funcion("seguimiento_de_un_trabajador",
                    ("case when c.plan is not null and a.plan_al_llegar is distinct from c.plan "
                     "then a.plan_al_llegar::text end", "a.plan_al_llegar::text"))
    assert _lineas(_planes(roto), "VE") == ["zz Salón Dos|basico|elite", "zz Salón Uno|pro|pro"]
