"""LA ATRIBUCIÓN DE LAS ALTAS (0070), contra la base, PostgREST y GoTrue de verdad.

05-10-2026: la comisión de un referido es a perpetuidad, así que lo
que se vigila aquí es lo que, si se rompiera, costaría dinero sin que nadie se
enterase:

  1. EL ALTA MIRA EL CÓDIGO: un alta con el código de un referido aprobado deja
     su atribución (con el código tal como llegó y el nombre de la cuenta), y
     una con un código que no vale sigue adelante y deja constancia.
  2. EL CÓDIGO SE COMPARA SIN MAYÚSCULAS Y SIN ESPACIOS ALREDEDOR.
  3. LA FILA NO SE TOCA: sin UPDATE, DELETE ni TRUNCATE para nadie, y el
     trigger lo impide también al superusuario.
  4. EL PLAN DE BORRADO DE LA 0068 NO SE LA LLEVA. (Que no tenga clave ajena a
     la cuenta lo vigila además `test_nuestros_registros_base_real.py`: las dos
     tablas están en `core/simulacro.SE_QUEDA`.)

Cada guarda con su control, dentro de una transacción que se deshace.

LA LIMPIEZA de las pruebas que pasan por el alta borra sus atribuciones como
superusuario y con los triggers apagados (`session_replication_role =
replica`). Es la única forma de quitarlas, a propósito, y solo la usan estas
pruebas para no dejar la base local llena de filas que no se pueden borrar.
"""

from __future__ import annotations

import subprocess
import uuid

import pytest
from starlette.testclient import TestClient

from app.db.postgrest_client import PostgrestClient
from app.main import app
from app.routers import auth as auth_router
from tests.test_alta_completa_e2e import SECRET_KEY, SUPABASE_URL, _dep, _settings
from tests.test_un_salon_no_puede_cambiarse_el_plan import _contenedor, sql

pytestmark = [pytest.mark.base_real]

TABLAS = ("public.altas_atribuidas", "public.codigos_que_no_valieron")


def _psql(guion: str) -> tuple[int, str, str]:
    r = subprocess.run(
        ["docker", "exec", "-i", _contenedor(), "psql", "-U", "postgres", "-At", "-v", "ON_ERROR_STOP=1"],
        input=guion, capture_output=True, text=True, encoding="utf-8",
    )
    return r.returncode, r.stdout, r.stderr


def _codigo() -> str:
    return "Z" + uuid.uuid4().hex[:5].upper()


def _referido(estado: str) -> tuple[str, str]:
    codigo = _codigo()
    rid = sql(
        "insert into public.referidos (nombre, email, codigo, estado) "
        f"values ('Referido de prueba', 'ref.{codigo.lower()}@estetixapp-e2e.com', '{codigo}', '{estado}') "
        "returning id;"
    ).splitlines()[0]
    return rid, codigo


def _quitar(referidos: list[str], codigos: list[str]) -> None:
    """Ver la cabecera: solo así se pueden quitar."""
    if not referidos and not codigos:
        return
    lista_r = ",".join(f"'{r}'" for r in referidos) or "null"
    lista_c = ",".join(f"'{c}'" for c in codigos) or "null"
    sql(
        "set session_replication_role = replica;\n"
        f"delete from public.altas_atribuidas where referido_id in ({lista_r});\n"
        f"delete from public.codigos_que_no_valieron where upper(btrim(codigo_llegado)) in ({lista_c});\n"
        f"delete from public.referidos where id in ({lista_r});\n"
    )


# ═══ 1. El alta mira el código ═══════════════════════════════════════════


@pytest.fixture()
def anyio_backend():
    return "asyncio"


