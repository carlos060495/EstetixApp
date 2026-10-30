from functools import lru_cache
import re
from urllib.parse import urlparse

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Vacía = no puesta: una línea «NOMBRE=» del .env deja el valor por defecto, no lo pisa con "".
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    env: str = "development"

    supabase_url: str
    supabase_secret_key: str
    supabase_jwks_url: str
    # Sin DATABASE_URL a propósito: todo va por HTTP (PostgREST/GoTrue). Una
    # variable obligatoria que nadie lee solo obliga a guardar una credencial.

    allowed_origins: str = "http://localhost:3000"
    admin_allowed_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    admin_jwt_secret: str = "change-me-admin-jwt-secret"
    admin_jwt_issuer: str = "estetixapp-admin"
    admin_jwt_audience: str = "estetixapp-admin-panel"
    admin_jwt_ttl_minutes: int = 720

    # Sesión de soporte. SIN default a propósito: un valor por defecto
    # aquí equivale a suplantar cualquier salón. Si no está bien configurada, los
    # endpoints de impersonación responden 503 (ver app/core/impersonation.py).
    jwt_impersonation_secret: str = ""

    # Stripe. Todos opcionales: en local van vacíos y el gating real
    # se apoya en salons.plan (BD). El plan es fuente de verdad DESDE Stripe: lo
    # escribe SOLO el webhook. La secret key nunca llega al frontend.
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    # El secreto del webhook de CONNECT (0072): otro punto de conexión en Stripe,
    # el de «eventos de cuentas conectadas», con su propio secreto.
    stripe_connect_webhook_secret: str = ""
    stripe_tax_rate_id: str = ""
    stripe_price_basico_mensual: str = ""
    stripe_price_basico_anual: str = ""
    stripe_price_pro_mensual: str = ""
    stripe_price_pro_anual: str = ""
    stripe_price_elite_mensual: str = ""
    stripe_price_elite_anual: str = ""
    # Reestructuración de precios: la moneda entra en la clave. Las de arriba se
    # quedan para poder LEER las suscripciones creadas con la tarifa antigua
    # (ver app/billing/prices.py); estas son las únicas con las que se vende.
    stripe_price_basico_mensual_eur: str = ""
    stripe_price_pro_mensual_eur: str = ""
    stripe_price_elite_mensual_eur: str = ""
    stripe_price_basico_mensual_usd: str = ""
    stripe_price_pro_mensual_usd: str = ""
    stripe_price_elite_mensual_usd: str = ""

    # Base del frontend para construir success_url/cancel_url/return_url.
    frontend_base_url: str = "http://localhost:3000"
    # Los días de prueba ya no se configuran aquí: viven en la base
    # (`plataforma_config.dias_de_prueba`, 0068). Ver `core/prueba.py`.

    # Agente de WhatsApp. Todos vacíos por defecto: sin ellos el
    # webhook responde 403 a todo (la firma nunca cuadra) y no envía nada, que
    # es el fallo seguro. Un default "de mentira" aquí sería una puerta abierta.
    #
    #   whatsapp_verify_token  el que se escribe en el panel de Meta al dar de
    #                          alta la URL; solo lo usa el GET de verificación.
    #   whatsapp_app_secret    App Secret de la app de Meta. Con él se valida la
    #                          firma X-Hub-Signature-256 de CADA POST.
    #   whatsapp_access_token  token de envío GLOBAL. Desde la 0085 es solo el
    #                          RESPALDO de los números que aún no se han conectado
    #                          por el camino nuevo; ver token_de_envio().
    #
    # Cada salón conecta su número (0085, Embedded Signup de Meta):
    #
    #   whatsapp_clave_tokens  clave Fernet con la que se cifra el token de cada
    #                          salón antes de guardarlo. Sin ella no se conecta
    #                          ningún número nuevo (ni se lee uno ya guardado).
    #   whatsapp_app_id        id de la app de Meta. Público: lo usa el botón.
    #   whatsapp_es_config_id  id de la «configuración» de Embedded Signup que se
    #                          crea en el panel de Meta. Público.
    whatsapp_verify_token: str = ""
    whatsapp_app_secret: str = ""
    whatsapp_access_token: str = ""
    whatsapp_clave_tokens: str = ""
    whatsapp_app_id: str = ""
    whatsapp_es_config_id: str = ""

    # Cerebro del agente. Sin clave el bot no genera nada y manda el
    # mensaje de cortesía: el webhook sigue guardando, que es lo que no se puede
    # perder.
    #
    # El MODELO no se clava en el código a propósito: Google retira versiones, y
    # con el nombre en el entorno el cambio es una variable y no un despliegue.
    # Flash por defecto: es el barato, y para responder preguntas frecuentes con
    # los datos ya inyectados no hace falta más.
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"

    # Email transaccional (confirmación de cuenta de signup).
    resend_api_key: str = ""
    email_from: str = "EstetixApp <noreply@estetixapp.es>"

    @staticmethod
    def _normalize_origin(origin: str) -> str | None:
        value = origin.strip().strip("\"'")
        if not value or value == "*":
            return None
        parsed = urlparse(value)
        if not parsed.scheme or not parsed.netloc:
            return None
        return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")

    @staticmethod
    def _split_origins(raw: str) -> list[str]:
        # Accept env values separated by comma, semicolon or whitespace.
        return [chunk for chunk in re.split(r"[,;\s]+", raw.strip()) if chunk]

    @property
    def allowed_origins_list(self) -> list[str]:
        out: list[str] = []
        for raw in self._split_origins(self.allowed_origins):
            normalized = self._normalize_origin(raw)
            if normalized and normalized not in out:
                out.append(normalized)
        return out

    @property
    def admin_allowed_origins_list(self) -> list[str]:
        out: list[str] = []
        for raw in self._split_origins(self.admin_allowed_origins):
            normalized = self._normalize_origin(raw)
            if normalized and normalized not in out:
                out.append(normalized)
        return out


@lru_cache
def get_settings() -> Settings:
    return Settings()
