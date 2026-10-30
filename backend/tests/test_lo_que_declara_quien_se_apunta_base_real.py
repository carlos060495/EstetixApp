"""LOTE DE ARREGLOS DE REFERIDOS (0079), contra la base de verdad. Todo en
transacciones que se deshacen: al acabar, la base está como estaba.

Los guardas (07-10-2026) que viven en la base, cada uno con su
CONTROL —la base rota a propósito dentro de la transacción— que sale rojo:

  1. CONVERTIR NO TOCA NADA DEL DINERO. Se DESCUBREN todas las tablas que
     apuntan a `referidos` (claves ajenas de `pg_constraint`): atribuciones con
     su % congelado, comisiones devengadas, cuenta de Stripe, pagos... Se fotografía
     todo lo del referido, y su propia fila menos `tipo`, antes y después de
     convertir. Lo único que puede cambiar es `tipo`, y lo único que crece es
     su registro (`cambios_de_tipo`, una fila).
  2. DE AGENCIA A PERSONA CON TRABAJADORES ACTIVOS SE RECHAZA (la función y,
     por debajo, el trigger). Con todos de baja, sí.
  3. LO DECLARADO NO FIJA EL TIPO: una agencia sin aprobación no puede existir
     (`una_agencia_la_decide_el_panel`), lo declarado no se cambia después, y
     recorriendo `pg_proc`: toda función que escribe `referidos.tipo` no mira
     `tipo_declarado`.

«Solo el owner convierte» vive en el backend: `test_lo_que_declara_quien_se_apunta.py`.
"""

from __future__ import annotations

import pytest

from tests.test_comisiones_base_real import _LEER_R, _intento, _lineas, _r
from tests.test_un_salon_no_puede_cambiarse_el_plan import sql

pytestmark = [pytest.mark.base_real]


def _v(k: str) -> str:
    return f"(select v::uuid from zz where k = '{k}')"


PERSONA, AGENCIA, SOLO_BAJAS = _v("persona"), _v("agencia"), _v("solo_bajas")
ADM = "'00000000-0000-0000-0000-0000000000bb'::uuid"
MOTIVO = "'Tiene comerciales y no lo sabíamos'"

# Una persona con TODO lo que tiene dinero: % propio, cuenta de Stripe, un salón
# atribuido con su % congelado y una comisión devengada. Una agencia con un
# trabajador activo, y otra cuyos trabajadores están todos de baja.
PREP = """
create temp table zz (k text primary key, v text) on commit drop;
do $$ declare p uuid; ag uuid; sb uuid; c uuid; begin
  update public.plataforma_config set comision_referido_pct = 10 where id;
  insert into public.referidos (nombre, email, codigo, estado, comision_pct, aprobado_at, tipo_declarado)
    values ('zz Persona', 'zz.persona@conv.test', 'ZZCP01', 'aprobado', 15, now(), 'persona') returning id into p;
  insert into public.referidos (nombre, email, codigo, estado, tipo, aprobado_at, tipo_declarado, nombre_comercial)
    values ('zz Agencia', 'zz.agencia@conv.test', 'ZZCA01', 'aprobado', 'agencia', now(), 'empresa', 'zz Agencia SL')
    returning id into ag;
  insert into public.referidos (nombre, email, codigo, estado, tipo, aprobado_at)
    values ('zz Solo bajas', 'zz.bajas@conv.test', 'ZZCB01', 'aprobado', 'agencia', now()) returning id into sb;
  perform public.alta_de_trabajador(ag, 'Bea Activa', 'ZZCT01');
  perform public.alta_de_trabajador(sb, 'Carla Se Fue', 'ZZCT02');
  perform public.baja_de_trabajador(sb, (select id from public.trabajadores_de_agencia where codigo = 'ZZCT02'));
  insert into public.referido_cobros (referido_id, cuenta_stripe) values (p, 'acct_zzconv01');
  insert into public.accounts (nombre, stripe_customer_id) values ('zz Salón Conv', 'cus_zzconv') returning id into c;
  insert into public.salons (account_id, nombre_comercial) values (c, 'zz Salón Conv');
  perform public.atribuir_alta(c, 'ZZCP01');
  perform public.devengar_comision('cus_zzconv', 'in_zzconv', 'inpay_zzconv', 'ch_zzconv01', null, 'eur',
                                   2900, 2900, now(), null);
  insert into zz values ('persona', p), ('agencia', ag), ('solo_bajas', sb), ('cuenta', c);
end $$;
"""


