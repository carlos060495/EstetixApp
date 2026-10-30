from __future__ import annotations

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.core.config import Settings, get_settings
from app.core.security import CurrentUser, get_current_user, get_postgrest_client, get_service_postgrest_client
from app.db.supabase_auth_client import SupabaseAuthError
from app.routers import auth, billing


class FakeAuthAdmin:
    def __init__(self, existing_email: str | None = None):
        self.existing_email = existing_email
        self.created: list[dict] = []
        self.updated: list[tuple[str, dict]] = []
        self.deleted: list[str] = []
        self.raise_on_update = False

    class _Props:
        def __init__(self, action_link: str):
            self.action_link = action_link

    class _Resp:
        def __init__(self, user: dict, action_link: str):
            self.user = user
            self.properties = FakeAuthAdmin._Props(action_link)

    async def find_by_email(self, email: str):
        if self.existing_email and email.lower() == self.existing_email.lower():
            # CONFIRMADA. Desde que el alta recicla identidades huérfanas, una
            # identidad SIN confirmar y sin ficha ya no significa «este email
            # está cogido»: significa «un alta se quedó a medias». Lo que sí lo
            # significa es una confirmada, o una ficha en `public.users`.
            return {
                "id": "already",
                "email": email,
                "email_confirmed_at": "2026-01-01T00:00:00Z",
            }
        return None

    async def generate_signup_link(self, email: str, password: str, redirect_to: str):
        user = {"id": "auth-user-1", "email": email}
        self.created.append(user)
        return FakeAuthAdmin._Resp(user, f"https://auth.example.test/verify?email={email}")

    async def update_user_by_id(self, user_id: str, attributes: dict):
        if self.raise_on_update:
            raise SupabaseAuthError(500, "update failed")
        self.updated.append((user_id, attributes))
        return {"id": user_id, **attributes}

    async def delete_user(self, user_id: str):
        self.deleted.append(user_id)
        return None


class FakeAuthAdminFlat(FakeAuthAdmin):
    async def generate_signup_link(self, email: str, password: str, redirect_to: str):
        user = {"id": "auth-user-1", "email": email}
        self.created.append(user)
        return {
            "id": user["id"],
            "email": email,
            "action_link": f"https://auth.example.test/verify?email={email}",
            "verification_type": "signup",
            "redirect_to": redirect_to,
        }


class FakeDB:
    def __init__(self):
        self.salons: dict[str, dict] = {}
        # La 0040 le da cuenta a cada salón nuevo con un trigger. El doble hace
        # lo mismo: si no, el alta seguiría dejando salones sin cuenta y este
        # test no probaría el camino que corre en producción.
        self.accounts: dict[str, dict] = {}
        self.users: list[dict] = []
        self.last_insert: list[tuple[str, dict]] = []

    def _cuenta_de(self, salon: dict) -> str:
        """El trigger de la 0040, también para los salones sembrados a mano."""
        account_id = salon.get("account_id") or f"cuenta-de-{salon['id']}"
        salon["account_id"] = account_id
        self.accounts.setdefault(account_id, {"id": account_id})
        return account_id

    @staticmethod
    def _eq(params: dict, key: str) -> str:
        return params[key].split(".", 1)[1]

    async def select(self, table: str, params: dict | None = None):
        params = params or {}
        if table == "users" and "email" in params:
            email = self._eq(params, "email")
            return [u for u in self.users if u["email"] == email][:1]
        if table == "salons" and "id" in params:
            sid = self._eq(params, "id")
            salon = self.salons.get(sid)
            if not salon:
                return []
            fila = {"id": sid, "account_id": self._cuenta_de(salon)}
            select = params.get("select") or ""
            if "accounts(" in select:
                cuenta = self.accounts[self._cuenta_de(salon)]
                pedidas = select.split("accounts(", 1)[1].split(")", 1)[0]
                fila["accounts"] = {
                    c.strip(): cuenta.get(c.strip()) for c in pedidas.split(",")
                }
            return [fila]
        if table == "subscriptions":
            return []
        if table == "plataforma_config":
            # Los días de prueba viven en la base (0068), no en Settings.
            return [{"dias_de_prueba": 21}]
        return []

    async def insert(self, table: str, body: dict):
        self.last_insert.append((table, body))
        if table == "salons":
            salon = dict(body)
            # El trigger `salon_entra_en_una_cuenta` de la 0040: un salón sin
            # cuenta dicha ES una cuenta nueva, y nace con lo que trae el salón.
            account_id = self._cuenta_de(salon)
            for columna in ("plan", "pending_plan", "stripe_customer_id",
                            "cif", "logo_url", "accent_color"):
                if columna in salon:
                    self.accounts[account_id][columna] = salon.pop(columna)
            self.salons[salon["id"]] = salon
            return body
        if table == "users":
            self.users.append(dict(body))
            return body
        if table == "stripe_events":
            return body
        if table == "subscriptions":
            return body
        return body

    async def update(self, table: str, params: dict, body: dict):
        if table == "salons" and "id" in params:
            sid = self._eq(params, "id")
            if sid in self.salons:
                self.salons[sid].update(body)
            return [body]
        if table == "accounts" and "id" in params:
            cid = self._eq(params, "id")
            self.accounts.setdefault(cid, {"id": cid}).update(body)
            return [body]
        return [body]

    async def delete(self, table: str, params: dict):
        if table == "salons" and "id" in params:
            sid = self._eq(params, "id")
            self.salons.pop(sid, None)
        return None


