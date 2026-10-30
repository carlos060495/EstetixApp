from __future__ import annotations

from datetime import datetime, timezone
import logging
from threading import Lock
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from app.core import demo, publico
from app.billing.estado_suscripcion import estado_de_sesion
from app.billing.precios import moneda_efectiva
from app.core import plazo_de_datos as plazos
from app.core import zona as zonas
from app.core.cuenta import con_la_cuenta_dentro
from app.core.cuenta import id_de_cuenta
from app.core.atribucion import atribuir_alta
from app.core.limites import AttemptRateLimiter
from app.core.telefonos import TelefonoNoValido, region_del_salon, revisar_telefono
from app.core.config import Settings, get_settings
from app.core.emails import EmailConfigError, construir_email_branded, enviar_email_branded
from app.core.emails import ofuscar_email as _ofuscar_email
from app.core.security import (
    admite_cuenta_cortada,
    CurrentUser,
    get_current_user,
    get_postgrest_client,
    get_service_postgrest_client,
    get_storage_client,
)
from app.db.supabase_auth_client import SupabaseAuthAdminClient, SupabaseAuthError
from app.db.postgrest_client import PostgrestClient, PostgrestError
from app.schemas.auth import RecuperarIn, RecuperarOut, SignupIn, SignupOut
from app.db.storage_client import StorageClient
from app.routers.billing import SUSCRIPCION_EXISTE
from app.routers.salon import sign_logo_url
from app.tareas import plazo_de_datos as tarea_plazo
from app.core.prueba import SinDiasDePrueba, dias_de_prueba

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)

_SIGNUP_CONFIRM_SUBJECT = "Confirma tu cuenta de EstetixApp"
_SIGNUP_CONFIRM_REDIRECT_TO = "https://estetixapp.es/login"
_SIGNUP_CONFIRM_TITLE = "Confirma tu cuenta"
_SIGNUP_CONFIRM_INTRO = (
    "¡Bienvenido/a! Solo falta un paso para empezar tu prueba de 14 días. "
    "Pulsa el botón para confirmar tu email y activar tu cuenta."
)
_SIGNUP_CONFIRM_CTA = "Confirmar mi cuenta"
_SIGNUP_CONFIRM_FOOTER = "Si no has creado ninguna cuenta en EstetixApp, puedes ignorar este correo."

_RECOVERY_SUBJECT = "Restablece tu contraseña de EstetixApp"
_RECOVERY_REDIRECT_TO = "https://estetixapp.es/nueva-contrasena"

# Envoltorio anti-escáner. Los escáneres de enlaces (Safe Links de Microsoft
# Defender y compañía) hacen GET a todo lo que hay en el correo, y ese GET
# CONSUME el token de un solo uso: el usuario llega después y se encuentra el
# enlace muerto. El correo apunta por eso a una página nuestra con el enlace
# real en el FRAGMENTO (#link=…): el fragmento no viaja al servidor, así que el
# escáner se descarga una página estática sin token y no consume nada.
#
# Esa página redirige sola en cuanto carga. Decisión consciente: protege frente
# a los escáneres que solo hacen GET (la mayoría) y renuncia a proteger frente a
# los que además ejecutan JS (raros), a cambio de que el usuario no vea ningún
# paso extra.
_RECOVERY_CONTINUE_URL = "https://estetixapp.es/recuperar/continuar"
_RECOVERY_TITLE = "Restablece tu contraseña"
_RECOVERY_INTRO = (
    "Hemos recibido una solicitud para restablecer la contraseña de tu cuenta de EstetixApp. "
    "Pulsa el botón para elegir una nueva. Por seguridad, el enlace caduca y solo se puede usar una vez."
)
_RECOVERY_CTA = "Restablecer contraseña"
_RECOVERY_FOOTER = (
    "Si no has solicitado restablecer tu contraseña, puedes ignorar este correo: "
    "tu contraseña actual seguirá funcionando."
)

# Respuesta única del endpoint de recuperación: la MISMA exista o no la cuenta.
_RECOVERY_GENERIC_MESSAGE = (
    "Si el email existe, te hemos enviado un correo con instrucciones para "
    "restablecer tu contraseña."
)


signup_rate_limiter = AttemptRateLimiter()

# Recuperación: cupo más estrecho que el alta. Cada intento dispara un correo,
# así que el abuso aquí se traduce en bombardeo al buzón de un tercero.
recovery_rate_limiter = AttemptRateLimiter(
    max_attempts_per_ip=8,
    max_attempts_per_email=3,
    ip_window_minutes=15,
    email_window_minutes=30,
)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _auth_admin(settings: Settings) -> SupabaseAuthAdminClient:
    return SupabaseAuthAdminClient(settings.supabase_url, settings.supabase_secret_key)


