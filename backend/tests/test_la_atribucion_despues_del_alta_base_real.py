"""LA ATRIBUCIÓN QUE NO SE HIZO EN EL ALTA (0078), contra la base de verdad.
Todo en transacciones que se deshacen: al acabar, la base está como estaba.

Los guardas (07-10-2026) que viven en la base, cada uno con su
CONTROL —la base rota a propósito dentro de la transacción— que sale rojo:

  1. UN SALÓN CON ATRIBUCIÓN NO RECIBE OTRA, POR NINGÚN CAMINO. Los caminos se
     DESCUBREN: toda función de `public` que escribe en `altas_atribuidas`. Un
     camino nuevo que este fichero no pruebe, rojo.
  2. FUERA DE PLAZO NO SE ASIGNA: ni el salón a los 31 días, ni el owner a los
     91 (y sí a los 29 y a los 89). El alta es la más antigua entre la cuenta y
     sus centros.
  3. SIN CÓDIGO INVÁLIDO APUNTADO NO HAY REPESCA. Y, recorriendo TODAS las
     repescas, cada una cuelga del código que no valió de SU cuenta.
  4. LA ASIGNACIÓN DEL SALÓN NUNCA DEVENGA SOBRE UN COBRO ANTERIOR, Y LA
     REPESCA NUNCA SE SALTA UN COBRO POSTERIOR AL ALTA. Recorriendo TODAS las
     comisiones: ningún devengo anterior al `devenga_desde` de su atribución.
     Y `devengar_comision` es la de la 0075 letra por letra más la guarda.
  5. UN CÓDIGO DE TRABAJADOR ASIGNADO POR EL SALÓN ES DE SU AGENCIA. Y,
     recorriendo TODAS las atribuciones con trabajador, el trabajador es de la
     agencia a la que se atribuyó.

«Solo el owner repesca» vive en el backend: `test_la_atribucion_despues_del_alta.py`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.test_comisiones_base_real import _LEER_R, _intento, _lineas, _r
from tests.test_un_salon_no_puede_cambiarse_el_plan import sql

pytestmark = [pytest.mark.base_real]

MIGRACIONES = Path(__file__).resolve().parents[2] / "supabase/migrations"
M0075 = MIGRACIONES / "0075_las_comisiones_de_los_referidos.sql"
M0078 = MIGRACIONES / "0078_la_atribucion_despues_del_alta.sql"


def _v(k: str) -> str:
    return f"(select v::uuid from zz where k = '{k}')"


VEND, AG, BEA, IND2 = _v("vendedora"), _v("agencia"), _v("bea"), _v("otra_persona")
USR = "'00000000-0000-0000-0000-0000000000aa'::uuid"
ADM = "'00000000-0000-0000-0000-0000000000bb'::uuid"
MOTIVO = "'El salón tecleó una O por un cero'"

# Vendedora al 15 % (propio); agencia con Bea; un referido pendiente; otra persona.
# Cuentas: «nueva» (hoy), «d29»/«d31» (alta hace 29/31 días), «r89»/«r91» (hace
# 89/91 días, con un código que no valió en su alta), «vieja» (cuenta de hoy,
# centro de hace 400 días: la de antes de la 0039), «sin_rastro» (código que no
# valió, pero de antes de la 0077: sin cuenta).
PREP = """
create temp table zz (k text primary key, v text) on commit drop;
do $$ declare r uuid; ag uuid; p uuid; o uuid; c uuid; nom text; d int; begin
  update public.plataforma_config set comision_referido_pct = 10 where id;
  insert into public.referidos (nombre, email, codigo, estado, comision_pct)
    values ('zz Vendedora', 'zz.vendedora@atr.test', 'ZZV001', 'aprobado', 15) returning id into r;
  insert into public.referidos (nombre, email, codigo, estado, tipo, aprobado_at)
    values ('zz Agencia Este', 'zz.este@atr.test', 'ZZAE01', 'aprobado', 'agencia', now()) returning id into ag;
  insert into public.referidos (nombre, email, codigo, estado)
    values ('zz Pendiente', 'zz.pend@atr.test', 'ZZPD01', 'pendiente') returning id into p;
  insert into public.referidos (nombre, email, codigo, estado)
    values ('zz Otra Persona', 'zz.otra@atr.test', 'ZZOP01', 'aprobado') returning id into o;
  perform public.alta_de_trabajador(ag, 'Bea Comercial', 'ZZTB01');
  insert into zz values ('vendedora', r), ('agencia', ag), ('pendiente', p), ('otra_persona', o),
    ('bea', (select id from public.trabajadores_de_agencia where codigo = 'ZZTB01'));
  foreach nom in array array['nueva', 'd29', 'd31', 'r89', 'r91', 'vieja'] loop
    d := case nom when 'd29' then 29 when 'd31' then 31 when 'r89' then 89 when 'r91' then 91 else 0 end;
    insert into public.accounts (nombre, stripe_customer_id, created_at)
      values ('zz Salón ' || nom, 'cus_zz' || nom, now() - make_interval(days => d)) returning id into c;
    insert into public.salons (account_id, nombre_comercial, created_at)
      values (c, 'zz Salón ' || nom, now() - make_interval(days => case nom when 'vieja' then 400 else d end));
    insert into zz values (nom, c);
  end loop;
  -- Las altas de r89 y r91 llegaron con un código mal tecleado.
  perform public.atribuir_alta((select v::uuid from zz where k = 'r89'), 'ZZV00O');
  perform public.atribuir_alta((select v::uuid from zz where k = 'r91'), 'ZZV00O');
  insert into public.codigos_que_no_valieron (codigo_llegado, motivo) values ('ZZV00O', 'no_existe');
  insert into zz values
    ('q89', (select id from public.codigos_que_no_valieron where cuenta_id = (select v::uuid from zz where k = 'r89'))),
    ('q91', (select id from public.codigos_que_no_valieron where cuenta_id = (select v::uuid from zz where k = 'r91'))),
    ('qsin', (select max(id) from public.codigos_que_no_valieron where cuenta_id is null));