def _settings() -> Settings:
    return Settings(
        supabase_url="http://localhost:54321",
        supabase_secret_key="secret",
        supabase_jwks_url="http://localhost:54321/jwks",
        database_url="postgresql://x",
        stripe_secret_key="sk_test_x",
        stripe_webhook_secret="whsec_x",
        stripe_price_basico_mensual="price_basico_m_test",
        stripe_price_pro_mensual="price_pro_m_test",
        stripe_price_elite_mensual="price_elite_m_test",
        frontend_base_url="http://localhost:3000",
        resend_api_key="re_test_key",
        email_from="EstetixApp <noreply@estetixapp.es>",
    )


def test_signup_rejects_used_email(monkeypatch):
    """Un email de alguien de verdad sigue dando 409.

    «De verdad» = identidad CONFIRMADA (alguien demostró tener ese buzón) o
    ficha en `public.users`. El caso de una identidad a medias, que antes caía
    aquí y atrapaba a la persona, vive ahora en
    `test_alta_identidad_huerfana.py`.
    """
    app = FastAPI()
    app.include_router(auth.router)

    db = FakeDB()
    app.dependency_overrides[get_settings] = _settings
    app.dependency_overrides[get_service_postgrest_client] = lambda: db
    monkeypatch.setattr(auth, "_auth_admin", lambda settings: FakeAuthAdmin(existing_email="used@example.com"))

    client = TestClient(app)
    resp = client.post(
        "/auth/signup",
        json={
            "salon_nombre": "Salon Test",
            "direccion": "Calle Mayor 1, Donostia",
            "telefono": "600 11 22 33",
            "admin_nombre": "Admin Test",
            "email": "used@example.com",
            "password": "Password!123",
            "plan": "pro",
            "cif": "B12345674",
        },
    )

    assert resp.status_code == 409
    assert "email ya está en uso" in resp.json()["detail"]


