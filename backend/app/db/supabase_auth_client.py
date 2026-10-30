from __future__ import annotations

import logging

import httpx

from app.db.conexiones import cliente_compartido

logger = logging.getLogger("estetix.supabase_auth")


class SupabaseAuthError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def _codigo_de(resp: httpx.Response) -> str | None:
    try:
        cuerpo = resp.json()
    except ValueError:
        return None
    return cuerpo.get("error_code") if isinstance(cuerpo, dict) else None


class SupabaseAuthAdminClient:
    """Cliente mínimo del Admin API de GoTrue para alta self-service."""

    def __init__(self, supabase_url: str, secret_key: str):
        self._base_url = f"{supabase_url.rstrip('/')}/auth/v1"
        self._headers = {
            "apikey": secret_key,
            "Authorization": f"Bearer {secret_key}",
            "Content-Type": "application/json",
        }

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """Una llamada al Admin API, por la conexión compartida del proceso.

        LAS CABECERAS VAN EN LA LLAMADA, no en el cliente, y aquí no es solo la
        regla general: antes iban pegadas al cliente (`headers=self._headers`),
        y dos métodos de abajo dependen de NO llevarlas. `verify_magiclink` va
        a propósito sin `Authorization` —es un endpoint público y la cabecera de
        servicio confunde a GoTrue sobre a quién emite— y `logout_local` manda
        la del usuario. Con un cliente compartido que llevara las de servicio,
        el primero las recuperaría sin que nadie lo notara.
        """
        return await cliente_compartido().request(
            method, f"{self._base_url}{path}", headers=self._headers, timeout=30.0, **kwargs
        )

    @staticmethod
    def _ensure_ok(response: httpx.Response) -> None:
        if response.status_code >= 400:
            detail = response.text
            try:
                payload = response.json()
                if isinstance(payload, dict):
                    detail = str(payload.get("msg") or payload.get("error_description") or payload.get("error") or payload)
            except ValueError:
                pass
            raise SupabaseAuthError(response.status_code, detail)

    async def find_by_email(self, email: str) -> dict | None:
        # GoTrue pagina resultados; recorremos páginas acotadas para evitar falsos negativos.
        target = email.lower()
        for page in range(1, 11):
            resp = await self._request("GET", "/admin/users", params={"page": page, "per_page": 200})
            self._ensure_ok(resp)
            payload = resp.json()
            users = payload.get("users", payload if isinstance(payload, list) else [])
            if not users:
                return None
            for user in users:
                if (user.get("email") or "").lower() == target:
                    return user
            if len(users) < 200:
                return None
        return None

    async def create_unconfirmed_user(self, email: str, password: str) -> dict:
        resp = await self._request(
            "POST",
            "/admin/users",
            json={
                "email": email,
                "password": password,
                "email_confirm": False,
            },
        )
        self._ensure_ok(resp)
        return resp.json()

    async def create_confirmed_user(self, email: str, password: str, app_metadata: dict) -> dict:
        """Un usuario ya confirmado, sin correo de por medio: el de la
        administradora de un salón de muestra (0080), cuyo buzón no existe."""
        resp = await self._request(
            "POST",
            "/admin/users",
            json={"email": email, "password": password, "email_confirm": True, "app_metadata": app_metadata},
        )
        self._ensure_ok(resp)
        return resp.json()

    async def generate_signup_link(self, email: str, password: str, redirect_to: str) -> dict:
        resp = await self._request(
            "POST",
            "/admin/generate_link",
            json={
                "type": "signup",
                "email": email,
                "password": password,
                "redirect_to": redirect_to,
                "options": {"redirect_to": redirect_to},
            },
        )
        self._ensure_ok(resp)
        return resp.json()

    async def generate_recovery_link(self, email: str, redirect_to: str) -> dict:
        """Enlace de recuperación de contraseña (mismo patrón que el de alta).

        GoTrue responde 4xx si el email no tiene cuenta: quien llama debe tratar
        ese caso como "no enviamos correo" SIN cambiar la respuesta al cliente,
        o el endpoint se convierte en un oráculo de emails registrados.
        """
        resp = await self._request(
            "POST",
            "/admin/generate_link",
            json={
                "type": "recovery",
                "email": email,
                "redirect_to": redirect_to,
                "options": {"redirect_to": redirect_to},
            },
        )
        self._ensure_ok(resp)
        return resp.json()

    async def generate_magiclink(self, email: str) -> dict:
        """Enlace mágico para un usuario YA existente, sin enviar correo.

        Es el primer paso del acuñado de una sesión de soporte: devuelve un
        `hashed_token` de un solo uso que `verify_magiclink` canjea por un
        access_token real, con los claims salon_id/rol que pone el hook.
        """
        resp = await self._request(
            "POST",
            "/admin/generate_link",
            json={"type": "magiclink", "email": email},
        )
        self._ensure_ok(resp)
        return resp.json()

    async def verify_magiclink(self, token_hash: str) -> dict:
        """Canjea el hashed_token por una sesión. Devuelve el cuerpo de GoTrue.

        Se llama SIN la cabecera de servicio: /verify es un endpoint público y
        la Authorization de servicio confundiría a GoTrue sobre a quién emite.

        Va en `token_hash` y SIN `email`, que es lo que espera GoTrue para un
        hash ya calculado. Con `token` + `email` vuelve a hashear lo recibido y
        responde 403 "Token has expired or is invalid": ese campo es para el OTP
        de 6 dígitos (`email_otp`), no para el `hashed_token`.
        """
        resp = await cliente_compartido().post(
            f"{self._base_url}/verify",
            headers={"apikey": self._headers["apikey"], "Content-Type": "application/json"},
            json={"type": "magiclink", "token_hash": token_hash},
            timeout=30.0,
        )
        self._ensure_ok(resp)
        return resp.json()

    async def logout_local(self, access_token: str) -> None:
        """Cierra SOLO la sesión de ese token (scope=local).

        Nunca uses scope global aquí: cerraría todas las sesiones del dueño,
        que no ha hecho nada malo por que le demos soporte.
        """
        resp = await cliente_compartido().post(
            f"{self._base_url}/logout",
            params={"scope": "local"},
            headers={
                "apikey": self._headers["apikey"],
                "Authorization": f"Bearer {access_token}",
            },
            timeout=30.0,
        )
        if resp.status_code not in (204, 401, 403):
            self._ensure_ok(resp)

    async def email_de(self, user_id: str) -> str | None:
        """El email VIGENTE de un usuario, preguntado a GoTrue en este momento."""
        resp = await self._request("GET", f"/admin/users/{user_id}")
        self._ensure_ok(resp)
        return (resp.json() or {}).get("email")

    async def contrasena_valida(self, email: str, password: str) -> bool:
        """¿Es esa la contraseña de inicio de sesión de ese email, AHORA?

        Se lo pregunta a GoTrue con un inicio de sesión de verdad
        (`grant_type=password`): no hay copia nuestra con la que comparar, así
        que el día que el salón cambie su contraseña la nueva vale aquí sola.
        La sesión que GoTrue abre al acertar se cierra en el acto
        (`logout_local`): nadie la usa y no se queda viva.

        LA CONTRASEÑA NO SALE DE AQUÍ: no se guarda, no se registra y ningún
        error la lleva. Por eso no pasa por `_ensure_ok`, que copia el cuerpo
        de la respuesta en el mensaje: aquí el error se decide por el código.

        Devuelve False SOLO si GoTrue dice `invalid_credentials`. Cualquier
        otra respuesta —el login apagado, un email sin confirmar, el límite de
        intentos de GoTrue, un 500— es que no se pudo comprobar, y se lanza SIN
        detalle, para que quien llame no la confunda con una contraseña mala.
        """
        resp = await cliente_compartido().post(
            f"{self._base_url}/token",
            params={"grant_type": "password"},
            headers={"apikey": self._headers["apikey"], "Content-Type": "application/json"},
            json={"email": email, "password": password},
            timeout=30.0,
        )
        if resp.status_code == 400 and _codigo_de(resp) == "invalid_credentials":
            return False
        if resp.status_code != 200:
            raise SupabaseAuthError(resp.status_code, "GoTrue no pudo comprobar la contraseña.")
        token = (resp.json() or {}).get("access_token")
        if token:
            try:
                await self.logout_local(token)
            except Exception as e:  # noqa: BLE001 — la sesión caduca sola; la comprobación ya está hecha
                # Sin el token ni el cuerpo: solo que no se pudo cerrar.
                logger.warning("No se pudo cerrar la sesión de la comprobación (%s).", type(e).__name__)
        return True

    async def update_user_by_id(self, user_id: str, attributes: dict) -> dict:
        resp = await self._request("PUT", f"/admin/users/{user_id}", json=attributes)
        self._ensure_ok(resp)
        return resp.json()

    async def delete_user(self, user_id: str) -> None:
        resp = await self._request("DELETE", f"/admin/users/{user_id}")
        self._ensure_ok(resp)
