"""El JWT propio de la sesión de soporte.

Dos invariantes que valen más que el resto del fichero: un secreto flojo
DESACTIVA la función en vez de emitir tokens débiles, y un token del panel
no vale como token de soporte (ni al revés).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.core.config import Settings
from app.core.impersonation import (
    AUD_IMPERSONACION,
    ISS_IMPERSONACION,
    crear_token_impersonacion,
    decodificar_token_impersonacion,
    secreto_impersonacion,
)
from tests.conftest import ADMIN_ID, ADMIN_JWT_SECRET_TEST, IMPERSONATION_SECRET_TEST, admin_cookie

SALON = "aaaaaaaa-0000-0000-0000-000000000001"
SID = "55555555-0000-0000-0000-000000000001"
CODE_ID = "cccccccc-0000-0000-0000-000000000001"


def _settings(secreto: str = IMPERSONATION_SECRET_TEST) -> Settings:
    return Settings(jwt_impersonation_secret=secreto)


def _token(settings: Settings) -> str:
    return crear_token_impersonacion(
        settings,
        sid=SID,
        admin_id=ADMIN_ID,
        admin_email="owner@estetixapp.test",
        salon_id=SALON,
        code_id=CODE_ID,
        expira=datetime.now(timezone.utc) + timedelta(minutes=30),
    )


def test_ida_y_vuelta_conserva_los_claims() -> None:
    settings = _settings()
    claims = decodificar_token_impersonacion(settings, _token(settings))
    assert claims["sid"] == SID
    assert claims["sub"] == ADMIN_ID
    assert claims["salon_id"] == SALON
    assert claims["iss"] == ISS_IMPERSONACION
    assert claims["aud"] == AUD_IMPERSONACION


def test_el_token_dice_con_que_consentimiento_y_cuando_se_emitio() -> None:
    """El `code_id` ata la sesión al código concreto que la autorizó, y el `iat`
    permite ver a posteriori cuándo se acuñó aunque la fila ya no exista."""
    settings = _settings()
    antes = int(datetime.now(timezone.utc).timestamp())
    claims = decodificar_token_impersonacion(settings, _token(settings))
    assert claims["code_id"] == CODE_ID
    assert claims["iat"] >= antes


@pytest.mark.parametrize(
    "secreto",
    ["", "corto", ADMIN_JWT_SECRET_TEST],
    ids=["vacio", "menos-de-32-caracteres", "igual-al-del-panel"],
)
def test_un_secreto_invalido_desactiva_la_funcion(secreto: str) -> None:
    """503, no 500 ni un token débil: si el despliegue está mal configurado,
    el modo soporte no existe."""
    with pytest.raises(HTTPException) as excinfo:
        secreto_impersonacion(_settings(secreto))
    assert excinfo.value.status_code == 503


@pytest.mark.parametrize(
    ("secreto", "esperado"),
    [
        ("", "vacío"),
        ("corto", "menos de 32"),
        (ADMIN_JWT_SECRET_TEST, "ADMIN_JWT_SECRET"),
    ],
    ids=["vacio", "menos-de-32-caracteres", "igual-al-del-panel"],
)
def test_un_secreto_invalido_deja_una_linea_critical(
    secreto: str, esperado: str, caplog: pytest.LogCaptureFixture
) -> None:
    """El 503 solo lo ve quien llama; el motivo tiene que verlo operaciones.

    Sin esta línea, un despliegue con `JWT_IMPERSONATION_SECRET` mal
    configurado falla EN SILENCIO: todo el modo soporte responde 503 y no hay
    nada en el servidor que explique por qué (spec §7.1).
    """
    with caplog.at_level(logging.CRITICAL, logger="app.core.impersonation"):
        with pytest.raises(HTTPException):
            secreto_impersonacion(_settings(secreto))

    criticos = [r for r in caplog.records if r.levelno == logging.CRITICAL]
    assert len(criticos) == 1, f"se esperaba una sola línea CRITICAL: {caplog.records}"
    assert esperado in criticos[0].getMessage()


@pytest.mark.parametrize(
    "secreto",
    ["corto", ADMIN_JWT_SECRET_TEST],
    ids=["menos-de-32-caracteres", "igual-al-del-panel"],
)
def test_el_critical_nunca_registra_el_secreto_ni_un_trozo(
    secreto: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Un secreto en los logs es un secreto quemado, y truncarlo tampoco vale:
    los primeros caracteres reducen el espacio de búsqueda de quien los lea."""
    with caplog.at_level(logging.CRITICAL, logger="app.core.impersonation"):
        with pytest.raises(HTTPException):
            secreto_impersonacion(_settings(secreto))

    for registro in caplog.records:
        texto = f"{registro.getMessage()} {registro.args!r}"
        assert secreto not in texto
        assert secreto[:8] not in texto


def test_una_caducidad_sin_huso_horario_no_se_firma() -> None:
    """Un `expira` naive no rompe: desplaza. `.timestamp()` lo interpretaría en
    la zona local del servidor, y el token saldría con una caducidad corrida
    —dos horas de más en verano— sin que nada lo delate. Mejor reventar."""
    with pytest.raises(ValueError):
        crear_token_impersonacion(
            _settings(),
            sid=SID,
            admin_id=ADMIN_ID,
            admin_email="owner@estetixapp.test",
            salon_id=SALON,
            code_id=CODE_ID,
            expira=datetime.now() + timedelta(minutes=30),  # sin tzinfo
        )


def test_un_token_del_panel_no_vale_como_token_de_soporte() -> None:
    with pytest.raises(HTTPException) as excinfo:
        decodificar_token_impersonacion(_settings(), admin_cookie())
    assert excinfo.value.status_code == 401


def test_un_token_de_soporte_no_vale_en_el_panel() -> None:
    from app.core.admin_security import decode_admin_token

    settings = _settings()
    with pytest.raises(HTTPException) as excinfo:
        decode_admin_token(settings, _token(settings))
    assert excinfo.value.status_code == 401


def test_un_token_caducado_no_vale() -> None:
    settings = _settings()
    caducado = crear_token_impersonacion(
        settings,
        sid=SID,
        admin_id=ADMIN_ID,
        admin_email="owner@estetixapp.test",
        salon_id=SALON,
        code_id=CODE_ID,
        expira=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    with pytest.raises(HTTPException) as excinfo:
        decodificar_token_impersonacion(settings, caducado)
    assert excinfo.value.status_code == 401


def test_un_token_firmado_con_otro_secreto_no_vale() -> None:
    ajeno = _settings("otro-secreto-de-al-menos-32-caracteres-x")
    with pytest.raises(HTTPException) as excinfo:
        decodificar_token_impersonacion(_settings(), _token(ajeno))
    assert excinfo.value.status_code == 401
