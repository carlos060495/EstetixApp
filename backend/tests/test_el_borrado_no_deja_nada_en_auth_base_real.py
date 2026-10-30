"""EL BORRADO NO DEJA NADA EN AUTH (0083), contra la base de verdad. Todo dentro
de transacciones que se deshacen: no se enciende nada fuera de ellas.

`auth.scim_users` cuelga del usuario con SET NULL: al borrar una cuenta, su fila
se quedaba sin dueño y con el correo dentro. Y `flow_state`, `refresh_tokens`
sin sesión y `saml_relay_states` lo nombran SIN clave ajena. Lo mismo en el
borrado a 90 días y en el de las demos: los dos usan `plan_de_borrado`.

  1. Con un usuario COMPLETO —doble paso, sus códigos, SCIM, sesiones, tokens,
     un flujo suelto y entradas de registro—, el simulacro cuenta lo que se va
     y no deja nada «sin referencia» ni «en Auth»; el borrado de verdad (el de
     90 días y el de una demo) no deja NINGUNA fila de Auth que lo nombre.
  2. EL GUARDA, descubriendo y sin lista: (a) toda clave ajena de Auth que no
     es en cascada y apunta a algo que se borra tiene su tabla en el plan; (b)
     después del borrado, ninguna tabla de Auth —las que haya— nombra a la
     cuenta; (c) los dos interruptores de borrado siguen apagados.
  3. CADA UNO, CON SU CONTROL: el plan sin la raíz de SCIM, una fila de Auth que
     nombra al usuario donde el plan no mira, una clave nueva con SET NULL, un
     interruptor encendido.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.test_el_borrado_a_90_dias_base_real import (
    _LEER_R, BORRA, ENCENDER, _base_vacia, _intento, _resultados)
from tests.test_un_salon_no_puede_cambiarse_el_plan import sql

pytestmark = [pytest.mark.base_real]

REPO = Path(__file__).resolve().parents[2]
CORREO = "zz.auth@borrado.test"


def _sembrar(*, demo: bool = False) -> str:
    """Una cuenta fuera de plazo (o una demo) con su administradora y TODO lo que
    Auth v2.197 puede tener de ella. Sus ids, en `zz`."""
    cuenta = ("insert into public.accounts (nombre, es_demo) values ('zz auth', true) returning id into a;"
              if demo else "insert into public.accounts (nombre) values ('zz auth') returning id into a;")
    # Una demo no tiene suscripción (0080); la otra, cancelada hace 100 días: fuera de plazo.
    suscripcion = "" if demo else ("insert into public.subscriptions (account_id, salon_id, estado, terminada_at) "
                                   "values (a, s, 'canceled', now() - interval '100 days');")
    demo_fila = ("insert into public.demos (account_id, navegador_hash, caduca_at) "
                 "values (a, repeat('a', 64), now() + interval '1 hour');") if demo else ""
    return f"""