def _obj_get(container: object, key: str) -> object | None:
    if isinstance(container, dict):
        return container.get(key)
    return getattr(container, key, None)


def _extract_user_id(payload: object) -> str | None:
    user = _obj_get(payload, "user")
    user_id_nested = _obj_get(user, "id") if user is not None else None
    if isinstance(user_id_nested, str) and user_id_nested:
        return user_id_nested

    user_id_root_dict = payload.get("id") if isinstance(payload, dict) else None
    if isinstance(user_id_root_dict, str) and user_id_root_dict:
        return user_id_root_dict

    user_id_root_obj = getattr(payload, "id", None)
    if isinstance(user_id_root_obj, str) and user_id_root_obj:
        return user_id_root_obj
    return None


def _extract_action_link(payload: object) -> str | None:
    properties = _obj_get(payload, "properties")
    nested_obj = _obj_get(properties, "action_link") if properties is not None else None
    if isinstance(nested_obj, str) and nested_obj:
        return nested_obj

    nested_dict = (
        properties.get("action_link")
        if isinstance(properties, dict)
        else None
    )
    if isinstance(nested_dict, str) and nested_dict:
        return nested_dict

    root_dict = payload.get("action_link") if isinstance(payload, dict) else None
    if isinstance(root_dict, str) and root_dict:
        return root_dict

    root_obj = getattr(payload, "action_link", None)
    if isinstance(root_obj, str) and root_obj:
        return root_obj
    return None


def _claves(container: object) -> list[str]:
    if isinstance(container, dict):
        return sorted(str(k) for k in container)
    return sorted(a for a in dir(container) if not a.startswith("_"))


def _resumen_generate_link(payload: object) -> str:
    """Resumen de la respuesta de generate_link SIN las credenciales que trae.

    La respuesta incluye `action_link`, `hashed_token` y `email_otp`: son
    credenciales de un solo uso que confirman una cuenta o resetean su
    contraseña. Volcar el objeto entero con `%r` las deja escritas en un log
    que se retiene y se replica, así que cualquiera que lo lea —o cualquier
    agregador al que se envíe— puede tomar la cuenta sin tocar la base de datos
    ni las claves.

    Se registran solo NOMBRES de claves y el `user.id`, que es exactamente lo
    que hace falta para diagnosticar por qué no vino lo que esperábamos.
    """
    propiedades = _obj_get(payload, "properties")
    partes = [
        f"user_id={_extract_user_id(payload)}",
        f"claves={_claves(payload)}",
    ]
    if propiedades is not None:
        partes.append(f"properties={_claves(propiedades)}")
    return " ".join(partes)


def _build_recovery_continue_link(action_link: str) -> str:
    """Envuelve el action_link real en el fragmento de nuestra página puente."""
    return f"{_RECOVERY_CONTINUE_URL}#link={quote(action_link, safe='')}"


def _build_signup_confirmation_html(action_link: str) -> str:
    return construir_email_branded(
        title=_SIGNUP_CONFIRM_TITLE,
        intro=_SIGNUP_CONFIRM_INTRO,
        cta_label=_SIGNUP_CONFIRM_CTA,
        action_link=action_link,
        footer_note=_SIGNUP_CONFIRM_FOOTER,
    )


def _build_recovery_html(action_link: str) -> str:
    """El botón NO lleva el action_link: lleva la página puente que lo envuelve."""
    return construir_email_branded(
        title=_RECOVERY_TITLE,
        intro=_RECOVERY_INTRO,
        cta_label=_RECOVERY_CTA,
        action_link=_build_recovery_continue_link(action_link),
        footer_note=_RECOVERY_FOOTER,
    )


async def _send_signup_confirmation_email(settings: Settings, to_email: str, action_link: str) -> None:
    await enviar_email_branded(
        settings=settings,
        to_email=to_email,
        subject=_SIGNUP_CONFIRM_SUBJECT,
        html=_build_signup_confirmation_html(action_link),
    )


async def _send_recovery_email(settings: Settings, to_email: str, action_link: str) -> None:
    await enviar_email_branded(
        settings=settings,
        to_email=to_email,
        subject=_RECOVERY_SUBJECT,
        html=_build_recovery_html(action_link),
    )