@pytest.fixture()
async def alta(monkeypatch):
    """Un alta de verdad por `/auth/signup`, sin correo. Limpia lo que crea."""
    ajustes = _settings()
    app.dependency_overrides.clear()
    app.dependency_overrides[_dep(ajustes)] = lambda: ajustes

    async def _no_manda_correo(**kw):
        return None

    monkeypatch.setattr(auth_router, "_send_signup_confirmation_email", _no_manda_correo)
    cliente = TestClient(app)
    creadas: list[dict] = []
    referidos: list[str] = []
    codigos: list[str] = []

    async def dar_de_alta(codigo_referido: str | None) -> dict:
        email = f"atrib.{uuid.uuid4().hex[:10]}@estetixapp-e2e.com"
        cuerpo = {
            "email": email, "password": "UnaClaveBastanteLarga123!",
            "salon_nombre": f"Salón Atribuido {uuid.uuid4().hex[:4]}", "direccion": "Calle Mayor 1, Donostia",
            "telefono": "600 11 22 33", "admin_nombre": "Dueña", "plan": "pro", "cif": "B12345674",
        }
        if codigo_referido is not None:
            cuerpo["codigo_referido"] = codigo_referido
        r = cliente.post("/auth/signup", json=cuerpo)
        assert r.status_code == 202, r.text
        db = PostgrestClient(SUPABASE_URL, SECRET_KEY)
        u = (await db.select("users", {"select": "salon_id,account_id,auth_user_id", "email": f"eq.{email}"}))[0]
        creadas.append(u)
        cuenta = await db.select("accounts", {"select": "nombre", "id": f"eq.{u['account_id']}"})
        return {**u, "cuenta_nombre": cuenta[0]["nombre"]}

    yield {"dar_de_alta": dar_de_alta, "referidos": referidos, "codigos": codigos}

    db = PostgrestClient(SUPABASE_URL, SECRET_KEY)
    for u in creadas:
        await db.delete("users", {"salon_id": f"eq.{u['salon_id']}"})
        await db.delete("salons", {"id": f"eq.{u['salon_id']}"})
        await db.delete("accounts", {"id": f"eq.{u['account_id']}"})
        sql(f"delete from auth.users where id = '{u['auth_user_id']}';")
    _quitar(referidos, codigos)
    app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_un_alta_con_el_codigo_de_un_aprobado_queda_atribuida(alta):
    rid, codigo = _referido("aprobado")
    alta["referidos"].append(rid)
    llegado = f"  {codigo.lower()} "  # como lo pega una persona
    cuenta = await alta["dar_de_alta"](llegado)
    fila = sql(
        "select referido_id || '|' || cuenta_nombre || '|' || codigo_llegado || '|' || (atribuida_el > now() - interval '5 minutes') "
        f"from public.altas_atribuidas where cuenta_id = '{cuenta['account_id']}';"
    )
    assert fila == f"{rid}|{cuenta['cuenta_nombre']}|{llegado}|true"


@pytest.mark.anyio
@pytest.mark.parametrize("estado", ["pendiente", "rechazado", None], ids=["pendiente", "rechazado", "no_existe"])
async def test_un_codigo_que_no_vale_no_para_el_alta_y_queda_apuntado(alta, estado):
    if estado:
        rid, codigo = _referido(estado)
        alta["referidos"].append(rid)
    else:
        codigo = _codigo()
    alta["codigos"].append(codigo)
    cuenta = await alta["dar_de_alta"](codigo)  # 202: el alta sigue
    assert sql(f"select count(*) from public.altas_atribuidas where cuenta_id = '{cuenta['account_id']}';") == "0"
    motivo = sql(f"select motivo from public.codigos_que_no_valieron where codigo_llegado = '{codigo}';")
    assert motivo == (estado or "no_existe")


@pytest.mark.anyio
async def test_un_alta_sin_codigo_no_escribe_nada(alta):
    antes = sql("select (select count(*) from public.altas_atribuidas) || '|' || (select count(*) from public.codigos_que_no_valieron);")
    await alta["dar_de_alta"](None)
    await alta["dar_de_alta"]("   ")
    despues = sql("select (select count(*) from public.altas_atribuidas) || '|' || (select count(*) from public.codigos_que_no_valieron);")
    assert antes == despues


# ═══ 2. Sin mayúsculas y sin espacios alrededor ══════════════════════════

_CUENTA_CUALQUIERA = "(select id from public.accounts order by created_at limit 1)"