end $$;
"""


def _q(k: str) -> str:
    return f"(select v::bigint from zz where k = '{k}')"


def _asignar(cuenta: str, codigo: str = "ZZV001", referido: str = VEND, trabajador: str = "null") -> str:
    return (f"(select resultado from public.asignar_su_vendedor({_v(cuenta)}, '{codigo}', {referido}, "
            f"{trabajador}, {USR}, 'admin@salon.test'))")


def _repescar(q: str, codigo: str = "ZZV001", referido: str = VEND, trabajador: str = "null",
              motivo: str = MOTIVO) -> str:
    return (f"(select resultado from public.repescar_atribucion({_q(q)}, '{codigo}', {referido}, {trabajador}, "
            f"{motivo}, {ADM}, 'owner@estetixapp.test'))")


def _funcion(nombre: str, *cambios: tuple[str, str], migracion: Path = M0078) -> str:
    texto = migracion.read_text(encoding="utf-8")
    m = re.search(rf"create (or replace )?function public\.{nombre}\(.*?\n\$\$;\n", texto, re.S)
    assert m, nombre
    cuerpo = re.sub(r"^create (or replace )?function", "create or replace function", m.group(0))
    for antes, despues in cambios:
        assert cuerpo.count(antes) == 1, (nombre, antes)
        cuerpo = cuerpo.replace(antes, despues)
    return cuerpo


def _en_transaccion(cuerpo: str, roto: str = "") -> str:
    return sql(f"begin;\n{PREP}{roto}\n{cuerpo}\nrollback;\n")


# ═══ 1. Un salón con atribución no recibe otra, por ningún camino ═════════

# Los caminos que este fichero sabe probar, con la llamada sobre una cuenta que
# YA tiene atribución. Un camino descubierto que no esté aquí, rojo.
CAMINOS = {
    "atribuir_alta": "public.atribuir_alta({cuenta}, 'ZZOP01')",
    "asignar_su_vendedor": "(select resultado from public.asignar_su_vendedor({cuenta}, 'ZZOP01', "
                           f"{IND2}, null, {USR}, 'a@b.test'))",
    "repescar_atribucion": "(select resultado from public.repescar_atribucion({rastro}, 'ZZOP01', "
                           f"{IND2}, null, {MOTIVO}, {ADM}, 'o@e.test'))",
}

_ESCRITORES = """
select 'ESCRIBE|' || p.proname from pg_proc p join pg_namespace n on n.oid = p.pronamespace
where n.nspname = 'public' and p.prosrc ~* 'insert\\s+into\\s+(public\\.)?altas_atribuidas'
order by 1;
"""


def escritores(roto: str = "") -> list[str]:
    return _lineas(_en_transaccion(_ESCRITORES, roto), "ESCRIBE")


def test_los_caminos_que_escriben_una_atribucion_estan_todos_probados():
    hallados = escritores()
    assert set(hallados) == set(CAMINOS), f"camino sin probar o probado de más: {hallados}"


def test_CONTROL_un_camino_nuevo_sin_probar_se_pone_rojo():
    roto = ("create function public.zz_otra_puerta(c uuid) returns void language sql as $f$ "
            "insert into public.altas_atribuidas (referido_id, cuenta_id, cuenta_nombre, codigo_llegado, pct, "
            "pct_de_donde) values (gen_random_uuid(), c, 'x', 'x', 1, 'global') $f$;")
    assert set(escritores(roto)) != set(CAMINOS)


def _segunda_por_cada_camino(roto: str = "") -> str:
    """La cuenta r89 ya atribuida (por la repesca); cada camino lo intenta otra vez."""
    llamadas = "\n".join(
        f"select 'CAMINO|{n}|' || coalesce(({c.format(cuenta=_v('r89'), rastro=_q('q89'))})::text, '-');"
        for n, c in sorted(CAMINOS.items()))
    return _en_transaccion(f"""