async def _cleanup_signup_artifacts(
    auth_admin: SupabaseAuthAdminClient,
    db: PostgrestClient,
    *,
    auth_user_id: str | None,
    salon_id: str,
    inserted_salon: bool,
) -> None:
    if auth_user_id:
        try:
            await auth_admin.delete_user(auth_user_id)
        except SupabaseAuthError:
            logger.exception("Rollback signup: no se pudo borrar auth user %s", auth_user_id)
        try:
            await db.delete("users", {"auth_user_id": f"eq.{auth_user_id}"})
        except PostgrestError:
            logger.exception("Rollback signup: no se pudo borrar public.users de auth_user_id=%s", auth_user_id)
    if inserted_salon:
        # LA CUENTA TAMBIÉN, que la creó este alta (el trigger de la 0040 al
        # insertar el salón). Antes se quedaba: una cuenta vacía, sin centro ni
        # usuario. Y desde la 0070 importa: una atribución cuenta en el panel si
        # su cuenta existe (`altas_que_cuentan`), así que un alta deshecha que
        # dejara la cuenta sumaría un alta que nunca existió.
        cuentas: list[dict] = []
        try:
            cuentas = await db.select("salons", {"select": "account_id", "id": f"eq.{salon_id}"})
        except PostgrestError:
            logger.exception("Rollback signup: no se pudo leer la cuenta del salon %s", salon_id)
        try:
            await db.delete("salons", {"id": f"eq.{salon_id}"})
        except PostgrestError:
            logger.exception("Rollback signup: no se pudo borrar salon %s", salon_id)
        for fila in cuentas:
            if not fila.get("account_id"):
                continue
            try:
                await db.delete("accounts", {"id": f"eq.{fila['account_id']}"})
            except PostgrestError:
                logger.exception("Rollback signup: no se pudo borrar la cuenta %s", fila["account_id"])


class SoporteEnCursoOut(BaseModel):
    """Datos del banner. Salen del backend verificado, nunca de una cookie."""

    admin_email: str
    expires_at: str


class PinDeMuestraOut(BaseModel):
    nombre: str
    pin: str


class DemoEnCursoOut(BaseModel):
    """Un salón de muestra (0080): cuándo se borra. Sale de la base. Y lo que la
    pantalla enseña para poder probar lo que pide un secreto (0081)."""

    caduca_at: str
    contrasena: str | None = None
    pines: list[PinDeMuestraOut] = []


class MeOut(BaseModel):
    sub: str
    salon_id: str
    rol: str
    email: str | None = None
    nombre_comercial: str
    plan: str
    pending_plan: str | None = None
    subscription_status: str = "none"
    # En que moneda se le COBRA a este salon. La necesitan los muros de las
    # funciones premium para no ofrecer «mejora a Elite por $62» a quien paga en
    # euros. Viaja aqui y no en /billing/subscription porque ese endpoint es
    # solo de admin y los muros los ve tambien el personal.
    #
    # `None` = no hay suscripcion viva que respetar; entonces manda la cookie
    # del visitante, que es la moneda en la que va a contratar.
    moneda: str | None = None
    trial_ends_at: str | None = None
    # EL PLAZO DE LOS DATOS (0064): con la suscripción terminada de verdad, el
    # día en que se borran («AAAA-MM-DD», en el calendario de la cuenta) y los
    # días que quedan. None si no ha terminado o ha vuelto.
    datos_se_borran_el: str | None = None
    dias_para_el_borrado: int | None = None
    trial_default_days: int
    # EL CORTE (0086), decidido por el backend y no por la pantalla: `None`
    # trabaja; «sin_activar» → el muro de activación; «impago» → la cuenta
    # cortada con su agenda y sus salidas. La pantalla no lleva su propia lista
    # de estados que bloquean: si la llevara, sería un corte que solo vive allí.
    corte: str | None = None
    # Cuándo vuelve a intentar cobrar Stripe en el periodo de gracia (0086).
    # `None` con `past_due` = ya no quedan intentos.
    proximo_intento_cobro: str | None = None
    logo_url: str | None = None
    # EN QUÉ CENTRO SE ABRE. Viene ya resuelto por el backend para que la
    # primera pantalla se pinte en el centro correcto: un parpadeo en el
    # centro equivocado es justo el que acaba en una cita donde no toca.
    centro_activo: str | None = None
    # El centro que la pantalla pidió ya no vale y se ha vuelto al propio.
    centro_rechazado: bool = False
    # Se está mirando la agenda de un centro EN PAUSA (0088): solo lectura.
    viendo_centro_en_pausa: bool = False
    nombre_usuario: str
    accent_color: str = "#C9A84C"
    soporte: SoporteEnCursoOut | None = None
    # La zona del centro (0060). Las pantallas pintan y crean horas en ESTA
    # zona, no en la del dispositivo: un portátil fuera de casa guardaba las
    # citas a otra hora.
    zona_horaria: str = "Europe/Madrid"
    # SALÓN DE MUESTRA (0080): la pantalla avisa de cuánto le queda y de que no
    # se escriban datos de verdad. None en un salón de verdad.
    demo: DemoEnCursoOut | None = None


