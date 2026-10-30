"""UN ALTA SIN CONFIRMAR CADUCA Y SU CORREO VUELVE A SERVIR (0087), contra la base de verdad.

El alta se hace por la API de verdad (`POST /auth/signup`, sin correo) con un
código de referido aprobado. Después:

  1. ANTES DEL PLAZO no caduca, y repetir el alta dice QUÉ PASA y QUÉ DÍA queda
     libre el correo (no «ese email está en uso»).
  2. PASADO EL PLAZO caduca, y no deja NI UNA FILA SUYA EN NINGÚN SITIO. Se
     busca sin lista: cada fila de cada tabla de `public`, `auth` y `storage`,
     leída entera como texto, contra los identificadores del alta (cuenta,
     centro, ficha, identidad) y su correo. Solo pueden quedar las dos filas
     que se quedan a propósito: la constancia de la caducidad y la atribución,
     ANULADA (no cuenta para el referido y dice por qué).
  3. Y EL MISMO CORREO VUELVE A REGISTRARSE (202).

CONTROLES: el buscador encuentra las filas del alta antes de caducar (si no
viera nada, el «no queda nada» no probaría nada); y una cuenta con trabajo
dentro —una clienta— NO se caduca aunque cumpla todo lo demás.

Lo que siembra, se lo lleva (también lo que es inmutable, saltándose sus
triggers solo en la limpieza).
"""

from __future__ import annotations

import secrets
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers import auth as auth_router
from tests.lo_que_deja_una_prueba import sql
from tests.test_alta_completa_e2e import _dep, _settings

pytestmark = [pytest.mark.base_real]

CLAVE = "UnaClaveBastanteLarga123!"
DESPUES_DEL_PLAZO = "now() + interval '8 days'"


def _alta(email: str, codigo: str | None = None):
    # El limitador de altas es por IP y por correo, y aquí todas salen de la
    # misma IP de prueba y repiten correo a propósito: se vacía, como en los
    # demás tests del alta.
    auth_router.signup_rate_limiter._by_ip.clear()
    auth_router.signup_rate_limiter._by_email.clear()
    payload = {"email": email, "password": CLAVE, "admin_nombre": "Ana Prueba", "salon_nombre": "Salón a medias",
               "direccion": "Calle Falsa 1", "telefono": "+34600111222", "plan": "pro", "cif": "B12345678"}
    if codigo:
        payload["codigo_referido"] = codigo
    return TestClient(app).post("/auth/signup", json=payload)


def _identificadores(email: str) -> dict[str, str]:
    fila = sql(
        "select a.id, s.id, u.id, u.auth_user_id from public.users u join public.salons s on s.id = u.salon_id "
        f"join public.accounts a on a.id = u.account_id where u.email = '{email}';"
    )
    cuenta, centro, ficha, identidad = fila.split("|")
    return {"cuenta": cuenta, "centro": centro, "ficha": ficha, "identidad": identidad}


# LA BÚSQUEDA SIN LISTA: cada fila de cada tabla, como texto, contra lo que
# identifica al alta. Devuelve «tabla: filas».
BUSCAR = """
create or replace function pg_temp.lo_que_nombra(p text[]) returns setof text language plpgsql as $$
declare t record; n bigint;
begin
  for t in select c.oid::regclass::text as tabla from pg_class c join pg_namespace s on s.oid = c.relnamespace
           where s.nspname in ('public', 'auth', 'storage') and c.relkind = 'r' loop
    execute format('select count(*) from %s x where lower(x::text) like any (%L::text[])', t.tabla, p) into n;
    if n > 0 then return next t.tabla || ': ' || n; end if;
  end loop;
end $$;
"""


def _lo_que_nombra(ids: dict[str, str], email: str) -> list[str]:
    patrones = "{" + ",".join(f"%{v.lower()}%" for v in [*ids.values(), email]) + "}"
    return sql(BUSCAR + f"select pg_temp.lo_que_nombra('{patrones}');").splitlines()


@pytest.fixture()
def alta():
    """Un referido aprobado y un correo nuevo. Al final se lleva TODO."""
    codigo = "ZCAD" + secrets.token_hex(3).upper()
    rid = sql("insert into public.referidos (nombre, email, codigo, estado) "
              f"values ('Referido de prueba', 'ref.{codigo.lower()}@estetixapp-e2e.com', '{codigo}', 'aprobado') "
              "returning id;").splitlines()[0]
    email = f"caduca.{uuid.uuid4().hex[:10]}@estetixapp-e2e.com"
    original = auth_router._send_signup_confirmation_email

    async def _no_manda(**kw):
        return None

    auth_router._send_signup_confirmation_email = _no_manda
    ajustes = _settings()
    app.dependency_overrides[_dep(ajustes)] = lambda: ajustes
    datos = {"email": email, "codigo": codigo, "referido": rid, "cuentas": []}
    try:
        yield datos
    finally:
        auth_router._send_signup_confirmation_email = original
        app.dependency_overrides.clear()
        # Lo que quede vivo de este correo (el alta repetida), por la misma función.
        sql(f"select * from public.caducar_altas_sin_confirmar({DESPUES_DEL_PLAZO});")
        cuentas = ",".join(f"'{c}'" for c in datos["cuentas"]) or "null"
        # A MANO TAMBIÉN, por si la función que se prueba es la que falla: la
        # limpieza no puede depender de ella. Primero con sus cascadas…
        sql(
            f"delete from public.clients where nombre = 'Clienta de {email}';\n"
            f"delete from public.users where email = '{email}';\n"
            f"delete from public.salons where account_id in ({cuentas});\n"
            f"delete from public.accounts where id in ({cuentas});\n"
            f"delete from auth.users where email = '{email}';\n"
            f"delete from auth.audit_log_entries where lower(payload::text) like '%{email}%';\n"
        )
        # …y en réplica SOLO lo inmutable: la réplica también apaga las cascadas.
        sql(
            "set session_replication_role = replica;\n"
            f"delete from public.altas_sin_confirmar_caducadas where cuenta_id in ({cuentas});\n"
            f"delete from public.altas_atribuidas where referido_id = '{rid}';\n"
            f"delete from public.referidos where id = '{rid}';\n"
        )
        quedan = _lo_que_nombra({"r": rid}, email)
        assert quedan == [], f"la prueba dejó filas: {quedan}"