def _resultados_de(prefijo: str, codigo: str) -> list[str]:
    llegados = [codigo, codigo.lower(), f"  {codigo}\t", f"{chr(160)}{codigo.lower()}{chr(160)}", f"\n{codigo} "]
    # Una cuenta nueva por forma de escribirlo (dentro de la transacción): así
    # cada una se mide por separado y no choca con «una por cuenta».
    consultas = "".join(
        "with c as (insert into public.accounts (nombre) values ('Cuenta del ensayo') returning id) "
        f"select 'R|' || public.atribuir_alta((select id from c), $c${x}$c$);\n" for x in llegados
    )
    codigo_ok, salida, error = _psql(
        "begin;\n" + prefijo
        + f"insert into public.referidos (nombre, email, codigo, estado) values ('N', 'n@zz', '{codigo}', 'aprobado');\n"
        + consultas + "rollback;\n"
    )
    assert codigo_ok == 0, error
    return [ln[2:] for ln in salida.splitlines() if ln.startswith("R|")]


def test_el_codigo_se_compara_sin_mayusculas_ni_espacios_alrededor():
    assert _resultados_de("", _codigo()) == ["atribuida"] * 5


def test_lo_de_en_medio_si_cuenta():
    """Solo mayúsculas y bordes: con un carácter de más en medio, NO casa (ver la
    cabecera de la 0070: casar «de más» podría atribuírselo a otra persona)."""
    codigo = _codigo()
    salida = _psql(
        "begin;\n"
        f"insert into public.referidos (nombre, email, codigo, estado) values ('N', 'n@zz', '{codigo}', 'aprobado');\n"
        f"select 'R|' || public.atribuir_alta({_CUENTA_CUALQUIERA}, '{codigo[:3]}-{codigo[3:]}');\n"
        "rollback;\n"
    )[1]
    assert "R|no_existe" in salida


def test_CONTROL_una_comparacion_exacta_se_pone_roja():
    fuente = sql("select pg_get_functiondef('public.atribuir_alta(uuid, text)'::regprocedure);")
    # Desde la 0077 la regla vive en `codigo_de_alta_normalizado` (la comparten
    # el alta y la comprobación del formulario): se rompe su uso en el alta.
    exacta = fuente.replace(
        "v_buscado text := public.codigo_de_alta_normalizado(p_codigo);",
        "v_buscado text := coalesce(p_codigo, '');",
    )
    assert exacta != fuente, "el control no encontró la línea que normaliza"
    resultados = _resultados_de(exacta + ";\n", _codigo())
    assert resultados != ["atribuida"] * 5 and resultados[0] == "atribuida", resultados


# ═══ 3. La fila no se toca ═══════════════════════════════════════════════

_PERMISOS = """
select table_schema || '.' || table_name || '|' || grantee || '|' || privilege_type
from information_schema.role_table_grants
where table_schema || '.' || table_name in ('public.altas_atribuidas', 'public.codigos_que_no_valieron')
  and privilege_type in ('UPDATE', 'DELETE', 'TRUNCATE')
  and grantee <> 'postgres'
order by 1;
"""


def _permisos_de_mas(prefijo: str = "") -> list[str]:
    salida = sql(f"begin;\n{prefijo}\n{_PERMISOS}rollback;\n" if prefijo else _PERMISOS)
    return [ln for ln in salida.splitlines() if ln.count("|") == 2]


def test_nadie_puede_editar_ni_borrar_una_atribucion():
    assert _permisos_de_mas() == []
    # Antivacío: la misma consulta ve lo que SÍ tienen.
    leen = sql(
        "select count(*) from information_schema.role_table_grants where table_name = 'altas_atribuidas' "
        "and grantee = 'service_role' and privilege_type in ('SELECT', 'INSERT');"
    )
    assert leen == "2"


@pytest.mark.parametrize("permiso", ["update", "delete", "truncate"])
def test_CONTROL_concederlo_se_pone_rojo(permiso):
    hallado = _permisos_de_mas(f"grant {permiso} on public.altas_atribuidas to service_role;")
    assert f"public.altas_atribuidas|service_role|{permiso.upper()}" in hallado