select 'PRIMERA|' || {_repescar('q89')};
{llamadas}
select 'FILAS|' || count(*) || '|' || (min(referido_id::text) = {VEND}::text)
  from public.altas_atribuidas where cuenta_id = {_v('r89')};
select 'NUEVA|' || {_asignar('nueva')};
select 'NUEVA2|' || {_asignar('nueva', 'ZZOP01', IND2)};
select 'NUEVA3|' || {_repescar('q89', 'ZZOP01', IND2)};
select 'FILAS_NUEVA|' || count(*) from public.altas_atribuidas where cuenta_id = {_v('nueva')};
""", roto)


def test_un_salon_con_atribucion_no_recibe_otra_por_ningun_camino():
    salida = _segunda_por_cada_camino()
    assert _lineas(salida, "PRIMERA") == ["repescada"]
    assert sorted(_lineas(salida, "CAMINO")) == [
        "asignar_su_vendedor|ya_atribuida", "atribuir_alta|ya_atribuida", "repescar_atribucion|ya_atribuida"]
    assert _lineas(salida, "FILAS") == ["1|true"]  # una, y la primera
    assert _lineas(salida, "NUEVA") == ["asignada"]
    assert _lineas(salida, "NUEVA2") == ["ya_atribuida"]
    assert _lineas(salida, "FILAS_NUEVA") == ["1"]


def test_CONTROL_sin_la_unica_y_sin_la_comprobacion_entra_una_segunda():
    # «Una por cuenta» convertida en «una por cuenta Y vendedor», y la comprobación quitada.
    roto = ("alter table public.altas_atribuidas drop constraint una_atribucion_por_cuenta;\n"
            "alter table public.altas_atribuidas add constraint una_atribucion_por_cuenta "
            "unique (cuenta_id, referido_id);\n"
            + _funcion("asignar_su_vendedor",
                       ("if exists (select 1 from public.altas_atribuidas a where a.cuenta_id = p_cuenta) then",
                        "if false then")))
    salida = _segunda_por_cada_camino(roto)
    assert _lineas(salida, "FILAS_NUEVA") == ["2"]


# ═══ 2. Fuera de plazo no se asigna ═══════════════════════════════════════

_PLAZOS = f"""
select 'SALON|29|' || {_asignar('d29')};
select 'SALON|31|' || {_asignar('d31')};
select 'SALON|vieja|' || {_asignar('vieja')};
select 'PUEDE|31|' || resultado from public.puede_asignar_su_vendedor({_v('d31')});
select 'OWNER|89|' || {_repescar('q89')};
select 'OWNER|91|' || {_repescar('q91')};
select 'LISTA|' || count(*) filter (where cuenta_id = {_v('r91')}) from public.salones_para_repescar();
"""


def test_fuera_de_plazo_no_se_asigna_ni_el_salon_ni_el_owner():
    salida = _en_transaccion(_PLAZOS)
    assert _lineas(salida, "SALON") == ["29|asignada", "31|fuera_de_plazo", "vieja|fuera_de_plazo"]
    assert _lineas(salida, "PUEDE") == ["31|fuera_de_plazo"]
    assert _lineas(salida, "OWNER") == ["89|repescada", "91|fuera_de_plazo"]
    assert _lineas(salida, "LISTA") == ["0"]  # a los 91 ni sale en la lista


@pytest.mark.parametrize("roto, linea, esperado", [
    (_funcion("asignar_su_vendedor", ("if now() > v_alta + public.plazo_para_atribuir('salon') then", "if false then")),
     "SALON", "31|asignada"),
    (_funcion("repescar_atribucion",
              ("if now() > v_alta + public.plazo_para_atribuir('repesca') then", "if false then")),
     "OWNER", "91|repescada"),
    # El plazo de los dos, alargado en su único sitio.
    (_funcion("plazo_para_atribuir", ("interval '30 days'", "interval '40 days'")), "SALON", "31|asignada"),
    # El alta leída solo de la cuenta: una cuenta de antes de la 0039 parece nueva.
    (_funcion("alta_de_la_cuenta", ("least(a.created_at, (select min(s.created_at) from public.salons s "
                                    "where s.account_id = a.id))", "a.created_at")),
     "SALON", "vieja|asignada"),
], ids=["salon_sin_plazo", "owner_sin_plazo", "plazo_alargado", "alta_sin_los_centros"])
def test_CONTROL_quitar_el_plazo_se_pone_rojo(roto, linea, esperado):
    assert esperado in _lineas(_en_transaccion(_PLAZOS, roto), linea)


# ═══ 3. Sin código inválido apuntado no hay repesca ═══════════════════════

# En TODA la tabla: una repesca cuelga del código que no valió de SU cuenta.
_REPESCAS_SIN_RASTRO = """
select 'SIN_RASTRO|' || count(*) from public.altas_atribuidas a
where a.origen = 'repesca' and not exists (
  select 1 from public.codigos_que_no_valieron q where q.id = a.codigo_que_no_valio_id and q.cuenta_id = a.cuenta_id);