def test_un_alta_sin_confirmar_caduca_sin_dejar_nada_y_el_correo_vuelve_a_servir(alta):
    email = alta["email"]
    r = _alta(email, alta["codigo"])
    assert r.status_code == 202, r.text
    ids = _identificadores(email)
    alta["cuentas"].append(ids["cuenta"])

    # CONTROL: el buscador ve el alta (cuenta, centro, ficha, identidad de Auth…).
    antes = _lo_que_nombra(ids, email)
    print("\nlo que deja un alta a medias:", antes)
    tablas = {x.split(":")[0] for x in antes}
    assert {"accounts", "salons", "users", "auth.users", "auth.identities"} <= tablas, antes

    # 1. ANTES DEL PLAZO: no caduca, y el alta repetida dice qué pasa y qué día.
    assert sql("select caducadas from public.caducar_altas_sin_confirmar(now());") == "0"
    r = _alta(email)
    assert r.status_code == 409 and "falta confirmarla" in r.json()["detail"], r.text
    assert "podrás registrarte de nuevo" in r.json()["detail"]
    print("repetida antes del plazo:", r.status_code, r.json()["detail"])

    # 2. PASADO EL PLAZO: caduca, y solo queda lo que se queda a propósito.
    hecho = sql(f"select caducadas || '|' || saltadas from public.caducar_altas_sin_confirmar({DESPUES_DEL_PLAZO});")
    assert hecho.startswith("1|"), hecho
    despues = _lo_que_nombra(ids, email)
    print("lo que queda tras caducar:", despues)
    assert sorted(despues) == ["altas_atribuidas: 1", "altas_sin_confirmar_caducadas: 1"], despues

    # La atribución, ANULADA: no cuenta para el referido y dice por qué.
    cuenta_el_referido = sql(f"select count(*) from public.altas_que_cuentan() where referido_id = '{alta['referido']}';")
    assert cuenta_el_referido == "0"
    motivo = sql(f"select motivo from public.atribuciones_anuladas where cuenta_id = '{ids['cuenta']}';")
    assert "ANULADA" in motivo and "No es un error" in motivo, motivo

    # 3. EL MISMO CORREO VUELVE A REGISTRARSE.
    r = _alta(email)
    print("el mismo correo, otra vez:", r.status_code)
    assert r.status_code == 202, r.text
    alta["cuentas"].append(_identificadores(email)["cuenta"])


def test_CONTROL_una_cuenta_con_trabajo_dentro_no_se_caduca(alta):
    email = alta["email"]
    assert _alta(email).status_code == 202
    ids = _identificadores(email)
    alta["cuentas"].append(ids["cuenta"])
    # Una clienta: alguien trabajó en esta cuenta, aunque nunca confirmara el correo.
    sql(f"insert into public.clients (salon_id, nombre) values ('{ids['centro']}', 'Clienta de {email}');")
    hecho = sql(f"select caducadas || '|' || saltadas from public.caducar_altas_sin_confirmar({DESPUES_DEL_PLAZO});")
    assert hecho == "0|1", hecho
    assert sql(f"select count(*) from public.accounts where id = '{ids['cuenta']}';") == "1"
    # La limpieza: sin la clienta, ya es un alta a medias y la caduca la propia función.
    sql(f"delete from public.clients where nombre = 'Clienta de {email}';")


def test_CONTROL_un_alta_confirmada_o_que_ya_entro_no_caduca(alta):
    """Lo más peligroso que podría hacer esto: borrar la cuenta de alguien que SÍ
    confirmó su correo, o que llegó a entrar. Con todo lo demás igual —sin
    suscripción, sin datos, pasado el plazo—, no se toca."""
    email = alta["email"]
    assert _alta(email).status_code == 202
    ids = _identificadores(email)
    alta["cuentas"].append(ids["cuenta"])
    for confirmado, entro in (("now()", "null"), ("null", "now()")):
        marca = f"confirmado={confirmado}, entró={entro}"
        sql(f"update auth.users set email_confirmed_at = {confirmado}, last_sign_in_at = {entro} where id = '{ids['identidad']}';")
        hecho = sql(f"select caducadas from public.caducar_altas_sin_confirmar({DESPUES_DEL_PLAZO});")
        assert hecho == "0", (marca, hecho)
        assert sql(f"select count(*) from public.accounts where id = '{ids['cuenta']}';") == "1", marca