def test_signup_creates_tenant_and_trial_checkout_uses_pending_plan(monkeypatch):
    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(billing.router)

    db = FakeDB()
    fake_auth = FakeAuthAdmin()

    app.dependency_overrides[get_settings] = _settings
    app.dependency_overrides[get_service_postgrest_client] = lambda: db
    app.dependency_overrides[get_postgrest_client] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        sub="auth-user-1",
        salon_id="11111111-1111-1111-1111-111111111111",
        rol="admin",
        email="nuevo@example.com",
    )
    monkeypatch.setattr(auth, "_auth_admin", lambda settings: fake_auth)

    sent_emails: list[tuple[str, str]] = []

    async def fake_send_signup_confirmation_email(settings: Settings, to_email: str, action_link: str):
        sent_emails.append((to_email, action_link))

    monkeypatch.setattr(auth, "_send_signup_confirmation_email", fake_send_signup_confirmation_email)

    async def fake_create_session(**kwargs):
        class _S:
            url = "https://stripe.test/checkout/session_123"

        fake_create_session.payload = kwargs
        return _S()

    async def fake_create_customer(**kwargs):
        class _C:
            id = "cus_test_123"

        return _C()

    monkeypatch.setattr(billing.stripe.checkout.Session, "create_async", fake_create_session)
    monkeypatch.setattr(billing.stripe.Customer, "create_async", fake_create_customer)

    # El checkout de prueba pregunta a Stripe si esta cuenta YA tiene una
    # suscripción, para no cobrar dos veces a quien el webhook dejó colgado.
    # Aquí no tiene ninguna: es un alta recién hecha.
    async def sin_suscripciones(**kw):
        return {"data": []}

    monkeypatch.setattr(billing.stripe.Subscription, "list_async", sin_suscripciones)

    signup_resp = TestClient(app).post(
        "/auth/signup",
        json={
            "salon_nombre": "Salon Nuevo",
            "direccion": "Calle Mayor 1, Donostia",
            "telefono": "600 11 22 33",
            "admin_nombre": "Admin Nuevo",
            "email": "nuevo@example.com",
            "password": "Password!123",
            "plan": "pro",
            "cif": "B12345674",
        },
    )

    assert signup_resp.status_code == 202
    created_salon_id = next(body["id"] for table, body in db.last_insert if table == "salons")
    assert sent_emails == [("nuevo@example.com", "https://auth.example.test/verify?email=nuevo@example.com")]
    assert fake_auth.updated == [
        (
            "auth-user-1",
            {
                "app_metadata": {
                    "salon_id": created_salon_id,
                    "role": "admin",
                    "pending_plan": "pro",
                }
            },
        )
    ]
    # El salón del token, con su cuenta: el plan pendiente y el customer de
    # Stripe viven ahí desde la 0040.
    db.salons["11111111-1111-1111-1111-111111111111"] = {
        "id": "11111111-1111-1111-1111-111111111111",
        "account_id": "cuenta-del-token",
    }
    db.accounts["cuenta-del-token"] = {
        "id": "cuenta-del-token",
        "pending_plan": "pro",
        "stripe_customer_id": None,
    }
    # El `pending_plan` que trae el alta acaba en la CUENTA, que es donde vive
    # desde la 0040. El salón ya no lo guarda.
    cuenta = db.accounts[db.salons[created_salon_id]["account_id"]]
    assert cuenta["pending_plan"] == "pro"

    trial_resp = TestClient(app).post("/billing/checkout/trial")
    assert trial_resp.status_code == 200
    assert trial_resp.json()["url"].startswith("https://stripe.test/")
    assert fake_create_session.payload["subscription_data"]["trial_period_days"] == 21
    assert fake_create_session.payload["subscription_data"]["metadata"]["salon_id"] == "11111111-1111-1111-1111-111111111111"
    assert fake_create_session.payload["payment_method_collection"] == "always"
    assert fake_create_session.payload["success_url"].endswith("/agenda?trial=started")
    assert fake_create_session.payload["cancel_url"].endswith("/agenda?trial=cancel")

    override_resp = TestClient(app).post("/billing/checkout/trial", json={"plan": "elite"})
    assert override_resp.status_code == 200
    assert fake_create_session.payload["line_items"][0]["price"] == "price_elite_m_test"