def _en_transaccion(cuerpo: str, roto: str = "") -> str:
    return sql(f"begin;\n{PREP}{roto}\n{cuerpo}\nrollback;\n")


def _convertir(quien: str, a: str, motivo: str = MOTIVO) -> str:
    return (f"(select resultado || coalesce(':' || trabajadores_activos, '') from public.cambiar_tipo_de_referido("
            f"{quien}, '{a}', {motivo}, {ADM}, 'owner@estetixapp.test'))")


# ═══ 1. Convertir no toca nada del dinero ═════════════════════════════════

# La foto de TODO lo de un referido, descubriendo las tablas por sus claves
# ajenas hacia `referidos`. Una línea por tabla; su propia fila, sin `tipo`.
_FOTO = """
create or replace function pg_temp.foto(p uuid, etiqueta text) returns setof text language plpgsql as $f$
declare t record; v text;
begin
  for t in
    -- Las que lo apuntan con clave ajena, y las que lo nombran SIN ella (los
    -- registros de dinero no cuelgan de nadie: `comisiones`, `pagos_a_referidos`).
    select c.conrelid::regclass::text as tabla, a.attname::text as columna
    from pg_constraint c
    join pg_attribute a on a.attrelid = c.conrelid and a.attnum = c.conkey[1]
    where c.contype = 'f' and c.confrelid = 'public.referidos'::regclass and array_length(c.conkey, 1) = 1
    union
    select k.table_name::text, k.column_name::text from information_schema.columns k
    where k.table_schema = 'public' and k.column_name = 'referido_id' and k.data_type = 'uuid'
    order by 1, 2
  loop
    execute format('select coalesce(string_agg(to_jsonb(x)::text, %L order by to_jsonb(x)::text), %L) '
                   'from %s x where %I = $1', ' ; ', '', t.tabla, t.columna) into v using p;
    return next etiqueta || '|' || t.tabla || '.' || t.columna || '|' || v;
  end loop;
  return next etiqueta || '|referidos|' || (select (to_jsonb(r) - 'tipo')::text from public.referidos r where r.id = p);
end $f$;
"""


def _foto_antes_y_despues(quien: str, a: str, roto: str = "") -> tuple[dict, dict, str]:
    salida = _en_transaccion(
        f"{_FOTO}\nselect pg_temp.foto({quien}, 'ANTES');\n"
        f"select 'RES|' || {_convertir(quien, a)};\n"
        f"select pg_temp.foto({quien}, 'DESPUES');\n", roto)
    antes = dict(ln.split("|", 1) for ln in _lineas(salida, "ANTES"))
    despues = dict(ln.split("|", 1) for ln in _lineas(salida, "DESPUES"))
    return antes, despues, _lineas(salida, "RES")[0]


def diferencias(antes: dict, despues: dict) -> dict[str, tuple[str, str]]:
    """Lo que cambió, salvo su propio registro de conversiones."""
    return {t: (antes.get(t, ""), despues.get(t, "")) for t in set(antes) | set(despues)
            if not t.startswith("cambios_de_tipo.") and antes.get(t) != despues.get(t)}


@pytest.mark.parametrize("quien, a", [(PERSONA, "agencia"), (SOLO_BAJAS, "individual")],
                         ids=["persona_a_agencia", "agencia_sin_activos_a_persona"])
def test_convertir_no_toca_nada_del_dinero(quien, a):
    antes, despues, res = _foto_antes_y_despues(quien, a)
    assert res == "cambiado:0"
    assert diferencias(antes, despues) == {}
    # Lo único que crece: su registro, con UNA fila.
    [registro] = [t for t in despues if t.startswith("cambios_de_tipo.")]
    assert antes[registro] == "" and despues[registro].count("tipo_despues") == 1


