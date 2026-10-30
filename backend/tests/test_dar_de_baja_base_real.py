"""DAR DE BAJA (0062), contra la base de verdad.

Se comprueba borrando y dando de baja de verdad, no en teoría. Aquí no hay
dobles: la cuenta se da de alta con el endpoint real, los
fichajes van a `time_entries`, la empleada que entra en la app es un usuario de
GoTrue con contraseña, y su token es uno que GoTrue firmó de verdad.

  1. GUARDA DE LA HISTORIA. Lee del esquema TODAS las claves ajenas que apuntan
     a `employees` y se pone rojo si una tabla de historia vuelve a borrar en
     cascada (o a dejar huérfano) o si aparece una clave nueva sin clasificar.
     El control: una tabla nueva con su clave en cascada, creada en una
     transacción que se deshace, la descubre y la señala.
  2. CON HISTORIA NO SE PUEDE BORRAR: ni con el botón (la da de baja) ni a
     pelo contra la base (la base lo rechaza). Y su historial se sigue leyendo.
  3. LA RECIÉN CREADA SÍ SE BORRA del todo: no hay nada que conservar.
  4. GUARDA DEL ACCESO. Dar de baja a alguien con usuario le retira el acceso
     en la base (`acceso_retirado_el`, por trigger) y la banea en Auth.

     DESDE LA 0063 SOLO ENTRA LA ADMINISTRADORA, y a ella no se la puede dar de
     baja: una estilista con usuario ya no tiene sesión ANTES de la baja. Por
     eso este guarda ya no comprueba la sesión abierta —eso lo hace, para
     cualquiera que no sea administradora,
     `tests/test_solo_entra_la_administradora_base_real.py`— sino lo que la baja
     sigue haciendo: la marca en la base, el baneo y el ajuste de centros. Sus
     controles: quitar el trigger o el baneo lo ponen rojo.
"""

from __future__ import annotations


import httpx
import pytest

from app.db.postgrest_client import PostgrestClient, PostgrestError
from app.db.supabase_auth_client import SupabaseAuthAdminClient
from tests.test_alta_completa_e2e import SECRET_KEY, SUPABASE_URL
from tests.test_camino_humano_dos_centros import (  # noqa: F401  (fixtures)
    _cabeceras,
    anyio_backend,
    dos_centros,
)
from tests.test_un_salon_no_puede_cambiarse_el_plan import sql

pytestmark = [pytest.mark.base_real, pytest.mark.anyio]

H = _cabeceras()
CLAVE = "Una-Clave-De-Estilista-1"

# ═══ 1. El guarda de la historia ════════════════════════════════════════════

# Qué hace cada clave hacia `employees` al borrar la ficha, por tabla. HISTORIA
# tiene que ser RESTRICT ('r'): es lo que la base no deja borrar. CONFIGURACIÓN
# puede caer en cascada: es lo que la persona tenía puesto, no lo que hizo.
# Una tabla que no esté en ninguna de las dos es una clave nueva sin pensar, y
# el guarda se pone rojo hasta que alguien la clasifique.
HISTORIA = {"time_entries", "employee_schedule_exceptions", "appointments"}
CONFIGURACION = {"employee_services", "employee_schedule", "pin_attempt_locks"}

_CLAVES_HACIA_EMPLOYEES = """
select k.conrelid::regclass::text || '|' || k.confdeltype::text
from pg_constraint k
where k.contype = 'f' and k.confrelid = 'public.employees'::regclass
order by 1;
"""


def _leer_claves(salida: str) -> dict[str, str]:
    claves = {}
    for linea in salida.splitlines():
        if "|" in linea:
            tabla, accion = linea.split("|", 1)
            claves[tabla.removeprefix("public.")] = accion
    return claves


def _lo_que_esta_mal(claves: dict[str, str]) -> list[str]:
    mal = []
    for tabla, accion in sorted(claves.items()):
        if tabla in HISTORIA:
            if accion != "r":
                mal.append(f"{tabla}: es historia y al borrar hace '{accion}' (tiene que ser RESTRICT)")
        elif tabla not in CONFIGURACION:
            mal.append(f"{tabla}: clave nueva hacia employees sin clasificar ('{accion}')")
    return mal


async def test_ninguna_historia_se_borra_con_la_ficha():
    claves = _leer_claves(sql(_CLAVES_HACIA_EMPLOYEES))
    # Antivacío: si no viera las que existen, el verde no valdría nada.
    assert HISTORIA | CONFIGURACION <= set(claves), claves
    assert _lo_que_esta_mal(claves) == []


async def test_CONTROL_el_guarda_ve_una_clave_nueva_en_cascada():
    salida = sql(
        "begin;\n"
        "create table public.zz_historia_de_prueba (employee_id uuid "
        "references public.employees(id) on delete cascade);\n"
        + _CLAVES_HACIA_EMPLOYEES
        + "rollback;\n"
    )
    mal = _lo_que_esta_mal(_leer_claves(salida))
    assert any(m.startswith("zz_historia_de_prueba:") for m in mal), mal