async def _reciclar_identidad_huerfana(
    auth_admin: SupabaseAuthAdminClient, identidad: dict, email: str
) -> None:
    """Borra una identidad de GoTrue que no tiene ficha detrás, para que quien
    la dejó a medias pueda volver a registrarse.

    SOLO SI ESTÁ SIN CONFIRMAR, y esa condición es toda la seguridad de esto.

    Reciclar no puede ser una vía para apoderarse de la cuenta de otro, así que
    hay que preguntarse quién acaba mandando en el buzón. Medido contra GoTrue,
    no supuesto:

      · `generate_link` crea la identidad con `email_confirmed_at = null`.
      · Con ella, `POST /token?grant_type=password` responde
        400 `email_not_confirmed`. No se entra.
      · Producción tiene `mailer_autoconfirm = false`, o sea que esa barrera
        está puesta ahí y no solo en local.

    De ahí sale la regla. Una identidad SIN CONFIRMAR no la ha reclamado nadie:
    es una fila que creamos nosotros y que no prueba que quien la pidió tenga el
    buzón. Borrarla no le quita nada a nadie, y la que la sustituye también nace
    sin confirmar — así que quien termine con la cuenta es quien pueda abrir el
    correo, que es el dueño del buzón. Un desconocido que teclee el email ajeno
    se queda exactamente donde estaba: sin poder entrar.

    UNA IDENTIDAD CONFIRMADA NO SE TOCA. Ahí alguien SÍ demostró tener el buzón,
    y borrarla sería dejar sin contraseña a una persona real cada vez que un
    desconocido teclea su correo — cambiar un cliente atrapado por una
    denegación de servicio. Ese caso sale por 409 y lo resuelve soporte.
    """
    # SE MIRAN LAS DOS CLAVES. Medido contra el listado de GoTrue: cuando la
    # identidad está sin confirmar la clave `email_confirmed_at` NO VIENE —no es
    # que venga a null—, y cuando sí lo está viene con su fecha junto a
    # `confirmed_at`. `.get()` distingue bien los dos casos, y mirar también
    # `confirmed_at` cubre cualquier otra forma de confirmación que GoTrue dé
    # por buena: ante la duda, no se borra.
    if identidad.get("email_confirmed_at") or identidad.get("confirmed_at"):
        logger.warning(
            "Signup: identidad CONFIRMADA sin ficha para %s; no se recicla.",
            _ofuscar_email(email),
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Ese email ya está en uso. Inicia sesión, recupera tu contraseña "
                "o escríbenos si crees que es un error."
            ),
        )

    identidad_id = identidad.get("id")
    if not identidad_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ese email ya está en uso. Inicia sesión o recupera tu contraseña.",
        )

    logger.info(
        "Signup: se recicla una identidad sin confirmar y sin ficha (%s). "
        "Un alta anterior se quedó a medias.",
        _ofuscar_email(email),
    )
    try:
        await auth_admin.delete_user(identidad_id)
    except SupabaseAuthError as e:
        logger.exception("Signup: no se pudo reciclar la identidad huérfana")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo completar el registro. Inténtalo de nuevo.",
        ) from e


async def _mensaje_de_correo_cogido(db: PostgrestClient, email: str) -> str:
    try:
        filas = await db.rpc("alta_pendiente_del_correo", {"p_email": email})
    except PostgrestError:
        logger.exception("Signup: no se pudo mirar si el alta está sin confirmar.")
        filas = []
    if filas and filas[0].get("pendiente") and filas[0].get("libre_el"):
        libre = zonas.fecha_local(filas[0]["libre_el"], zonas.zona("Europe/Madrid"))
        return (
            "Ya empezaste un alta con este correo y falta confirmarla. Busca el correo de "
            "confirmación que te enviamos (mira también en spam) y pulsa el botón. Si no lo "
            f"encuentras, a partir del {plazos.fecha_larga(libre)} podrás registrarte de nuevo "
            "con este correo."
        )
    return "Ese email ya está en uso. Inicia sesión o recupera tu contraseña."


