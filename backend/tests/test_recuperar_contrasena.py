from __future__ import annotations

from urllib.parse import unquote

from fastapi import FastAPI
from starlette.testclient import TestClient

from app.core.config import Settings, get_settings
from app.db.supabase_auth_client import SupabaseAuthError
from app.routers import auth

GENERIC = (
    "Si el email existe, te hemos enviado un correo con instrucciones para "
    "restablecer tu contraseña."
)


class FakeRecoveryAdmin:
    """Admin API de GoTrue falso: 4xx cuando el email no tiene cuenta."""

    def __init__(self, known_email: str | None = None, error: SupabaseAuthError | None = None):
        self.known_email = known_email
        self.error = error
        self.calls: list[tuple[str, str]] = []

    async def generate_recovery_link(self, email: str, redirect_to: str):
        self.calls.append((email, redirect_to))
        if self.error:
            raise self.error
        if self.known_email and email.lower() != self.known_email.lower():
            raise SupabaseAuthError(404, "User not found")
        return {
            "properties": {
                "action_link": f"https://auth.example.test/verify?email={email}",
                "email_otp": "123456",
            }
        }


def _settings() -> Settings:
    return Settings(
        supabase_url="http://localhost:54321",
        supabase_secret_key="secret",
        supabase_jwks_url="http://localhost:54321/jwks",
        database_url="postgresql://x",
        resend_api_key="re_test_key",
        email_from="EstetixApp <noreply@estetixapp.es>",
    )


def _app(fake_admin: FakeRecoveryAdmin, settings_factory=_settings) -> FastAPI:
    app = FastAPI()
    app.include_router(auth.router)
    app.dependency_overrides[get_settings] = settings_factory
    auth._auth_admin = lambda settings: fake_admin
    # El limitador vive en memoria del módulo: sin resetear, un test arrastraría
    # los intentos del anterior y el siguiente vería un 429 fantasma.
    auth.recovery_rate_limiter = auth.AttemptRateLimiter(
        max_attempts_per_ip=8,
        max_attempts_per_email=3,
        ip_window_minutes=15,
        email_window_minutes=30,
    )
    return app


def test_recuperar_envia_correo_branded_cuando_el_email_existe(monkeypatch):
    fake_admin = FakeRecoveryAdmin(known_email="existe@example.com")
    app = _app(fake_admin)

    sent: list[tuple[str, str]] = []

    async def fake_send(settings: Settings, to_email: str, action_link: str):
        sent.append((to_email, action_link))

    monkeypatch.setattr(auth, "_send_recovery_email", fake_send)

    resp = TestClient(app).post("/auth/recuperar", json={"email": "Existe@Example.com"})

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "message": GENERIC}
    assert sent == [("existe@example.com", "https://auth.example.test/verify?email=existe@example.com")]
    assert fake_admin.calls == [("existe@example.com", "https://estetixapp.es/nueva-contrasena")]


def test_recuperar_no_filtra_nada_del_payload_de_supabase(monkeypatch):
    """La respuesta es solo el mensaje genérico.

    El action_link lleva el token dentro: si se colara en el cuerpo HTTP,
    cualquiera podría restablecer la contraseña de cualquier cuenta pidiendo
    una recuperación. El email_otp, igual.
    """
    fake_admin = FakeRecoveryAdmin(known_email="existe@example.com")
    app = _app(fake_admin)

    async def fake_send(settings: Settings, to_email: str, action_link: str):
        return None

    monkeypatch.setattr(auth, "_send_recovery_email", fake_send)

    resp = TestClient(app).post("/auth/recuperar", json={"email": "existe@example.com"})

    assert resp.json() == {"status": "ok", "message": GENERIC}
    assert "auth.example.test" not in resp.text
    assert "123456" not in resp.text


def test_recuperar_no_revela_si_el_email_no_existe(monkeypatch):
    fake_admin = FakeRecoveryAdmin(known_email="existe@example.com")
    app = _app(fake_admin)

    sent: list[str] = []

    async def fake_send(settings: Settings, to_email: str, action_link: str):
        sent.append(to_email)

    monkeypatch.setattr(auth, "_send_recovery_email", fake_send)

    client = TestClient(app)
    existe = client.post("/auth/recuperar", json={"email": "existe@example.com"})
    no_existe = client.post("/auth/recuperar", json={"email": "fantasma@example.com"})

    # Mismo status y mismo cuerpo: nada distingue una cuenta real de una que no.
    assert no_existe.status_code == existe.status_code == 200
    assert no_existe.json() == existe.json()
    # Y al inexistente no le sale ningún correo.
    assert sent == ["existe@example.com"]


