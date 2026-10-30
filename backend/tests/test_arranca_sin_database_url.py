"""El backend arranca sin DATABASE_URL.

Todo va por HTTP (PostgREST/GoTrue): exigirla solo obligaba a guardar una
credencial de la base que nadie lee. Al quitarla de Railway la app no levantó.
"""

from app.core.config import Settings


def test_settings_no_exige_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    s = Settings(
        _env_file=None,
        supabase_url="http://127.0.0.1:54321",
        supabase_secret_key="sb_secret_tests",
        supabase_jwks_url="http://127.0.0.1:54321/auth/v1/.well-known/jwks.json",
    )
    assert not hasattr(s, "database_url")