"""


def test_sin_codigo_invalido_apuntado_no_hay_repesca():
    salida = _en_transaccion(f"""
select 'SIN_CUENTA|' || {_repescar('qsin')};
select 'NO_EXISTE|' || (select resultado from public.repescar_atribucion(-1, 'ZZV001', {VEND}, null, {MOTIVO},
                                                                         {ADM}, 'o@e.test'));
select 'BUENA|' || {_repescar('q89')};
{_REPESCAS_SIN_RASTRO}""")
    assert _lineas(salida, "SIN_CUENTA") == ["sin_rastro"] and _lineas(salida, "NO_EXISTE") == ["sin_rastro"]
    assert _lineas(salida, "BUENA") == ["repescada"]
    assert _lineas(salida, "SIN_RASTRO") == ["0"]


def test_cada_origen_lleva_lo_suyo_o_no_se_escribe():
    """La tabla misma: una repesca sin su código que no valió, o sin motivo; una
    del salón sin quién; una de las de siempre con «devenga desde»: rechazadas."""
    base = ("insert into public.altas_atribuidas (referido_id, cuenta_id, cuenta_nombre, codigo_llegado, pct, "
            "pct_de_donde, origen, devenga_desde, asignada_por, asignada_por_email, motivo, codigo_que_no_valio_id) "
            f"values ({VEND}, {_v('nueva')}, 'x', 'x', 1, 'global', ")
    salida = _en_transaccion(
        _intento("REPESCA_SIN_CODIGO", base + f"'repesca', now(), {ADM}, 'o@e.test', {MOTIVO}, null)")
        + _intento("REPESCA_SIN_MOTIVO", base + f"'repesca', now(), {ADM}, 'o@e.test', 'corto', {_q('q89')})")
        + _intento("SALON_SIN_QUIEN", base + "'salon', now(), null, null, null, null)")
        + _intento("ALTA_CON_DESDE", base + "'alta', now(), null, null, null, null)")
        + _intento("SALON_BIEN", base + f"'salon', now(), {USR}, 'a@b.test', null, null)")
        + _LEER_R)
    assert _r(salida) == {"REPESCA_SIN_CODIGO": "RECHAZADA:23514", "REPESCA_SIN_MOTIVO": "RECHAZADA:23514",
                          "SALON_SIN_QUIEN": "RECHAZADA:23514", "ALTA_CON_DESDE": "RECHAZADA:23514",
                          "SALON_BIEN": "PASA"}


@pytest.mark.parametrize("roto", [
    # Una repesca sobre un código que no valió de OTRA cuenta.
    f"""do $$ begin
  insert into public.altas_atribuidas (referido_id, cuenta_id, cuenta_nombre, codigo_llegado, pct, pct_de_donde,
    origen, devenga_desde, asignada_por, asignada_por_email, motivo, codigo_que_no_valio_id)
  values ({VEND}, {_v('nueva')}, 'x', 'x', 1, 'global', 'repesca', now(), {ADM}, 'o@e.test', {MOTIVO},
          {_q('q91')});
