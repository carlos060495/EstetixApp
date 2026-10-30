"""EL BORRADO A 90 DÍAS (0068), contra la base de verdad. NO SE ENCIENDE NUNCA
fuera de una transacción que se deshace.

  1. LA SELECCIÓN es una sola (`relojes_del_plazo`): se prueban sus reglas
     —el día 90 todavía es suyo, el calendario es el de la cuenta, quién nunca
     pagó, qué estados han terminado— y que sus constantes son las de Python.
  2. EL BORRADO, de verdad: apagado se niega con un error; encendido borra lo
     que dice el simulacro, ni una fila más ni una menos, deja lo nuestro y
     apunta la constancia; borra HASTA el tope, las más antiguas primero (0089);
     una cuenta que falla vuelve entera y no frena a las demás.
  3. EL GUARDA. Camina el esquema y el código y se pone ROJO si: el borrado deja
     de usar la selección y el plan del simulacro o los reimplementa; aparece
     una tabla de la cuenta que el borrado no alcanza; el interruptor
     desaparece, pasa a ser un parámetro o nace encendido; se quita el tope;
     la llama alguien que no sea la tarea (0089), o la puede llamar alguien
     que no sea service_role; la constancia gana UPDATE, DELETE o una
     columna con datos personales; deja de ser una sola transacción; o el
     orden salones-antes-que-correcciones se invierte. CADA REGLA, CON SU
     CONTROL: la misma comprobación sobre algo cambiado a propósito sale roja.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.core import plazo_de_datos as plazos
from app.core.config import Settings
from app.core.simulacro import SE_QUEDA
from app.routers.billing import _TERMINA_DE_VERDAD
from tests.test_un_salon_no_puede_cambiarse_el_plan import sql

pytestmark = [pytest.mark.base_real]

REPO = Path(__file__).resolve().parents[2]
MIGRACION = REPO / "supabase/migrations/0068_el_borrado_a_90_dias.sql"
BORRA = "borrar_cuentas_fuera_de_plazo"
PASADA = "proxima_pasada_de_borrado"
# Las sembradas, dadas por preparadas (Stripe y Storage limpios): lo que hace la tarea.
LLAMAR = f"public.{BORRA}(array(select cuenta from zz_cuentas))"
ENCENDER = ("update public.borrado_interruptor set encendido = true, cambiado_por = 'guarda', "
            "motivo = 'prueba en una transacción que se deshace';")


# ═══ Herramientas ═════════════════════════════════════════════════════════


def _sembrar(n: int, *, dias: int = 100) -> str:
    """`n` cuentas fuera de plazo (suscripción cancelada hace `dias`), cada una
    con un centro, una clienta, su administradora CON SU ACCESO DE AUTH y dos
    entradas suyas en el registro de Auth (una por id, otra solo por correo), y
    UNA CORRECCIÓN DE FICHAJE —que solo se puede borrar después de su centro—.
    Sus ids, en `zz_cuentas`."""
    return f"""
create temp table if not exists zz_cuentas (cuenta uuid, centro uuid, i int, acceso uuid) on commit drop;
do $$ declare a uuid; s uuid; au uuid; begin
  for i in 1..{n} loop
    insert into public.accounts (nombre) values ('zz borrado ' || i) returning id into a;
    insert into public.salons (account_id, nombre_comercial) values (a, 'zz centro ' || i) returning id into s;
    insert into public.clients (salon_id, nombre) values (s, 'zz clienta');
    insert into auth.users (id, email) values (gen_random_uuid(), 'zz' || i || '@borrado.test') returning id into au;
    insert into auth.audit_log_entries (id, payload) values
      (gen_random_uuid(), jsonb_build_object('action', 'login', 'actor_id', au::text,
                                             'actor_username', 'zz' || i || '@borrado.test')::json),
      (gen_random_uuid(), jsonb_build_object('action', 'user_modified', 'actor_id', 'otro',
                                             'traits', jsonb_build_object('user_email', 'ZZ' || i || '@Borrado.test'))::json);
    insert into public.users (account_id, salon_id, nombre, email, rol, auth_user_id)
      values (a, s, 'zz administradora', 'zz' || i || '@borrado.test', 'admin', au);
    insert into public.correcciones_de_fichaje
      (lote, account_id, salon_id, employee_id, accion, marca_id, tipo, ts_despues, motivo, usuario_id)
      values (gen_random_uuid(), a, s, gen_random_uuid(), 'nueva', gen_random_uuid(), 'salida', now(),
              'corrección del guarda del borrado', gen_random_uuid());
    insert into public.subscriptions (account_id, salon_id, estado, terminada_at)
      values (a, s, 'canceled', now() - interval '{dias} days');
    insert into zz_cuentas values (a, s, i, au);
  end loop;
end $$;
"""


def _intento(clave: str, sentencia: str) -> str:
    """Un intento que se DESHACE siempre (pase o no), y dice qué pasó."""
    return f"""
create temp table if not exists zz_r (k text, v text) on commit drop;
do $$ declare n bigint; begin
  begin
    {sentencia};
    get diagnostics n = row_count;
    raise exception using errcode = 'ZZ001', message = n::text;
  exception
    when sqlstate 'ZZ001' then insert into zz_r values ('{clave}', 'PASA');
    when others then insert into zz_r values ('{clave}', 'RECHAZADA:' || sqlstate || ':' || sqlerrm);
  end;
end $$;
"""


def _resultados(salida: str) -> dict[str, str]:
    return {k: v for k, v in (ln.split("|", 2)[1:] for ln in salida.splitlines() if ln.startswith("R|"))}


_LEER_R = "select 'R|' || k || '|' || v from zz_r order by k;\n"


def _base_vacia() -> None:
    fuera = sql("select count(*) from public.cuentas_fuera_de_plazo();")
    assert fuera == "0", (f"la base local ya tiene {fuera} cuentas fuera de plazo: estas pruebas siembran "
                          "las suyas y cuentan con el tope; levanta el local limpio")


# ═══ 1. La selección ══════════════════════════════════════════════════════


def _reloj_de(prep: str, ahora: str) -> str:
    return sql(f"""
begin;
{prep}
select 'X|' || coalesce(string_agg(r.origen || ',' || r.terminada_el || ',' || r.se_borran_el || ','
                                   || r.dias_restantes, ';'), '')
