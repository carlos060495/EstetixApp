"""Una variable vacía es una variable NO puesta.

`backend/.env.example` trae solo nombres («NOMBRE=»). Copiado tal cual como
`.env` y con las tres obligatorias rellenas, el backend tiene que arrancar con
los valores por defecto del resto: antes, «ADMIN_JWT_TTL_MINUTES=» era el
número "" y no arrancaba, y los textos vacíos pisaban sus valores por defecto.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError
from pydantic_settings import SettingsConfigDict

from app.core.config import Settings

EJEMPLO = Path(__file__).resolve().parents[1] / ".env.example"
OBLIGATORIAS = {
    "SUPABASE_URL": "http://127.0.0.1:54321",
    "SUPABASE_SECRET_KEY": "sb_secret_de_prueba",
    "SUPABASE_JWKS_URL": "http://127.0.0.1:54321/auth/v1/.well-known/jwks.json",
}


@pytest.fixture
def entorno_limpio(monkeypatch):
    """Que solo cuente el fichero: el entorno del proceso gana al .env."""
    for nombre in Settings.model_fields:
        monkeypatch.delenv(nombre.upper(), raising=False)


def _env_copiado_del_ejemplo(tmp_path: Path, rellenas: dict[str, str]) -> Path:
    lineas = []
    for linea in EJEMPLO.read_text(encoding="utf-8").splitlines():
        nombre = linea.split("=", 1)[0]
        lineas.append(f"{nombre}={rellenas[nombre]}" if nombre in rellenas else linea)
    destino = tmp_path / ".env"
    destino.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    return destino


def test_el_ejemplo_con_las_tres_obligatorias_arranca_con_los_valores_por_defecto(tmp_path, entorno_limpio):
    ajustes = Settings(_env_file=_env_copiado_del_ejemplo(tmp_path, OBLIGATORIAS))
    assert ajustes.supabase_url == OBLIGATORIAS["SUPABASE_URL"]
    for nombre, campo in Settings.model_fields.items():
        if not campo.is_required():
            assert getattr(ajustes, nombre) == campo.default, nombre


def test_una_obligatoria_vacia_falta_y_lo_dice(tmp_path, entorno_limpio):
    sin_jwks = {k: v for k, v in OBLIGATORIAS.items() if k != "SUPABASE_JWKS_URL"}
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=_env_copiado_del_ejemplo(tmp_path, sin_jwks))
    assert [(e["loc"], e["type"]) for e in error.value.errors()] == [(("supabase_jwks_url",), "missing")]


def test_CONTROL_si_lo_vacio_contara_como_valor_no_arrancaria(tmp_path, entorno_limpio):
    class ComoAntes(Settings):
        model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=False)

    with pytest.raises(ValidationError):
        ComoAntes(_env_file=_env_copiado_del_ejemplo(tmp_path, OBLIGATORIAS))