def test_signup_returns_clear_error_when_confirmation_email_fails(monkeypatch):
    app = FastAPI()
    app.include_router(auth.router)

    db = FakeDB()
    fake_auth = FakeAuthAdmin()

    app.dependency_overrides[get_settings] = _settings
    app.dependency_overrides[get_service_postgrest_client] = lambda: db
    monkeypatch.setattr(auth, "_auth_admin", lambda settings: fake_auth)

    async def fake_send_signup_confirmation_email(settings: Settings, to_email: str, action_link: str):
        raise RuntimeError("resend down")

    monkeypatch.setattr(auth, "_send_signup_confirmation_email", fake_send_signup_confirmation_email)

    client = TestClient(app)
    resp = client.post(
        "/auth/signup",
        json={
            "salon_nombre": "Salon Test",
            "direccion": "Calle Mayor 1, Donostia",
            "telefono": "600 11 22 33",
            "admin_nombre": "Admin Test",
            "email": "falla@example.com",
            "password": "Password!123",
            "plan": "pro",
            "cif": "B12345674",
        },
    )

    assert resp.status_code == 502
    assert resp.json()["detail"] == "No se pudo enviar el correo."
    assert fake_auth.deleted == ["auth-user-1"]
    assert db.salons == {}


def test_signup_rolls_back_and_returns_error_when_profile_save_fails(monkeypatch):
    app = FastAPI()
    app.include_router(auth.router)

    db = FakeDB()
    fake_auth = FakeAuthAdmin()
    fake_auth.raise_on_update = True

    app.dependency_overrides[get_settings] = _settings
    app.dependency_overrides[get_service_postgrest_client] = lambda: db
    monkeypatch.setattr(auth, "_auth_admin", lambda settings: fake_auth)

    async def fake_send_signup_confirmation_email(settings: Settings, to_email: str, action_link: str):
        return None

    monkeypatch.setattr(auth, "_send_signup_confirmation_email", fake_send_signup_confirmation_email)

    client = TestClient(app)
    resp = client.post(
        "/auth/signup",
        json={
            "salon_nombre": "Salon Test",
            "direccion": "Calle Mayor 1, Donostia",
            "telefono": "600 11 22 33",
            "admin_nombre": "Admin Test",
            "email": "perfil-falla@example.com",
            "password": "Password!123",
            "plan": "pro",
            "cif": "B12345674",
        },
    )

    assert resp.status_code == 502
    assert resp.json()["detail"] == "No se pudo guardar el perfil."
    assert fake_auth.deleted == ["auth-user-1"]
    assert db.salons == {}


def test_signup_returns_clear_error_when_email_config_is_missing(monkeypatch):
    app = FastAPI()
    app.include_router(auth.router)

    db = FakeDB()
    fake_auth = FakeAuthAdmin()

    def _settings_missing_email() -> Settings:
        base = _settings()
        base.resend_api_key = ""
        base.email_from = ""
        return base

    app.dependency_overrides[get_settings] = _settings_missing_email
    app.dependency_overrides[get_service_postgrest_client] = lambda: db
    monkeypatch.setattr(auth, "_auth_admin", lambda settings: fake_auth)

    client = TestClient(app)
    resp = client.post(
        "/auth/signup",
        json={
            "salon_nombre": "Salon Test",
            "direccion": "Calle Mayor 1, Donostia",
            "telefono": "600 11 22 33",
            "admin_nombre": "Admin Test",
            "email": "config-falla@example.com",
            "password": "Password!123",
            "plan": "pro",
            "cif": "B12345674",
        },
    )

    assert resp.status_code == 500
    assert "RESEND_API_KEY" in resp.json()["detail"]
    assert "EMAIL_FROM" in resp.json()["detail"]
    assert fake_auth.deleted == ["auth-user-1"]
    assert db.salons == {}


def test_signup_accepts_flat_generate_link_response(monkeypatch):
    app = FastAPI()
    app.include_router(auth.router)

    db = FakeDB()
    fake_auth = FakeAuthAdminFlat()

    app.dependency_overrides[get_settings] = _settings
    app.dependency_overrides[get_service_postgrest_client] = lambda: db
    monkeypatch.setattr(auth, "_auth_admin", lambda settings: fake_auth)

    sent_emails: list[tuple[str, str]] = []

    async def fake_send_signup_confirmation_email(settings: Settings, to_email: str, action_link: str):
        sent_emails.append((to_email, action_link))

    monkeypatch.setattr(auth, "_send_signup_confirmation_email", fake_send_signup_confirmation_email)

    client = TestClient(app)
    resp = client.post(
        "/auth/signup",
        json={
            "salon_nombre": "Salon Flat",
            "direccion": "Calle Mayor 1, Donostia",
            "telefono": "600 11 22 33",
            "admin_nombre": "Admin Flat",
            "email": "flat@example.com",
            "password": "Password!123",
            "plan": "pro",
            "cif": "B12345674",
        },
    )

    assert resp.status_code == 202
    assert sent_emails == [("flat@example.com", "https://auth.example.test/verify?email=flat@example.com")]
    assert fake_auth.updated and fake_auth.updated[0][0] == "auth-user-1"