@router.post("/signup", dependencies=[publico.CUALQUIERA], response_model=SignupOut, status_code=status.HTTP_202_ACCEPTED)
async def signup(
    payload: SignupIn,
    request: Request,
    settings: Settings = Depends(get_settings),
    db: PostgrestClient = Depends(get_service_postgrest_client),
) -> SignupOut:
    ip = _client_ip(request)
    email = payload.email.lower()

    blocked, retry_after = signup_rate_limiter.is_blocked(ip, email)
    if blocked:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Demasiados intentos. Inténtalo más tarde.",
            headers={"Retry-After": str(retry_after)},
        )
    signup_rate_limiter.add_attempt(ip, email)

    auth_admin = _auth_admin(settings)

    # QUIÉN MANDA SOBRE «ESTE EMAIL ESTÁ COGIDO» ES `public.users`, NO GOTRUE.
    #
    # Antes mandaba GoTrue, y por eso una identidad a medias atrapaba a una
    # persona para siempre: si el proceso moría entre crear la identidad y
    # escribir su ficha, `find_by_email` la encontraba, el alta devolvía «ese
    # email ya está en uso», y esa persona tampoco podía entrar porque sin ficha
    # `/auth/me` da 404. Un cliente perdido sin forma de recuperarse solo.
    #
    # La ficha es la que dice si hay una cuenta de verdad. La identidad de
    # GoTrue es una pieza NUESTRA, y una que nadie ha reclamado se puede
    # reciclar.
    try:
        existing_app_user = await db.select(
            "users", {"select": "id", "email": f"eq.{email}", "limit": "1"}
        )
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo validar el email.",
        ) from e
    if existing_app_user:
        # UN ALTA SUYA SIN CONFIRMAR (0087): no es «ese email está en uso», es
        # «ya empezaste y te falta un paso». Se le dice qué pasa y QUÉ DÍA queda
        # libre el correo, que es cuando caduca (`plazo_alta_sin_confirmar`).
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=await _mensaje_de_correo_cogido(db, email),
        )

    existing_auth_user = await auth_admin.find_by_email(email)
    if existing_auth_user:
        await _reciclar_identidad_huerfana(auth_admin, existing_auth_user, email)

    # EL TELÉFONO, MARCABLE. En el alta no hay de dónde deducir el país todavía
    # —no hay centro—, así que se lee del propio número: con prefijo se agrupa
    # bien y sin él se cae en la región por defecto, que es lo mismo que hace el
    # resto del proyecto (`core/telefonos.region_del_salon`).
    try:
        telefono_salon = revisar_telefono(
            payload.telefono, region_del_salon(payload.telefono)
        )
    except TelefonoNoValido as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"codigo": "telefono_no_valido", "mensaje": str(e)},
        ) from e

    salon_id = str(uuid4())
    auth_user_id: str | None = None
    action_link: str | None = None
    inserted_salon = False

    try:
        await db.insert(
            "salons",
            {
                "id": salon_id,
                "nombre_comercial": payload.salon_nombre,
                "direccion": payload.direccion.strip(),
                "telefono": telefono_salon,
                "email": email,
                "pending_plan": payload.plan,
            },
        )
        inserted_salon = True

        # LA IDENTIFICACIÓN FISCAL VA A LA CUENTA, no al centro: la entidad que
        # factura es la cuenta, y un dueño con dos locales tiene un solo número
        # fiscal. El `account_id` lo acaba de poner el trigger de la 0040, así
        # que se relee en vez de suponerlo.
        account_id = await id_de_cuenta(db, salon_id)
        if not account_id:
            raise PostgrestError(500, "El salón se creó sin cuenta.")
        # Solo `cif`: el país (`cif_pais`) ya no se pide en pantalla y la
        # cuenta nace con él en nulo, hasta que la facturación lo pida.
        await db.update(
            "accounts",
            {"id": f"eq.{account_id}"},
            {"cif": payload.cif},
        )

        generated = await auth_admin.generate_signup_link(
            email=email,
            password=payload.password,
            redirect_to=_SIGNUP_CONFIRM_REDIRECT_TO,
        )
        logger.info("Signup: generate_link OK (%s)", _resumen_generate_link(generated))

        auth_user_id = _extract_user_id(generated)
        if not auth_user_id:
            logger.error(
                "Signup: generate_link no devolvió user.id (%s)",
                _resumen_generate_link(generated),
            )
            await _cleanup_signup_artifacts(
                auth_admin,
                db,
                auth_user_id=auth_user_id,
                salon_id=salon_id,
                inserted_salon=inserted_salon,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="No se pudo generar el enlace.",
            )

        action_link = _extract_action_link(generated)
        if not action_link:
            logger.error(
                "Signup: generate_link no devolvió action_link (%s)",
                _resumen_generate_link(generated),
            )
            await _cleanup_signup_artifacts(
                auth_admin,
                db,
                auth_user_id=auth_user_id,
                salon_id=salon_id,
                inserted_salon=inserted_salon,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="No se pudo generar el enlace.",
            )
    except SupabaseAuthError as e:
        await _cleanup_signup_artifacts(
            auth_admin,
            db,
            auth_user_id=auth_user_id,
            salon_id=salon_id,
            inserted_salon=inserted_salon,
        )
        if e.status_code in {400, 409, 422}:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Ese email ya está en uso. Inicia sesión o recupera tu contraseña.",
            ) from e
        logger.exception("Signup: error generando enlace de confirmación")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo crear el usuario en autenticación.",
        ) from e
    except PostgrestError as e:
        logger.exception("Signup: error guardando datos previos al alta auth")
        await _cleanup_signup_artifacts(
            auth_admin,
            db,
            auth_user_id=auth_user_id,
            salon_id=salon_id,
            inserted_salon=inserted_salon,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo completar el registro.",
        ) from e

    try:
        await auth_admin.update_user_by_id(
            auth_user_id,
            {
                "app_metadata": {
                    "salon_id": salon_id,
                    "role": "admin",
                    "pending_plan": payload.plan,
                }
            },
        )

        await db.insert(
            "users",
            {
                "salon_id": salon_id,
                "nombre": payload.admin_nombre,
                "email": email,
                "rol": "admin",
                "auth_user_id": auth_user_id,
            },
        )

        # QUIÉN TRAJO ESTA CUENTA (0070), en el mismo bloque que la crea: si no
        # se puede apuntar, el alta se deshace entera, igual que si fallara la
        # ficha. Va lo más tarde posible —justo antes del correo, lo único que
        # puede fallar después— porque la atribución no se puede borrar: un
        # alta deshecha después de apuntarla dejaría una atribución sin cuenta.
        # Un código que no vale NO para nada: la base lo apunta y sigue.
        if payload.codigo_referido and payload.codigo_referido.strip():
            resultado = await atribuir_alta(db, account_id, payload.codigo_referido)
            logger.info("Signup: código de referido → %s", resultado)

        await _send_signup_confirmation_email(settings=settings, to_email=email, action_link=action_link)
    except (SupabaseAuthError, PostgrestError) as e:
        logger.exception("Signup: no se pudo guardar el perfil para %s", email)
        await _cleanup_signup_artifacts(
            auth_admin,
            db,
            auth_user_id=auth_user_id,
            salon_id=salon_id,
            inserted_salon=inserted_salon,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo guardar el perfil.",
        ) from e
    except EmailConfigError as e:
        logger.exception("Signup: configuración de correo incompleta para %s", email)
        await _cleanup_signup_artifacts(
            auth_admin,
            db,
            auth_user_id=auth_user_id,
            salon_id=salon_id,
            inserted_salon=inserted_salon,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        ) from e
    except RuntimeError as e:
        logger.exception("Signup: no se pudo enviar el correo para %s", email)
        await _cleanup_signup_artifacts(
            auth_admin,
            db,
            auth_user_id=auth_user_id,
            salon_id=salon_id,
            inserted_salon=inserted_salon,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo enviar el correo.",
        ) from e

    return SignupOut(
        status="pending_email_confirmation",
        message="Te hemos enviado un email de confirmación. Revísalo para activar tu cuenta.",
    )