from zz_cuentas z left join public.relojes_del_plazo('{ahora}'::timestamptz, z.cuenta) r on true;
rollback;
""")


def _x(salida: str) -> str:
    return next(ln[2:] for ln in salida.splitlines() if ln.startswith("X|"))


def test_el_dia_90_todavia_es_suyo_y_el_91_ya_no():
    prep = _sembrar(1) + "update public.subscriptions set terminada_at = '2026-10-05T10:00:00Z' " \
                         "where account_id = (select cuenta from zz_cuentas);"
    assert _x(_reloj_de(prep, "2027-01-03T12:00:00Z")) == "suscripcion,2026-10-05,2027-01-03,0"
    assert _x(_reloj_de(prep, "2027-01-04T12:00:00Z")) == "suscripcion,2026-10-05,2027-01-03,-1"
    def fuera(ahora: str) -> str:
        return _x(sql(f"begin;\n{prep}\nselect 'X|' || count(*) from public.cuentas_fuera_de_plazo('{ahora}') "
                      "f join zz_cuentas z on z.cuenta = f.account_id;\nrollback;\n"))
    # La frontera es la medianoche de MADRID (UTC+1 en enero), no la de UTC.
    assert fuera("2027-01-03T22:59:00Z") == "0"
    assert fuera("2027-01-03T23:00:00Z") == "1"


def test_los_dias_son_del_calendario_de_la_cuenta():
    # Las 22:30 UTC del 5 de octubre son las 00:30 del 6 en Madrid y las 23:30 del 5 en Canarias.
    base = _sembrar(1) + "update public.subscriptions set terminada_at = '2026-10-05T22:30:00Z' " \
                         "where account_id = (select cuenta from zz_cuentas);"
    assert _x(_reloj_de(base, "2026-10-06T12:00:00Z")).split(",")[1] == "2026-10-06"
    canarias = base + "update public.salons set zona_horaria = 'Atlantic/Canary' " \
                      "where id = (select centro from zz_cuentas);"
    assert _x(_reloj_de(canarias, "2026-10-06T12:00:00Z")).split(",")[1] == "2026-10-05"


def test_quien_nunca_pago_entra_cuando_habria_acabado_la_prueba_y_sale_al_activarla():
    dias = int(sql("select dias_de_prueba from public.plataforma_config;"))
    sin_sub = "delete from public.subscriptions where account_id = (select cuenta from zz_cuentas);"
    vieja = _sembrar(1) + sin_sub + "update public.accounts set created_at = now() - interval '20 days' " \
                                    "where id = (select cuenta from zz_cuentas);"
    salida = sql(f"""
begin;
{vieja}
select 'X|' || r.origen || ',' || extract(epoch from r.terminada_at - a.created_at)::bigint
from zz_cuentas z join public.accounts a on a.id = z.cuenta
join public.relojes_del_plazo(now(), z.cuenta) r on true;
rollback;
""")
    # Días de 24 horas exactas desde el alta, y los días de su único sitio.
    assert _x(salida) == f"sin_pago,{dias * 86400}"
    nueva = _sembrar(1) + sin_sub + "update public.accounts set created_at = now() - interval '5 days' " \
                                    "where id = (select cuenta from zz_cuentas);"
    assert _x(sql(f"begin;\n{nueva}\nselect 'X|' || count(*) from zz_cuentas z "
                  "join public.relojes_del_plazo(now(), z.cuenta) r on true;\nrollback;\n")) == "0"
    activa = vieja + "insert into public.subscriptions (account_id, salon_id, estado) " \
                     "select cuenta, centro, 'trialing' from zz_cuentas;"
    assert _x(_reloj_de(activa, "2100-01-01T00:00:00Z")) == ""


@pytest.mark.parametrize("estado", ["canceled", "incomplete_expired", "unpaid", "past_due", "active", "trialing"])
def test_solo_tiene_reloj_lo_que_billing_da_por_terminado(estado):
    prep = _sembrar(1) + f"update public.subscriptions set estado = '{estado}' " \
                         "where account_id = (select cuenta from zz_cuentas);"
    tiene = _x(_reloj_de(prep, "2100-01-01T00:00:00Z")) != ""
    assert tiene == (estado in _TERMINA_DE_VERDAD)


def test_las_constantes_son_las_de_python():
    salida = _x(_reloj_de(_sembrar(1), "2100-01-01T00:00:00Z"))
    _, terminada, borrado, _ = salida.split(",")
    from datetime import date
    assert (date.fromisoformat(borrado) - date.fromisoformat(terminada)).days == plazos.PLAZO_DIAS


def test_una_cuenta_ya_borrada_no_vuelve_al_reloj():
    prep = _sembrar(1) + "insert into public.cuentas_borradas (account_id, motivo, origen, se_borraba_el, filas) " \
                         "select cuenta, 'fuera_de_plazo_90_dias', 'suscripcion', current_date, '{}' from zz_cuentas;"
    assert _x(_reloj_de(prep, "2100-01-01T00:00:00Z")) == ""


# ═══ 2. El borrado, de verdad ═════════════════════════════════════════════


def test_apagado_se_niega_con_un_error_y_no_toca_nada():
    _base_vacia()
    salida = sql(f"""
begin;
{_sembrar(1)}
{_intento("apagado", f"perform * from {LLAMAR}")}
select 'X|' || (select count(*) from public.salons s join zz_cuentas z on z.centro = s.id)
       || ',' || (select count(*) from public.cuentas_borradas);
{_LEER_R}
rollback;
""")
    r = _resultados(salida)["apagado"]
    assert r.startswith("RECHAZADA:55000:El borrado a 90 días está APAGADO"), salida
    assert _x(salida).startswith("1,"), salida


def test_encendido_borra_lo_que_dice_el_simulacro_deja_lo_nuestro_y_apunta_la_constancia():
    _base_vacia()
    salida = sql(f"""
begin;
{_sembrar(2)}
create temp table zz_sim on commit drop as
  select z.cuenta, s.tabla, s.filas from zz_cuentas z, public.simulacro_de_borrado(z.cuenta) s where s.paso = 1;
{ENCENDER}
create temp table zz_borradas on commit drop as select * from {LLAMAR} where error is null;
select 'B|' || (select count(*) from zz_borradas);
-- La constancia dice exactamente lo que dijo el simulacro, tabla a tabla.
select 'DISCREPA|' || s.cuenta || '|' || s.tabla || '|' || s.filas || '|' || coalesce(c.filas ->> s.tabla, '-')
from zz_sim s join public.cuentas_borradas c on c.account_id = s.cuenta
where (c.filas ->> s.tabla)::bigint is distinct from s.filas;
select 'SIM|' || count(*) from zz_sim where filas > 0;
-- Lo que se iba, no está; lo nuestro, sí.
select 'QUEDA|' || (select count(*) from public.salons s join zz_cuentas z on z.cuenta = s.account_id)
  || ',' || (select count(*) from public.users u join zz_cuentas z on z.cuenta = u.account_id)
  || ',' || (select count(*) from public.correcciones_de_fichaje c join zz_cuentas z on z.cuenta = c.account_id)
  || ',' || (select count(*) from public.accounts a join zz_cuentas z on z.cuenta = a.id)
  || ',' || (select count(*) from public.subscriptions s join zz_cuentas z on z.cuenta = s.account_id)
  || ',' || (select count(*) from auth.users u join zz_cuentas z on z.acceso = u.id)
  || ',' || (select count(*) from auth.audit_log_entries e where e.payload::text ilike '%@borrado.test%');
