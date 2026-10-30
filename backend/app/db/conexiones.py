"""LA CONEXIÓN SE COMPARTE. EL TOKEN NO.

POR QUÉ EXISTE. Hasta ahora cada llamada a la base abría su propia conexión y
la tiraba: diecisiete `async with httpx.AsyncClient(...)` repartidos por `app/`,
uno por método. Medido contra el stack local, doce peticiones simultáneas —lo
que hace una ráfaga— tardaban 4,08 s solo en abrir conexiones, contra un plazo
de 5,0 s. Reutilizándolas, las mismas doce: 48 ms. Ochenta y cinco veces menos,
sin tocar la concurrencia.

LA REGLA QUE NO SE PUEDE ROMPER, y por la que este módulo es pequeño y aburrido
a propósito: el cliente es COMPARTIDO por todo el proceso, así que cualquier
cosa que se guarde EN él la ven todas las peticiones de todos los salones. Un
token de una cuenta pegado al cliente sería un salón leyendo los datos de otro
— el peor fallo que puede tener esta aplicación, y mucho peor que la lentitud
que venimos a arreglar.

Por eso el cliente se crea SIN estado de nadie:

  · sin `headers=`   — las cabeceras (apikey, Authorization) van en cada
                       llamada, que es como ya lo hacían `PostgrestClient`,
                       `StorageClient`, Resend, Gemini y la Cloud API;
  · sin `auth=`      — mismo motivo, y además `auth` se aplica sola a todas;
  · sin `base_url=`  — quien llama pasa la URL entera, así que no hay forma de
                       apuntar sin querer al Supabase de otro;
  · SIN GALLETAS     — httpx guarda en el cliente las cookies que le devuelvan.
                       En un cliente compartido eso es memoria entre inquilinos:
                       una cookie que pusiera Supabase en la respuesta de un
                       salón viajaría en la petición del siguiente. Aquí el
                       tarro no recuerda nada (`_SinMemoria`).

Hay un guarda que comprueba las cuatro cosas, y un test de base real que lanza
a la vez peticiones con dos tokens de dos cuentas distintas y exige que ninguna
vea nada de la otra. Ver `tests/test_la_conexion_se_comparte_el_token_no.py`.

UN CLIENTE POR BUCLE DE EVENTOS. Las conexiones vivas de httpx pertenecen al
bucle en el que se abrieron; usarlas desde otro revienta. En producción hay un
único bucle y esto es un único cliente. En los tests, cada uno trae el suyo, y
por eso se guardan en un diccionario con el bucle por llave.
"""

from __future__ import annotations

import asyncio

import httpx

# EL TECHO DE CONEXIONES VIVAS. `max_keepalive` es cuántas se quedan calientes
# esperando a la siguiente petición: es el número que convierte 330 ms en 0.
# `max_connections` es el tope duro; por encima, httpx hace cola en vez de
# abrir sin límite, que es lo que protege a Supabase de nosotros.
LIMITES = httpx.Limits(max_connections=100, max_keepalive_connections=40, keepalive_expiry=30.0)

# Un plazo por si alguien llama sin decir el suyo. Cada llamada pasa el que le
# corresponde (`timeout=` en la petición), que es donde vivían antes.
TIMEOUT_POR_DEFECTO = 30.0

# ═══ DOS TUBERÍAS: LA BASE Y LOS SERVICIOS LENTOS ═══════════════════════════
#
# Hasta aquí había UNA, de 100, y por ella salían a la vez la base (PostgREST,
# GoTrue, el almacenamiento, las claves) y los servicios lentos: la IA (hasta
# 20 s por llamada), WhatsApp (15 s) y el correo (30 s). El webhook de WhatsApp
# hace hasta cuatro llamadas a la IA dentro de la misma petición, así que un rato
# movido de WhatsApp podía dejar a la base sin huecos, y el error le salía a
# todos los salones.
#
# Ahora cada una tiene su cliente y su límite. Agotar la de los servicios lentos
# no le quita ni un hueco a la de la base: son dos colas distintas.
#
# LA DE LA BASE se queda como estaba (100 / 40 calientes): es la que atiende cada
# pantalla de cada salón, sus llamadas duran milisegundos y ese techo ya protege
# a Supabase de nosotros. Por ella solo sale Supabase (lo vigila
# `tests/test_nada_de_fuera_para_la_aplicacion.py`).
#
# LA DE LOS SERVICIOS LENTOS, 20 / 10 calientes. Veinte llamadas a la vez a la
# IA son cinco conversaciones del bot en su peor caso (cuatro llamadas cada
# una), y un solo proceso no atiende más que eso con respuestas de hasta 20 s
# sin que la cola sea el problema. Y espera COMO MUCHO 5 s a que se libere un
# hueco: si la tubería está llena, esa llamada falla rápido (y el bot dice que no
# puede atender) en vez de quedarse en la cola detrás de llamadas de 20 s.
LIMITES_SERVICIOS = httpx.Limits(max_connections=20, max_keepalive_connections=10, keepalive_expiry=30.0)
ESPERA_HUECO_SERVICIOS = 5.0