def test_recuperar_responde_igual_si_falla_el_envio(monkeypatch):
    """Un fallo de Resend solo ocurre si el email existe: no puede notarse."""
    fake_admin = FakeRecoveryAdmin(known_email="existe@example.com")
    app = _app(fake_admin)

    async def fake_send(settings: Settings, to_email: str, action_link: str):
        raise RuntimeError("resend down")

    monkeypatch.setattr(auth, "_send_recovery_email", fake_send)

    resp = TestClient(app).post("/auth/recuperar", json={"email": "existe@example.com"})

    assert resp.status_code == 200
    assert resp.json()["message"] == GENERIC


def test_recuperar_responde_igual_si_supabase_falla(monkeypatch):
    fake_admin = FakeRecoveryAdmin(error=SupabaseAuthError(500, "gotrue down"))
    app = _app(fake_admin)

    resp = TestClient(app).post("/auth/recuperar", json={"email": "quien-sea@example.com"})

    assert resp.status_code == 200
    assert resp.json()["message"] == GENERIC


def test_recuperar_aplica_rate_limit_por_email(monkeypatch):
    fake_admin = FakeRecoveryAdmin(known_email="existe@example.com")
    app = _app(fake_admin)

    async def fake_send(settings: Settings, to_email: str, action_link: str):
        return None

    monkeypatch.setattr(auth, "_send_recovery_email", fake_send)

    client = TestClient(app)
    for _ in range(3):
        assert client.post("/auth/recuperar", json={"email": "existe@example.com"}).status_code == 200

    blocked = client.post("/auth/recuperar", json={"email": "existe@example.com"})
    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers


def test_recuperar_falla_claro_si_falta_configuracion_de_correo():
    fake_admin = FakeRecoveryAdmin(known_email="existe@example.com")

    def _settings_sin_email() -> Settings:
        base = _settings()
        base.resend_api_key = ""
        base.email_from = ""
        return base

    app = _app(fake_admin, settings_factory=_settings_sin_email)

    resp = TestClient(app).post("/auth/recuperar", json={"email": "existe@example.com"})

    # No depende del email, así que no es un oráculo: se puede reportar tal cual.
    assert resp.status_code == 500
    assert "RESEND_API_KEY" in resp.json()["detail"]
    assert fake_admin.calls == []


def test_recuperar_rechaza_email_mal_formado():
    app = _app(FakeRecoveryAdmin())

    resp = TestClient(app).post("/auth/recuperar", json={"email": "no-es-un-email"})

    assert resp.status_code == 422


def test_email_de_recuperacion_usa_plantilla_branded():
    html = auth._build_recovery_html("https://auth.example.test/verify?token=abc")

    assert "Restablecer contraseña" in html
    assert "Restablece tu contraseña" in html
    assert "#C9A84C" in html  # oro de marca
    assert "#0A0A0B" in html  # fondo negro
    # La plantilla compartida no deja huecos sin rellenar.
    assert "{{" not in html


def test_el_correo_no_expone_el_action_link_a_los_escaneres():
    """Capa 1: el token viaja en el FRAGMENTO de nuestra página puente.

    Si el action_link crudo apareciera como href, el GET del escáner lo
    consumiría y el usuario recibiría un enlace ya muerto.
    """
    action_link = "https://auth.example.test/verify?token=abc&type=recovery"
    html = auth._build_recovery_html(action_link)

    # El enlace real solo aparece dentro del fragmento, url-encoded.
    assert 'href="https://auth.example.test/verify' not in html
    assert "https://estetixapp.es/recuperar/continuar#link=" in html
    assert "https%3A%2F%2Fauth.example.test%2Fverify%3Ftoken%3Dabc%26type%3Drecovery" in html


def test_la_url_puente_codifica_el_enlace_entero():
    envuelto = auth._build_recovery_continue_link("https://auth.example.test/verify?a=1&b=2")

    fragmento = envuelto.split("#link=", 1)[1]
    # Nada de separadores sin escapar: un & suelto partiría el fragmento.
    assert "&" not in fragmento
    assert "?" not in fragmento
    assert unquote(fragmento) == "https://auth.example.test/verify?a=1&b=2"


def test_email_de_alta_conserva_su_texto_tras_compartir_plantilla():
    html = auth._build_signup_confirmation_html("https://auth.example.test/verify?token=xyz")

    assert "Confirma tu cuenta" in html
    assert "Confirmar mi cuenta" in html
    assert "Si no has creado ninguna cuenta en EstetixApp" in html
    assert "{{" not in html