def test_la_foto_ve_el_dinero_de_verdad():
    """Antivacío: se descubren las tablas de dinero y la persona tiene filas en ellas."""
    antes, _d, _r2 = _foto_antes_y_despues(PERSONA, "agencia")
    for tabla in ("altas_atribuidas.referido_id", "comisiones.referido_id",
                  "referido_cobros.referido_id", "pagos_a_referidos.referido_id"):
        assert tabla in antes, sorted(antes)
    assert '"pct": 15' in antes["altas_atribuidas.referido_id"]
    assert '"comision_centimos": 435' in antes["comisiones.referido_id"]
    assert "acct_zzconv01" in antes["referido_cobros.referido_id"]
    assert '"codigo": "ZZCP01"' in antes["referidos"] and '"comision_pct": 15' in antes["referidos"]


_ROMPER_CONVERSION = {
    # Convertir y, de paso, tocar lo suyo.
    "el_porcentaje": "update public.referidos set tipo = p_tipo, comision_pct = 20 where id = p_referido;",
    "el_codigo": "update public.referidos set tipo = p_tipo, codigo = 'ZZCP99' where id = p_referido;",
    "la_cuenta_de_stripe": ("update public.referidos set tipo = p_tipo where id = p_referido; "
                            "update public.referido_cobros set cuenta_stripe = 'acct_zzotra' "
                            "where referido_id = p_referido;"),
    # «Empezar de cero»: la atribución pasa a otro (por debajo del trigger de inmutables).
    "una_atribucion": ("update public.referidos set tipo = p_tipo where id = p_referido; "
                       "set local session_replication_role = replica; "
                       "update public.altas_atribuidas set pct = 10 where referido_id = p_referido; "
                       "set local session_replication_role = origin;"),
    "el_devengado": ("update public.referidos set tipo = p_tipo where id = p_referido; "
                     "set local session_replication_role = replica; "
                     "delete from public.comisiones where referido_id = p_referido; "
                     "set local session_replication_role = origin;"),
}


def _funcion_rota(cambio: str) -> str:
    from pathlib import Path
    import re

    texto = (Path(__file__).resolve().parents[2] / "supabase/migrations/0079_lo_que_declara_quien_se_apunta.sql"
             ).read_text(encoding="utf-8")
    m = re.search(r"create function public\.cambiar_tipo_de_referido\(.*?\n\$\$;\n", texto, re.S)
    assert m
    cuerpo = m.group(0).replace("create function", "create or replace function")
    antes = "update public.referidos set tipo = p_tipo where id = p_referido;"
    assert cuerpo.count(antes) == 1
    return cuerpo.replace(antes, cambio)


@pytest.mark.parametrize("que", sorted(_ROMPER_CONVERSION))
def test_CONTROL_una_conversion_que_toca_el_dinero_se_pone_roja(que):
    antes, despues, res = _foto_antes_y_despues(PERSONA, "agencia", roto=_funcion_rota(_ROMPER_CONVERSION[que]))
    assert res == "cambiado:0"
    assert diferencias(antes, despues) != {}, que


# ═══ 2. De agencia a persona con trabajadores activos, no ═════════════════

def test_con_trabajadores_activos_no_vuelve_a_persona():
    salida = _en_transaccion(
        f"select 'R|funcion|' || {_convertir(AGENCIA, 'individual')};\n"
        f"{_intento('update_a_mano', "update public.referidos set tipo = 'individual' where id = " + AGENCIA)}"
        f"select 'R|tipo|' || tipo from public.referidos where id = {AGENCIA};\n"
        f"select 'R|registro|' || count(*) from public.cambios_de_tipo where referido_id = {AGENCIA};\n{_LEER_R}")
    r = _r(salida)
    assert r["funcion"] == "tiene_trabajadores:1"
    assert r["update_a_mano"] == "RECHAZADA:23514"
    assert r["tipo"] == "agencia" and r["registro"] == "0"


def test_con_todos_de_baja_si_vuelve_y_no_entra_ningun_trabajador_nuevo():
    salida = _en_transaccion(
        f"select 'R|funcion|' || {_convertir(SOLO_BAJAS, 'individual')};\n"
        f"select 'R|alta|' || resultado from public.alta_de_trabajador({SOLO_BAJAS}, 'Nueva', 'ZZCT03');\n"
        f"{_intento('insert_a_mano', 'insert into public.trabajadores_de_agencia (agencia_id, nombre, codigo) values (' + SOLO_BAJAS + ", 'x', 'ZZCT04')")}"
        f"{_LEER_R}")
    r = _r(salida)
    assert r["funcion"] == "cambiado:0"
    assert r["alta"] == "no_es_una_agencia"
    assert r["insert_a_mano"] == "RECHAZADA:23514"