select 'LOG|' || c.motivo || ',' || c.origen from public.cuentas_borradas c join zz_cuentas z on z.cuenta = c.account_id;
select 'OTRA|' || count(*) from {LLAMAR};
rollback;
""")
    lineas = salida.splitlines()
    assert "B|2" in lineas, salida
    assert not [ln for ln in lineas if ln.startswith("DISCREPA|")], salida
    assert int(next(ln for ln in lineas if ln.startswith("SIM|"))[4:]) >= 8, salida  # antivacío
    # Centros, usuarios, correcciones, la cuenta, la suscripción, el acceso de
    # Auth y sus entradas en el registro de Auth: nada (0068, 04-10).
    assert "QUEDA|0,0,0,0,0,0,0" in lineas, salida
    assert lineas.count("LOG|fuera_de_plazo_90_dias,suscripcion") == 2, salida
    assert "OTRA|0" in lineas, salida  # una segunda pasada no vuelve a borrar


def test_el_tope_borra_las_mas_antiguas_y_deja_el_resto_para_la_siguiente():
    """0089: el tope vive en la fila del interruptor. Con tres fuera de plazo y
    tope 2, se van las dos que más tiempo llevan; la tercera espera."""
    _base_vacia()
    salida = sql(f"""
begin;
{_sembrar(3)}
-- La 1 terminó hace 300 días, la 2 hace 200, la 3 hace 100: la 3 es la más reciente.
update public.subscriptions s set terminada_at = now() - ((4 - z.i) * 100 || ' days')::interval
  from zz_cuentas z where s.account_id = z.cuenta;
update public.borrado_interruptor set encendido = true, tope_por_pasada = 2, cambiado_por = 'guarda',
  motivo = 'prueba en una transacción que se deshace';
select 'P|' || string_agg(z.i::text, ',' order by p.se_borran_el) || '|' || max(p.fuera_de_plazo)
  from public.{PASADA}() p join zz_cuentas z on z.cuenta = p.account_id;
select 'B|' || string_agg(z.i::text, ',' order by z.i) from {LLAMAR} b join zz_cuentas z on z.cuenta = b.account_id
  where b.error is null;
select 'X|' || string_agg(z.i::text, ',' order by z.i) from zz_cuentas z join public.accounts a on a.id = z.cuenta;
select 'H|' || tope_por_pasada from public.borrado_interruptor_historial order by id desc limit 1;
rollback;
""")
    lineas = salida.splitlines()
    assert "P|1,2|3" in lineas, salida  # la pasada: las dos más antiguas, de tres
    assert "B|1,2" in lineas, salida
    assert _x(salida) == "3", salida  # la más reciente sigue ahí
    assert "H|2" in lineas, salida  # el cambio del tope queda en el historial


def test_el_tope_nace_en_10_y_apagado():
    assert sql("select encendido || '|' || tope_por_pasada from public.borrado_interruptor;") == "false|10"


def test_una_cuenta_que_falla_vuelve_entera_y_no_frena_a_las_demas():
    """Cada cuenta en su bloque (0089): la segunda no se puede borrar (algo la
    sujeta con RESTRICT) y vuelve ENTERA, con su motivo; la primera se borra."""
    _base_vacia()
    salida = sql(f"""
begin;
{_sembrar(2)}
create table public.zz_freno (salon_id uuid references public.salons (id) on delete restrict);
insert into public.zz_freno select centro from zz_cuentas where i = 2;
{ENCENDER}
select 'E|' || z.i || '|' || coalesce(left(b.error, 2), 'ok') from {LLAMAR} b join zz_cuentas z on z.cuenta = b.account_id
  order by z.i;
select 'X|' || (select count(*) from public.salons s join zz_cuentas z on z.centro = s.id)
       || ',' || (select count(*) from public.correcciones_de_fichaje c join zz_cuentas z on z.cuenta = c.account_id)
       || ',' || (select count(*) from public.cuentas_borradas c join zz_cuentas z on z.cuenta = c.account_id)
       || ',' || (select count(*) from public.clients c join zz_cuentas z on z.centro = c.salon_id where z.i = 2);
rollback;
""")
    lineas = salida.splitlines()
    assert "E|1|ok" in lineas and "E|2|23" in lineas, salida
    assert _x(salida) == "1,1,1,1", salida  # de la 2 queda todo: centro, corrección y clienta


def test_solo_borra_las_que_llegan_preparadas():
    """Si Stripe falló con una, la tarea no la da por preparada y la base no la toca."""
    _base_vacia()
    salida = sql(f"""
begin;
{_sembrar(2)}
{ENCENDER}
select 'B|' || count(*) from public.{BORRA}(array(select cuenta from zz_cuentas where i = 1));
select 'X|' || string_agg(z.i::text, ',') from zz_cuentas z join public.accounts a on a.id = z.cuenta;
rollback;
""")
    assert "B|1" in salida.splitlines() and _x(salida) == "2", salida


# ═══ 3. El guarda ═════════════════════════════════════════════════════════


# El cuerpo en una línea. Con \r también: lo que se manda desde Windows llega
# con \r\n, y `splitlines` partiría la línea en el \r.
_UNA_LINEA = r"regexp_replace(p.prosrc, E'[\r\n]+', ' ', 'g')"


def _fuente(nombre: str, prefijo: str = "") -> str:
    salida = sql(f"{prefijo}\nselect 'F|' || {_UNA_LINEA} from pg_proc p "
                 f"where p.proname = '{nombre}' and p.pronamespace = 'public'::regnamespace;\n"
                 + ("rollback;\n" if prefijo else ""))
    return " ".join(ln[2:] for ln in salida.splitlines() if ln.startswith("F|"))


_FICHA_DEL_BORRADO = f"""
select 'P|' || p.prokind::text || '|' || p.pronargs || '|' || {_UNA_LINEA}
  from pg_proc p where p.proname = '{BORRA}' and p.pronamespace = 'public'::regnamespace;
select 'S|' || {_UNA_LINEA} from pg_proc p
  where p.proname = 'simulacro_de_borrado' and p.pronamespace = 'public'::regnamespace;
select 'I|' || case when to_regclass('public.borrado_interruptor') is null then 'sin tabla'
  else (xpath('/row/c/text()', query_to_xml('select count(*) as c from public.borrado_interruptor',
                                            false, true, '')))[1]::text end || '|'
  || coalesce((select column_default from information_schema.columns where table_schema = 'public'
               and table_name = 'borrado_interruptor' and column_name = 'encendido'), 'sin columna');
select 'G|' || p.proname || ':' || r from unnest(array['anon', 'authenticated', 'service_role']) r,
  pg_proc p where p.proname in ('{BORRA}', '{PASADA}') and p.pronamespace = 'public'::regnamespace
                and has_function_privilege(r, p.oid, 'execute');
