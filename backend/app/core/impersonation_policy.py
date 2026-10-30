"""Qué puede hacer una sesión de soporte, y qué queda registrado.

Modelo: lista negra sobre "acceso completo". Las tres listas de abajo clasifican
HOY las 51 rutas mutables de la app, una por una, para que se pueda distinguir
"decidido que sí" de "nadie lo ha mirado todavía". Una lista negra sin red se
pudre —el endpoint nuevo queda permitido por omisión—, así que la red es el test
de cobertura que exigirá que toda ruta mutable esté clasificada: llega en la
Task 13 y consumirá `rutas_mutables()` de este mismo módulo. Hasta que exista,
mantener estas listas al añadir un endpoint es responsabilidad de quien lo añade.

La segunda mitad del fichero es la defensa CSRF. Es obligatoria y no
decorativa: la cookie de soporte es `SameSite=None` (el panel y la app del
salón viven en orígenes distintos), así que el navegador la adjunta también a
las peticiones que dispare CUALQUIER otra web abierta en otra pestaña. Sin
comprobar el origen, un `POST /clients` desde una página cualquiera se
atendería como si lo hubiese hecho el agente de soporte.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import NoReturn
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Request, status
from starlette.routing import BaseRoute

from app.core.admin_audit import append_admin_log, client_ip, completar_admin_log
from app.core.config import Settings, get_settings
from app.core.impersonation import (
    COOKIE_IMPERSONACION,
    MINUTOS_SESION,
    ImpersonationContext,
    cargar_contexto_sesion,
    decodificar_token_impersonacion,
)
from app.core.security import get_service_postgrest_client
from app.db.postgrest_client import PostgrestClient

_log = logging.getLogger(__name__)

MUTABLES = frozenset({"POST", "PATCH", "PUT", "DELETE"})

# Prefijo del panel Super Admin. Sus rutas NO pasan por esta guardia: se
# autentican con `__Host-sa-access-token` (no con la cookie de soporte) y ya tienen su
# propia defensa CSRF en el middleware de app/main.py. En particular
# `POST /admin/impersonation/end` es la ÚNICA salida limpia del modo soporte:
# si la guardia lo bloquease, la sesión solo podría terminar por caducidad.
PREFIJO_PANEL = "/admin"

# Facturación, consentimiento y credenciales de quiosco elegidas: "acceso
# completo" es la decisión tomada, y esto es lo que el dueño NO delega al pedir
# ayuda. Cobrar o cancelar es su dinero; renovarse el propio permiso (o
# retirárselo al dueño) rompería el consentimiento; y fijar un PIN concreto
# deja una credencial conocida que sigue valiendo cuando la sesión termine.
#
# Lo del PIN son TRES piezas y hay que mantenerlas juntas: `PUT .../pin` aquí,
# más el campo `pin_fichaje` de `POST /employees` y `PATCH /employees/{id}` en
# CAMPOS_BLOQUEADOS. Los dos endpoints de empleado aceptan un PIN elegido en el
# cuerpo y el router lo hashea igual que el `PUT` (app/routers/employees.py), o
# sea que bloquear solo el `PUT` sería un control decorativo: el mismo PIN
# entraría por `PATCH /employees/{id}` con `{"pin_fichaje": "1234"}`.
BLOQUEADOS = frozenset(
    {
        # CORREGIR Y ANULAR EN CAJA (0092): el registro dice QUIÉN cambió un
        # importe, y en modo soporte apuntaría a la dueña como autora de algo
        # que hizo soporte. En lo que toca dinero el «quién» tiene que ser
        # cierto: lo hace el salón. (Antes soporte podía editar y BORRAR.)
        "POST /expenses/{expense_id}/corregir",
        "POST /expenses/{expense_id}/anular",
        "POST /incomes/{income_id}/corregir",
        "POST /incomes/{income_id}/anular",
        "POST /billing/checkout",
        "POST /billing/checkout/trial",
        "POST /billing/portal",
        # El plan de un salón de muestra (0081): es de quien prueba la demo, no de
        # soporte. Y a una cuenta de verdad no le sirve de nada (solo demos).
        "POST /demo/plan",
        # Abrir un centro nuevo cambia la forma del negocio del cliente y
        # depende de su plan. Es una decisión suya, de las que no delega al
        # pedir ayuda; soporte puede mirar sus centros (el GET no es mutable)
        # y decirle cómo abrirlo.
        "POST /salon/centros",
        # Y recordar el centro activo tampoco. La cabecera sigue funcionando,
        # así que soporte PUEDE mirar el otro centro durante su sesión; lo que
        # no puede es dejar escrito en la ficha del dueño dónde se le abre la
        # aplicación mañana. Cambiarle eso sin que se entere es justo el fallo
        # que esta fase existe para evitar: trabajar en el centro equivocado
        # sin darse cuenta.
        "PUT /salon/centros/activo",
        # Y cuál de sus centros se queda activo cuando el plan no los cubre
        # todos (0088): decide qué local deja de atender y gasta el cambio de
        # los 30 días. Es de la dueña; soporte le dice dónde se hace.
        "PUT /salon/centros/el-que-sigue",
        # `POST /billing/sincronizar` NO está aquí: está entre los PERMITIDOS.
        # Los tres de arriba mueven dinero —contratan, cobran, abren el portal
        # donde se cancela—. Sincronizar no decide nada: copia a nuestra base
        # lo que Stripe YA dice, así que no puede subir a nadie de plan ni
        # cobrarle. Y es justo lo que soporte necesita poder hacer cuando
        # alguien ha pagado y se ha quedado fuera, que es el caso que lo trajo.
        # Cambiar de plan es cambiar lo que se le cobra al dueño cada mes. Un
        # agente de soporte no toca eso ni con consentimiento: es exactamente
        # lo que el dueño NO delega al pedir ayuda. La vista previa va con él
        # porque no tiene sentido dejar consultar el precio de una acción
        # bloqueada.
        "POST /billing/cambiar-plan",
        "POST /billing/cambiar-plan/preview",
        # Y anular una bajada programada también: si soporte lo anulara, el
        # dueño seguiría pagando el plan caro un mes más sin haberlo decidido.
        "POST /billing/cambiar-plan/anular",
        # EL VENDEDOR DEL SALÓN (0078): asignarlo desde una sesión suplantada es
        # regalarle a alguien una comisión perpetua con el dinero del dueño. Y
        # comprobar el código tampoco: enseña el nombre de un vendedor y no tiene
        # sentido sin la confirmación, que está bloqueada.
        "POST /billing/vendedor",
        "POST /billing/vendedor/comprobar",
        # LA BAJA, NUNCA (25-09-2026), y reanudarla tampoco. Cancelar la
        # suscripción de otro desde una sesión suplantada es lo que no se
        # quiere tener que explicar nunca; y reanudar vuelve a cobrarle a quien
        # decidió irse. Las dos son decisiones de dinero del dueño. La vista
        # previa (`GET /billing/baja`) no es mutable, y la pantalla no enseña el
        # enlace en modo soporte.
        "POST /billing/baja",
        "POST /billing/baja/reanudar",
        # LLEVARSE LOS DATOS DEL SALÓN tampoco. Es un ZIP con los datos
        # personales de todas sus clientas y de su equipo: que salgan del salón
        # es una decisión del dueño, no algo que delega al pedir ayuda. La
        # vista previa (`GET /cuenta/datos`) no es mutable.
        "POST /cuenta/datos/descarga",
        # Encender las reservas online PUBLICA en internet los servicios, los
        # precios y la disponibilidad real del salon. Es una accion hacia
        # FUERA, la misma categoria que mandar un WhatsApp a un cliente: la ve
        # gente que no ha consentido nada y es una decision comercial del dueño,
        # no algo que delegue al pedir ayuda. Leer el estado no esta bloqueado
        # (el GET no es mutable), asi que soporte puede mirarlo y decirle al
        # salon que lo encienda el.
        "PATCH /salon/reservas-online",
        # Encender los recordatorios hace que empiecen a salir correos
        # automaticos FIRMADOS CON EL NOMBRE DEL SALON hacia clientes que no han
        # consentido nada — se les escribe porque tienen una cita. Es la misma
        # categoria que mandar un WhatsApp: sale hacia fuera y lo recibe un
        # tercero. Leer el estado no esta bloqueado, asi que soporte puede
        # mirarlo y decirle al salon que lo encienda el.
        "PATCH /salon/recordatorios",
        # CONECTAR O DESCONECTAR EL WHATSAPP DEL SALÓN (0085). Conectar pone a
        # hablar en nombre del salón un número de su cuenta de Meta, con su
        # permiso; desconectar deja mudo a su asistente. Las dos son decisiones
        # del dueño sobre su canal con sus clientes. Mirar el estado (GET) no es
        # mutable: soporte puede ver «con problema» y decirle qué hacer.
        "POST /bot/whatsapp/conectar",
        "POST /bot/whatsapp/desconectar",
        "POST /soporte/codigo",
        "DELETE /soporte/acceso",
        "PUT /employees/{employee_id}/pin",
        # Corregir un fichaje (0061). El registro de jornada es lo que el salón
        # enseña ante una inspección: si soporte pudiera añadir o cambiar horas
        # —aunque quede su nombre—, el registro dejaría de demostrar nada. Lo
        # corrige la administradora; soporte puede mirarlo y decírselo. El
        # endpoint lo vuelve a comprobar por si esta línea se perdiera.
        "POST /employees/{employee_id}/fichajes/correcciones",
        # Escribir por WhatsApp a un cliente del salón, desde el número del
        # salón y firmado como el salón. Es la única acción de todo el inquilino
        # que sale HACIA FUERA y llega a un tercero que no ha consentido nada:
        # el cliente ve un mensaje del negocio, no de un agente de soporte, y no
        # hay forma de que distinga una cosa de la otra. Un dueño que pide ayuda
        # con la bandeja está delegando que se la miren, no que se hable en su
        # nombre con su clienta.
        #
        # Leer la bandeja y RESOLVER una conversación sí están permitidos: son
        # exactamente lo que hace falta para diagnosticar («no me aparecen las
        # conversaciones», «se me ha quedado una atascada en manos de nadie»).
        "POST /bot/conversaciones/{session_id}/responder",
        # EL PIN DE GESTIÓN DEL DUEÑO (0046). Es de la misma familia que el PIN
        # elegido del quiosco, y peor: un PIN puesto o cambiado por soporte es
        # una llave que el dueño no conoce y que sigue valiendo al terminar la
        # sesión; quitarlo le deja el mostrador abierto sin avisar. Probar PINES
        # (`verificar`) tampoco: en soporte la cortina no se aplica, así que no
        # hay nada que abrir y sí un contador de intentos que gastarle al dueño.
        # Y el correo de «lo he olvidado» sale al buzón del dueño con un enlace
        # que sustituye su PIN: lo pide él, no quien le ayuda.
        "PUT /salon/pin-gestion",
        "DELETE /salon/pin-gestion",
        "POST /salon/pin-gestion/verificar",
        "POST /salon/pin-gestion/olvidado",
        # EL MOSTRADOR (0090): el dispositivo y la gestión son del salón, y todo
        # pide el PIN de la dueña. Soporte no convierte dispositivos ni abre su
        # gestión, y probar el PIN le gastaría los intentos. Desde el navegador
        # de soporte, además, no hay dispositivo: las puertas darían 401.
        "POST /mostrador/activar",
        "POST /mostrador/gestion",
        "DELETE /mostrador/gestion",
        "POST /mostrador/salir",
        # Y la salida con contraseña (29-10-2026): gastaría el mismo contador.
        "POST /mostrador/salir-con-contrasena",
        # Retirar un mostrador de la lista de Ajustes deja una tablet del salón
        # en el inicio de sesión en mitad de la jornada: lo decide la dueña.
        "DELETE /mostrador/dispositivos/{dispositivo_id}",
    }
)

# Rutas sin sesión de tenant: no dependen de la cookie de soporte, así que
# bloquearlas no añadiría nada (cualquiera puede llamarlas sin sesión ninguna)
# y comprobarles el origen tampoco (no hay autoridad ambiental que robar; el
# webhook de Stripe, además, llega de un servidor y jamás trae `Origin`).
PUBLICOS = frozenset(
    {
        "POST /auth/signup",
        "POST /auth/recuperar",
        "POST /webhooks/stripe",
        # Lo llama Meta, no una persona: sin cookie, sin token y sin `Origin`.
        # Su protección es la FIRMA del cuerpo con el App Secret
        # (X-Hub-Signature-256), que se comprueba antes de mirar nada más; sin
        # ella responde 403 y no toca la base de datos. Un agente de soporte no
        # puede "usar" esta ruta en el sentido que vigila esta política: no
        # sabría firmar. Ver app/routers/webhooks_whatsapp.py.
        "POST /webhooks/whatsapp",
        # Formulario publico de alta de referidos: no hay sesion de ningun tipo
        # y solo puede crear una solicitud en estado 'pendiente'. No toca datos
        # de ningun salon —los referidos son entidades de plataforma—, asi que
        # no hay nada que un agente de soporte pueda 'usar' aqui en el sentido
        # que vigila esta politica. Su freno es la ventana por IP.
        "POST /referidos/registro",
        # Abrir un salón de muestra desde la portada (0080): sin sesión. Crea (o
        # devuelve) la demo de ESE navegador, que no es ningún salón de verdad.
        "POST /demo",
        # Comprobar un codigo de alta desde el formulario (0077): sin sesion,
        # solo contesta si vale o no. Su freno es la ventana por IP.
        "POST /referidos/comprobar-codigo",
        # El portal del referido (0071): su propia puerta y su propia sesion
        # (cookie `__Host-ref-sesion`), ni la de un salon ni la del panel. El modo
        # soporte entra con la sesion de un SALON, que ninguna de estas acepta:
        # aqui no hay nada del salon que un agente pueda tocar.
        "POST /referido/entrar",
        "POST /referido/recuperar",
        "POST /referido/contrasena",
        "POST /referido/salir",
        "POST /referido/cobros/conectar",
        "POST /referido/cobros/sincronizar",
        # Los trabajadores de una agencia (0077): la misma sesion del referido.
        "POST /referido/trabajadores",
        "POST /referido/trabajadores/{trabajador_id}/baja",
        "POST /referido/trabajadores/{trabajador_id}/seguimiento",
        "POST /referido/trabajadores/{trabajador_id}/seguimiento/revocar",
        # El webhook de Connect (0072): lo autoriza la firma de Stripe.
        "POST /webhooks/stripe-connect",
        # La pagina publica de reservas. No hay sesion de ningun tipo: se entra
        # por el slug del salon, que es un identificador publico, y el backend
        # acuña por su cuenta el token del inquilino igual que hace el webhook
        # del bot. Un agente de soporte no puede "usarla" en el sentido que
        # vigila esta politica, porque cualquiera puede llamarla sin credencial
        # ninguna. Sus frenos son otros: el interruptor del salon, el tope
        # anti-spam contra la BD y el candado EXCLUDE.
        "POST /publico/{slug}/reservar",
        # La baja de recordatorios. La abre un cliente del salon desde el pie de
        # un correo: no tiene sesion, no tiene cuenta y lo unico que trae es un
        # token aleatorio que solo conoce quien recibio ese correo. Un agente de
        # soporte no puede "usarla" en el sentido que vigila esta politica —
        # cualquiera puede llamarla sin credencial— y ademas solo apaga un aviso
        # de UNA ficha, sin borrar nada.
        "POST /recordatorios/baja/{token}",
        # Elegir PIN de gestión nuevo con el enlace del correo. Sin sesión: lo
        # que autoriza es un token aleatorio de un solo uso que solo llega al
        # buzón de la cuenta, así que no hay autoridad ambiental que un agente de
        # soporte pueda "usar" aquí.
        "POST /salon/pin-gestion/restablecer",
    }
)

# El resto del inquilino. Se listan una a una —y no "todo lo que no esté
# bloqueado"— para que el test de cobertura pueda distinguir "decidido que sí"
# de "nadie lo ha mirado todavía".
PERMITIDOS = frozenset(
    {
        "POST /appointments",
        # Recuperar a quien pagó y se quedó fuera. NO mueve dinero: copia a
        # nuestra base lo que Stripe ya dice, así que no puede subir a nadie de
        # plan ni cobrarle. Es la avería que lo trajo —Stripe con la suscripción
        # viva y la persona mirando «todavía no se ha confirmado la prueba»— y
        # desatascarla es soporte de manual, igual que resolver una conversación
        # atascada. Sus hermanas que sí mueven dinero están en BLOQUEADOS.
        "POST /billing/sincronizar",
        # Cerrar una conversación de la bandeja. No manda nada a nadie: solo le
        # devuelve la palabra al bot. Es la avería típica —«se me quedó una
        # conversación en manos de una persona que ya no está»— y desatascarla
        # es soporte de manual. Su hermana `responder`, que sí escribe al
        # cliente, está en BLOQUEADOS.
        "POST /bot/conversaciones/{session_id}/resolver",
        "PATCH /appointments/{appointment_id}",
        "DELETE /appointments/{appointment_id}",
        "POST /clients",
        "POST /clients/bulk",
        "PATCH /clients/{client_id}",
        "DELETE /clients/{client_id}",
        "POST /employees",
        "PATCH /employees/{employee_id}",
        # Puestos: son etiquetas de trabajo, no permisos (ver 0027). Un
        # agente de soporte que ayuda a montar el salón necesita poder
        # crearlos, y renombrar «Barbero» no le abre ninguna puerta.
        "POST /puestos",
        "PATCH /puestos/{puesto_id}",
        "DELETE /puestos/{puesto_id}",
        "DELETE /employees/{employee_id}",
        # Devuelve un PIN de quiosco en claro. Permitido a propósito: es soporte
        # legítimo ("no puedo fichar") y el PIN sale aleatorio, así que el
        # agente no puede dejarse uno elegido de antes. Queda auditado con su
        # ruta —el PIN NO se registra en ningún sitio—, así que siempre se sabe
        # que se generó una credencial. Su hermano `PUT .../pin`, que sí deja
        # elegir el número, está en BLOQUEADOS.
        "POST /employees/{employee_id}/pin/generar",
        "DELETE /employees/{employee_id}/pin",
        "POST /expenses",
        # El quiosco de fichaje: el agente puede corregir un fichaje mal dado.
        "POST /fichaje",
        "POST /incomes",
        "POST /products",
        "PATCH /products/{product_id}",
        "POST /products/{product_id}/salida",
        "POST /products/{product_id}/reposicion",
        "DELETE /products/{product_id}",
        "POST /providers",
        "PATCH /providers/{provider_id}",
        "DELETE /providers/{provider_id}",
        # Permitida salvo el campo `email` (ver CAMPOS_BLOQUEADOS).
        "PATCH /salon/profile",
        "POST /salon/logo",
        "DELETE /salon/logo",
        # Horario de apertura: configuración operativa del salón, ni credencial
        # ni dinero. «La agenda no me deja poner citas los sábados» es
        # exactamente el tipo de avería que el agente tiene que poder arreglar.
        "PUT /salon/schedule",
        # Festivos y cierres: la misma naturaleza que el horario semanal —
        # configuración operativa, ni credencial ni dinero—. «Se me ha quedado
        # el salón cerrado en agosto» es avería de soporte de manual.
        "POST /salon/schedule/exceptions",
        "DELETE /salon/schedule/exceptions/{exception_id}",
        # Horario propio de cada ficha (0045): la misma naturaleza que el horario
        # del salón —configuración operativa, ni credencial ni dinero—. «Ana
        # sale en el bot los martes y los martes libra» es avería de soporte.
        "PUT /employees/{employee_id}/horario",
        "DELETE /employees/{employee_id}/horario",
        "POST /employees/{employee_id}/horario/excepciones",
        "DELETE /employees/{employee_id}/horario/excepciones/{excepcion_id}",
        "POST /service-categories",
        "PATCH /service-categories/{category_id}",
        "DELETE /service-categories/{category_id}",
        "POST /services",
        "PATCH /services/{service_id}",
        "DELETE /services/{service_id}",
    }
)

# Bloqueo a nivel de CAMPO: el endpoint sirve, el campo no. Lo que tienen en
# común los tres es que dejan una credencial en manos del agente que SOBREVIVE
# al final de la sesión de soporte:
#   - `email` del salón: cambiarlo es apropiarse de la cuenta, porque de ahí
#     sale el correo de reseteo de contraseña. El resto del perfil (nombre, CIF,
#     dirección, teléfono, color) se sigue pudiendo corregir, que es justo de lo
#     que va dar soporte.
#   - `pin_fichaje` al crear o editar un empleado: es la otra puerta al PIN
#     elegido que cierra `PUT /employees/{id}/pin` en BLOQUEADOS. Sin estas dos
#     entradas, aquel bloqueo no sirve de nada. El camino legítimo sigue abierto:
#     `POST /employees/{id}/pin/generar`, que sortea uno aleatorio.
CAMPOS_BLOQUEADOS: dict[str, frozenset[str]] = {
    "PATCH /salon/profile": frozenset({"email"}),
    "POST /employees": frozenset({"pin_fichaje"}),
    "PATCH /employees/{employee_id}": frozenset({"pin_fichaje"}),
}

# Por qué se rechaza cada campo, para que el 403 diga algo útil en pantalla.
_MENSAJES_CAMPO: dict[str, str] = {
    "PATCH /salon/profile": "El email de la cuenta no se puede cambiar en modo soporte.",
    "POST /employees": "El PIN de fichaje no se puede elegir en modo soporte.",
    "PATCH /employees/{employee_id}": "El PIN de fichaje no se puede elegir en modo soporte.",
}

# Lecturas que dejan rastro, por RGPD: las que devuelven datos de una persona
# —clientas, citas con nombres, empleadas, fichajes, conversaciones—. El resto
# de GET no se audita para que el rastro útil no se ahogue en ruido de
# navegación. NO SE ESCRIBE A OJO: la descubre el guarda
# `tests/test_lecturas_de_personas.py` mirando lo que devuelve cada ruta, y se
# pone rojo si aquí falta una.
LECTURAS_AUDITADAS = frozenset(
    {
        "GET /agenda/alternativas",
        "GET /agenda/por-resolver",
        "GET /appointments",
        "GET /appointments/disponibilidad",
        "GET /bot/conversaciones",
        "GET /bot/conversaciones/{session_id}",
        "GET /clients",
        "GET /clients/para-cita",
        "GET /clients/{client_id}",
        "GET /employees",
        "GET /employees/agenda",
        "GET /employees/vinculables",
        "GET /employees/{employee_id}",
        "GET /employees/{employee_id}/baja",
        "GET /employees/{employee_id}/fichajes",
        "GET /employees/{employee_id}/horario",
        "GET /expenses",
        "GET /expenses/{expense_id}/registro",
        "GET /fichaje/estado",
        "GET /incomes/{income_id}/registro",
        "GET /providers",
        "GET /reservas",
        "GET /rgpd/documentos/{doc_id}/pdf",
        "GET /salon/centros/en-pausa/citas",
        "GET /stats/equipo",
    }
)

_PROHIBIDO = "Esta acción no está disponible en modo soporte."

# Por qué se dio el portazo. Va en el `detalle` de la fila de auditoría porque
# sin él las tres ramas son indistinguibles —mismo `accion`, mismo `status`,
# solo cambia `path`— y la que provoca un TERCERO (origen) sepultaría a las que
# provoca el agente, que son las que este rastro existe para conservar:
#   - "origen":    la comprobación CSRF. Lo dispara cualquier web abierta en
#                  otra pestaña, NO el agente de soporte.
#   - "bloqueado": el agente contra la lista negra (facturación, consentimiento).
#   - "campo":     el agente contra un campo vetado de un endpoint permitido.
MOTIVO_ORIGEN = "origen"
MOTIVO_BLOQUEADO = "bloqueado"
MOTIVO_CAMPO = "campo"


def iterar_rutas(routes: Iterable[BaseRoute]) -> Iterator[BaseRoute]:
    """Todas las rutas de la app, incluidas las que cuelgan de un router incluido.

    NO SIMPLIFICAR a `for r in app.routes`. Desde FastAPI 0.139 / Starlette 1.3,
    `include_router` no aplana las rutas dentro de `app.routes`: mete un objeto
    `_IncludedRouter` que las guarda en `.original_router.routes`. Ese objeto no
    tiene ni `.path` ni `.methods`, así que un recorrido plano de `app.routes`
    devuelve CERO rutas de negocio — y cualquier red construida encima (el test
    de cobertura de la clasificación) pasaría en vacío para siempre sin que
    nadie se entere.
    """
    for ruta in routes:
        interno = getattr(ruta, "original_router", None)
        if interno is not None:
            yield from iterar_rutas(interno.routes)
        else:
            yield ruta


def rutas_mutables(app: FastAPI) -> set[str]:
    """`{"POST /clients", "DELETE /clients/{client_id}", ...}` de toda la app."""
    return {
        f"{metodo} {ruta.path}"
        for ruta in iterar_rutas(app.routes)
        for metodo in (getattr(ruta, "methods", None) or ())
        if metodo in MUTABLES
    }


def _origen(valor: str | None) -> str | None:
    """`https://app.estetixapp.es/clientes?x=1` → `https://app.estetixapp.es`."""
    if not valor:
        return None
    try:
        partes = urlparse(valor)
    except ValueError:
        return None
    if not partes.scheme or not partes.netloc:
        return None
    return f"{partes.scheme}://{partes.netloc}".rstrip("/")


def _origen_permitido(request: Request, settings: Settings) -> bool:
    """Origen (o, si no viene, Referer) contra los orígenes de la app del salón.

    Se comparan orígenes normalizados, nunca prefijos de cadena: con `startswith`
    valdría `https://estetixapp.es.sitio-malicioso.test`.
    """
    permitidos = set(settings.allowed_origins_list)
    cabecera = request.headers.get("origin")
    if cabecera is not None:
        # Si VIENE el header manda el header, aunque no se pueda parsear. Se
        # decide sobre su presencia y no sobre el valor ya normalizado: si no,
        # un `Origin: null` (iframe en sandbox, redirección entre orígenes) o un
        # `Origin: @@@` se colarían por el Referer, que es justo la cabecera que
        # el atacante puede provocar navegando desde su propia página.
        normalizado = _origen(cabecera)
        return normalizado is not None and normalizado in permitidos
    referer = _origen(request.headers.get("referer"))
    return bool(referer and referer in permitidos)


def clave_de_ruta(request: Request) -> str | None:
    """`POST /clients/{client_id}` — con la plantilla, nunca con el id real.

    Se resuelve leyendo `scope["route"]`, que existe porque las dependencias
    corren DESPUÉS del enrutado. Si no estuviera, devuelve None y quien llama
    falla cerrado.
    """
    ruta = request.scope.get("route")
    plantilla = getattr(ruta, "path", None)
    return f"{request.method} {plantilla}" if plantilla else None


async def _portazo(
    request: Request,
    settings: Settings,
    service_db: PostgrestClient,
    detalle: str,
    motivo: str,
) -> NoReturn:
    """403 que deja rastro.

    Antes de cortar, revalida la sesión contra la BD y deja el contexto en
    `request.state`, que es de donde lo lee el middleware de auditoría: así el
    intento bloqueado queda registrado con `status: 403` sin que este módulo
    escriba nada. Los intentos son lo más valioso que hay para investigar
    después —querer cobrar con la tarjeta del dueño, renovarse el acceso o
    quitárselo a él, y el 403 por Origin, que es una web ajena escribiendo con
    la cookie de soporte—.

    La revalidación es lo que sostiene el invariante: solo se audita lo que se
    ha comprobado VIVO. Si la sesión ya murió o la cookie no es auténtica,
    `cargar_contexto_sesion`/`decodificar_token_impersonacion` cortan con 401 y
    no queda contexto, así que no se escribe una sola fila. La petición sigue
    sin atenderse en cualquiera de los dos casos, que es lo que importa.

    Solo se paga la lectura extra en la rama de bloqueo, que es la rara.
    """
    claims = decodificar_token_impersonacion(
        settings, request.cookies[COOKIE_IMPERSONACION]
    )
    request.state.impersonation = await cargar_contexto_sesion(settings, service_db, claims)
    request.state.motivo_bloqueo = motivo
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detalle)


async def guard_impersonacion(
    request: Request,
    settings: Settings = Depends(get_settings),
    service_db: PostgrestClient = Depends(get_service_postgrest_client),
) -> None:
    """Dependencia global. Solo actúa si la petición viene en modo soporte."""
    # Un Authorization manda sobre la cookie (igual que en get_current_user):
    # esto es una sesión normal del salón, no de soporte, y no debe verse
    # afectada en nada — ni por los bloqueos ni por la comprobación de origen,
    # que ahí no hace falta (un Bearer no lo adjunta el navegador solo).
    if request.headers.get("authorization"):
        return
    if not request.cookies.get(COOKIE_IMPERSONACION):
        return
    if request.url.path.startswith(PREFIJO_PANEL):
        return
    if request.method not in MUTABLES:
        return

    clave = clave_de_ruta(request)
    if clave is None:
        # Sin ruta resuelta no se puede saber si está bloqueada, así que se
        # falla CERRADO: preferimos un soporte que no puede escribir a un
        # soporte que escribe donde no debe.
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_PROHIBIDO)

    if clave in PUBLICOS:
        return

    if not _origen_permitido(request, settings):
        await _portazo(request, settings, service_db, "Origen no permitido", MOTIVO_ORIGEN)

    if clave in BLOQUEADOS:
        await _portazo(request, settings, service_db, _PROHIBIDO, MOTIVO_BLOQUEADO)

    campos = CAMPOS_BLOQUEADOS.get(clave)
    if not campos:
        return

    try:
        cuerpo = await request.json()
    except Exception:  # noqa: BLE001 - cuerpo vacío o no-JSON: nada que mirar
        return
    # `request.body()` queda cacheado en el objeto Request y el endpoint recibe
    # ESE mismo objeto, así que volver a leer el cuerpo más abajo funciona.
    if isinstance(cuerpo, dict) and campos & set(cuerpo):
        await _portazo(
            request, settings, service_db, _MENSAJES_CAMPO.get(clave, _PROHIBIDO), MOTIVO_CAMPO
        )


# ── El rastro: qué hizo quien impersonó ─────────────────────────────────────


def _db_auditoria(settings: Settings) -> PostgrestClient:
    """Cliente de service role para escribir en admin_logs.

    Envuelto en función para que los tests lo sustituyan: un middleware no
    puede usar `Depends`. Va con la secret key y no con el token del salón
    porque `admin_logs` es del panel, no del inquilino: la RLS del salón no
    le da acceso (ni debe).
    """
    return PostgrestClient(settings.supabase_url, settings.supabase_secret_key)


# Ventana del freno de `motivo="origen"`. Cinco minutos es el punto en el que
# un ataque sostenido sigue dejando marca a menudo —una sesión dura 30 minutos,
# así que un CSRF que no pare deja hasta 6 filas con sus cuentas— y a la vez un
# bucle automatizado no puede escribir más que eso en toda la vida de la sesión.
_MINUTOS_FRENO_ORIGEN = 5


class _FrenoDeOrigen:
    """Como mucho una fila de `motivo="origen"` por sesión y ventana.

    NO es un rate limit, y la diferencia importa: un rate limit DESCARTA lo que
    sobra, y aquí no se puede perder la cuenta de cuántas veces lo intentaron.
    Los intentos callados se acumulan y viajan en el `detalle` de la siguiente
    fila que sí se escriba — hacia delante, porque `admin_logs` es append-only y
    la fila ya escrita no se puede corregir.

    Solo frena esta rama. `"bloqueado"` y `"campo"` los provoca el AGENTE y se
    limitan solos: son exactamente lo que hay que conservar intento por intento.

    Vive en memoria del proceso, como los demás frenos de la app: con varias
    réplicas cada una lleva la suya y, como mucho, se escriben más filas de las
    previstas. Ninguna forma de fallar aquí pierde información del rastro.

    No se reutiliza `AdminLoginRateLimiter`: aquella contesta "¿bloqueo?" sobre
    una ventana deslizante y su `_trim` descarta las marcas viejas, que son
    justo las que aquí hay que contar.
    """

    def __init__(self, minutos: int = _MINUTOS_FRENO_ORIGEN) -> None:
        self._ventana = timedelta(minutes=minutos)
        # sid -> (cuándo se escribió su última fila, intentos callados desde ella)
        self._sesiones: dict[str, tuple[datetime, int]] = {}
        # Lock de threading, no asyncio: solo se mantiene durante accesos
        # síncronos al diccionario, JAMÁS a través de un `await`.
        self._lock = Lock()

    @staticmethod
    def _ahora() -> datetime:
        # Envuelto en método para que los tests muevan el reloj sin parchear datetime.
        return datetime.now(timezone.utc)

    def _purgar(self, ahora: datetime) -> None:
        """Un `sid` es único por sesión y una sesión dura como mucho
        MINUTOS_SESION, así que pasado ese tiempo la entrada ya no puede volver
        a usarse jamás. Con ello el diccionario queda acotado a las sesiones
        vivas y no hace falta ninguna tarea de fondo."""
        muerto = timedelta(minutes=MINUTOS_SESION)
        for sid in [s for s, (ultima, _) in self._sesiones.items() if ahora - ultima > muerto]:
            del self._sesiones[sid]

    def intentos_a_registrar(self, sid: str) -> int | None:
        """Cuántos intentos anota la fila de ahora, o None si toca callar.

        La ventana se da por consumida de forma OPTIMISTA, aquí y bajo el
        candado, ANTES de que nadie escriba nada. Es lo que hace que dos
        peticiones concurrentes no puedan colar dos filas. A cambio, quien
        llama está obligado a `deshacer()` si el insert acaba fallando.
        """
        with self._lock:
            ahora = self._ahora()
            self._purgar(ahora)
            entrada = self._sesiones.get(sid)
            # La PRIMERA ocurrencia se escribe SIEMPRE, sin esperar a nada:
            # perder la primera señal de un CSRF sería justo lo contrario de lo
            # que se busca al registrarlo.
            if entrada is None or ahora - entrada[0] >= self._ventana:
                callados = 0 if entrada is None else entrada[1]
                self._sesiones[sid] = (ahora, 0)
                return callados + 1
            self._sesiones[sid] = (entrada[0], entrada[1] + 1)
            return None

    def deshacer(self, sid: str, intentos: int) -> None:
        """La fila no llegó a escribirse: ni la ventana está consumida ni esos
        intentos están contados en ninguna parte.

        Sin esto, el freno se quedaría convencido de que existe una fila que
        nunca llegó —y la auditoría PUEDE fallar, que para eso su `except` es
        ancho—, así que un corte de red de un segundo dejaría hasta una ventana
        entera de un ataque EN CURSO sin una sola línea. Justo lo que el freno
        existe para impedir.

        Los intentos se SUMAN a lo que haya en la entrada, no la sustituyen:
        mientras el insert estaba en vuelo pueden haber entrado más peticiones
        del mismo sid —se contaron como calladas—, y pisar la entrada con la
        cuenta que capturó la llamada fallida las borraría sin que nadie se
        entere. Es una intercalación normal de corutinas en el mismo bucle, no
        un caso de hilos.

        La marca se deja al borde de la ventana, ya vencida, en vez de borrarla:
        así el siguiente intento vuelve a escribir de inmediato y con la cuenta
        completa, y la entrada conserva una fecha real para que la purga siga
        acotando el diccionario.
        """
        with self._lock:
            _, callados = self._sesiones.get(sid, (None, 0))
            self._sesiones[sid] = (self._ahora() - self._ventana, callados + intentos)

    def limpiar(self) -> None:
        """Solo para tests: deja el freno sin memoria entre casos."""
        with self._lock:
            self._sesiones.clear()


_freno_de_origen = _FrenoDeOrigen()


def accion_auditable(request: Request, clave: str | None) -> str | None:
    """Qué se registra de esta petición: una mutación, una lectura sensible o nada.

    Sin `clave` no se registra NADA. Es deliberado: la clave es la única forma
    de nombrar la ruta con plantilla, y `admin_logs` no se puede corregir
    después, así que se prefiere perder una línea de rastro a meter en ella el
    id real que viene en la URL.
    """
    if clave is None:
        return None
    if request.method in MUTABLES:
        return "impersonation_action"
    if clave in LECTURAS_AUDITADAS:
        return "impersonation_read"
    return None


_SIN_RASTRO = {
    "impersonation_action": "No se ha podido dejar constancia en el registro de soporte, así que no se ha hecho. "
    "Vuelve a intentarlo en un momento.",
    "impersonation_read": "No se ha podido dejar constancia en el registro de soporte, así que no se ha leído. "
    "Vuelve a intentarlo en un momento.",
}


async def apuntar_antes_de_atender(
    request: Request, settings: Settings, contexto: ImpersonationContext
) -> None:
    """SIN SU LÍNEA NO SE HACE NI SE LEE (20-10-2026).

    Corre al resolver la identidad (`core/security.py`), con la sesión ya
    comprobada viva y ANTES del endpoint. Si la fila no entra, 503 y el
    endpoint no llega a correr. La fila no lleva `status`: aún no existe; lo
    añade `completar_lo_apuntado` al terminar.
    """
    clave = clave_de_ruta(request)
    accion = accion_auditable(request, clave)
    if accion is None or getattr(request.state, "apunte_de_soporte", None):
        return
    metodo, _, plantilla = clave.partition(" ")
    try:
        apunte = await append_admin_log(
            _db_auditoria(settings),
            admin_id=contexto.admin_id,
            accion=accion,
            ip=client_ip(request),
            target_salon_id=contexto.salon_id,
            detalle={"sid": contexto.sid, "method": metodo, "path": plantilla},
        )
    except Exception as e:  # noqa: BLE001 - también un corte de red, que no es PostgrestError
        _log.error(
            "No se pudo auditar la actividad de soporte; no se atiende: accion=%s salon=%s ruta=%s sid=%s",
            accion,
            contexto.salon_id,
            clave,
            contexto.sid,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_SIN_RASTRO[accion]
        ) from e
    request.state.apunte_de_soporte = (apunte, accion)


async def completar_lo_apuntado(request: Request, settings: Settings, status_code: int) -> None:
    """Cómo acabó una acción apuntada (0091): su `status`, en una fila aparte.

    La acción ya pasó y no depende de esto: si no se puede escribir, el apunte
    se queda sin resultado —«se intentó, no sabemos cómo acabó»— y una línea en
    el registro del proceso. Las lecturas no se completan: se cuentan juntas
    (`admin_logs_vista`).
    """
    apunte = getattr(request.state, "apunte_de_soporte", None)
    if not apunte or apunte[1] != "impersonation_action":
        return
    try:
        await completar_admin_log(_db_auditoria(settings), apunte[0], {"status": status_code})
    except Exception:  # noqa: BLE001 - la acción ya ocurrió
        _log.error("No se pudo completar el apunte de soporte %s con status=%s", apunte[0], status_code, exc_info=True)


async def registrar_actividad_impersonacion(
    settings: Settings,
    contexto: ImpersonationContext,
    accion: str,
    clave: str,
    status_code: int,
    ip: str,
    motivo: str | None = None,
) -> None:
    """Una fila por acción. NUNCA cuerpos: admin_logs no se puede corregir.

    La ruta va con plantilla (`/clients/{client_id}`), así que el rastro dice
    QUÉ se hizo sin llevarse por delante datos personales del salón. Lo que
    devolvió el endpoint tampoco entra: la auditoría de lectura existe para
    dejar constancia de que se consultaron datos personales, y copiarlos aquí
    iría contra su propio motivo.

    `motivo` solo viene cuando hubo portazo, y es lo que permite separar el
    trabajo normal del agente de los intentos — y, entre estos, los suyos del
    ruido que puede meter un tercero desde otra pestaña.
    """
    metodo, _, plantilla = clave.partition(" ")
    detalle: dict = {
        "sid": contexto.sid,
        "method": metodo,
        "path": plantilla,
        "status": status_code,
    }
    if motivo is not None:
        detalle["motivo"] = motivo
    intentos: int | None = None
    if motivo == MOTIVO_ORIGEN:
        # ÚNICA rama frenada: la dispara quien ataca, no el agente (ver
        # _FrenoDeOrigen). Los intentos callados no se pierden, se acumulan en
        # la cuenta de la siguiente fila.
        intentos = _freno_de_origen.intentos_a_registrar(contexto.sid)
        if intentos is None:
            return
        detalle["intentos"] = intentos

    try:
        await append_admin_log(
            _db_auditoria(settings),
            admin_id=contexto.admin_id,
            accion=accion,
            ip=ip,
            target_salon_id=contexto.salon_id,
            detalle=detalle,
        )
    except Exception:  # noqa: BLE001
        # Esta fila no existe, así que el freno no puede seguir creyendo que sí:
        # se le devuelve la ventana y la cuenta para que el siguiente intento
        # vuelva a escribir.
        if intentos is not None:
            _freno_de_origen.deshacer(contexto.sid, intentos)
        # Aquí solo llegan PORTAZOS: lo atendido se apunta antes, en
        # `apuntar_antes_de_atender`. No se hizo nada, y el 403 ya está dado:
        # se traga cualquier fallo, pero no en silencio.
        #
        # El mensaje lleva accion, salón y sesión porque este renglón es el
        # ÚNICO síntoma: si la escritura se rompe de forma sistémica, el modo
        # soporte sigue funcionando al 100% sin dejar rastro. Que se pueda
        # buscar por salón y por sid es lo que hace la línea accionable.
        _log.error(
            "No se pudo auditar la actividad de soporte: accion=%s salon=%s ruta=%s sid=%s",
            accion,
            contexto.salon_id,
            clave,
            contexto.sid,
            exc_info=True,
        )