create temp table zz (cuenta uuid, acceso uuid, sso uuid) on commit drop;
do $$ declare a uuid; s uuid; au uuid := gen_random_uuid(); f uuid := gen_random_uuid();
             frc uuid := gen_random_uuid(); rs uuid := gen_random_uuid(); se uuid := gen_random_uuid();
             sso uuid := gen_random_uuid(); begin
  {cuenta}
  insert into public.salons (account_id, nombre_comercial) values (a, 'zz centro') returning id into s;
  insert into auth.users (id, email, phone) values (au, '{CORREO}', '34600111222');
  insert into public.users (account_id, salon_id, nombre, email, rol, auth_user_id)
    values (a, s, 'zz administradora', '{CORREO}', 'admin', au);
  {demo_fila}
  {suscripcion}
  -- El doble paso: un factor TOTP y el de los códigos de recuperación, con 3 códigos.
  insert into auth.mfa_factors (id, user_id, friendly_name, factor_type, status, created_at, updated_at)
    values (f, au, 'zz totp', 'totp', 'verified', now(), now()),
           (frc, au, 'zz códigos', 'recovery_code', 'verified', now(), now());
  insert into auth.mfa_recovery_code_sets (id, user_id, mfa_factor_id) values (rs, au, frc);
  insert into auth.mfa_recovery_codes (id, mfa_recovery_code_set_id, code_hash)
    select gen_random_uuid(), rs, 'zz-hash-' || g from generate_series(1, 3) g;
  insert into auth.mfa_challenges (id, factor_id, created_at, ip_address) values (gen_random_uuid(), f, now(), '127.0.0.1');
  insert into auth.identities (provider_id, user_id, identity_data, provider)
    values (au::text, au, jsonb_build_object('sub', au::text, 'email', '{CORREO}'), 'email');
  insert into auth.sessions (id, user_id) values (se, au);
  insert into auth.refresh_tokens (token, user_id, session_id) values ('zz-con-sesion', au::text, se),
                                                                      ('zz-sin-sesion', au::text, null);
  insert into auth.one_time_tokens (id, user_id, token_type, token_hash, relates_to)
    values (gen_random_uuid(), au, 'recovery_token', 'zz-ott', '{CORREO}');
  insert into auth.flow_state (id, user_id, provider_type, authentication_method, auth_code, code_challenge,
                               code_challenge_method)
    values (gen_random_uuid(), au, 'email', 'magiclink', 'zz-code', 'zz-challenge', 's256');
  -- SCIM: un proveedor (no es de la cuenta, se queda) y su usuario, con el correo dentro.
  insert into auth.sso_providers (id) values (sso);
  insert into auth.scim_users (id, sso_provider_id, user_id, resource)
    values (gen_random_uuid(), sso, au,
            jsonb_build_object('userName', '{CORREO}', 'emails', jsonb_build_array(jsonb_build_object('value', '{CORREO}')), 'active', true));
  -- El registro: por id, por correo en mayúsculas, y en un campo que la 0068 no miraba.
  insert into auth.audit_log_entries (id, payload) values
    (gen_random_uuid(), jsonb_build_object('action', 'login', 'actor_id', au::text)::json),
    (gen_random_uuid(), jsonb_build_object('action', 'x', 'traits', jsonb_build_object('user_email', upper('{CORREO}')))::json),
    (gen_random_uuid(), jsonb_build_object('action', 'mfa', 'traits', jsonb_build_object('factor_owner', '{CORREO}'))::json);
  insert into zz values (a, au, sso);
end $$;
create temp table zz_marcas on commit drop as select public.marcas_de_auth((select cuenta from zz)) as m;
"""


_LO_QUE_QUEDA = """
select 'QUEDA|' || q.tabla || '|' || q.filas from public.lo_que_nombra_en_auth((select m from zz_marcas)) q;
select 'SSO|' || count(*) from auth.sso_providers where id = (select sso from zz);
"""


def _queda(salida: str) -> dict[str, int]:
    return {ln.split("|")[1]: int(ln.split("|")[2]) for ln in salida.splitlines() if ln.startswith("QUEDA|")}


# ═══ 1. Con un usuario completo ═════════════════════════════════════════════


def test_el_simulacro_cuenta_todo_lo_de_auth_y_no_deja_nada_sin_dueno():
    salida = sql(f"""
begin;
{_sembrar()}
select 'S|' || paso || '|' || tabla || '|' || coalesce(filas::text, '') || '|' || accion
  from public.simulacro_de_borrado((select cuenta from zz));
select 'ANTES|' || q.tabla || '|' || q.filas from public.lo_que_nombra_en_auth((select m from zz_marcas)) q;
rollback;
""")
    filas = [ln.split("|")[1:] for ln in salida.splitlines() if ln.startswith("S|")]
    borra = {t: int(n) for p, t, n, a in filas if p == "1" and a == "borra"}
    malas = [f for f in filas if f[3] in ("queda_sin_referencia", "queda_en_auth", "impide_el_borrado")]
    assert malas == [], salida
    # Lo que cuenta, tabla a tabla: lo sembrado.
    assert {t: borra.get(t) for t in (
        "auth.users", "auth.mfa_factors", "auth.mfa_recovery_code_sets", "auth.mfa_recovery_codes",
        "auth.mfa_challenges", "auth.identities", "auth.sessions", "auth.refresh_tokens", "auth.one_time_tokens",
        "auth.flow_state", "auth.scim_users", "auth.audit_log_entries")} == {
        "auth.users": 1, "auth.mfa_factors": 2, "auth.mfa_recovery_code_sets": 1, "auth.mfa_recovery_codes": 3,
        "auth.mfa_challenges": 1, "auth.identities": 1, "auth.sessions": 1, "auth.refresh_tokens": 2,
        "auth.one_time_tokens": 1, "auth.flow_state": 1, "auth.scim_users": 1, "auth.audit_log_entries": 3}, salida
    # Antivacío: ANTES del borrado, la búsqueda sí la encuentra en todas esas.
    antes = {ln.split("|")[1] for ln in salida.splitlines() if ln.startswith("ANTES|")}
    assert {"auth.users", "auth.scim_users", "auth.flow_state", "auth.refresh_tokens",
            "auth.identities", "auth.audit_log_entries", "auth.one_time_tokens"} <= antes, salida


def test_el_borrado_a_90_dias_no_deja_nada_que_la_nombre_en_auth():
    _base_vacia()
    salida = sql(f"""