select 'Q|' || {_UNA_LINEA} from pg_proc p where p.proname = '{PASADA}' and p.pronamespace = 'public'::regnamespace;
select 'A|' || format_type(p.proargtypes[0], null) from pg_proc p
  where p.proname = '{BORRA}' and p.pronamespace = 'public'::regnamespace and p.pronargs = 1;
select 'C|' || count(*) from pg_namespace n join pg_class c on c.relnamespace = n.oid
  where n.nspname = 'cron' and c.relname = 'job';
"""


def _cron(prefijo: str = "") -> str:
    """Si existe `cron.job` (pg_cron), ¿alguna tarea llama al borrado?"""
    hay = sql(f"{prefijo}\nselect 'H|' || (to_regclass('cron.job') is not null);\n"
              + ("rollback;\n" if prefijo else ""))
    if "H|true" not in hay:
        return ""
    return sql(f"{prefijo}\nselect 'CRON|' || count(*) from cron.job where command ilike '%{BORRA}%';\n"
               + ("rollback;\n" if prefijo else ""))


def _rojos_del_borrado(salida: str) -> list[str]:
    rojos: list[str] = []
    fichas = [ln.split("|", 3) for ln in salida.splitlines() if ln.startswith("P|")]
    if len(fichas) != 1:
        return [f"hay {len(fichas)} funciones {BORRA}: tiene que haber una"]
    _, kind, nargs, src = fichas[0]
    cuerpo = src.lower()
    if kind != "f":
        rojos.append("no es una función: un procedimiento puede hacer COMMIT y partir el borrado")
    # UN parámetro, y es la lista de preparadas (0089): ni un booleano ni nada que
    # pueda hacer de interruptor en la llamada.
    tipos = [ln[2:] for ln in salida.splitlines() if ln.startswith("A|")]
    if nargs != "1" or tipos != ["uuid[]"]:
        rojos.append(f"sus parámetros no son solo las preparadas (uuid[]): {nargs} {tipos}")
    if re.search(r"\b(commit|rollback)\b|dblink|pg_background", cuerpo):
        rojos.append("sale de la transacción (commit, dblink o pg_background)")
    pasada = next((ln[2:] for ln in salida.splitlines() if ln.startswith("Q|")), "").lower()
    # Las filas de la pasada salen de la selección, no de una consulta propia.
    if f"public.{PASADA}()" not in cuerpo or not re.search(r"from public\.cuentas_fuera_de_plazo\(\) f\s+order by", pasada):
        rojos.append("no lee la selección del simulacro (cuentas_fuera_de_plazo(), por la próxima pasada)")
    if "public.plan_de_borrado(" not in cuerpo:
        rojos.append("no lee el plan del simulacro (plan_de_borrado)")
    if m := re.findall(r"pg_constraint|terminada_at|created_at|public\.subscriptions|relojes_del_plazo|interval", cuerpo):
        rojos.append(f"reimplementa la selección o el plan: {sorted(set(m))}")
    if "public.borrado_interruptor" not in cuerpo or "errcode = '55000'" not in cuerpo:
        rojos.append("no mira el interruptor, o no se niega con un error")
    if "limit v_inter.tope_por_pasada" not in pasada or "public.borrado_interruptor" not in pasada:
        rojos.append("no tiene tope (el de la fila del interruptor)")
    if "errcode = '55000'" not in pasada:
        rojos.append("la próxima pasada no se niega apagada: la tarea tocaría Stripe con él apagado")
    sim = next((ln[2:] for ln in salida.splitlines() if ln.startswith("S|")), "")
    if "public.plan_de_borrado(" not in sim or "pendientes" in sim:
        rojos.append("el simulacro no usa el plan (o tiene uno propio)")
    filas, default = next(ln for ln in salida.splitlines() if ln.startswith("I|")).split("|")[1:]
    if filas != "1":
        rojos.append(f"el interruptor no es una fila: {filas}")
    if default != "false":
        rojos.append(f"el interruptor no nace apagado (default {default})")
    g = sorted(ln[2:] for ln in salida.splitlines() if ln.startswith("G|"))
    if g != sorted([f"{BORRA}:service_role", f"{PASADA}:service_role"]):
        rojos.append(f"lo puede llamar alguien más que service_role (o ni él): {g}")
    return rojos


def test_el_borrado_usa_la_seleccion_y_el_plan_y_nace_apagado_con_tope():
    salida = sql(_FICHA_DEL_BORRADO)
    assert _rojos_del_borrado(salida) == [], salida
    # Y la fila, hoy, apagada.
    assert sql("select encendido from public.borrado_interruptor;") == "f"


def _definicion(nombre: str) -> str:
    return sql(f"select pg_get_functiondef('public.{nombre}'::regproc);")


def _mutar(viejo: str, nuevo: str) -> str:
    d = _definicion(BORRA)
    assert viejo in d, viejo
    return d.replace(viejo, nuevo) + ";"


_SELECCION_A_MANO = ("(select s.account_id, 'suscripcion'::text as origen, current_date as se_borran_el "
                     "from public.subscriptions s where s.terminada_at < now() - interval '90 days')")

_CONTROLES = {
    "reimplementa_la_seleccion": lambda: _definicion(PASADA).replace(
        "from public.cuentas_fuera_de_plazo() f\n", f"from {_SELECCION_A_MANO} f\n", 1) + ";",
    "un_parametro": lambda: _definicion(BORRA).replace(
        f"public.{BORRA}(p_preparadas uuid[])", f"public.{BORRA}(p_preparadas uuid[], p_encendido boolean default false)",
        1) + ";",
    "sin_tope": lambda: _definicion(PASADA).replace("limit v_inter.tope_por_pasada", "", 1) + ";",
    "la_pasada_no_se_niega_apagada": lambda: _definicion(PASADA).replace("errcode = '55000'", "errcode = 'P0001'", 1) + ";",
    "un_procedimiento": lambda: (f"drop function public.{BORRA}(uuid[]);\ncreate procedure public.{BORRA}(p uuid[]) "
                                 "language plpgsql as $$ begin commit; end $$;"),
    "llamable_desde_una_ruta": lambda: f"grant execute on function public.{BORRA}(uuid[]) to authenticated;",
    "sin_interruptor": lambda: "drop table public.borrado_interruptor cascade;",
    "nace_encendido": lambda: "alter table public.borrado_interruptor alter column encendido set default true;",
    "el_simulacro_con_su_propio_plan": lambda: _definicion("simulacro_de_borrado").replace(
        "public.plan_de_borrado(p_cuenta)", "(select null::text as tabla, null::text as predicado, "
        "null::text as via, null::int as orden, 'pendientes' as x where false)", 1) + ";",
}


@pytest.mark.parametrize("control", _CONTROLES.values(), ids=_CONTROLES.keys())
def test_CONTROL_el_guarda_del_borrado_se_pone_rojo(control):
    salida = sql(f"begin;\n{control()}\n{_FICHA_DEL_BORRADO}rollback;\n")
    assert _rojos_del_borrado(salida), salida


def test_sin_el_interruptor_en_el_cuerpo_borraria_apagado():
    """El control de comportamiento del interruptor: con su `if` quitado, el
    texto sigue diciendo 55000, pero la llamada apagada BORRA. La prueba de
    «apagado se niega» lo vería; esto demuestra que lo vería."""
    _base_vacia()
    mutada = (_mutar("if v_encendido is distinct from true then", "if false then") + "\n"
              + _definicion(PASADA).replace("if v_inter.encendido is distinct from true then", "if false then", 1) + ";")
    salida = sql(f"begin;\n{_sembrar(1)}\n{mutada}\n"
                 f"{_intento('apagado', f'perform * from {LLAMAR}')}\n{_LEER_R}rollback;\n")
    assert _resultados(salida)["apagado"] == "PASA", salida


def test_la_migracion_hace_nacer_el_interruptor_apagado():
    texto = MIGRACION.read_text(encoding="utf-8")
    assert _nace_apagado(texto)
    # EL CONTROL: la misma migración naciendo encendida.
    assert not _nace_apagado(texto.replace("values (false, 'nace apagado (0068)')",
                                           "values (true, 'nace apagado (0068)')"))


def _nace_apagado(texto: str) -> bool:
    return bool(re.search(r"insert into public\.borrado_interruptor \(encendido, motivo\) values \(false,", texto)) \
        and "encendido boolean not null default false" in texto


# — Nadie lo llama: ni cron, ni ruta, ni botón —

_DONDE_SE_LLAMARIA = ("backend/app", "frontend/src", "supabase/functions", "supabase/snippets",
                      "scripts", "supabase/seed.sql")


def _quien_lo_llama(raiz: Path) -> list[str]:
    llamadas = []
    for donde in _DONDE_SE_LLAMARIA:
        base = raiz / donde
        ficheros = [base] if base.is_file() else (base.rglob("*") if base.is_dir() else [])
        for f in ficheros:
            if f.is_file() and "node_modules" not in f.parts and f.suffix not in (".pyc", ".png", ".jpg"):
                try:
                    if BORRA in f.read_text(encoding="utf-8", errors="ignore"):
                        llamadas.append(f.relative_to(raiz).as_posix())
                except OSError:
                    continue
    return llamadas


TAREA = "backend/app/tareas/borrado_a_90_dias.py"


def test_solo_lo_llama_la_tarea_ni_cron_ni_ruta():
    assert _quien_lo_llama(REPO) == [TAREA]
    assert "CRON|" not in _cron() or "CRON|0" in _cron()
    assert sorted(ln[2:] for ln in sql(_FICHA_DEL_BORRADO).splitlines() if ln.startswith("G|")) == sorted(
        [f"{BORRA}:service_role", f"{PASADA}:service_role"])


def test_CONTROL_una_llamada_en_el_codigo_se_pone_roja(tmp_path):
    (tmp_path / "backend/app/routers").mkdir(parents=True)
    (tmp_path / "backend/app/routers/borrar.py").write_text(
        f'await servicio.rpc("{BORRA}", {{}})\n', encoding="utf-8")
    assert _quien_lo_llama(tmp_path) == ["backend/app/routers/borrar.py"]


def test_CONTROL_una_tarea_de_cron_se_pone_roja():
    falso = ("create schema cron; create table cron.job (command text); "
             f"insert into cron.job values ('select public.{BORRA}()');")
    assert "CRON|1" in _cron(f"begin;\n{falso}")


# — La constancia: sin UPDATE, sin DELETE, sin nada personal —

COLUMNAS_DE_LA_CONSTANCIA = {
    "id": "bigint", "account_id": "uuid", "borrada_at": "timestamp with time zone", "motivo": "text",
    "origen": "text", "se_borraba_el": "date", "filas": "jsonb",
}

_CONSTANCIA = """
select 'COL|' || column_name || '|' || data_type from information_schema.columns
  where table_schema = 'public' and table_name = 'cuentas_borradas';
