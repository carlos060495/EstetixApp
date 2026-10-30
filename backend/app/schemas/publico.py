"""Lo que ve la calle. Esquemas de la página pública de reservas.

ESTE FICHERO ES LA FRONTERA DE SEGURIDAD, y conviene entender por qué antes de
tocarlo.

La RLS aísla un salón de otro y eso sigue siendo imprescindible. Pero DENTRO de
un salón no protege nada: la política de `clients` es `salon_id =
current_salon_id()`, sin restricción por cliente, y la de `appointments` igual.
El widget entra con el token de un usuario del salón —como el bot—, así que a
efectos de base de datos puede leer el CRM entero y la agenda con sus notas.

Lo único que impide que eso salga a internet es esta lista de campos.

DE AHÍ LAS DOS REGLAS:

  1. NO SE REUTILIZA NINGÚN ESQUEMA INTERNO. `ServiceOut` arrastra
     `visible_online` y `category_id`; el de empleados lleva teléfono, email y
     comisiones. Heredar de ellos publicaría cada campo nuevo que alguien les
     añada, sin que nadie lo decida.
  2. NO SE PASA NADA POR REFERENCIA. Aunque `huecos_del_dia` hoy devuelva justo
     lo que hace falta, se copia campo a campo: el día que ese diccionario
     interno crezca, no crecerá también la respuesta pública.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, ValidationInfo


class SalonPublico(BaseModel):
    """La cabecera de la página: quién es este salón.

    Solo lo que hace falta para que el cliente sepa que está en el sitio
    correcto y sepa llegar. Ni CIF, ni email, ni plan, ni nada de suscripción.
    """

    nombre: str
    logo_url: str | None = None
    color: str = "#C9A84C"
    direccion: str | None = None
    telefono: str | None = None
    # La zona del centro (0060). La página la usa para decir «a las 10:00» en
    # la hora DEL SALÓN aunque la clienta abra el enlace desde otro país.
    zona_horaria: str = "Europe/Madrid"
    # Interruptor del hueco de recordatorios: hoy SIEMPRE False. Está desde el
    # principio para que encender la feature no cambie el contrato — ver el
    # comentario de `ReservaIn.email`.
    recordatorios_email: bool = False
    # NO SE PUEDE RESERVAR POR AQUÍ AHORA MISMO. La página se abre igual: este
    # salón está abierto, tiene un QR en la puerta y coge clientas por teléfono.
    #
    # A LA CLIENTA NO SE LE CUENTA NUESTRA FACTURACIÓN: ni plan, ni suscripción,
    # ni pausa. Un booleano y un texto que solo dice lo que le sirve. El motivo
    # se queda en el log del servidor, donde le importa a alguien.
    reservas_cerradas: bool = False


class ServicioPublico(BaseModel):
    """Un servicio reservable. Solo los `visible_online`, que es la casilla que
    el salón ya gestiona desde Servicios."""

    id: str
    nombre: str
    precio: float | None = None
    duracion_min: int


class ProfesionalPublico(BaseModel):
    """Quién puede atender. `id` y `nombre`, y nada más: ni foto, ni puesto, ni
    teléfono, ni si está de baja."""

    id: str
    nombre: str


class HuecoPublico(BaseModel):
    inicio: str
    fin: str
    hora: str
    # Quiénes podrían atenderlo. Va ORDENADO por carga del día: el primero es el
    # que menos citas tiene, y es el que se asigna cuando el cliente elige
    # «cualquiera disponible».
    empleados: list[ProfesionalPublico] = []


class DisponibilidadPublica(BaseModel):
    fecha: str
    # `cerrado` y una lista vacía NO son lo mismo: «hoy no abrimos» y «hoy está
    # todo cogido» llevan a decidir cosas distintas, y la pantalla dice una u
    # otra. `motivo` lo redacta el motor de horario.
    cerrado: bool = False
    motivo: str | None = None
    huecos: list[HuecoPublico] = []


class PaginaPublica(BaseModel):
    """Lo que se pide de una sola vez al abrir la página, para que en un móvil
    con datos flojos no sean tres viajes antes de ver nada."""

    salon: SalonPublico
    servicios: list[ServicioPublico] = []


class ReservaIn(BaseModel):
    """Lo ÚNICO que se acepta del navegador.

    LO QUE NO ESTÁ AQUÍ ES LA MITAD DEL DISEÑO: no hay `salon_id` (sale del slug
    de la ruta), ni `estado`, ni `canal`, ni `client_id`, ni `notas`, ni
    `precio`. Los pone el servidor. Que no se puedan mandar desde fuera es lo
    que impide escribir en el salón de al lado o colar una cita ya cobrada.
    """

    service_id: str
    # Nulo = «cualquiera disponible». El servidor coge entonces el primero del
    # hueco, que viene ordenado por carga.
    employee_id: str | None = None
    inicio: str
    nombre: str = Field(min_length=2, max_length=80)
    telefono: str = Field(min_length=6, max_length=30)

    # EL CORREO DEL RECORDATORIO. Opcional siempre, y solo se guarda si el
    # salón tiene los recordatorios encendidos y la ficha no traía ya uno.
    #
    # El `max_length` de aquí es lo que cabe en la columna, NO la validación:
    # que sea un correo de verdad lo decide `core/email_valido.py`, el mismo
    # criterio que usa el bot. Se comprueba allí y no aquí a propósito — un 422
    # tumbaría la reserva entera por una errata en un campo opcional.
    email: str | None = Field(default=None, max_length=120)

    @field_validator("nombre", "telefono")
    @classmethod
    def _sin_espacios_de_sobra(cls, v: str, info: ValidationInfo) -> str:
        limpio = v.strip()
        if not limpio:
            raise ValueError("Escribe tu nombre." if info.field_name == "nombre" else "Escribe tu móvil.")
        return limpio


class ReservaOut(BaseModel):
    """La confirmación.

    REPITE SOLO LO QUE LA PERSONA ESCRIBIÓ, más lo que ella misma eligió. NUNCA
    un dato leído del CRM.

    No es una cuestión de estilo: `asegurar_cliente` reutiliza la ficha
    existente cuando el teléfono ya está en el CRM. Si esta respuesta devolviera
    el nombre GUARDADO en vez del tecleado, el widget se convertiría en un
    buscador de teléfono a nombre sobre la clientela entera del salón: bastaría
    con ir probando números.
    """

    id: str
    servicio: str
    profesional: str | None = None
    inicio: str
    fin: str
    precio: float | None = None
    # El nombre TECLEADO en el formulario. Nunca `clients.nombre`.
    nombre: str