@pytest.mark.parametrize("roto", [
    # La función sin su comprobación, y el trigger sin la suya.
    _funcion_rota("update public.referidos set tipo = p_tipo where id = p_referido;").replace(
        "if v_activos > 0 then", "if false then") +
    "drop trigger referidos_agencia_con_trabajadores on public.referidos;",
], ids=["sin_comprobar"])
def test_CONTROL_sin_las_dos_comprobaciones_la_agencia_con_activos_vuelve_a_persona(roto):
    salida = _en_transaccion(f"select 'R|funcion|' || {_convertir(AGENCIA, 'individual')};\n", roto)
    assert _r(salida)["funcion"] == "cambiado:0"  # rojo: el guarda de arriba lo cazaría


PENDIENTE = "(select id from public.referidos where email = 'zz.pend@conv.test')"


def test_otros_resultados():
    salida = _en_transaccion(
        f"select 'R|ya_es|' || {_convertir(PERSONA, 'individual')};\n"
        "insert into public.referidos (nombre, email, estado) values ('zz Pend', 'zz.pend@conv.test', 'pendiente');\n"
        f"select 'R|pendiente|' || {_convertir(PENDIENTE, 'agencia')};\n"
        f"select 'R|no_existe|' || {_convertir('gen_random_uuid()', 'agencia')};\n"
        f"{_intento('sin_motivo', 'perform public.cambiar_tipo_de_referido(' + PERSONA + ", 'agencia', 'corto', " + ADM + ", 'o@x.es')")}"
        f"{_LEER_R}")
    r = _r(salida)
    assert r == {"ya_es": "ya_es", "pendiente": "no_aprobado", "no_existe": "no_existe", "sin_motivo": "RECHAZADA:22023"}


def test_el_registro_de_conversiones_no_se_toca_y_solo_lo_lee_el_backend():
    salida = _en_transaccion(
        f"select {_convertir(PERSONA, 'agencia')};\n"
        f"{_intento('update', "update public.cambios_de_tipo set motivo = 'otra cosa distinta'")}"
        f"{_intento('delete', 'delete from public.cambios_de_tipo')}"
        f"{_intento('servicio_inserta', 'set local role service_role; insert into public.cambios_de_tipo (referido_id, tipo_antes, tipo_despues, motivo, hecho_por, hecho_por_email) values (' + PERSONA + ", 'agencia', 'individual', 'a mano sin funcion', " + ADM + ", 'o@x.es')")}"
        f"{_intento('salon_lee', 'set local role authenticated; perform 1 from public.cambios_de_tipo')}"
        f"{_intento('salon_convierte', 'set local role authenticated; perform public.cambiar_tipo_de_referido(' + PERSONA + ", 'individual', " + MOTIVO + ', ' + ADM + ", 'o@x.es')")}"
        f"{_intento('servicio_lee', 'set local role service_role; perform 1 from public.cambios_de_tipo')}"
        f"{_LEER_R}")
    r = _r(salida)
    assert r["update"] == r["delete"] == "RECHAZADA:42501"
    assert r["servicio_inserta"] == r["salon_lee"] == r["salon_convierte"] == "RECHAZADA:42501"
    assert r["servicio_lee"] == "PASA"


# ═══ 3. Lo declarado no fija el tipo ══════════════════════════════════════

_DECLARADO = {
    # El registro público, como lo hace el backend: pendiente, con lo declarado.
    "registro_con_agencia": "insert into public.referidos (nombre, email, estado, tipo, tipo_declarado, nombre_comercial) "
                            "values ('zz X', 'zz.x@conv.test', 'pendiente', 'agencia', 'empresa', 'X SL')",
    # Pasarlo a agencia sin aprobarlo.
    "update_sin_aprobar": "update public.referidos set tipo = 'agencia' where email = 'zz.dec@conv.test'",
    # Cambiar lo que declaró.
    "cambiar_lo_declarado": "update public.referidos set tipo_declarado = 'persona' where email = 'zz.dec@conv.test'",
    "nombre_comercial_de_persona": "insert into public.referidos (nombre, email, estado, tipo_declarado, nombre_comercial) "
                                   "values ('zz Y', 'zz.y@conv.test', 'pendiente', 'persona', 'Y SL')",
}