end $$;""",
    # La comprobación del rastro, quitada, y la tabla sin su restricción.
    "alter table public.altas_atribuidas drop constraint cada_origen_con_lo_suyo;\n"
    "alter table public.altas_atribuidas drop constraint altas_atribuidas_codigo_que_no_valio_id_fkey;\n"
    + _funcion("repescar_atribucion",
               ("if not found or v_rastro.cuenta_id is null then", "if false then"),
               ("cuenta_id := v_rastro.cuenta_id;", f"cuenta_id := {_v('nueva')}; v_rastro.cuenta_id := cuenta_id;"))
    + f"\nselect public.repescar_atribucion(-1, 'ZZV001', {VEND}, null, {MOTIVO}, {ADM}, 'o@e.test');",
], ids=["rastro_de_otra_cuenta", "sin_comprobar_el_rastro"])
def test_CONTROL_una_repesca_sin_su_rastro_se_pone_rojo(roto):
    assert _lineas(_en_transaccion(_REPESCAS_SIN_RASTRO, roto), "SIN_RASTRO") != ["0"]


# ═══ 4. Desde cuándo devenga ══════════════════════════════════════════════

# En TODA la tabla: ningún devengo anterior a desde cuándo devenga su atribución.
_ANTES_DE_TIEMPO = """
select 'ANTES|' || count(*) from public.comisiones c
join public.altas_atribuidas a on a.cuenta_id = c.cuenta_id and a.referido_id = c.referido_id
where c.tipo = 'devengo' and a.devenga_desde is not null and c.ocurrido_at < a.devenga_desde;
"""


def _devengar(cuenta: str, n: int, cuando: str) -> str:
    return (f"(select resultado from public.devengar_comision('cus_zz{cuenta}', 'in_zz{n}', 'inpay_zz{n}', "
            f"'ch_zz{n}', null, 'eur', 4900, 4900, {cuando}, null))")


_DEVENGOS = f"""
select 'ASIGNA|' || {_asignar('d29')};
select 'SALON_ANTES|' || {_devengar('d29', 1, "now() - interval '1 second'")};
select 'SALON_DESPUES|' || {_devengar('d29', 2, "clock_timestamp() + interval '1 second'")};
select 'REPESCA|' || {_repescar('q89')};
select 'REPESCA_PASADO|' || {_devengar('r89', 3, "now() - interval '88 days'")};
select 'REPESCA_PASADO2|' || {_devengar('r89', 4, "now() - interval '30 days'")};
select 'IMPORTES|' || string_agg(comision_centimos::text || '@' || pct, ',' order by id)
  from public.comisiones where cuenta_id in ({_v('d29')}, {_v('r89')});
{_ANTES_DE_TIEMPO}"""


def test_el_salon_devenga_desde_la_asignacion_y_la_repesca_desde_el_alta():
    salida = _en_transaccion(_DEVENGOS)
    assert _lineas(salida, "ASIGNA") == ["asignada"]
    assert _lineas(salida, "SALON_ANTES") == ["antes_de_la_atribucion"]
    assert _lineas(salida, "SALON_DESPUES") == ["devengada"]
    assert _lineas(salida, "REPESCA") == ["repescada"]
    # Los dos cobros anteriores a la repesca (y posteriores al alta) devengan.
    assert _lineas(salida, "REPESCA_PASADO") == ["devengada"]
    assert _lineas(salida, "REPESCA_PASADO2") == ["devengada"]
    # 49 € al 15 % (propio de la vendedora) = 735 céntimos, las tres.
    assert _lineas(salida, "IMPORTES") == ["735@15.00,735@15.00,735@15.00"]
    assert _lineas(salida, "ANTES") == ["0"]


def test_CONTROL_sin_la_guarda_el_salon_devenga_un_cobro_anterior():
    roto = _funcion("devengar_comision", (
        "if v_alta.devenga_desde is not null and coalesce(p_pagado_at, now()) < v_alta.devenga_desde then",
        "if false then"))
    salida = _en_transaccion(_DEVENGOS, roto)
    assert _lineas(salida, "SALON_ANTES") == ["devengada"]
    assert _lineas(salida, "ANTES") != ["0"]


def test_CONTROL_una_repesca_que_devengara_desde_la_repesca_se_salta_los_cobros_pasados():
    roto = _funcion("repescar_atribucion", ("'repesca', v_alta, p_admin", "'repesca', clock_timestamp(), p_admin"))
    salida = _en_transaccion(_DEVENGOS, roto)
    assert _lineas(salida, "REPESCA_PASADO") == ["antes_de_la_atribucion"]


def _cuerpo(migracion: Path) -> str:
    texto = migracion.read_text(encoding="utf-8")
    m = re.search(r"function public\.devengar_comision\(.*?\nas \$\$\n(.*?)\n\$\$;", texto, re.S)
    assert m
    return m.group(1)


def sin_la_guarda(cuerpo: str) -> str:
    return re.sub(r"\n  -- ── 0078 ──.*?-- ── fin 0078 ──\n", "", cuerpo, flags=re.S)


def test_el_calculo_de_la_comision_es_el_de_la_0075_letra_por_letra():
    """Lo único que añade la 0078 a `devengar_comision` es la guarda marcada."""
    assert sin_la_guarda(_cuerpo(M0078)) == _cuerpo(M0075)
    assert "antes_de_la_atribucion" in _cuerpo(M0078)
    # Y es la que hay en la base.
    assert "antes_de_la_atribucion" in sql(
        "select prosrc from pg_proc where proname = 'devengar_comision' and pronamespace = 'public'::regnamespace;")


def test_CONTROL_tocar_el_calculo_se_pone_rojo():
    tocado = _cuerpo(M0078).replace("v_comision := round(p_base::numeric * v_pct / 100);",
                                    "v_comision := floor(p_base::numeric * v_pct / 100);")
    assert sin_la_guarda(tocado) != _cuerpo(M0075)


# ═══ 5. El código de un trabajador es de su agencia ═══════════════════════

# En TODA la tabla: el trabajador de una atribución es de la agencia a la que se atribuyó.
_TRABAJADOR_DE_OTRA = """
select 'DE_OTRA|' || count(*) from public.altas_atribuidas a
where a.trabajador_id is not null and not exists (
  select 1 from public.trabajadores_de_agencia t where t.id = a.trabajador_id and t.agencia_id = a.referido_id);
