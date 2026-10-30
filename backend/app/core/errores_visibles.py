"""LO QUE LEE UNA PERSONA CUANDO LA BASE DICE QUE NO.

POR QUÉ EXISTE. Al guardar el perfil con un CIF distinto, a un salón le salió
esto EN PANTALLA:

    {"code":"42501","details":null,"hint":"Grant the required privileges to the
     current role with: GRANT UPDATE ON public.accounts TO authenticated;",
     "message":"permission denied for table accounts"}

A ella no le sirve de nada: no sabe qué es un GRANT ni tiene forma de darlo. A
otro le sirve de plano: le hemos dicho cómo se llama nuestra tabla, qué motor
hay debajo y dónde está flojo el permiso. Y el patrón que lo produjo
—`detail=e.detail`, el cuerpo literal de PostgREST— estaba repetido en 35 sitios
de 15 ficheros.

QUÉ HACE ESTE MÓDULO. Traducir. Una frase que la persona entiende, y el detalle
de verdad se queda donde ya estaba: en los registros, que `_raise_for_status`
escribe con el método, la tabla, el estado y el motivo de PostgREST.

POR QUÉ NO SE LIMPIA `PostgrestError.detail` EN EL ORIGEN, que sería una línea
en vez de 35: porque hay ONCE sitios que LEEN ese texto para decidir qué
contestar —buscan `23505` (clave duplicada), `23503` (fila en uso) y el código
de solape— y con eso distinguen un 409 de un 500. Entre ellos, el webhook de
Stripe y el de WhatsApp, donde reconocer un duplicado ES la idempotencia. Si el
detalle dejara de traer el código, esos once empezarían a contestar 500 donde
hoy contestan 409 y ningún test lo vería. El texto crudo se queda para quien lo
inspecciona; lo que se corta es que SALGA.

EL GUARDA lo vigila descubriendo, no con una lista: recorre `app/` y denuncia
cualquier `HTTPException` cuyo `detail` venga de la excepción capturada. Ver
`tests/test_ningun_error_de_la_base_llega_crudo.py`.
"""

from __future__ import annotations

# El final de todas las frases. Dice qué hacer ahora —reintentar— y deja una
# salida que no es «mira los registros»: escribirnos.
_SALIDA = "Vuelve a intentarlo. Si sigue pasando, escríbenos y lo miramos."


def visible(accion: str | None = None) -> str:
    """La frase que se le enseña a una persona cuando la base falla.

    `accion` es lo que se estaba intentando, en infinitivo y en su idioma —
    «guardar el perfil», «crear la cita»—. No lleva NADA de la excepción: ni el
    texto, ni el código, ni el nombre de la tabla. Ese es el punto.

        raise HTTPException(400, detail=visible("guardar el perfil"))
        → "No hemos podido guardar el perfil. Vuelve a intentarlo. Si sigue
           pasando, escríbenos y lo miramos."

    EN PRIMERA PERSONA DEL PLURAL, y no por simpatía: «no se ha podido guardar
    LOS datos fiscales» no concuerda, y la forma que sí concuerda —«no se han
    podido»— obliga a saber si lo que sigue es singular o plural. «No hemos
    podido» vale con cualquier objeto, y además dice quién no pudo.

    Sin `accion` queda la genérica, que sigue siendo mejor que un JSON de
    Postgres, pero conviene ponerla: «no hemos podido guardar» delante de un
    formulario largo no dice QUÉ no se guardó.
    """
    if accion:
        return f"No hemos podido {accion}. {_SALIDA}"
    return f"No hemos podido completar la operación. {_SALIDA}"