async def test_CONTROL_el_guarda_ve_los_fichajes_en_cascada_otra_vez():
    salida = sql(
        "begin;\n"
        "alter table public.time_entries drop constraint time_entries_employee_id_fkey;\n"
        "alter table public.time_entries add constraint time_entries_employee_id_fkey "
        "foreign key (employee_id) references public.employees(id) on delete cascade;\n"
        + _CLAVES_HACIA_EMPLOYEES
        + "rollback;\n"
    )
    mal = _lo_que_esta_mal(_leer_claves(salida))
    assert any(m.startswith("time_entries:") for m in mal), mal


# ═══ Lo que comparten los demás ═════════════════════════════════════════════


def _db() -> PostgrestClient:
    return PostgrestClient(SUPABASE_URL, SECRET_KEY, timeout=30.0)


def _alta(cliente, nombre: str) -> str:
    r = cliente.post("/employees", json={"nombre": nombre, "telefono": "600111222"}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ═══ 2. Con historia no se puede borrar ════════════════════════════════════


async def test_con_fichajes_no_se_borra_se_da_de_baja_y_su_historial_se_sigue_leyendo(dos_centros):
    cliente, salon = dos_centros["cliente"], dos_centros["salon_id"]
    ficha = _alta(cliente, "Lucía Con Historia")
    db = _db()
    await db.insert("time_entries", {"salon_id": salon, "employee_id": ficha, "tipo": "entrada",
                                     "ts": "2026-09-14T07:00:00+00:00"})
    await db.insert("time_entries", {"salon_id": salon, "employee_id": ficha, "tipo": "salida",
                                     "ts": "2026-09-14T15:00:00+00:00"})

    # A pelo contra la base, con service_role (el que se salta la RLS): no.
    with pytest.raises(PostgrestError):
        await db.delete("employees", {"id": f"eq.{ficha}"})
    assert await db.count("time_entries", {"employee_id": f"eq.{ficha}"}) == 2

    previa = cliente.get(f"/employees/{ficha}/baja", headers=H)
    assert previa.status_code == 200, previa.text
    assert previa.json()["borrar_del_todo"] is False and previa.json()["fichajes"] == 2

    r = cliente.delete(f"/employees/{ficha}", headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["resultado"] == "baja"

    fila = (await db.select("employees", {"select": "estado,baja_el", "id": f"eq.{ficha}"}))[0]
    assert fila["estado"] == "baja" and fila["baja_el"]

    # Fuera del equipo, de la reserva y de los selectores…
    assert ficha not in {e["id"] for e in cliente.get("/employees", headers=H).json()}
    # …pero su ficha se puede abrir y su registro de jornada se sigue leyendo.
    con_bajas = {e["id"]: e for e in cliente.get("/employees?con_bajas=true", headers=H).json()}
    assert con_bajas[ficha]["estado"] == "baja"
    historial = cliente.get(f"/employees/{ficha}/fichajes?mes=2026-09", headers=H)
    assert historial.status_code == 200, historial.text
    assert historial.json()["horas_mes_min"] == 8 * 60

    # Y no se le puede dar hora nueva.
    otra = cliente.patch(f"/employees/{ficha}", json={"estado": "activo"}, headers=H)
    assert otra.status_code == 409, otra.text


# ═══ 3. La recién creada sí se borra ════════════════════════════════════════


async def test_la_recien_creada_se_borra_del_todo(dos_centros):
    cliente = dos_centros["cliente"]
    ficha = _alta(cliente, "Recién Llegada")
    previa = cliente.get(f"/employees/{ficha}/baja", headers=H).json()
    assert previa["borrar_del_todo"] is True

    r = cliente.delete(f"/employees/{ficha}", headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["resultado"] == "borrada"
    assert await _db().select("employees", {"select": "id", "id": f"eq.{ficha}"}) == []


# ═══ 4. El guarda del acceso ═══════════════════════════════════════════════


def _entrar(email: str) -> httpx.Response:
    return httpx.post(f"{SUPABASE_URL}/auth/v1/token", params={"grant_type": "password"},
                      headers={"apikey": SECRET_KEY}, json={"email": email, "password": CLAVE})


async def test_a_la_administradora_no_se_le_quita_el_acceso_asi(dos_centros):
    cliente = dos_centros["cliente"]
    db = _db()
    ficha = _alta(cliente, "La Dueña Que También Corta")
    await db.update("employees", {"id": f"eq.{ficha}"}, {"user_id": dos_centros["id"]})
    r = cliente.delete(f"/employees/{ficha}", headers=H)
    assert r.status_code == 409, r.text
    assert "administradora" in r.json()["detail"]
    # Y la base, por si alguien lo intenta por otro lado.
    with pytest.raises(PostgrestError):
        await db.update("employees", {"id": f"eq.{ficha}"}, {"estado": "baja", "baja_el": "2026-10-02T10:00:00+00:00"})
    await db.update("employees", {"id": f"eq.{ficha}"}, {"user_id": None})

