"""Idempotent CLI for the only two accounts that need a real password:
the demo admin's Supabase Auth login and the platform superadmin(s) in
public.admin_users. This is the single place those passwords/TOTP secrets
are ever handled — never in versioned SQL.

Requires SUPABASE_URL and SUPABASE_SECRET_KEY in the environment (the new
Supabase key system — see backend/.env.example). Every write targets
whatever project SUPABASE_URL points to, so double-check that before running
against production; the script prints the target and asks for confirmation
unless --yes is passed.

Usage (run from backend/, with the venv from requirements.txt active):

  # Create/reset the demo admin's login (auth.users + public.users link).
  # Safe to re-run: re-running with a new --password resets it.
  python scripts/manage_prod_accounts.py demo-admin --email admin@salondemo.es

  # Create the first superadmin. Password/TOTP are generated fresh; prints
  # the otpauth URI + an ASCII QR to add to an authenticator app.
  python scripts/manage_prod_accounts.py superadmin --email owner@estetixapp.com --rol owner

  # Reset only the superadmin's password (TOTP untouched):
  python scripts/manage_prod_accounts.py superadmin --email owner@estetixapp.com --rol owner

  # Reset only the superadmin's TOTP secret (password untouched, omit --password):
  python scripts/manage_prod_accounts.py superadmin --email owner@estetixapp.com --rol owner --regen-totp --keep-password

  # Create a backup superadmin (independent account, own password/TOTP):
  python scripts/manage_prod_accounts.py superadmin --email backup@estetixapp.com --rol support

Passwords are never taken as bare CLI args by default: pass --password only
if you accept it landing in shell history, otherwise leave it out and the
script prompts with getpass (nothing echoed, nothing logged).
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
from pathlib import Path

import httpx
import pyotp
from passlib.context import CryptContext

# backend/ (parent of this scripts/ dir) needs to be on sys.path so
# `app.db.postgrest_client` resolves regardless of the caller's cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.postgrest_client import PostgrestClient, PostgrestError  # noqa: E402

PASSWORD_CTX = CryptContext(schemes=["argon2"])


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"Falta la variable de entorno {name}.")
    return value


def _resolve_secret(cli_value: str | None, env_var: str, prompt: str) -> str:
    if cli_value:
        return cli_value
    env_value = os.environ.get(env_var)
    if env_value:
        return env_value
    return getpass.getpass(prompt)


def _confirm_target(supabase_url: str, action: str, skip: bool) -> None:
    print(f"Destino: {supabase_url}")
    print(f"Acción:  {action}")
    if skip:
        return
    answer = input("Escribe 'yes' para continuar: ")
    if answer.strip().lower() != "yes":
        raise SystemExit("Cancelado.")


def _print_ascii_qr(uri: str) -> None:
    try:
        import qrcode
    except ImportError:
        print("(instala 'qrcode' para ver un QR en terminal; usa la otpauth URI de arriba mientras tanto)")
        return
    qr = qrcode.QRCode(border=1)
    qr.add_data(uri)
    qr.make(fit=True)
    try:
        qr.print_ascii(invert=True)
    except UnicodeEncodeError:
        # Some terminals (e.g. Windows cp1252) can't render the QR's block
        # characters; the otpauth URI above is already enough to proceed.
        print("(la terminal no soporta los caracteres del QR; usa la otpauth URI de arriba)")


class AdminAuthClient:
    """Thin wrapper over the Supabase Auth Admin API (GoTrue)."""

    def __init__(self, supabase_url: str, secret_key: str):
        self._client = httpx.AsyncClient(
            base_url=f"{supabase_url.rstrip('/')}/auth/v1",
            headers={
                "apikey": secret_key,
                "Authorization": f"Bearer {secret_key}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

    async def find_by_email(self, email: str) -> dict | None:
        resp = await self._client.get("/admin/users", params={"page": 1, "per_page": 200})
        resp.raise_for_status()
        payload = resp.json()
        users = payload.get("users", payload if isinstance(payload, list) else [])
        for user in users:
            if user.get("email", "").lower() == email.lower():
                return user
        return None

    async def create(self, email: str, password: str) -> dict:
        resp = await self._client.post(
            "/admin/users", json={"email": email, "password": password, "email_confirm": True}
        )
        resp.raise_for_status()
        return resp.json()

    async def set_password(self, user_id: str, password: str) -> dict:
        resp = await self._client.put(f"/admin/users/{user_id}", json={"password": password})
        resp.raise_for_status()
        return resp.json()

    async def aclose(self) -> None:
        await self._client.aclose()


async def upsert_demo_admin(
    supabase_url: str, secret_key: str, email: str, password: str, salon_nombre: str
) -> None:
    auth = AdminAuthClient(supabase_url, secret_key)
    try:
        existing = await auth.find_by_email(email)
        if existing:
            auth_user = await auth.set_password(existing["id"], password)
            print(f"auth.users: contraseña actualizada para {email} (id={auth_user['id']})")
        else:
            auth_user = await auth.create(email, password)
            print(f"auth.users: creado {email} (id={auth_user['id']})")
    finally:
        await auth.aclose()

    pg = PostgrestClient(supabase_url, secret_key)
    salons = await pg.select(
        "salons", {"select": "id", "nombre_comercial": f"eq.{salon_nombre}"}
    )
    if len(salons) != 1:
        raise SystemExit(
            f"Se esperaba exactamente 1 salón llamado '{salon_nombre}', "
            f"se encontraron {len(salons)}."
        )
    salon_id = salons[0]["id"]

    rows = await pg.select(
        "users", {"select": "id", "salon_id": f"eq.{salon_id}", "email": f"eq.{email}"}
    )
    if rows:
        await pg.update(
            "users", {"id": f"eq.{rows[0]['id']}"}, {"auth_user_id": auth_user["id"], "rol": "admin"}
        )
        print(f"public.users: vinculado auth_user_id a la fila existente de {email}")
    else:
        await pg.insert(
            "users",
            {
                "salon_id": salon_id,
                "nombre": "Admin Demo",
                "email": email,
                "rol": "admin",
                "auth_user_id": auth_user["id"],
            },
        )
        print(f"public.users: creada fila admin para {email}")


async def upsert_superadmin(
    supabase_url: str,
    secret_key: str,
    email: str,
    rol: str,
    nombre: str | None,
    password: str | None,
    regen_totp: bool,
) -> None:
    pg = PostgrestClient(supabase_url, secret_key)
    try:
        rows = await pg.select(
            "admin_users", {"select": "id,totp_secret,nombre", "email": f"eq.{email}"}
        )
    except PostgrestError as exc:
        raise SystemExit(f"No se pudo leer admin_users: {exc.detail}") from exc

    totp_secret: str
    if rows:
        admin_id = rows[0]["id"]
        body: dict = {"rol": rol}
        if nombre:
            body["nombre"] = nombre
        if password:
            body["password_hash"] = PASSWORD_CTX.hash(password)
        existing_secret = rows[0].get("totp_secret")
        if regen_totp or not existing_secret:
            totp_secret = pyotp.random_base32()
            body["totp_secret"] = totp_secret
            body["totp_enabled"] = True
        else:
            totp_secret = existing_secret
        await pg.update("admin_users", {"id": f"eq.{admin_id}"}, body)
        changes = []
        if password:
            changes.append("password reseteada")
        if regen_totp or not existing_secret:
            changes.append("TOTP regenerado")
        print(f"admin_users: actualizado {email} (rol={rol}{', ' + ', '.join(changes) if changes else ''})")
    else:
        if not password:
            raise SystemExit(f"{email} no existe todavía en admin_users: se necesita --password para crearlo.")
        totp_secret = pyotp.random_base32()
        await pg.insert(
            "admin_users",
            {
                "email": email,
                "nombre": nombre or email,
                "rol": rol,
                "password_hash": PASSWORD_CTX.hash(password),
                "totp_secret": totp_secret,
                "totp_enabled": True,
            },
        )
        print(f"admin_users: creado superadmin {email} (rol={rol})")

    uri = pyotp.TOTP(totp_secret).provisioning_uri(name=email, issuer_name="EstetixApp Admin")
    print(f"otpauth URI: {uri}")
    _print_ascii_qr(uri)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    demo_admin = sub.add_parser("demo-admin", help="Crea o resetea el login del admin demo.")
    demo_admin.add_argument("--email", required=True)
    demo_admin.add_argument("--password", help="Si se omite, se pide de forma interactiva (getpass).")
    demo_admin.add_argument("--salon-nombre", default="Salón Demo")
    demo_admin.add_argument("--yes", action="store_true", help="Omite la confirmación interactiva del destino.")

    superadmin = sub.add_parser("superadmin", help="Crea o resetea un superadmin de plataforma.")
    superadmin.add_argument("--email", required=True)
    superadmin.add_argument("--yes", action="store_true", help="Omite la confirmación interactiva del destino.")
    superadmin.add_argument("--rol", choices=["owner", "support", "viewer"], default="owner")
    superadmin.add_argument("--nombre")
    superadmin.add_argument("--password", help="Si se omite y --keep-password no está, se pide interactivamente.")
    superadmin.add_argument(
        "--keep-password", action="store_true",
        help="No tocar la contraseña (usar junto con --regen-totp para un reset de TOTP puro).",
    )
    superadmin.add_argument(
        "--regen-totp", action="store_true", help="Regenera el secreto TOTP aunque ya exista uno.",
    )

    return parser


async def main_async(args: argparse.Namespace) -> None:
    supabase_url = _require_env("SUPABASE_URL")
    secret_key = _require_env("SUPABASE_SECRET_KEY")

    if args.command == "demo-admin":
        _confirm_target(supabase_url, f"crear/resetear login de {args.email}", args.yes)
        password = _resolve_secret(args.password, "DEMO_ADMIN_PASSWORD", f"Contraseña para {args.email}: ")
        await upsert_demo_admin(supabase_url, secret_key, args.email, password, args.salon_nombre)

    elif args.command == "superadmin":
        _confirm_target(supabase_url, f"crear/resetear superadmin {args.email} (rol={args.rol})", args.yes)
        password = None
        if not args.keep_password:
            password = _resolve_secret(args.password, "SUPERADMIN_PASSWORD", f"Contraseña para {args.email}: ")
        await upsert_superadmin(
            supabase_url, secret_key, args.email, args.rol, args.nombre, password, args.regen_totp
        )


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
