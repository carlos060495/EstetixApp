from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from starlette.requests import Request
from starlette.responses import Response
from urllib.parse import urlparse

from starlette.responses import JSONResponse

from app.core.security import CODIGO_CORTADA, MENSAJE_CORTADA
from app.db.postgrest_client import PostgrestError
from app.core import demo as salon_de_muestra  # instala el corte de Stripe (0080)
from app.core import stripe_con_plazo  # noqa: F401 — plazo y reintentos de Stripe, para todas
from app.core.errores_de_formulario import mensaje_de_validacion
from app.core import mostrador as dispositivo_mostrador
from app.core.cookies_de_sesion import COOKIE_PANEL, poner_cookie_de_sesion
from app.core.publico import CUALQUIERA as PARA_CUALQUIERA
from app.core.publico import exigir_publico_declarado
from app.core.admin_audit import client_ip
from app.db.conexiones import cerrar_clientes, cliente_compartido
from app.core.centro_activo import CABECERA_CENTRO
from app.core.admin_security import decode_admin_token
from app.core.config import get_settings
from app.core.impersonation_policy import (
    accion_auditable,
    clave_de_ruta,
    completar_lo_apuntado,
    guard_impersonacion,
    registrar_actividad_impersonacion,
)
from app.routers import (
    admin_auth,
    admin_plataforma,
    admin_tenants,
    agenda,
    appointments,
    auth,
    baja,
    billing,
    bot_asistente,
    bot_conversaciones,
    centros,
    clients,
    datos,
    demo,
    employees,
    excepciones,
    expenses,
    fichaje,
    horario,
    horario_empleada,
    impersonation,
    incomes,
    mostrador,
    pin_gestion,
    products,
    providers,
    publico,
    recordatorios,
    reservas_online,
    puestos,
    portal_referido,
    seguimiento,
    referidos,
    reservas,
    rgpd,
    salon,
    service_categories,
    services,
    soporte,
    stats,
    vendedor,
    webhook_stripe_connect,
    webhooks_whatsapp,
)

settings = get_settings()


def _all_allowed_origins() -> list[str]:
    merged = sorted(set(settings.allowed_origins_list + settings.admin_allowed_origins_list))
    if not merged:
        raise RuntimeError("No hay orígenes permitidos configurados para CORS.")
    return merged


def _origin_from_referer(referer: str | None) -> str | None:
    if not referer:
        return None
    try:
        parsed = urlparse(referer)
    except ValueError:
        return None
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def _is_allowed_admin_origin(request: Request) -> bool:
    allowed = set(settings.admin_allowed_origins_list)
    origin = request.headers.get("origin")
    if origin:
        origin = origin.rstrip("/")
    if origin and origin in allowed:
        return True
    referer_origin = _origin_from_referer(request.headers.get("referer"))
    if referer_origin:
        referer_origin = referer_origin.rstrip("/")
    return bool(referer_origin and referer_origin in allowed)

@asynccontextmanager
async def ciclo_de_vida(_: FastAPI):
    """Lo que se abre al arrancar y se cierra al parar.

    DE MOMENTO SOLO LAS CONEXIONES. El cliente HTTP compartido se crea solo la
    primera vez que alguien lo pide —así funciona igual en un `TestClient` que
    no levanta esto— pero cerrarlo sí tiene sitio: al parar el backend se
    sueltan las conexiones vivas hacia Supabase en vez de dejarlas morir de
    viejas al otro lado.

    Y SE ABRE AQUÍ, antes de atender a nadie: crearlo carga los certificados
    (~0,4 s de CPU medidos el 14-10) y, hecho en la primera petición, paraba el
    proceso entero ese rato. Funciona igual sin esto (se crea al vuelo).
    """
    cliente_compartido()
    yield
    await cerrar_clientes()