def _tocar(prefijo: str, orden: str) -> tuple[int, str]:
    rid = "00700000-0000-0000-0000-000000000001"
    codigo, _, error = _psql(
        "begin;\n" + prefijo
        + f"insert into public.referidos (id, nombre, email, codigo, estado) values ('{rid}', 'N', 'n@zz', 'ZTOCAR', 'aprobado');\n"
        + f"insert into public.altas_atribuidas (referido_id, cuenta_id, cuenta_nombre, codigo_llegado, pct, pct_de_donde) values ('{rid}', gen_random_uuid(), 'C', 'ZTOCAR', 10, 'global');\n"
        + "insert into public.codigos_que_no_valieron (codigo_llegado, motivo) values ('ZZ', 'no_existe');\n"
        + orden + "\nrollback;\n"
    )
    return codigo, error


@pytest.mark.parametrize("orden", [
    "update public.altas_atribuidas set cuenta_nombre = 'otra';",
    "delete from public.altas_atribuidas;",
    "truncate public.altas_atribuidas;",
    "update public.codigos_que_no_valieron set motivo = 'pendiente';",
    "delete from public.codigos_que_no_valieron;",
], ids=["update", "delete", "truncate", "update_codigos", "delete_codigos"])
def test_ni_el_superusuario_la_cambia(orden):
    codigo, error = _tocar("", orden)
    assert codigo != 0 and "es un registro" in error, error


def test_CONTROL_sin_el_trigger_si_se_podria():
    codigo, error = _tocar(
        "drop trigger altas_atribuidas_sin_update_ni_delete on public.altas_atribuidas;\n",
        "update public.altas_atribuidas set cuenta_nombre = 'otra';",
    )
    assert codigo == 0, error


# ═══ 4. El borrado de la 0068 no se la lleva ═════════════════════════════


def _tablas_del_plan(prefijo: str) -> set[str]:
    cuenta = sql("select id from public.accounts order by created_at limit 1;")
    salida = _psql(
        "begin;\n" + prefijo
        + "insert into public.referidos (id, nombre, email, codigo, estado) "
        "values ('00700000-0000-0000-0000-000000000002', 'N', 'n2@zz', 'ZPLAN1', 'aprobado');\n"
        + f"select public.atribuir_alta('{cuenta}', 'ZPLAN1');\n"
        + f"select 'T|' || tabla from public.plan_de_borrado('{cuenta}');\n"
        + "rollback;\n"
    )
    assert salida[0] == 0, salida[2]
    return {ln[2:] for ln in salida[1].splitlines() if ln.startswith("T|")}


def test_el_plan_de_borrado_no_se_lleva_las_atribuciones():
    plan = _tablas_del_plan("")
    assert "public.accounts" in plan  # antivacío: el plan es el de la cuenta
    assert plan & set(TABLAS) == set(), plan


def test_CONTROL_con_una_clave_ajena_a_la_cuenta_se_la_llevaria():
    plan = _tablas_del_plan(
        "alter table public.altas_atribuidas add constraint zz foreign key (cuenta_id) "
        "references public.accounts(id) on delete cascade not valid;\n"
    )
    assert "public.altas_atribuidas" in plan, plan


# ═══ 5. El panel cuenta lo que cuenta ════════════════════════════════════
#
# 05-10-2026: una atribución cuenta si su cuenta existe, o si consta
# como borrada a los 90 días. Un alta deshecha no deja ninguna de las dos cosas.

