"""VOLVER A IDENTIFICARSE CON LA CONTRASEÑA, para lo que saca datos de la app.

Mirar 300 fichas en pantalla se queda dentro; un fichero con las 300 sale y no
vuelve. Por eso la descarga de datos pide la contraseña de inicio de sesión,
siempre (01-10-2026). Toda cuenta tiene una: el alta la exige, y en
producción no hay ningún usuario de acceso sin ella (comprobado el 01-10-2026).

CONTRA LA CONTRASEÑA VIGENTE, NUNCA CONTRA UNA COPIA. La comprueba el servicio
de autenticación en ese momento (`SupabaseAuthAdminClient.contrasena_valida`).
Aquí no se guarda, no se cachea y no se compara con nada nuestro: el día que el
salón la cambie, la nueva vale sola.

EL MISMO CONTADOR DE INTENTOS QUE EL PIN (`pin_gestion_intento`, 0046): cinco
fallos y espera de 1, 5, 15 y 60 minutos, por cuenta. El intento se apunta
ANTES de preguntar, con la fila bloqueada, así que cien peticiones a la vez
cuentan cien. Es el mismo contador y no uno parecido: quien prueba PINes y
contraseñas sobre la misma cuenta suma en los dos.

NUNCA EN UN LOG NI EN UN ERROR: la contraseña solo existe en esta llamada.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from app.core.config import Settings
from app.db.postgrest_client import PostgrestClient, PostgrestError
from app.db.supabase_auth_client import SupabaseAuthAdminClient, SupabaseAuthError

logger = logging.getLogger("estetix.contrasena")

# La función del contador y sus tandas, las del PIN de gestión (0046).
FUNCION_INTENTO = "pin_gestion_intento"
INTENTOS_POR_TANDA = 5


@dataclass(frozen=True)
class Resultado:
    """`ok`, `incorrecta` (con los intentos que quedan antes de la espera) o
    `en_espera` (con hasta cuándo). `fallo` si no se pudo comprobar."""

    estado: str
    intentos_restantes: int | None = None
    bloqueado_hasta: str | None = None


def minutos_hasta(momento: str) -> int:
    hasta = datetime.fromisoformat(str(momento).replace("Z", "+00:00"))
    return max(1, math.ceil((hasta - datetime.now(timezone.utc)).total_seconds() / 60))


async def comprobar_contrasena(
    settings: Settings, servicio: PostgrestClient, account_id: str, auth_user_id: str, contrasena: str
) -> Resultado:
    try:
        intento = (await servicio.rpc(FUNCION_INTENTO, {"p_account_id": account_id}))[0]
    except (PostgrestError, IndexError):
        logger.exception("contrasena: no se pudo apuntar el intento (cuenta %s)", account_id)
        return Resultado("fallo")

    if not intento.get("permitido"):
        return Resultado("en_espera", bloqueado_hasta=intento["bloqueado_hasta"])

    auth = SupabaseAuthAdminClient(settings.supabase_url, settings.supabase_secret_key)
    try:
        email = await auth.email_de(auth_user_id)
        valida = bool(email) and await auth.contrasena_valida(email, contrasena)
    except (SupabaseAuthError, httpx.HTTPError, OSError) as e:
        # Sin el detalle: ni la contraseña ni el cuerpo de GoTrue van al log.
        logger.warning("contrasena: GoTrue no respondió bien (cuenta %s, %s)", account_id, type(e).__name__)
        return Resultado("fallo")

    if valida:
        try:
            await servicio.delete("pin_attempt_locks", {"account_id": f"eq.{account_id}"})
        except PostgrestError:
            logger.exception("contrasena: no se pudieron limpiar los intentos")
        return Resultado("ok")

    logger.info("contrasena: incorrecta (cuenta %s)", account_id)
    if intento.get("bloqueado_hasta"):
        return Resultado("en_espera", bloqueado_hasta=intento["bloqueado_hasta"])
    fallos = int(intento.get("fallos") or 0)
    return Resultado("incorrecta", intentos_restantes=INTENTOS_POR_TANDA - (fallos % INTENTOS_POR_TANDA))
