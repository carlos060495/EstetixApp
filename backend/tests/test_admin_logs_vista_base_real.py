"""LO QUE SOPORTE HIZO, CÓMO ACABÓ Y CUÁNTAS VECES LEYÓ (0091), contra la base.

Todo dentro de una transacción que se deshace: `admin_logs` no deja borrar, así
que una fila de prueba que se confirmase se quedaría para siempre.
"""

from __future__ import annotations

import subprocess

import pytest

from tests.test_un_salon_no_puede_cambiarse_el_plan import _contenedor

pytestmark = [pytest.mark.base_real]

ADMIN = "0a000000-0000-0000-0000-0000000000a1"
SID = "0b000000-0000-0000-0000-0000000000b1"
OTRA_SID = "0b000000-0000-0000-0000-0000000000b2"
ACCION = "0c000000-0000-0000-0000-0000000000c1"


def _psql(guion: str) -> tuple[int, str, str]:
    r = subprocess.run(
        ["docker", "exec", "-i", _contenedor(), "psql", "-U", "postgres", "-At", "-v", "ON_ERROR_STOP=1"],
        input=guion, capture_output=True, text=True, encoding="utf-8",
    )
    return r.returncode, r.stdout, r.stderr


def _en_transaccion(cuerpo: str) -> tuple[int, str, str]:
    return _psql(
        "begin;\n"
        f"insert into public.admin_users (id, email, nombre, password_hash) "
        f"values ('{ADMIN}', 'vista-0091@estetixapp-e2e.com', 'Prueba 0091', 'x');\n"
        f"{cuerpo}\n"
        "rollback;\n"
    )


def _lectura(sid: str, ruta: str, cuando: str) -> str:
    return (
        "insert into public.admin_logs (admin_id, accion, detalle, ip, timestamp) values "
        f"('{ADMIN}', 'impersonation_read', '{{\"sid\": \"{sid}\", \"method\": \"GET\", \"path\": \"{ruta}\"}}', "
        f"'1.1.1.1', '{cuando}');"
    )


def test_cuarenta_lecturas_son_una_fila_que_dice_cuarenta() -> None:
    lecturas = "\n".join(
        _lectura(SID, "/clients", f"2026-10-20T10:{m:02d}:00+00") for m in range(40)
    )
    codigo, salida, error = _en_transaccion(
        lecturas
        + "\n"
        + _lectura(SID, "/appointments", "2026-10-20T10:05:00+00")
        + "\n"
        + _lectura(OTRA_SID, "/clients", "2026-10-20T11:00:00+00")
        + "\n"
        f"select detalle->>'sid', detalle->>'path', veces, timestamp, ultima from public.admin_logs_vista "
        f"where admin_id = '{ADMIN}' order by detalle->>'sid', detalle->>'path';"
    )
    assert codigo == 0, error
    filas = [linea.split("|") for linea in salida.strip().splitlines() if "|" in linea]
    assert [(f[0], f[1], f[2]) for f in filas] == [
        (SID, "/appointments", "1"),
        (SID, "/clients", "40"),
        (OTRA_SID, "/clients", "1"),
    ]
    # La primera y la última de las cuarenta.
    assert filas[1][3].startswith("2026-10-20 10:00:00") and filas[1][4].startswith("2026-10-20 10:39:00")


def test_una_accion_lleva_su_resultado_y_sin_el_se_lee_vacio() -> None:
    codigo, salida, error = _en_transaccion(
        "insert into public.admin_logs (id, admin_id, accion, detalle) values "
        f"('{ACCION}', '{ADMIN}', 'impersonation_action', '{{\"path\": \"/providers\"}}'), "
        f"(gen_random_uuid(), '{ADMIN}', 'impersonation_action', '{{\"path\": \"/clients\"}}');\n"
        f"insert into public.admin_logs_resultado (admin_log_id, detalle) values ('{ACCION}', '{{\"status\": 201}}');\n"
        f"select detalle->>'path', coalesce(resultado::text, 'sin resultado'), veces from public.admin_logs_vista "
        f"where admin_id = '{ADMIN}' order by detalle->>'path';"
    )
    assert codigo == 0, error
    filas = [l for l in salida.strip().splitlines() if "|" in l]
    assert filas == ["/clients|sin resultado|1", '/providers|{"status": 201}|1']


@pytest.mark.parametrize("cambio", ["update public.admin_logs_resultado set detalle = '{}'", "delete from public.admin_logs_resultado"])
def test_el_resultado_no_se_cambia_ni_se_borra(cambio: str) -> None:
    codigo, _, error = _en_transaccion(
        f"insert into public.admin_logs (id, admin_id, accion) values ('{ACCION}', '{ADMIN}', 'impersonation_action');\n"
        f"insert into public.admin_logs_resultado (admin_log_id, detalle) values ('{ACCION}', '{{\"status\": 201}}');\n"
        f"{cambio} where admin_log_id = '{ACCION}';"
    )
    assert codigo != 0
    assert "append-only" in error


def test_un_apunte_tiene_un_solo_resultado() -> None:
    codigo, _, error = _en_transaccion(
        f"insert into public.admin_logs (id, admin_id, accion) values ('{ACCION}', '{ADMIN}', 'impersonation_action');\n"
        f"insert into public.admin_logs_resultado (admin_log_id, detalle) values ('{ACCION}', '{{\"status\": 201}}');\n"
        f"insert into public.admin_logs_resultado (admin_log_id, detalle) values ('{ACCION}', '{{\"status\": 500}}');"
    )
    assert codigo != 0
    assert "duplicate key" in error


@pytest.mark.parametrize("rol", ["anon", "authenticated"])
def test_solo_el_panel_lo_lee(rol: str) -> None:
    for objeto in ("public.admin_logs_resultado", "public.admin_logs_vista"):
        codigo, _, error = _psql(f"begin; set local role {rol}; select 1 from {objeto} limit 1; rollback;")
        assert codigo != 0, f"{rol} pudo leer {objeto}"
        assert "permission denied" in error