begin;
{_sembrar()}
{ENCENDER}
select 'B|' || count(*) from public.{BORRA}(array(select cuenta from zz)) where error is null;
{_LO_QUE_QUEDA}
rollback;
""")
    assert "B|1" in salida.splitlines(), salida
    assert _queda(salida) == {}, salida
    assert "SSO|1" in salida.splitlines(), salida  # el proveedor no era suyo: se queda


def test_el_borrado_de_una_demo_tampoco():
    salida = sql(f"""
begin;
{_sembrar(demo=True)}
select 'B|' || (public.borrar_una_demo((select cuenta from zz)) is not null);
{_LO_QUE_QUEDA}
rollback;
""")
    assert "B|true" in salida.splitlines(), salida
    assert _queda(salida) == {}, salida


# ═══ 2 y 3. El guarda y sus controles ═══════════════════════════════════════

# — (b) Lo que queda después: el código roto y la base rota —


_SIN_RAIZ_SCIM = """
do $$ declare d text; begin
  d := pg_get_functiondef('public.plan_de_borrado(uuid)'::regprocedure);
  if position('to_regclass(''auth.scim_users'')' in d) = 0 then raise exception 'el control no encuentra la raíz'; end if;
  execute replace(d, 'if to_regclass(''auth.scim_users'') is not null then', 'if false then');
end $$;
"""


def test_CONTROL_sin_la_raiz_de_scim_el_simulacro_avisa_y_el_borrado_se_niega():
    """El código de antes de la 0083: SCIM no está en el plan."""
    _base_vacia()
    salida = sql(f"""
begin;
{_sembrar()}
{_SIN_RAIZ_SCIM}
select 'S|' || tabla || '|' || accion from public.simulacro_de_borrado((select cuenta from zz))
  where accion in ('queda_sin_referencia', 'queda_en_auth');
{ENCENDER}
select 'E|' || coalesce(error, 'ok') from public.{BORRA}(array(select cuenta from zz));
select 'CUENTA|' || count(*) from public.accounts where id = (select cuenta from zz);
rollback;
""")
    lineas = salida.splitlines()
    assert "S|auth.scim_users|queda_sin_referencia" in lineas, salida
    assert "S|auth.scim_users|queda_en_auth" in lineas, salida
    # 0089: la cuenta vuelve entera con su motivo en la fila, en vez de parar la pasada.
    r = next(ln for ln in lineas if ln.startswith("E|"))
    assert r.startswith("E|P0001:") and "dejó en Auth filas que la nombran: auth.scim_users (1)" in r, salida
    assert "CUENTA|1" in lineas, salida  # no se borró nada


def test_CONTROL_una_fila_de_auth_que_el_plan_no_conoce_para_el_borrado():
    """La base rota: algo de Auth que nombra al usuario donde el plan no mira (lo
    que haría una tabla o una columna nueva de una versión futura)."""
    _base_vacia()
    salida = sql(f"""
begin;
{_sembrar()}
insert into auth.sso_domains (id, sso_provider_id, domain) values (gen_random_uuid(), (select sso from zz), '{CORREO}');
select 'S|' || tabla || '|' || accion from public.simulacro_de_borrado((select cuenta from zz)) where accion = 'queda_en_auth';
{ENCENDER}
select 'E|' || coalesce(error, 'ok') from public.{BORRA}(array(select cuenta from zz));
rollback;
""")
    assert "S|auth.sso_domains|queda_en_auth" in salida.splitlines(), salida
    assert any(ln.startswith("E|") and "dejó en Auth filas que la nombran: auth.sso_domains (1)" in ln
               for ln in salida.splitlines()), salida
    # Y el de las demos, igual.
    demo = sql(f"""