@router.post("/recuperar", dependencies=[publico.CUALQUIERA], response_model=RecuperarOut)
async def recuperar_contrasena(
    payload: RecuperarIn,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> RecuperarOut:
    """Envía el correo de restablecimiento de contraseña. Público.

    REGLA DURA (anti-enumeración): la respuesta es SIEMPRE la misma, exista o no
    la cuenta. Cualquier `raise` que dependa de si el email está registrado
    convierte este endpoint en un listador de clientes, así que los fallos del
    camino "el email sí existe" (Supabase caído, Resend caído) se registran en
    logs y se responden como éxito. Solo se propagan errores que NO dependen del
    email: rate limit (429) y configuración de correo ausente (500).
    """
    ip = _client_ip(request)
    email = payload.email.lower()

    blocked, retry_after = recovery_rate_limiter.is_blocked(ip, email)
    if blocked:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Demasiadas solicitudes. Inténtalo más tarde.",
            headers={"Retry-After": str(retry_after)},
        )
    recovery_rate_limiter.add_attempt(ip, email)

    if not settings.resend_api_key or not settings.email_from:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Faltan variables de entorno de email: RESEND_API_KEY y/o EMAIL_FROM.",
        )

    generic = RecuperarOut(status="ok", message=_RECOVERY_GENERIC_MESSAGE)
    auth_admin = _auth_admin(settings)

    try:
        generated = await auth_admin.generate_recovery_link(
            email=email,
            redirect_to=_RECOVERY_REDIRECT_TO,
        )
    except SupabaseAuthError as e:
        # 4xx = ese email no tiene cuenta: no hay correo que enviar y salimos
        # por la misma puerta que el caso bueno. 5xx sí es un fallo nuestro,
        # pero tampoco puede distinguirse desde fuera.
        if e.status_code >= 500:
            logger.exception("Recuperación: Supabase falló generando el enlace para %s", email)
        else:
            logger.info("Recuperación solicitada para un email sin cuenta (%s)", e.status_code)
        return generic

    action_link = _extract_action_link(generated)
    if not action_link:
        logger.error(
            "Recuperación: generate_link no devolvió action_link (%s)",
            _resumen_generate_link(generated),
        )
        return generic

    try:
        await _send_recovery_email(settings=settings, to_email=email, action_link=action_link)
    except Exception:  # noqa: BLE001 - ver docstring: nada puede filtrar si el email existe
        logger.exception("Recuperación: no se pudo enviar el correo para %s", email)

    return generic


