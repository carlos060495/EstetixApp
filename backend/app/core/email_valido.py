"""¿Es esto un correo? Un solo criterio para las dos puertas por las que entra.

HAY DOS SITIOS donde un cliente da su email y ninguno de los dos es de fiar:

  · El WIDGET público, donde lo teclea en un móvil.
  · El BOT de WhatsApp, donde lo dicta en mitad de una conversación y lo que
    llega aquí es lo que un modelo de lenguaje ha entendido.

El segundo es el que obliga a que esto exista. Gemini redacta y entiende; lo
que decide si un correo vale es este módulo, contra una librería que conoce las
reglas. Si la validación viviera en el prompt, «juan@gmail» sería un correo el
día que al modelo le pareciera que sí — y el fallo no se vería hasta que el cron
intentara escribir a esa dirección semanas después.

POR QUÉ `email_validator` Y NO UNA EXPRESIÓN REGULAR: porque es la librería que
usa `EmailStr` de Pydantic, ya está en `requirements.txt`, y sabe cosas que una
regular casera no sabe. La que más importa aquí: `.test`, `.local`, `.invalid`
y `.example` son dominios RESERVADOS que nunca van a recibir un correo, y una
regular los da por buenos porque tienen su punto y sus letras.

SIN RED, SIEMPRE. `check_deliverability=False` apaga la consulta de DNS. Esto se
ejecuta dentro de la respuesta a un mensaje de WhatsApp, con una persona
esperando: no se le puede añadir un viaje a un servidor de nombres. Se comprueba
la FORMA de la dirección, no que el buzón exista — y que exista no lo sabe nadie
hasta que se le escribe.
"""

from __future__ import annotations

from email_validator import EmailNotValidError, validate_email

# Lo que cabe en `clients.email`. Se comprueba antes de validar para no gastar
# trabajo en una cadena que de todas formas no se va a poder guardar.
MAX_EMAIL = 120


def normalizar_email(bruto: str | None) -> str | None:
    """El correo listo para guardar, o `None` si no es un correo.

    LOS ESPACIOS SE QUITAN AQUÍ y no se dan por perdidos: la librería rechaza
    « juan@gmail.com » con espacios alrededor, y quien pega un correo desde el
    teclado de un móvil los arrastra casi siempre. Rechazar por eso sería
    hacerle repetir un correo que estaba bien escrito.

    LO QUE SE NORMALIZA Y LO QUE NO: el dominio se pasa a minúsculas, porque los
    dominios no distinguen mayúsculas; la parte de delante de la arroba se deja
    EXACTAMENTE como la escribió la persona. Bajarla también sería lo cómodo y
    lo que casi todo el mundo hace, pero el estándar dice que esa parte sí
    distingue mayúsculas y quien decide si su servidor las distingue es el
    servidor, no nosotros. Tocar de menos aquí no rompe nada; tocar de más deja
    un correo que no llega.

    Devolver `None` en vez de lanzar es lo que permite que quien llama decida:
    el widget se lo calla y sigue con la reserva, el bot pide que se lo repitan.
    """
    limpio = (bruto or "").strip()
    if not limpio or len(limpio) > MAX_EMAIL:
        return None
    try:
        return validate_email(limpio, check_deliverability=False).normalized
    except EmailNotValidError:
        return None
