from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CentroOut(BaseModel):
    id: str
    nombre_comercial: str
    slug: str | None = None
    # EL PLAN YA NO LO INCLUYE. No se esconde: sigue en la lista, marcado y sin
    # poder entrar. Esconderlo haría pensar que se han borrado los datos —y le
    # hemos prometido por escrito que no se borra nada—, y además el selector
    # desaparece con menos de dos centros, así que quitarlo de aquí quitaría la
    # barra entera y con ella el único sitio donde se ve que el centro existe.
    en_pausa: bool = False
    # LE FALTA EL TELÉFONO, LA DIRECCIÓN O LAS DOS. Desde hoy no se puede crear
    # un centro sin ellos, pero los que ya existen los tienen en null: su página
    # pública no sabe decirle a una clienta ni por dónde escribir ni adónde ir.
    #
    # Es un AVISO, no un bloqueo: un salón que no puede entrar a media mañana
    # porque le exigimos un dato nuestro es peor problema que el que arregla.
    falta_contacto: bool = False


class CentrosOut(BaseModel):
    """Los centros de esta persona y en cuál está trabajando.

    `activo` sale del backend y no del navegador: es el que la petición acaba
    de usar de verdad, ya comprobado. Que la pantalla lo pinte de ahí evita el
    caso peor —creerse en un centro y estar en otro—.
    """

    activo: str
    centros: list[CentroOut]
    # El centro que se pidió ya no vale y se ha vuelto al propio. La pantalla
    # borra el guardado y lo dice; nunca se cae hacia atrás en silencio.
    centro_rechazado: bool = False
    # El centro al que se iba está EN PAUSA por el plan y se ha abierto otro.
    # Va aparte de `centro_rechazado` porque el texto es distinto: aquí el
    # centro sigue siendo suyo y no se ha perdido nada.
    centro_en_pausa: bool = False
    # LA TITULAR HA ELEGIDO cuál se queda (0088). Con centros en pausa y sin
    # elección, se quedó el más antiguo sin que ella lo dijera: la pantalla se
    # lo cuenta y le da el camino para cambiarlo.
    sigue_elegido: bool = False
    # Hasta cuándo no puede volver a cambiarlo (una vez cada 30 días). Vacío =
    # puede ya. Solo con centros en pausa.
    cambio_de_centro_desde: str | None = None


class ElegirCentroIn(BaseModel):
    """Qué centro se queda activo cuando el plan no los cubre todos (0088)."""

    model_config = ConfigDict(extra="forbid")

    centro_que_sigue: str = Field(min_length=1, max_length=64)


class ElegirCentroOut(BaseModel):
    centro_que_sigue: str
    # El día a partir del cual podrá volver a cambiarlo.
    se_puede_cambiar_desde: str | None = None


class CrearCentroIn(BaseModel):
    """Lo mínimo para que un centro pueda atender a alguien.

    TELÉFONO Y DIRECCIÓN SON OBLIGATORIOS, y no es burocracia. Un centro creado
    solo con nombre nace con su página pública diciéndole a una clienta
    «escríbenos por otro medio o pásate por el salón» sin decir por qué medio ni
    adónde; y su bot, cuando no puede ayudar, tampoco tiene nada que darle. Pasó
    con San Sebastián: nombre, y los tres campos de contacto en null.

    La LONGITUD se valida aquí; que el teléfono sea marcable de verdad lo decide
    `core/telefonos.revisar_telefono` en el router, porque hace falta saber de
    qué país es la cuenta y eso está en la base."""

    model_config = ConfigDict(extra="forbid")

    nombre_comercial: str = Field(min_length=2, max_length=200)
    # `min_length` y no solo «no vacío»: una dirección de tres letras no lleva a
    # nadie a ninguna parte, y el caso de uso es una clienta que no ha pisado
    # nunca el local y llega por un QR de Instagram.
    direccion: str = Field(min_length=6, max_length=250)
    telefono: str = Field(min_length=6, max_length=30)
    # De qué centro copiar el catálogo. UNA VEZ: a partir de ahí cada uno lleva
    # sus precios. `None` = empezar con el catálogo vacío.
    copiar_servicios_de: str | None = None


class CitasEnPausaOut(BaseModel):
    """Lo que sigue pendiente en los centros que el plan ha dejado fuera.

    `total` es EXACTO —sale de un recuento, no de contar las filas devueltas—, y
    `citas` viene recortada: son las más próximas, que son a las que hay que
    llamar antes. `se_pintan_como_mucho` deja que la pantalla diga «y 120 más»
    sin tener que adivinar dónde estaba el corte.
    """

    total: int = 0
    # La cita MÁS LEJANA. Es la que dice hasta cuándo dura esto.
    ultima: str | None = None
    citas: list[dict] = Field(default_factory=list)
    se_pintan_como_mucho: int = 0