async def _plazo_de_los_datos(servicio: PostgrestClient, account_id: str):
    """El plazo de los datos de esta cuenta, leído de LA selección de la base
    (`relojes_del_plazo`, 0068): la misma que los correos, el panel y el
    borrado. El muro y el correo no pueden decir dos fechas distintas. Nunca
    tumba `/auth/me`.
    """
    try:
        filas = await tarea_plazo.relojes(servicio, datetime.now(timezone.utc), account_id)
    except (PostgrestError, ValueError, KeyError):
        logger.exception("No se pudo calcular el plazo de los datos de %s.", account_id)
        return None
    return filas[0].plazo if filas else None


@router.get("/me", dependencies=[publico.ADMINISTRADORA])
@admite_cuenta_cortada("agenda")
async def me(
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
    storage: StorageClient = Depends(get_storage_client),
    settings: Settings = Depends(get_settings),
    servicio: PostgrestClient = Depends(get_service_postgrest_client),
) -> MeOut:
    """Devuelve claims del JWT + contexto de salón y nombre de usuario.

    salon_id/rol siempre salen del token verificado; nombre comercial, plan,
    logo y nombre visible se leen en BD para poblar la shell del frontend.
    El logo sale como signed URL fresca (TTL ~1 h); en BD vive la ruta.
    """
    try:
        salon_rows = await db.select(
            "salons",
            {
                "select": (
                    "nombre_comercial,account_id,zona_horaria,"
                    "accounts(plan,pending_plan,logo_url,accent_color)"
                ),
                "id": f"eq.{user.salon_id}",
                "limit": "1",
            },
        )
    except PostgrestError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo leer el salón actual",
        )

    if not salon_rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Salón no encontrado")

    # Se aplana AQUÍ y no al final porque la suscripción se busca por la cuenta,
    # y eso pasa unas líneas más abajo.
    salon = con_la_cuenta_dentro(salon_rows[0])

    user_rows: list[dict] = []
    sub_rows: list[dict] = []
    try:
        # LA IDENTIDAD NO ES POR CENTRO. Quién eres, tu rol y tu cuenta no
        # cambian al cambiar de centro; lo único que cambia son los datos que
        # estás mirando.
        #
        # Aquí había un filtro por `salon_id`, y desde que `user.salon_id`
        # significa «el centro ACTIVO» eso dejó a la gente sin existir al
        # cambiar de centro: la ficha vive en el centro 1, se preguntaba por el
        # 2, y `/auth/me` contestaba 404. Todo lo demás seguía respondiendo 200,
        # así que la sesión quedaba viva pero sin saber quién era —ni que era
        # administradora—, y sin forma de volver desde la interfaz.
        #
        # Se resuelve por `auth_user_id`, que es la identidad y solo la
        # identidad. Una identidad tiene UNA ficha (solo el alta crea filas en
        # `users`, y comprueba que el email no esté cogido en toda la tabla).
        user_rows = await db.select(
            "users",
            {
                "select": "nombre",
                "auth_user_id": f"eq.{user.sub}",
                "limit": "1",
            },
        )
        if not user_rows and user.email:
            # Respaldo para fichas antiguas sin `auth_user_id`. Tampoco lleva
            # centro: el email es único en toda la tabla —lo impone el alta—,
            # así que acotar por centro solo podía quitar la fila correcta.
            user_rows = await db.select(
                "users",
                {
                    "select": "nombre",
                    "email": f"eq.{user.email}",
                    "limit": "1",
                },
            )
    except PostgrestError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo leer el usuario actual",
        )

    try:
        sub_rows = await db.select(
            "subscriptions",
            {
                "select": "estado,moneda,trial_ends_at,terminada_at,proximo_intento_at",
                "account_id": f"eq.{salon['account_id']}",
                "limit": "1",
            },
        )
    except PostgrestError:
        # Deploy parcial: si trial_ends_at aun no existe, seguimos con estado.
        try:
            sub_rows = await db.select(
                "subscriptions",
                {
                    "select": "estado",
                    "account_id": f"eq.{salon['account_id']}",
                    "limit": "1",
                },
            )
        except PostgrestError:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="No se pudo leer el estado de suscripción",
            )

    if not user_rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado")

    app_user = user_rows[0]
    estado = ((sub_rows[0].get("estado") if sub_rows else None) or "").lower()
    # EL MAPA DE ESTADOS VIVE EN UN SOLO SITIO (`billing/estado_suscripcion`).
    # Aquí antes todo lo que no era trialing/active/canceled caía en
    # `past_due`: con la barra de aviso, una cuenta `unpaid` —que Stripe ya dio
    # por perdida— habría entrado al panel como si solo tuviera la tarjeta
    # caducada, y una `incomplete` habría saltado el muro de activación.
    subscription_status = estado_de_sesion(estado)

    # La moneda EFECTIVA, con la misma regla que la pestana Suscripcion: manda la
    # de la suscripcion (NULL = euros, es anterior a la 0033) y solo si esta
    # viva. `SUSCRIPCION_EXISTE` se importa en vez de copiarse porque una segunda
    # copia de esa lista se separaria de la primera con el tiempo.
    #
    # En el camino de emergencia de arriba —backend desplegado antes que la
    # migracion— la fila no trae `moneda` y `moneda_efectiva(None)` responde
    # euros, que es lo unico que se vendia entonces.
    moneda = (
        moneda_efectiva(sub_rows[0].get("moneda"))
        if sub_rows and estado in SUSCRIPCION_EXISTE
        else None
    )

    # Con service_role: la selección y los días de prueba son de la plataforma,
    # y la sesión no los lee.
    plazo = await _plazo_de_los_datos(servicio, salon["account_id"])
    demo_en_curso = None
    if user.es_demo:
        try:
            vida = await servicio.select(
                "demos", {"select": "caduca_at,contrasena_de_muestra", "account_id": f"eq.{salon['account_id']}",
                          "limit": "1"})
        except PostgrestError as e:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudo leer la cuenta.") from e
        if vida:
            demo_en_curso = DemoEnCursoOut(
                caduca_at=str(vida[0]["caduca_at"]), contrasena=vida[0].get("contrasena_de_muestra"),
                pines=[PinDeMuestraOut(nombre=n, pin=p) for n, p in demo.PINES_DE_MUESTRA.items()])
    try:
        prueba = await dias_de_prueba(servicio)
    except (PostgrestError, SinDiasDePrueba) as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudo leer la cuenta.") from e

    return MeOut(
        sub=user.sub,
        salon_id=user.salon_id,
        rol=user.rol,
        email=user.email,
        nombre_comercial=salon["nombre_comercial"],
        plan=salon["plan"],
        pending_plan=salon.get("pending_plan"),
        subscription_status=subscription_status,
        moneda=moneda,
        trial_ends_at=sub_rows[0].get("trial_ends_at") if sub_rows else None,
        datos_se_borran_el=plazo.se_borra_el.isoformat() if plazo else None,
        dias_para_el_borrado=plazo.dias_restantes if plazo else None,
        trial_default_days=prueba,
        corte=user.motivo_corte,
        proximo_intento_cobro=(
            str(sub_rows[0]["proximo_intento_at"]) if sub_rows and sub_rows[0].get("proximo_intento_at") else None
        ),
        logo_url=await sign_logo_url(storage, salon.get("logo_url")),
        centro_activo=user.salon_id,
        centro_rechazado=user.centro_rechazado,
        viendo_centro_en_pausa=user.viendo_centro_en_pausa,
        nombre_usuario=app_user["nombre"],
        accent_color=salon.get("accent_color") or "#C9A84C",
        zona_horaria=zonas.zona(salon.get("zona_horaria")).key,
        # El banner de modo soporte se pinta a partir de esto, no de la cookie
        # marcador del frontend: esa no tiene autoridad y cualquiera puede
        # escribírsela. `user.impersonation` viene de la sesión ya revalidada
        # contra la BD en cada petición.
        soporte=(
            SoporteEnCursoOut(
                admin_email=user.impersonation.admin_email,
                expires_at=user.impersonation.expires_at,
            )
            if user.impersonation
            else None
        ),
        demo=demo_en_curso,
    )