"""

_TRABAJADOR = f"""
select 'VE|' || referido_nombre || '|' || trabajador_nombre from public.vendedor_de_un_codigo(' zztb01 ');
select 'ASIGNA|' || {_asignar('nueva', 'zztb01', AG, BEA)};
select 'FILA|' || (referido_id = {AG}) || '|' || (trabajador_id = {BEA}) || '|' || trabajador_nombre || '|' || origen
  from public.altas_atribuidas where cuenta_id = {_v('nueva')};
{_TRABAJADOR_DE_OTRA}"""


def test_el_codigo_de_un_trabajador_asignado_por_el_salon_es_de_su_agencia():
    salida = _en_transaccion(_TRABAJADOR)
    assert _lineas(salida, "VE") == ["zz Agencia Este|Bea Comercial"]
    assert _lineas(salida, "ASIGNA") == ["asignada"]
    assert _lineas(salida, "FILA") == ["true|true|Bea Comercial|salon"]
    assert _lineas(salida, "DE_OTRA") == ["0"]


def test_CONTROL_atribuirlo_a_otro_con_el_trabajador_dentro_se_pone_rojo():
    roto = _funcion("asignar_su_vendedor", ("values (v_referido.id, p_cuenta, v_nombre, left(btrim(p_codigo), 64),",
                                            f"values ({IND2}, p_cuenta, v_nombre, left(btrim(p_codigo), 64),"))
    assert _lineas(_en_transaccion(_TRABAJADOR, roto), "DE_OTRA") == ["1"]


# ═══ Lo que lo rodea ══════════════════════════════════════════════════════


def test_no_valen_ni_un_pendiente_ni_un_trabajador_de_baja_y_la_confirmacion_es_la_persona_vista():
    salida = _en_transaccion(f"""