def _declarado(roto: str = "") -> dict[str, str]:
    cuerpo = ("insert into public.referidos (nombre, email, estado, tipo_declarado, nombre_comercial) "
              "values ('zz Dec', 'zz.dec@conv.test', 'pendiente', 'empresa', 'Dec SL');\n"
              + "".join(_intento(k, s) for k, s in _DECLARADO.items())
              + "select 'R|tipo_del_pendiente|' || tipo from public.referidos where email = 'zz.dec@conv.test';\n"
              + _LEER_R)
    return _r(_en_transaccion(cuerpo, roto))


def test_lo_declarado_no_fija_el_tipo_en_la_base():
    r = _declarado()
    assert r["registro_con_agencia"] == r["update_sin_aprobar"] == r["nombre_comercial_de_persona"] == "RECHAZADA:23514"
    assert r["cambiar_lo_declarado"] == "RECHAZADA:42501"
    # Declaró empresa y sigue sin tipo decidido: el de por defecto, hasta que el panel elija.
    assert r["tipo_del_pendiente"] == "individual"


def test_CONTROL_sin_la_restriccion_una_agencia_nace_sin_aprobar():
    r = _declarado("alter table public.referidos drop constraint una_agencia_la_decide_el_panel;")
    assert r["registro_con_agencia"] == "PASA" and r["update_sin_aprobar"] == "PASA"


# Toda función de la base que escribe `referidos.tipo` (descubierta en pg_proc).
_ESCRITORES_DEL_TIPO = r"""
select 'F|' || p.proname || '|' || (p.prosrc ~* 'tipo_declarado')::text
from pg_proc p join pg_namespace n on n.oid = p.pronamespace
where n.nspname = 'public' and p.prosrc ~* 'update\s+(public\.)?referidos\s+set[^;]*\mtipo\M\s*=';
"""


def _escritores(roto: str = "") -> dict[str, str]:
    return dict(ln.split("|", 1) for ln in _lineas(_en_transaccion(_ESCRITORES_DEL_TIPO, roto), "F"))


def test_ninguna_funcion_que_escribe_el_tipo_mira_lo_declarado():
    escritores = _escritores()
    assert "cambiar_tipo_de_referido" in escritores  # antivacío
    assert {f for f, mira in escritores.items() if mira == "true"} == set()


def test_CONTROL_una_funcion_que_pasa_las_empresas_a_agencia_se_pone_roja():
    roto = ("create function public.zz_empresas_a_agencia() returns void language sql as $x$ "
            "update public.referidos set tipo = 'agencia' where tipo_declarado = 'empresa' $x$;")
    assert _escritores(roto).get("zz_empresas_a_agencia") == "true"


# ═══ El dueño de un salón ═════════════════════════════════════════════════

def test_un_referido_con_el_correo_de_una_administradora_se_descubre():
    salida = _en_transaccion("""
do $$ declare c uuid; s uuid; begin
  insert into public.accounts (nombre) values ('zz Cuenta Dueña') returning id into c;
  insert into public.salons (account_id, nombre_comercial) values (c, 'zz Centro Uno') returning id into s;
  insert into public.salons (account_id, nombre_comercial) values (c, 'zz Centro Dos');
  insert into public.users (salon_id, account_id, nombre, email, rol)
    values (s, c, 'Dueña', 'Zz.Duena@Salon.test', 'admin');
  insert into public.referidos (nombre, email, estado) values ('zz Dueña', ' zz.duena@salon.test', 'pendiente');
  insert into public.referidos (nombre, email, estado) values ('zz Ajena', 'zz.ajena@salon.test', 'pendiente');
end $$;
select 'D|' || r.nombre || '|' || d.cuenta_nombre || '|' || array_to_string(d.salones, ',')
from public.referidos_duenos_de_salon() d join public.referidos r on r.id = d.referido_id
where r.nombre like 'zz %';
""")
    [linea] = _lineas(salida, "D")  # la ajena no sale
    nombre, cuenta, salones = linea.split("|")
    assert (nombre, cuenta) == ("zz Dueña", "zz Cuenta Dueña")
    assert sorted(salones.split(",")) == ["zz Centro Dos", "zz Centro Uno"]