select 'PRIV|' || grantee || '|' || privilege_type from information_schema.role_table_grants
  where table_schema = 'public' and table_name = 'cuentas_borradas' and grantee <> 'postgres';
select 'FK|' || conname from pg_constraint where contype = 'f' and conrelid = 'public.cuentas_borradas'::regclass;
select 'TRG|' || string_agg(distinct case when (t.tgtype & 8) <> 0 then 'DELETE' end, '') || ','
              || string_agg(distinct case when (t.tgtype & 16) <> 0 then 'UPDATE' end, '') || ','
              || string_agg(distinct case when (t.tgtype & 32) <> 0 then 'TRUNCATE' end, '')
  from pg_trigger t join pg_proc p on p.oid = t.tgfoid
  where t.tgrelid = 'public.cuentas_borradas'::regclass and not t.tgisinternal
    and p.proname like '%\\_no\\_se\\_tocan';
"""


def _rojos_de_la_constancia(salida: str) -> list[str]:
    rojos = []
    columnas = dict(ln.split("|")[1:] for ln in salida.splitlines() if ln.startswith("COL|"))
    if columnas != COLUMNAS_DE_LA_CONSTANCIA:
        rojos.append(f"las columnas cambiaron (¿algo personal?): {columnas}")
    if p := [ln[5:] for ln in salida.splitlines() if ln.startswith("PRIV|") and not ln.endswith("|SELECT")]:
        rojos.append(f"permisos de más: {p}")
    if f := [ln[3:] for ln in salida.splitlines() if ln.startswith("FK|")]:
        rojos.append(f"clave ajena: {f}")
    if "TRG|DELETE,UPDATE,TRUNCATE" not in salida.splitlines():
        rojos.append("le falta el trigger que rechaza UPDATE, DELETE y TRUNCATE")
    return rojos


def test_la_constancia_no_se_toca_ni_dice_de_quien_era():
    assert _rojos_de_la_constancia(sql(_CONSTANCIA)) == [], sql(_CONSTANCIA)
    salida = sql(f"""
