"""Lo que la bandeja de conversaciones enseña y lo que acepta.

DOS RELOJES, y conviene no confundirlos nunca al leer esto:

  · `ultimo_mensaje_cliente_at` → `ventana_expira_at`. Es la ventana de 24 h de
    Meta: pasada, la Cloud API ya no acepta texto libre hacia ese número y el
    salón NO puede contestar aunque quiera. Es un límite de fuera.
  · `handoff_at` → cuánto lleva esperando esta persona. Es lo que ordena la cola
    de la bandeja. Es un dato nuestro.

`ventana_expira_at` viaja calculado desde el servidor y no se deja al frontend:
es la misma cuenta que hace el endpoint de responder para aceptar o rechazar el
mensaje, y dos cuentas separadas acabarían discrepando —la pantalla dejando
escribir un mensaje que el servidor va a rechazar—.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class ConversacionResumen(BaseModel):
    """Una fila de la bandeja."""

    id: str
    telefono: str
    # Del CRM si el número está fichado; si no, el del perfil de WhatsApp; si
    # tampoco, nada y se enseña el número. Un salón reconoce a su clienta por el
    # nombre, no por un +34.
    nombre: str | None = None

    atencion: str
    handoff_at: datetime | None = None
    handoff_motivo: str | None = None
    atendida_por: str | None = None
    resuelta_at: datetime | None = None
    expirada_at: datetime | None = None

    ultimo_mensaje_cliente_at: datetime | None = None
    ventana_expira_at: datetime | None = None
    updated_at: datetime | None = None

    # Para no tener que abrir la conversación solo para saber de qué va.
    ultimo_mensaje: str | None = None
    ultimo_rol: str | None = None


class MensajeOut(BaseModel):
    """Un mensaje del hilo, ya listo para pintar.

    `rol` viaja tal cual está en la base de datos (`user`, `model`, `agente`,
    `system`, `tool`): la bandeja tiene que poder enseñar quién dijo cada cosa,
    y ahí la diferencia entre el bot y una compañera es justamente lo que se
    quiere ver.
    """

    id: str
    rol: str
    contenido: str
    created_at: datetime | None = None


class ConversacionDetalle(ConversacionResumen):
    mensajes: list[MensajeOut] = Field(default_factory=list)


class RespuestaIn(BaseModel):
    """Lo que escribe una persona del salón desde la bandeja."""

    # 4096 es el tope de la Cloud API para un mensaje de texto. Se corta aquí y
    # no en Meta: un 400 de Google al final del camino le diría al agente que
    # «algo ha fallado» después de haber escrito el mensaje entero.
    texto: str = Field(min_length=1, max_length=4096)

    @field_validator("texto")
    @classmethod
    def _no_vacio(cls, v: str) -> str:
        limpio = v.strip()
        if not limpio:
            raise ValueError("El mensaje no puede estar vacío.")
        return limpio


class ResumenBandeja(BaseModel):
    """Lo que necesita la campana del header, y nada más.

    Es lo único que se consulta en bucle (cada minuto, en cada pestaña abierta
    de cada salón), así que devuelve números y no filas: la lista completa se
    pide una vez, al entrar en la bandeja.
    """

    pendientes: int
    # De las pendientes, cuántas tienen la ventana de Meta ya cerrada o a punto
    # (menos de una hora). Es la diferencia entre «hay trabajo» y «hay trabajo
    # que se va a perder».
    urgentes: int


class ProblemaDeEnvio(BaseModel):
    """Meta rechaza los envíos de este número (0085). En castellano llano."""

    desde: str
    que_pasa: str
    que_hacer: str
    codigo: str | None = None
    # Lo que dijo Meta, tal cual: para soporte.
    detalle: str | None = None
    # Cuántos envíos no han salido desde entonces.
    envios_fallidos: int = 0


class NumeroConectado(BaseModel):
    """Un número de WhatsApp del salón. Puede haber varios: la 0024 admite dos
    sedes con dos números, y esconder el segundo dejaría a un salón creyendo que
    tiene el bot entero funcionando cuando le falta media casa."""

    # El id de la fila: con él se desconecta ESE número (0085).
    id: str | None = None
    # Nulo cuando Meta no lo ha mandado todavía. Se enseña el hueco en la
    # pantalla en vez de fabricar un número que nadie ha confirmado.
    numero: str | None = None
    activo: bool
    # ¿Lo conectó el salón por el camino nuevo (con su propio token)? Los de
    # antes envían con el token global de respaldo (0085).
    propio: bool = False
    problema: ProblemaDeEnvio | None = None


class EstadoAsistente(BaseModel):
    """Lo que la pantalla «Asistente IA» necesita para no mentir.

    CUATRO ESTADOS (0085): `sin_conectar`, `atendiendo`, `apagado` y
    `con_problema`. El último manda sobre los demás: un salón con un número que
    Meta rechaza no está «atendiendo» aunque el interruptor esté encendido.
    `conectado` y `activo` se quedan por compatibilidad con la pantalla de antes.
    """

    estado: str = "sin_conectar"
    conectado: bool
    activo: bool
    numeros: list[NumeroConectado] = []
    # ¿Se puede conectar desde aquí? Falso si falta la configuración de Meta.
    puede_conectar: bool = False


class DatosDeMeta(BaseModel):
    """Lo que el botón necesita para abrir el diálogo de Meta. Son públicos."""

    disponible: bool
    app_id: str | None = None
    config_id: str | None = None
    version: str


class ConectarIn(BaseModel):
    """Lo que Meta le dio al navegador al terminar el diálogo."""

    code: str = Field(min_length=1, max_length=2000)
    waba_id: str = Field(min_length=1, max_length=40, pattern=r"^[0-9]+$")
    phone_number_id: str = Field(min_length=1, max_length=40, pattern=r"^[0-9]+$")
    coexistencia: bool = False


class DesconectarIn(BaseModel):
    numero_id: str = Field(min_length=1, max_length=60)
