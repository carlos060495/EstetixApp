"""En qué centro está trabajando esta petición.

POR QUÉ ESTO CABE EN UN SITIO. La Fase A dejó la frontera en la cuenta: las 50
políticas de inquilino filtran por `salon_id = any(salones_visibles())`, y
`current_salon_id()` se quedó sin un solo uso —medido: cero políticas, cero
valores por defecto, cero funciones—. O sea que el claim `salon_id` del token
ya no decide NADA en la base de datos.

Por eso `CurrentUser.salon_id` puede pasar de significar «el centro de tu
ficha» a significar «el centro en el que estás trabajando» sin tocar los 188
sitios del backend que lo leen: se resuelve una vez, aquí, y todo lo demás
sigue igual.

EL SELECTOR ES COMODIDAD, NO FRONTERA. Esta comprobación existe para que la
aplicación conteste algo sensato —un 403 con motivo— cuando alguien pide un
centro que no es suyo. Si fallara, la RLS seguiría sin devolver una sola fila
de ese centro: la frontera está en la base, no aquí. Las dos, no una.

LA EQUIVALENCIA CON UN SOLO CENTRO. Sin cabecera no hay consulta extra, no hay
validación y el centro activo es el de siempre. Hoy todas las cuentas tienen un
centro y ninguna manda la cabecera, así que para todas ellas esto no existe.
"""

from __future__ import annotations

import logging
from typing import NamedTuple

from app.billing.lo_que_incluye_el_plan import Incluido
from app.db.postgrest_client import PostgrestClient, PostgrestError

logger = logging.getLogger("estetix.centro")

# La cabecera que manda el navegador. No es una cookie a propósito: una cookie
# viaja sola en cada navegación y en peticiones que no ha originado la
# aplicación; una cabecera la pone el cliente de API y solo él.
CABECERA_CENTRO = "X-Centro-Activo"

# Lo que la pantalla enseña cuando el centro guardado ha dejado de valer. Va
# aquí, junto a la regla, para que el texto y el motivo no se separen.
MENSAJE_CENTRO_RECHAZADO = (
    "El centro que tenías abierto ya no está disponible. Te hemos devuelto al tuyo."
)

# Y cuando el centro existe y es suyo, pero el plan ya no lo incluye. Es OTRO
# motivo y por eso es otro texto: «ya no está disponible» haría pensar que se ha
# perdido algo, y no se ha perdido nada.
MENSAJE_CENTRO_EN_PAUSA = (
    "Ese centro está en pausa porque tu plan incluye menos centros. "
    "No se ha borrado nada. Te hemos abierto el otro."
)


class Resolucion(NamedTuple):
    """En qué centro corre la petición, y qué hubo que decirle a la pantalla."""

    centro: str
    # Se pidió uno que no es de esta persona.
    rechazado: bool
    # El centro al que se iba está EN PAUSA por el plan. Si además `centro`
    # sigue siendo ese, es que no había ninguno activo al que llevarla: quien
    # llama tiene que cortar (ver `core/security.py`).
    en_pausa: bool