begin;
insert into public.cuentas_borradas (account_id, motivo, origen, se_borraba_el, filas)
  values (gen_random_uuid(), 'fuera_de_plazo_90_dias', 'sin_pago', current_date, '{{}}');
{_intento("update", "update public.cuentas_borradas set motivo = motivo")}
{_intento("delete", "delete from public.cuentas_borradas")}
{_intento("truncate", "truncate public.cuentas_borradas")}
{_LEER_R}
rollback;
""")
    assert all(v.startswith("RECHAZADA:42501") for v in _resultados(salida).values()), salida
    assert len(_resultados(salida)) == 3


_CONTROLES_CONSTANCIA = {
    "gana_update": "grant update on public.cuentas_borradas to service_role;",
    "gana_delete": "grant delete on public.cuentas_borradas to authenticated;",
    "sin_trigger": "drop trigger cuentas_borradas_sin_update_ni_delete on public.cuentas_borradas;",
    "columna_personal": "alter table public.cuentas_borradas add column email text;",
    "clave_a_la_cuenta": "alter table public.cuentas_borradas add constraint zz foreign key (account_id) "
                         "references public.accounts (id) not valid;",
}


@pytest.mark.parametrize("control", _CONTROLES_CONSTANCIA.values(), ids=_CONTROLES_CONSTANCIA.keys())
def test_CONTROL_la_constancia_se_pone_roja(control):
    assert _rojos_de_la_constancia(sql(f"begin;\n{control}\n{_CONSTANCIA}rollback;\n"))


# — Ninguna tabla de la cuenta se queda sin alcanzar —

_DE_LA_CUENTA = """
select distinct 'T|' || c.oid::regclass::text from pg_class c
  join pg_namespace n on n.oid = c.relnamespace
  join pg_attribute a on a.attrelid = c.oid and a.attnum > 0 and not a.attisdropped
  where n.nspname = 'public' and c.relkind = 'r'
    and a.attname in ('account_id', 'salon_id', 'target_salon_id')
union
select distinct 'T|' || k.conrelid::regclass::text from pg_constraint k
  where k.contype = 'f' and k.confrelid in ('public.accounts'::regclass, 'public.salons'::regclass);
select 'PLAN|' || tabla from public.plan_de_borrado(gen_random_uuid());
"""


def _sin_alcanzar(salida: str) -> list[str]:
    def pub(t: str) -> str:
        return t if "." in t else f"public.{t}"
    de_la_cuenta = {pub(ln[2:]) for ln in salida.splitlines() if ln.startswith("T|")}
    plan = {pub(ln[5:]) for ln in salida.splitlines() if ln.startswith("PLAN|")}
    assert "public.clients" in plan and "public.correcciones_de_fichaje" in plan  # antivacío
    return sorted(de_la_cuenta - plan - set(SE_QUEDA) - {"public.accounts"})


def test_ninguna_tabla_de_la_cuenta_se_queda_sin_alcanzar():
    assert _sin_alcanzar(sql(_DE_LA_CUENTA)) == []


@pytest.mark.parametrize("control", [
    "create table public.zz_cuelga (account_id uuid);",
    "create table public.zz_cuelga (salon_id uuid references public.salons (id) on delete set null);",
], ids=["por_account_id", "por_salon_sin_cascada"])
def test_CONTROL_una_tabla_nueva_sin_alcanzar_se_pone_roja(control):
    assert _sin_alcanzar(sql(f"begin;\n{control}\n{_DE_LA_CUENTA}rollback;\n")) == ["public.zz_cuelga"]


# — El orden: los centros antes que sus correcciones —


def _rojos_del_orden(filas: list[tuple[str, str]]) -> list[str]:
    orden = {t: int(o) for t, o in filas if o}
    if "public.salons" not in orden or "public.correcciones_de_fichaje" not in orden:
        return [f"faltan en el plan: {orden}"]
    if orden["public.correcciones_de_fichaje"] <= orden["public.salons"]:
        return ["las correcciones van antes que sus centros: el trigger de la 0067 lo rechazaría"]
    return []


def _orden() -> list[tuple[str, str]]:
    return [tuple(ln.split("|")[1:]) for ln in sql(
        "select 'O|' || tabla || '|' || coalesce(orden::text, '') "
        "from public.plan_de_borrado(gen_random_uuid());").splitlines()]


def test_los_centros_van_antes_que_sus_correcciones():
    assert _rojos_del_orden(_orden()) == []


def test_CONTROL_el_orden_invertido_se_pone_rojo():
    filas = _orden()
    o = dict(filas)
    invertido = [(t, o["public.salons"] if t == "public.correcciones_de_fichaje"
                  else o["public.correcciones_de_fichaje"] if t == "public.salons" else v) for t, v in filas]
    assert _rojos_del_orden(invertido)


def test_CONTROL_al_reves_el_trigger_lo_rechaza_y_no_se_borra_nada():
    """EXACTAMENTE el orden invertido, a mano: las correcciones primero. El
    trigger de la 0067 lo rechaza, y el centro sigue ahí."""
    salida = sql(f"""
begin;
{_sembrar(1)}
{_intento("al_reves", "delete from public.correcciones_de_fichaje where account_id = (select cuenta from zz_cuentas); "
                      "delete from public.salons where id = (select centro from zz_cuentas)")}
{_intento("del_derecho", "delete from public.salons where id = (select centro from zz_cuentas); "
                         "delete from public.correcciones_de_fichaje where account_id = (select cuenta from zz_cuentas)")}
{_LEER_R}
rollback;
""")
    r = _resultados(salida)
    assert r["al_reves"].startswith("RECHAZADA:42501"), salida
    assert r["del_derecho"] == "PASA", salida


def test_el_simulacro_y_el_borrado_salen_del_mismo_sitio_en_python():
    """En el backend tampoco se decide: el panel, los avisos y el muro leen la
    selección de la base."""
    assert not _python_reimplementa(_fuentes_python())


def _fuentes_python() -> dict[str, str]:
    app = REPO / "backend/app"
    return {p: (app / p).read_text(encoding="utf-8") for p in
            ("tareas/plazo_de_datos.py", "routers/admin_plataforma.py", "routers/auth.py")}


def _python_reimplementa(fuentes: dict[str, str]) -> list[str]:
    rojos = []
    if 'rpc("relojes_del_plazo"' not in fuentes["tareas/plazo_de_datos.py"]:
        rojos.append("los avisos no leen relojes_del_plazo")
    if '"terminada_at": "not.is.null"' in fuentes["tareas/plazo_de_datos.py"] or "timedelta(days=" in fuentes[
            "tareas/plazo_de_datos.py"]:
        rojos.append("los avisos vuelven a calcular quién está en plazo")
    if 'rpc("cuentas_fuera_de_plazo"' not in fuentes["routers/admin_plataforma.py"] or \
            "dias_restantes < 0" in fuentes["routers/admin_plataforma.py"]:
        rojos.append("el simulacro del panel no lee cuentas_fuera_de_plazo")
    if "tarea_plazo.relojes(" not in fuentes["routers/auth.py"]:
        rojos.append("el muro no lee la selección")
    return rojos


def test_CONTROL_python_que_vuelve_a_decidir_se_pone_rojo():
    f = _fuentes_python()
    f["routers/admin_plataforma.py"] = f["routers/admin_plataforma.py"].replace(
        'rpc("cuentas_fuera_de_plazo"', 'rpc("otra_cosa"')
    assert _python_reimplementa(f)


def test_la_constancia_solo_lleva_nombres_de_tabla_y_numeros():
    _base_vacia()
    salida = sql(f"""