select 'PEND|' || {_asignar('nueva', 'ZZPD01', _v('pendiente'))};
select 'VE_PEND|' || count(*) from public.vendedor_de_un_codigo('ZZPD01');
select 'BAJA|' || public.baja_de_trabajador({AG}, {BEA});
select 'TRAB_BAJA|' || {_asignar('nueva', 'ZZTB01', AG, BEA)};
-- Se le enseñó la vendedora y confirma con el código de otra persona.
select 'OTRA|' || {_asignar('nueva', 'ZZOP01', VEND)};
select 'NADA|' || count(*) from public.altas_atribuidas where cuenta_id = {_v('nueva')};
""")
    assert _lineas(salida, "PEND") == ["no_vale"] and _lineas(salida, "VE_PEND") == ["0"]
    assert _lineas(salida, "TRAB_BAJA") == ["no_vale"]
    assert _lineas(salida, "OTRA") == ["ha_cambiado"]
    assert _lineas(salida, "NADA") == ["0"]


def test_el_porcentaje_se_congela_y_queda_quien_cuando_y_por_que():
    salida = _en_transaccion(f"""
select 'A|' || {_asignar('d29')};
update public.referidos set comision_pct = 20 where id = {VEND};
select 'R|' || {_repescar('q89')};
select 'FILA|' || origen || '|' || pct || '|' || coalesce(asignada_por::text, '-') || '|' || asignada_por_email
       || '|' || coalesce(motivo, '-') || '|' || coalesce((codigo_que_no_valio_id = {_q('q89')})::text, '-')
       || '|' || (devenga_desde = atribuida_el) || '|' || (devenga_desde = public.alta_de_la_cuenta(cuenta_id))
  from public.altas_atribuidas where cuenta_id in ({_v('d29')}, {_v('r89')}) order by origen;
select 'DEVENGO|' || pct from public.comisiones where cuenta_id = {_v('d29')};
select 'D|' || {_devengar('d29', 9, "clock_timestamp() + interval '1 second'")};
select 'PCT|' || pct from public.comisiones where stripe_cobro = 'ch_zz9';
""")
    assert _lineas(salida, "FILA") == [
        f"repesca|20.00|00000000-0000-0000-0000-0000000000bb|owner@estetixapp.test|El salón tecleó una O por un cero"
        "|true|false|true",
        "salon|15.00|00000000-0000-0000-0000-0000000000aa|admin@salon.test|-|-|true|false",
    ]
    # El de la asignación (15), aunque la vendedora vaya hoy al 20.
    assert _lineas(salida, "PCT") == ["15.00"]


def test_sin_motivo_no_hay_repesca():
    salida = _en_transaccion(
        _intento("CORTO", f"perform public.repescar_atribucion({_q('q89')}, 'ZZV001', {VEND}, null, 'corto', "
                          f"{ADM}, 'o@e.test')")
        + _intento("SIN_QUIEN", f"perform public.asignar_su_vendedor({_v('nueva')}, 'ZZV001', {VEND}, null, null, '')")
        + _LEER_R)
    assert _r(salida) == {"CORTO": "RECHAZADA:22023", "SIN_QUIEN": "RECHAZADA:22023"}


def test_las_funciones_nuevas_son_solo_de_service_role():
    filas = sql("""
select p.proname || '|' || string_agg(distinct r.rolname, ',' order by r.rolname)
from pg_proc p join pg_namespace n on n.oid = p.pronamespace
cross join pg_roles r
where n.nspname = 'public' and r.rolname in ('anon', 'authenticated', 'service_role')
  and p.proname in ('asignar_su_vendedor', 'repescar_atribucion', 'vendedor_de_un_codigo',
                    'puede_asignar_su_vendedor', 'salones_para_repescar', 'alta_de_la_cuenta',
                    'atribucion_de_la_cuenta')
  and has_function_privilege(r.rolname, p.oid, 'execute')
group by p.proname order by 1;""").splitlines()
    assert len(filas) == 7 and all(f.endswith("|service_role") for f in filas), filas