begin;
{_sembrar(demo=True)}
insert into auth.sso_domains (id, sso_provider_id, domain) values (gen_random_uuid(), (select sso from zz), '{CORREO}');
{_intento("demo", "perform public.borrar_una_demo((select cuenta from zz))")}
{_LEER_R}
rollback;
""")
    assert "dejó en Auth filas que la nombran: auth.sso_domains (1)" in _resultados(demo)["demo"], demo


# — (a) Las claves ajenas de Auth que no son en cascada, todas en el plan —


_CLAVES = """
select 'FK|' || k.conrelid::regclass || '|' || k.confrelid::regclass || '|' || k.confdeltype::text
  from pg_constraint k
  where k.contype = 'f' and k.confdeltype <> 'c'
    and k.conrelid in (select c.oid from pg_class c join pg_namespace n on n.oid = c.relnamespace where n.nspname = 'auth');
select 'PLAN|' || tabla from public.plan_de_borrado(gen_random_uuid());
"""


def _claves_sin_plan(salida: str) -> list[str]:
    plan = {ln[5:] for ln in salida.splitlines() if ln.startswith("PLAN|")}
    assert "auth.users" in plan  # antivacío
    return sorted(f"{h} → {p} ({t})" for h, p, t in (ln.split("|")[1:] for ln in salida.splitlines()
                                                    if ln.startswith("FK|")) if p in plan and h not in plan)


def test_toda_clave_de_auth_sin_cascada_hacia_lo_que_se_borra_esta_en_el_plan():
    salida = sql(_CLAVES)
    assert "FK|auth.scim_users|auth.users|n" in salida.splitlines(), salida  # antivacío: la del 12-10
    assert _claves_sin_plan(salida) == []


def test_CONTROL_una_clave_nueva_con_set_null_se_pone_roja():
    """Las tablas de Auth no son nuestras (no se pueden crear desde aquí): el
    control añade a lo que lee el guarda la clave que traería una versión nueva."""
    salida = sql(_CLAVES) + "\nFK|auth.zz_nueva|auth.users|n"
    assert _claves_sin_plan(salida) == ["auth.zz_nueva → auth.users (n)"]


# — (c) Los dos interruptores, apagados —


_INTERRUPTORES = ("borrado_interruptor", "barrido_demos_interruptor")


def _encendidos(salida: str, migraciones: dict[str, str]) -> list[str]:
    rojos = [f"{ln.split('|')[1]} está encendido" for ln in salida.splitlines() if ln.startswith("I|") and
             ln.split("|")[2] != "false"]
    if sum(ln.startswith("I|") for ln in salida.splitlines()) != len(_INTERRUPTORES):
        rojos.append("no encuentro los dos interruptores")
    for nombre, texto in migraciones.items():
        # Sin comentarios ni textos entre comillas: la 0068 explica CÓMO
        # encenderlo en un `comment on`, y eso no lo enciende.
        sin_comentarios = re.sub(r"'(?:[^']|'')*'", "''", re.sub(r"--[^\n]*", "", texto))
        if re.search(r"update\s+public\.(" + "|".join(_INTERRUPTORES) + r")\s+set[^;]*encendido\s*=\s*true",
                     sin_comentarios, re.I):
            rojos.append(f"{nombre} lo enciende")
    return rojos


_LEER_INTERRUPTORES = "".join(f"select 'I|{t}|' || encendido from public.{t};\n" for t in _INTERRUPTORES)


def _migraciones() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in (REPO / "supabase/migrations").glob("*.sql")}


def test_los_dos_interruptores_de_borrado_siguen_apagados():
    assert _encendidos(sql(_LEER_INTERRUPTORES), _migraciones()) == []


def test_CONTROL_un_interruptor_encendido_se_pone_rojo():
    encendido = sql(f"begin;\nupdate public.borrado_interruptor set encendido = true, cambiado_por = 'guarda', "
                    f"motivo = 'control';\n{_LEER_INTERRUPTORES}rollback;\n")
    assert _encendidos(encendido, {}) == ["borrado_interruptor está encendido"]
    una = {"0099_x.sql": "-- update public.borrado_interruptor set encendido = true (comentario)\n"
                         "update public.barrido_demos_interruptor set encendido = true, motivo = 'x';"}
    assert _encendidos(sql(_LEER_INTERRUPTORES), una) == ["0099_x.sql lo enciende"]