async def resolver_centro_activo(
    service_db: PostgrestClient,
    *,
    pedido: str | None,
    ficha: dict,
    incluido: Incluido | None = None,
) -> Resolucion:
    """El centro en el que corre esta petición, ya comprobado.

    Dos preguntas distintas, en este orden: ¿es suyo? —eso es permiso, y lo
    contesta `_puede_entrar` replicando la frontera— y ¿lo incluye el plan? —eso
    es comercial, y lo contesta `incluido`, que ya viene resuelto de la misma
    consulta de sesión—. `rechazado` es True cuando se pidió uno que no es de
    esta persona: entonces se cae a SU centro y quien llama tiene que DECIRLO.

    POR QUÉ SE CAE Y NO SE REVIENTA — y esto corrige una decisión anterior.
    Antes esto lanzaba un 403 para que nadie creyera estar en un centro y
    estuviera en otro. La intención era buena y la ejecución, no: si el centro
    guardado deja de valer, TODAS las peticiones fallan, incluida `/auth/me`,
    que es justo la que la aplicación usa para saber quién es y dónde puede
    trabajar. La sesión se queda viva y muerta a la vez, y la única salida es
    tocar la base a mano. Pasó en producción.

    Lo que había que evitar era el SILENCIO, no la vuelta atrás. Así que se
    vuelve al centro propio y se marca: `/auth/me` y `/salon/centros` devuelven
    la bandera, la barra de arriba enseña el centro REAL —que sale del backend,
    no del navegador— y la pantalla lo dice. Nadie se cree en un sitio en el
    que no está, y nadie se queda encerrado.

    NO PUEDE EXISTIR UN ESTADO EN EL QUE ENTRAS Y DEL QUE NO PUEDES SALIR.
    """
    propio = ficha.get("salon_id")

    # SOLO UNA CADENA CUENTA COMO «me pides un centro». Quien llame a
    # `get_current_user` como función normal —los tests, o cualquier uso
    # interno— no pasa la cabecera, y FastAPI deja ahí su objeto `Header(...)`,
    # que es truthy. Sin esta comprobación, ese objeto se tomaba por un centro
    # pedido y la petición acababa en un 403 absurdo.
    destino, rechazado = propio, False
    if isinstance(pedido, str) and pedido.strip() and pedido != propio:
        if await _puede_entrar(service_db, ficha, pedido):
            destino = pedido
        else:
            logger.warning(
                "Centro activo rechazado: el usuario %s pidió %s y no le pertenece; "
                "se vuelve a %s.",
                ficha.get("id"),
                pedido,
                propio,
            )
            rechazado = True

    # Sin `incluido` no se pausa nada: es el caso de una caída —`ficha` sin
    # cuenta— y el de todo el código que llamaba a esto antes de que el plan
    # fuera frontera. Ante la duda, se trabaja.
    if incluido is None or incluido.esta_activo(destino):
        return Resolucion(destino, rechazado, False)

    activo = await _primero_activo(service_db, ficha, incluido)
    if activo:
        logger.info(
            "Centro %s en pausa por el plan (%s); se abre %s.", destino, incluido.plan, activo
        )
        return Resolucion(activo, rechazado, True)

    # NO HAY NINGUNO AL QUE LLEVARLA. Le pasa a quien solo tiene ficha en el
    # centro pausado: una administradora siempre tiene otro, porque ve toda su
    # cuenta y el reparto garantiza que queda uno activo. Se devuelve el centro
    # pausado marcado, y `core/security.py` corta con un motivo — porque
    # mandarla a un centro donde la RLS no le devuelve una sola fila sería
    # dejarla mirando una aplicación vacía, que es un fallo aunque no salte nada
    # rojo.
    logger.warning(
        "El usuario %s no tiene ningún centro activo: todos los suyos están en pausa.",
        ficha.get("id"),
    )
    return Resolucion(destino, rechazado, True)


async def _primero_activo(
    service_db: PostgrestClient, ficha: dict, incluido: Incluido
) -> str | None:
    """Un centro activo al que ESTA persona pueda entrar, o None.

    Se recorre `centros_activos` en su orden —que es el del reparto: primero el
    elegido, después por antigüedad— para que dos peticiones de la misma persona
    caigan siempre en el mismo sitio. Una caída de la que no se pueda salir
    también sería determinista, así que el orden no basta: por eso hay `None`.

    Solo la administradora tiene sesión (0063, 0069), y entra en cualquier
    centro de su cuenta por regla; el reparto viene de esa misma cuenta: el
    primero activo le vale.
    """
    return incluido.centros_activos[0] if incluido.centros_activos else None


async def _puede_entrar(service_db: PostgrestClient, ficha: dict, centro: str) -> bool:
    """¿Está este centro entre los suyos?

    Se replica en Python lo que hace `salones_visibles()` en la base, con la
    misma regla: la administradora —la única con sesión (0063, 0069)— entra en
    cualquier centro DE SU CUENTA, por regla y no por filas, para que abrir un
    centro nuevo no dependa de que alguien se acuerde de darle de alta en él.

    Que esté replicado no es una duplicación de la frontera: es la comodidad
    contestando rápido. La frontera es la de la base, y sigue ahí debajo.
    """
    try:
        filas = await service_db.select(
            "salons",
            {
                "select": "id",
                "id": f"eq.{centro}",
                "account_id": f"eq.{ficha.get('account_id')}",
                "limit": "1",
            },
        )
        return bool(filas)
    except PostgrestError:
        # NO SE CONCEDE ANTE LA DUDA. Es el criterio contrario al de la
        # suspensión, y a propósito: allí una avería no puede echar a nadie de
        # su propia casa; aquí, conceder por no poder comprobar sería dejar
        # entrar en la de al lado. Quien no pueda cambiar de centro durante una
        # caída se queda en el suyo, que es lo que ya tenía.
        logger.exception("No se pudo comprobar el centro pedido; no se concede.")
        return False