_TRES_CUENTAS = """
insert into public.referidos (id, nombre, email, codigo, estado)
values ('00700000-0000-0000-0000-000000000005', 'N', 'n5@zz', 'ZCUEN1', 'aprobado');
insert into public.accounts (id, nombre) values
  ('00700000-0000-0000-0000-0000000000b1', 'Viva'),
  ('00700000-0000-0000-0000-0000000000b2', 'Alta deshecha'),
  ('00700000-0000-0000-0000-0000000000b3', 'Borrada a los 90 días');
select public.atribuir_alta('00700000-0000-0000-0000-0000000000b1', 'ZCUEN1');
select public.atribuir_alta('00700000-0000-0000-0000-0000000000b2', 'ZCUEN1');
select public.atribuir_alta('00700000-0000-0000-0000-0000000000b3', 'ZCUEN1');
-- La deshecha: el alta borra la cuenta que creó. La de los 90 días: se borra y
-- queda su constancia.
delete from public.accounts where id in ('00700000-0000-0000-0000-0000000000b2', '00700000-0000-0000-0000-0000000000b3');
insert into public.cuentas_borradas (account_id, motivo, origen, se_borraba_el, filas)
values ('00700000-0000-0000-0000-0000000000b3', 'fuera_de_plazo_90_dias', 'suscripcion', current_date, '{}');
"""


def _cuentan(prefijo: str = "") -> tuple[str, str]:
    codigo, salida, error = _psql(
        "begin;\n" + prefijo + _TRES_CUENTAS
        + "select 'F|' || count(*) from public.altas_atribuidas where referido_id = '00700000-0000-0000-0000-000000000005';\n"
        + "select 'C|' || coalesce((select altas from public.altas_que_cuentan() "
          "where referido_id = '00700000-0000-0000-0000-000000000005'), 0);\n"
        + "rollback;\n"
    )
    assert codigo == 0, error
    r = {ln[0]: ln[2:] for ln in salida.splitlines() if ln[:2] in ("F|", "C|")}
    return r["F"], r["C"]


def test_cuenta_la_viva_y_la_borrada_y_no_la_deshecha():
    filas, cuentan = _cuentan()
    assert filas == "3"  # las tres atribuciones existen: son inmutables
    assert cuentan == "2"


def test_CONTROL_contar_a_ciegas_se_pone_rojo():
    a_ciegas = (
        "create or replace function public.altas_que_cuentan() returns table (referido_id uuid, altas bigint) "
        "language sql stable as $$ select referido_id, count(*) from public.altas_atribuidas group by 1 $$;\n"
    )
    assert _cuentan(a_ciegas)[1] == "3"


@pytest.mark.anyio
async def test_un_alta_deshecha_no_deja_la_cuenta_y_no_suma(alta, monkeypatch):
    """De verdad, por `/auth/signup`: el correo de confirmación falla DESPUÉS de
    apuntar la atribución. El alta se deshace (502), la atribución se queda
    —es inmutable— y no suma, porque el alta se llevó su cuenta."""
    rid, codigo = _referido("aprobado")
    alta["referidos"].append(rid)

    async def _falla_el_correo(**kw):
        raise RuntimeError("Resend no responde")

    monkeypatch.setattr(auth_router, "_send_signup_confirmation_email", _falla_el_correo)
    ajustes = _settings()
    app.dependency_overrides[_dep(ajustes)] = lambda: ajustes
    email = f"atrib.deshecha.{uuid.uuid4().hex[:8]}@estetixapp-e2e.com"
    r = TestClient(app).post("/auth/signup", json={
        "email": email, "password": "UnaClaveBastanteLarga123!", "salon_nombre": "Alta Que Se Deshace",
        "direccion": "Calle Mayor 1, Donostia", "telefono": "600 11 22 33", "admin_nombre": "Dueña",
        "plan": "pro", "cif": "B12345674", "codigo_referido": codigo,
    })
    assert r.status_code == 502, r.text
    cuenta = sql(f"select cuenta_id from public.altas_atribuidas where referido_id = '{rid}';")
    assert cuenta, "la atribución tenía que haberse escrito antes del correo"
    assert sql(f"select count(*) from public.accounts where id = '{cuenta}';") == "0", "el alta dejó su cuenta"
    assert sql("select count(*) from public.salons where nombre_comercial = 'Alta Que Se Deshace';") == "0"
    assert sql(f"select count(*) from public.altas_que_cuentan() where referido_id = '{rid}';") == "0"