begin;
{_sembrar(1)}
{ENCENDER}
select 'J|' || filas::text from {LLAMAR};
rollback;
""")
    filas = json.loads(next(ln[2:] for ln in salida.splitlines() if ln.startswith("J|")))
    assert filas and all(re.fullmatch(r"(public|auth)\.[a-z_]+", k) and isinstance(v, int) for k, v in filas.items())


# — La forma de las tablas de Auth que el borrado toca (04-10-2026) —
# Foto de Auth v2.197.0, la de producción (12-10-2026; ver
# `supabase/versiones_de_produccion.txt`). El borrado escribe en `auth.users`
# (y en cascada en lo que cuelga de ella), en `auth.audit_log_entries` y, desde
# la 0083, en `scim_users`, `flow_state`, `refresh_tokens` y
# `saml_relay_states` por la columna que nombra al usuario, sin la API de Auth. Si una versión de Auth cambia
# su forma, el borrado podría romperse ENTERO: hay que enterarse por aquí, y no
# por un borrado que falla. Si esto se pone rojo: mirar qué cambió, ajustar
# `plan_de_borrado` si hace falta, pasar el simulacro, y ENTONCES actualizar
# esta foto.

_FORMA_DE_AUTH = r"""
select 'COL|' || table_name || '.' || column_name || '|' || data_type from information_schema.columns
  where table_schema = 'auth' and table_name in ('users', 'audit_log_entries', 'scim_users', 'flow_state',
                                                'refresh_tokens', 'saml_relay_states')
  order by table_name, ordinal_position;
select 'FK|' || k.conrelid::regclass || '|' || k.confrelid::regclass || '|' || k.confdeltype::text from pg_constraint k
  where k.contype = 'f' and k.confrelid in (
    select c.oid from pg_class c join pg_namespace n on n.oid = c.relnamespace where n.nspname = 'auth')
  order by 1;