# La guardia va como dependencia GLOBAL, no como middleware: necesita la ruta
# ya resuelta (`scope["route"]`) para decidir con la plantilla —`PATCH
# /clients/{client_id}`— en vez de con el id concreto de la URL. Como
# dependencia global corre ANTES que las del endpoint, así que una acción
# bloqueada se corta sin tocar la BD ni Stripe. Fuera de modo soporte se sale
# en la primera línea.
app = FastAPI(
    title="EstetixApp API",
    # LA PRIMERA: una ruta que no declara para quién es, deniega antes de que
    # corra nada más (ver `core/publico.py`). Después, la guarda del modo soporte.
    dependencies=[Depends(exigir_publico_declarado), Depends(guard_impersonacion)],
    lifespan=ciclo_de_vida,
)


@app.middleware("http")
async def security_headers(request: Request, call_next) -> Response:
    """Cabeceras de seguridad obligatorias (DIRECTRICES_PROYECTO.md §4.6)."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    if settings.env == "production":
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    return response


@app.middleware("http")
async def renovar_el_mostrador(request: Request, call_next) -> Response:
    """LA COOKIE DEL MOSTRADOR SE RENUEVA CON EL USO (29-10-2026). Lo decide
    `security._usar_el_dispositivo` (cada cinco minutos de uso como mucho) y se
    hace aquí, que es quien tiene la respuesta. Si la respuesta ya toca esa
    cookie —salir del modo la borra—, manda la respuesta."""
    response = await call_next(request)
    valor = getattr(request.state, "renovar_mostrador", None)
    ya_la_toca = any(
        k == b"set-cookie" and v.startswith(f"{dispositivo_mostrador.COOKIE}=".encode()) for k, v in response.raw_headers
    )
    if valor and not ya_la_toca:
        poner_cookie_de_sesion(response, dispositivo_mostrador.COOKIE, valor, dispositivo_mostrador.SEGUNDOS_COOKIE)
    return response


@app.middleware("http")
async def admin_auth_guard(request: Request, call_next) -> Response:
    path = request.url.path
    if request.method == "OPTIONS":
        return await call_next(request)

    if path.startswith("/admin") and request.method in {"POST", "PATCH", "PUT", "DELETE"}:
        # SameSite=None requiere defensa CSRF en endpoints mutables.
        if not _is_allowed_admin_origin(request):
            return Response(
                content='{"detail":"Origen no permitido"}',
                status_code=403,
                media_type="application/json",
            )

    if not path.startswith("/admin") or path == "/admin/login":
        return await call_next(request)

    # 401 = no hay sesión válida. El 403 de arriba (Origin) es lo contrario:
    # sesión posiblemente buena, petición rechazada. La autorización por rol
    # también responde 403, desde app/core/admin_deps.py.
    token = request.cookies.get(COOKIE_PANEL)
    if not token:
        return Response(
            content='{"detail":"No autorizado"}',
            status_code=401,
            media_type="application/json",
        )

    try:
        request.state.admin_claims = decode_admin_token(settings, token)
    except HTTPException:
        return Response(
            content='{"detail":"No autorizado"}',
            status_code=401,
            media_type="application/json",
        )

    return await call_next(request)


@app.middleware("http")
async def impersonation_audit(request: Request, call_next) -> Response:
    """Cómo acabó lo que soporte hizo, y los PORTAZOS.

    Lo que se atiende se apunta ANTES, y sin su línea no se atiende
    (`apuntar_antes_de_atender`, 20-10-2026); aquí solo se completa con su
    resultado (0091). Los intentos que la guardia cortó, que no llegan a
    resolver la identidad, se apuntan aquí.

    Va DESPUÉS del endpoint —al revés que la guardia, que es dependencia
    global— porque necesita dos cosas que antes no existen: el código de estado
    y el resultado del enrutado. Un middleware corre FUERA del enrutador, pero
    el `scope` que el enrutador rellena es el MISMO diccionario que sostiene a
    este `request` (BaseHTTPMiddleware pasa el scope por referencia a la app de
    dentro), así que al volver de `call_next` ya están ahí tanto `scope["route"]`
    —la plantilla, `/clients/{client_id}`, nunca el id real— como el contexto
    que `get_current_user` dejó en `request.state`.

    Si el enrutado no dejó ruta (un 404, por ejemplo) no se registra nada:
    `admin_logs` es append-only y no se puede corregir, así que se prefiere
    perder la línea a escribir en ella la URL con el id de un cliente.

    Va en `try/finally` porque no toda petición termina en respuesta: FastAPI
    convierte `HTTPException` y `RequestValidationError`, pero una
    `ResponseValidationError` —o cualquier excepción del endpoint— sube por
    aquí. Ese es justo el caso en el que más falta hace el rastro: la mutación
    ya se aplicó en el salón y el endpoint se cayó después. Sin `finally`, la
    excepción se llevaría por delante el único registro de que ocurrió.
    """
    response: Response | None = None
    try:
        response = await call_next(request)
        return response
    finally:
        contexto = getattr(request.state, "impersonation", None)
        motivo = getattr(request.state, "motivo_bloqueo", None)
        # Lo apuntado ANTES se completa con cómo acabó: sin respuesta, 500.
        # Se traga sus errores, así que no tapa la excepción que suba.
        await completar_lo_apuntado(
            request, settings, response.status_code if response is not None else 500
        )
        # Fuera de modo soporte no se escribe absolutamente nada. El contexto lo
        # ponen `get_current_user` (petición atendida) y la guardia justo antes
        # de dar un portazo (intento bloqueado), y ambos han revalidado la
        # sesión contra la BD: auditar sobre la cookie a secas dejaría que una
        # cookie caducada o falsificada metiese filas en un log imborrable.
        if contexto is not None and motivo is not None:
            clave = clave_de_ruta(request)
            accion = accion_auditable(request, clave)
            if accion is not None:
                # `registrar_actividad_impersonacion` se traga sus propios
                # errores, así que este `finally` no puede enmascarar la
                # excepción que venga subiendo.
                await registrar_actividad_impersonacion(
                    settings,
                    contexto,
                    accion,
                    clave,
                    # Sin respuesta la petición reventó: se anota como 500, que
                    # es lo que el cliente va a ver.
                    response.status_code if response is not None else 500,
                    client_ip(request),
                    motivo=motivo,
                )


# CORS must be the outermost middleware so it can append ACAO headers to
# every response, including 4xx/5xx and short-circuited responses returned
# by inner middlewares (e.g. admin auth guard).
app.add_middleware(
    CORSMiddleware,
    allow_origins=_all_allowed_origins(),
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    # LAS CABECERAS QUE EL NAVEGADOR PUEDE MANDAR. Una que falte aquí no da un
    # error en esa petición: tumba la aplicación entera, porque el navegador
    # hace un preflight `OPTIONS` antes de cada llamada y `CORSMiddleware`
    # responde «400 Disallowed CORS headers» a cualquier preflight que pida una
    # cabecera no declarada. Pasó al desplegar el selector de centro: 400 en
    # `/auth/me`, en `/services`, en todo — incluidas rutas que no se habían
    # tocado.
    #
    # `CABECERA_CENTRO` viene del módulo que la define, no escrita a mano: el
    # nombre vive en un sitio. Y `test_cors_preflight.py` comprueba que TODA
    # cabecera que el frontend manda esté en esta lista, para que renombrarla en
    # cualquiera de los dos lados se ponga rojo en vez de tirar producción.
    #
    # `Idempotency-Key` la manda la baja de la suscripción (`routers/baja.py`):
    # un doble toque en el móvil no puede cancelar dos veces.
    # `X-Uso` la manda el mostrador (0090) cuando una petición nace de un toque:
    # solo esa renueva la gestión (`core/mostrador.py`).
    allow_headers=["Authorization", "Content-Type", CABECERA_CENTRO, "Idempotency-Key", "X-Uso"],
)


app.include_router(auth.router)
app.include_router(admin_auth.router)
app.include_router(admin_tenants.router)
app.include_router(admin_plataforma.router)
app.include_router(impersonation.router)
app.include_router(clients.router)
app.include_router(services.router)
app.include_router(service_categories.router)
app.include_router(employees.router)
app.include_router(puestos.router)
app.include_router(products.router)
app.include_router(providers.router)
app.include_router(salon.router)
app.include_router(centros.router)
app.include_router(horario.router)
app.include_router(excepciones.router)
app.include_router(horario_empleada.router)
app.include_router(expenses.router)
app.include_router(incomes.router)
app.include_router(stats.router)
app.include_router(appointments.router)
app.include_router(agenda.router)
app.include_router(reservas.router)
app.include_router(fichaje.router)
# El PIN de gestión: una CORTINA en la aplicación, no un permiso (ver el router).
app.include_router(pin_gestion.router)
app.include_router(mostrador.router)
app.include_router(rgpd.router)
app.include_router(billing.router)
# El salón asigna el código de su vendedor después del alta (0078).
app.include_router(vendedor.router)
app.include_router(baja.router)
app.include_router(soporte.router)
app.include_router(webhooks_whatsapp.router)
# La bandeja de conversaciones del salon: mismo dominio que el webhook, pero
# con usuario detras y token del inquilino.
app.include_router(bot_conversaciones.router)
app.include_router(bot_asistente.router)
# La pagina publica de reservas: sin login. Entra por slug y token del salon.
app.include_router(publico.router)
app.include_router(reservas_online.router)
# El interruptor de los recordatorios y la baja publica del cliente.
app.include_router(recordatorios.router)
# Dos routers: el publico (/referidos/registro) y el del panel
# (/admin/referidos/*), que va detras del guard de admin_users.
app.include_router(referidos.router)
app.include_router(referidos.router_admin)
# El portal del referido (0071): su puerta (sin sesión) y su portal (REFERIDO).
app.include_router(portal_referido.router_puerta)
app.include_router(portal_referido.router_portal)
# El enlace de seguimiento de un trabajador de agencia (0077): público a propósito.
app.include_router(seguimiento.router)
# Lo que Stripe dice de las cuentas de cobro de los referidos (0072). Otro
# punto de conexión que el de la facturación, con su propio secreto.
app.include_router(webhook_stripe_connect.router)
# Llevarse los datos (RGPD, portabilidad): el ZIP de la cuenta.
app.include_router(datos.router)


# El salón de muestra (0080): abrirlo desde la portada, y su parte del panel.
app.include_router(demo.router)
app.include_router(demo.router_admin)


# ALGO INTENTÓ SALIR AL MUNDO (o contratar) DESDE UNA DEMO: un 403 con su
# código, para que la pantalla lo cuente igual venga de donde venga.
@app.exception_handler(salon_de_muestra.SalidaDesdeUnaDemo)
async def _desde_una_demo(_request: Request, e: salon_de_muestra.SalidaDesdeUnaDemo) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": {
        "codigo": salon_de_muestra.CODIGO_DEMO, "mensaje": salon_de_muestra.MENSAJE_DEMO, "canal": e.canal}})


# LA BASE HA CORTADO (0086): `centros_que_trabajan()` lanza 42501
# `cuenta_cortada`. Llegue por donde llegue —una ruta que la sesión dejó pasar y
# que tocó algo que no es la agenda—, sale como el mismo 403 legible que da la
# sesión, y no como un 500.
@app.exception_handler(PostgrestError)
async def _la_base_ha_cortado(request: Request, e: PostgrestError) -> JSONResponse:
    if "cuenta_cortada" in (e.detail or ""):
        return JSONResponse(status_code=403, content={"detail": {"codigo": CODIGO_CORTADA, "mensaje": MENSAJE_CORTADA}})
    raise e


# UN DATO QUE NO VALE se dice para la persona, no para un programador: el 422
# de FastAPI traía «Value error, …» o la frase en inglés de Pydantic, y las
# pantallas lo enseñaban tal cual. Ver `core/errores_de_formulario.py`.
@app.exception_handler(RequestValidationError)
async def _dato_que_no_vale(_request: Request, e: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": mensaje_de_validacion(list(e.errors()))})


@app.get("/health", dependencies=[PARA_CUALQUIERA])
async def health() -> dict:
    return {"status": "ok"}