_clientes: dict[asyncio.AbstractEventLoop, httpx.AsyncClient] = {}
_clientes_servicios: dict[asyncio.AbstractEventLoop, httpx.AsyncClient] = {}


class _SinMemoria(httpx.Cookies):
    """Un tarro de galletas que no guarda ninguna.

    No es paranoia: `httpx.AsyncClient` guarda por defecto las cookies de las
    respuestas y las reenvía en las siguientes peticiones DEL MISMO CLIENTE.
    Mientras el cliente era de usar y tirar eso no podía cruzarse con nadie;
    compartido, sí.
    """

    def extract_cookies(self, response: httpx.Response) -> None:  # noqa: D102
        return

    def set_cookie_header(self, request: httpx.Request) -> None:  # noqa: D102
        return


def _nuevo(limites: httpx.Limits = LIMITES) -> httpx.AsyncClient:
    cliente = httpx.AsyncClient(
        timeout=TIMEOUT_POR_DEFECTO,
        limits=limites,
        follow_redirects=False,
    )
    # EL TARRO SE PONE DESPUÉS, Y NO ES UN CAPRICHO. Pasarlo como `cookies=` al
    # constructor no sirve: httpx no se queda el objeto, construye uno suyo a
    # partir de él, y el nuestro se pierde por el camino. Lo descubrió su propio
    # guarda —`test_el_cliente_compartido_NO_recuerda_galletas`— con esta misma
    # función ya escrita y en verde por lo demás.
    cliente._cookies = _SinMemoria()
    return cliente


def cliente_compartido() -> httpx.AsyncClient:
    """El cliente de este bucle de eventos, creándolo la primera vez.

    Se crea al vuelo y no en el arranque a propósito: así funciona igual dentro
    de un `TestClient` que no levanta el ciclo de vida de la aplicación, y no
    hay un orden de importación del que acordarse.
    """
    return _del_bucle(_clientes, LIMITES)


def cliente_de_servicios() -> httpx.AsyncClient:
    """La tubería de los servicios lentos: la IA, WhatsApp y el correo.

    Las mismas reglas que la de la base (sin estado de nadie, sin galletas), con
    su propio límite. Ver el bloque de las DOS TUBERÍAS.
    """
    return _del_bucle(_clientes_servicios, LIMITES_SERVICIOS)


def plazo_de_servicio(segundos: float) -> httpx.Timeout:
    """El plazo de una llamada a un servicio lento: `segundos` para la llamada, y
    como mucho `ESPERA_HUECO_SERVICIOS` esperando un hueco en su tubería. Va en
    cada llamada porque el plazo de la petición sustituye entero al del cliente."""
    return httpx.Timeout(segundos, pool=ESPERA_HUECO_SERVICIOS)


def _del_bucle(clientes: dict, limites: httpx.Limits) -> httpx.AsyncClient:
    bucle = asyncio.get_running_loop()
    cliente = clientes.get(bucle)
    if cliente is not None and not cliente.is_closed:
        return cliente
    cliente = _nuevo(limites)
    clientes[bucle] = cliente
    _olvidar_bucles_cerrados()
    return cliente


def _olvidar_bucles_cerrados() -> None:
    """Los bucles que ya no existen no se quedan en el diccionario.

    Sus conexiones se fueron con ellos; lo único que quedaría es la entrada.
    """
    for clientes in (_clientes, _clientes_servicios):
        for bucle in [b for b in clientes if b.is_closed()]:
            clientes.pop(bucle, None)


async def cerrar_clientes() -> None:
    """Cierra los clientes de este bucle. Lo llama la parada del backend."""
    _olvidar_bucles_cerrados()
    try:
        bucle = asyncio.get_running_loop()
    except RuntimeError:
        return
    for clientes in (_clientes, _clientes_servicios):
        cliente = clientes.pop(bucle, None)
        if cliente is not None and not cliente.is_closed:
            await cliente.aclose()