"""

FORMA_DE_AUTH = """
COL|audit_log_entries.instance_id|uuid
COL|audit_log_entries.id|uuid
COL|audit_log_entries.payload|json
COL|audit_log_entries.created_at|timestamp with time zone
COL|audit_log_entries.ip_address|character varying
COL|flow_state.id|uuid
COL|flow_state.user_id|uuid
COL|flow_state.auth_code|text
COL|flow_state.code_challenge_method|USER-DEFINED
COL|flow_state.code_challenge|text
COL|flow_state.provider_type|text
COL|flow_state.provider_access_token|text
COL|flow_state.provider_refresh_token|text
COL|flow_state.created_at|timestamp with time zone
COL|flow_state.updated_at|timestamp with time zone
COL|flow_state.authentication_method|text
COL|flow_state.auth_code_issued_at|timestamp with time zone
COL|flow_state.invite_token|text
COL|flow_state.referrer|text
COL|flow_state.oauth_client_state_id|uuid
COL|flow_state.linking_target_id|uuid
COL|flow_state.email_optional|boolean
COL|refresh_tokens.instance_id|uuid
COL|refresh_tokens.id|bigint
COL|refresh_tokens.token|character varying
COL|refresh_tokens.user_id|character varying
COL|refresh_tokens.revoked|boolean
COL|refresh_tokens.created_at|timestamp with time zone
COL|refresh_tokens.updated_at|timestamp with time zone
COL|refresh_tokens.parent|character varying
COL|refresh_tokens.session_id|uuid
COL|saml_relay_states.id|uuid
COL|saml_relay_states.sso_provider_id|uuid
COL|saml_relay_states.request_id|text
COL|saml_relay_states.for_email|text
COL|saml_relay_states.redirect_to|text
COL|saml_relay_states.created_at|timestamp with time zone
COL|saml_relay_states.updated_at|timestamp with time zone
COL|saml_relay_states.flow_state_id|uuid
COL|scim_users.id|uuid
COL|scim_users.sso_provider_id|uuid
COL|scim_users.user_id|uuid
COL|scim_users.resource|jsonb
COL|scim_users.user_name|text
COL|scim_users.external_id|text
COL|scim_users.active|boolean
COL|scim_users.created_at|timestamp with time zone
COL|scim_users.updated_at|timestamp with time zone
COL|scim_users.deleted_at|timestamp with time zone
COL|users.instance_id|uuid
COL|users.id|uuid
COL|users.aud|character varying
COL|users.role|character varying
COL|users.email|character varying
COL|users.encrypted_password|character varying
COL|users.email_confirmed_at|timestamp with time zone
COL|users.invited_at|timestamp with time zone
COL|users.confirmation_token|character varying
COL|users.confirmation_sent_at|timestamp with time zone
COL|users.recovery_token|character varying
COL|users.recovery_sent_at|timestamp with time zone
COL|users.email_change_token_new|character varying
COL|users.email_change|character varying
COL|users.email_change_sent_at|timestamp with time zone
COL|users.last_sign_in_at|timestamp with time zone
COL|users.raw_app_meta_data|jsonb
COL|users.raw_user_meta_data|jsonb
COL|users.is_super_admin|boolean
COL|users.created_at|timestamp with time zone
COL|users.updated_at|timestamp with time zone
COL|users.phone|text
COL|users.phone_confirmed_at|timestamp with time zone
COL|users.phone_change|text
COL|users.phone_change_token|character varying
COL|users.phone_change_sent_at|timestamp with time zone
COL|users.confirmed_at|timestamp with time zone
COL|users.email_change_token_current|character varying
COL|users.email_change_confirm_status|smallint
COL|users.banned_until|timestamp with time zone
COL|users.reauthentication_token|character varying
COL|users.reauthentication_sent_at|timestamp with time zone
COL|users.is_sso_user|boolean
COL|users.deleted_at|timestamp with time zone
COL|users.is_anonymous|boolean
FK|auth.identities|auth.users|c
FK|auth.mfa_amr_claims|auth.sessions|c
FK|auth.mfa_challenges|auth.mfa_factors|c
FK|auth.mfa_factors|auth.users|c
FK|auth.mfa_recovery_code_sets|auth.mfa_factors|c
FK|auth.mfa_recovery_code_sets|auth.users|c
FK|auth.mfa_recovery_codes|auth.mfa_recovery_code_sets|c
FK|auth.oauth_authorizations|auth.oauth_clients|c
FK|auth.oauth_authorizations|auth.users|c
FK|auth.oauth_consents|auth.oauth_clients|c
FK|auth.oauth_consents|auth.users|c
FK|auth.one_time_tokens|auth.users|c
FK|auth.refresh_tokens|auth.sessions|c
FK|auth.saml_providers|auth.sso_providers|c
FK|auth.saml_relay_states|auth.flow_state|c
FK|auth.saml_relay_states|auth.sso_providers|c
FK|auth.scim_tokens|auth.sso_providers|c
FK|auth.scim_users|auth.sso_providers|c
FK|auth.scim_users|auth.users|n
FK|auth.sessions|auth.oauth_clients|c
FK|auth.sessions|auth.users|c
FK|auth.sso_domains|auth.sso_providers|c
FK|auth.webauthn_challenges|auth.users|c
FK|auth.webauthn_credentials|auth.users|c
FK|users|auth.users|n
""".strip().splitlines()


def _cambios_en_auth(salida: str) -> list[str]:
    hoy = [ln for ln in salida.splitlines() if ln.startswith(("COL|", "FK|"))]
    return ([f"nuevo: {x}" for x in hoy if x not in FORMA_DE_AUTH]
            + [f"ya no está: {x}" for x in FORMA_DE_AUTH if x not in hoy])


def test_la_forma_de_auth_es_la_que_conoce_el_borrado():
    assert _cambios_en_auth(sql(_FORMA_DE_AUTH)) == []


def test_CONTROL_una_clave_nueva_hacia_auth_users_se_pone_roja():
    """Lo que de verdad rompería el borrado: algo que apunte a `auth.users`
    sin cascada. Las tablas de Auth no son nuestras (no se pueden alterar desde
    aquí); una tabla nueva que apunte a ellas, sí."""
    salida = sql(f"begin;\ncreate table public.zz_apunta (u uuid references auth.users (id));\n"
                 f"{_FORMA_DE_AUTH}rollback;\n")
    assert _cambios_en_auth(salida) == ["nuevo: FK|zz_apunta|auth.users|a"]


@pytest.mark.parametrize("cambio", [
    ("COL|audit_log_entries.payload|json", "COL|audit_log_entries.payload|jsonb"),
    ("COL|users.email|character varying", ""),
], ids=["cambia_el_tipo_del_registro", "desaparece_el_correo"])
def test_CONTROL_una_columna_de_auth_que_cambia_se_pone_roja(cambio):
    """Las tablas de Auth no se pueden alterar desde aquí (no son de `postgres`):
    el control cambia la foto que lee el guarda, que es lo que vería si Auth
    las cambiara."""
    viejo, nuevo = cambio
    salida = sql(_FORMA_DE_AUTH).replace(viejo, nuevo)
    assert _cambios_en_auth(salida)


# — Los días de prueba, en un solo sitio —


def _dias_de_prueba_en_dos_sitios(fuentes: dict[str, str], campos: set[str], relojes: str) -> list[str]:
    rojos = []
    if "trial_default_days" in campos:
        rojos.append("Settings vuelve a tener trial_default_days")
    for ruta, texto in fuentes.items():
        # Leerlo de Settings, o de la variable de entorno a mano (nombrarla en un
        # comentario, como hace `core/prueba.py` para contar su historia, no es leerla).
        if "settings.trial_default_days" in texto or re.search(
                r"""(getenv|environ(\.get)?)\s*[\[(]\s*["']TRIAL_DEFAULT_DAYS""", texto):
            rojos.append(f"{ruta} lee los días de prueba de la configuración")
    if "public.plataforma_config" not in relojes or re.search(r"\d+\s+as\s+dias_de_prueba", relojes):
        rojos.append("relojes_del_plazo no lee los días de prueba de plataforma_config")
    return rojos


def _fuentes_del_backend() -> dict[str, str]:
    app = REPO / "backend/app"
    return {p.relative_to(app).as_posix(): p.read_text(encoding="utf-8") for p in app.rglob("*.py")}


def test_los_dias_de_prueba_viven_solo_en_la_base():
    assert _dias_de_prueba_en_dos_sitios(_fuentes_del_backend(), set(Settings.model_fields),
                                         _fuente("relojes_del_plazo")) == []


@pytest.mark.parametrize("control", ["settings", "campo", "constante"])
def test_CONTROL_los_dias_de_prueba_en_dos_sitios_se_pone_rojo(control):
    fuentes, campos = _fuentes_del_backend(), set(Settings.model_fields)
    relojes = _fuente("relojes_del_plazo")
    if control == "settings":
        fuentes["routers/billing.py"] += "\ndias = settings.trial_default_days\n"
    elif control == "campo":
        campos.add("trial_default_days")
    else:
        relojes = relojes.replace("p.dias_de_prueba from public.plataforma_config p", "14 as dias_de_prueba")
    assert _dias_de_prueba_en_dos_sitios(fuentes, campos, relojes)


# — Stripe y Storage: el simulacro dice qué borrar antes, y la función no empieza sin ello —


def test_el_simulacro_imprime_el_cliente_de_stripe_y_las_rutas_de_los_logos():
    salida = sql(f"""
begin;
{_sembrar(1)}
update public.accounts set stripe_customer_id = 'cus_ZZcuenta' where id = (select cuenta from zz_cuentas);
update public.subscriptions set stripe_customer_id = 'cus_ZZsub' where account_id = (select cuenta from zz_cuentas);
insert into storage.buckets (id, name) values ('salon-assets', 'salon-assets') on conflict do nothing;
insert into storage.objects (bucket_id, name) select 'salon-assets', centro || '/logo.png' from zz_cuentas;
select 'S5|' || s.tabla || '|' || s.filas || '|' || s.detalle || '|' || (select centro from zz_cuentas)
from zz_cuentas z, public.simulacro_de_borrado(z.cuenta) s where s.paso = 5;
rollback;
""")
    filas = {ln.split("|")[1]: ln.split("|")[2:] for ln in salida.splitlines() if ln.startswith("S5|")}
    stripe_n, stripe_detalle, _ = filas["stripe (clientes)"]
    assert stripe_n == "2" and "cus_ZZcuenta" in stripe_detalle and "cus_ZZsub" in stripe_detalle
    logo_n, logo_detalle, centro = filas["storage.objects (salon-assets)"]
    assert logo_n == "1" and f"{centro}/logo.png" in logo_detalle


def test_con_un_logo_en_storage_esa_cuenta_no_se_borra():
    """Storage va ANTES. Si el paso 2 no se hizo, esa cuenta no se borra y dice por qué."""
    _base_vacia()
    salida = sql(f"""
begin;
{_sembrar(1)}
insert into storage.buckets (id, name) values ('salon-assets', 'salon-assets') on conflict do nothing;
insert into storage.objects (bucket_id, name) select 'salon-assets', centro || '/logo.png' from zz_cuentas;
{ENCENDER}
select 'E|' || error from {LLAMAR};
select 'X|' || (select count(*) from public.accounts a join zz_cuentas z on z.cuenta = a.id);
rollback;
""")
    assert "logos en Storage: primero se borran ellos" in salida, salida
    assert _x(salida) == "1", salida