# ═══════════════ el primer centro tampoco es una excepción (21-09-2026)
#
# Se pedían solo al abrir el SEGUNDO, y eso dejaba abierta la puerta grande: el
# primero se crea con calma, así que es el que más fácil se rellena, y sin ellos
# su página pública nace sin saber decirle a una clienta ni adónde ir ni por
# dónde llamar.


def _payload(**extra) -> dict:
    return {
        "salon_nombre": "Salon Nuevo",
        "direccion": "Calle Mayor 1, Donostia",
        "telefono": "600 11 22 33",
        "admin_nombre": "Admin Nuevo",
        "email": "nuevo@example.com",
        "password": "Password!123",
        "plan": "pro",
        "cif": "B12345674",
        **extra,
    }


def _client(monkeypatch: pytest.MonkeyPatch, db: FakeDB | None = None) -> TestClient:
    """El alta, con sus dobles —puestos con `monkeypatch`, que los devuelve aunque
    el test falle a mitad—. El correo NO se manda: estos tests miran lo que
    se guarda y lo que se rechaza, no el envío."""
    app = FastAPI()
    app.include_router(auth.router)
    app.dependency_overrides[get_settings] = _settings
    app.dependency_overrides[get_service_postgrest_client] = lambda: db or FakeDB()
    app.dependency_overrides[get_postgrest_client] = lambda: db or FakeDB()
    monkeypatch.setattr(auth, "_auth_admin", lambda settings: FakeAuthAdmin())

    async def _sin_correo(settings, to_email, action_link):
        return None

    monkeypatch.setattr(auth, "_send_signup_confirmation_email", _sin_correo)
    # EL LIMITADOR ES GLOBAL AL PROCESO y cada test de aquí hace un alta: sin
    # esto, el que corra después falla por un cupo que agotó otro.
    auth.signup_rate_limiter._by_ip.clear()
    auth.signup_rate_limiter._by_email.clear()
    return TestClient(app)


@pytest.mark.parametrize("falta", ["direccion", "telefono"])
def test_el_alta_SIN_direccion_o_SIN_telefono_se_rechaza(falta, monkeypatch):
    cuerpo = _payload()
    cuerpo.pop(falta)

    r = _client(monkeypatch).post("/auth/signup", json=cuerpo)

    assert r.status_code == 422, r.text


def test_un_telefono_que_no_se_puede_marcar_se_rechaza_con_su_codigo(monkeypatch):
    """Que sea marcable de verdad lo decide `core/telefonos`, igual que en el
    perfil y en el alta del segundo centro. El código deja que el formulario
    ponga el motivo debajo del campo."""
    r = _client(monkeypatch).post("/auth/signup", json=_payload(telefono="600 11"))

    assert r.status_code == 422, r.text
    assert r.json()["detail"]["codigo"] == "telefono_no_valido"


def test_el_primer_centro_nace_con_su_direccion_y_su_telefono_en_E164(monkeypatch):
    """Y guardados como el resto del proyecto, para que el `tel:` del correo y
    el del widget marquen escriba la gente como escriba."""
    db = FakeDB()
    r = _client(monkeypatch, db).post("/auth/signup", json=_payload())

    assert r.status_code == 202, r.text
    salon = next(b for tabla, b in db.last_insert if tabla == "salons")
    assert salon["direccion"] == "Calle Mayor 1, Donostia"
    assert salon["telefono"] == "+34600112233"
