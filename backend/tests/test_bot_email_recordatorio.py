"""El bot ofrece el recordatorio por correo, y lo hace sin estorbar la cita.

LO QUE SE FIJA AQUÍ, por orden de gravedad si se rompiera:

  1. QUE LA CITA NO DEPENDA DE ESTO. El ofrecimiento va después de crear la
     cita y no la puede tocar: ni retrasarla, ni condicionarla, ni tumbarla si
     falla. Es un extra, y un extra que se lleva por delante lo que la persona
     vino a hacer no es un extra.
  2. QUE NO SE PREGUNTE A QUIEN NO TOCA. Salón con los recordatorios apagados,
     ficha que ya tiene correo, alguien que ya dijo que no, alguien de baja.
  3. QUE NO SE INSISTA. Un correo mal escrito se pide una vez más; a la segunda
     se deja estar. Y quien dice que no, no se le vuelve a preguntar nunca.
  4. QUE EL SERVIDOR VALIDE. El correo no lo da por bueno el modelo: lo da por
     bueno `core/email_valido.py`, el mismo criterio que el widget.
  5. QUE NO SE PISE EL CORREO DEL CRM. La condición va dentro del UPDATE, y el
     doble la respeta —si no, este test no probaría nada—.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tests.zona_de_prueba import ZONA_SALON
from app.core import bot_herramientas
from app.core.bot_herramientas import ContextoBot, _ejecutar_reserva, ejecutar
from app.db.postgrest_client import PostgrestError

SALON = "11111111-1111-1111-1111-111111111111"
SESION = "5e551000-0000-0000-0000-000000000001"
SERVICIO = "22222222-2222-2222-2222-222222222222"
EMPLEADO = "33333333-3333-3333-3333-333333333333"
CLIENTE = "a0000000-0000-0000-0000-00000000000a"
TELEFONO = "+34600112233"

INICIO = (datetime.now(timezone.utc) + timedelta(days=2)).replace(
    hour=10, minute=0, second=0, microsecond=0
)


@pytest.fixture(autouse=True)
def _la_hora_de_este_test() -> None:
    """LA HORA SE TOMA AL EMPEZAR CADA TEST, no al importar el fichero. Tomada al
    importar, una tirada que cruzara la medianoche dejaba el día de la cita un día
    más cerca de lo que el test cree (15-10-2026)."""
    global INICIO
    INICIO = (datetime.now(timezone.utc) + timedelta(days=2)).replace(
        hour=10, minute=0, second=0, microsecond=0
    )


# --------------------------------------------------------------------- fake


class FakeDB:
    """Doble de PostgREST con las DOS condiciones que aquí importan.

    Un doble que no respetara los filtros de la consulta sería más permisivo
    que Postgres, y entonces los dos tests que más valen —que no se pisa el
    correo del CRM, y que un «no» se apunta en su columna— pasarían igual
    aunque el código dejara de mandar la condición.
    """

    def __init__(self, *, salon: dict | None = None, clientes=None, mensajes=None):
        self.salones = [salon] if salon else []
        self.clientes = [dict(c) for c in (clientes or [])]
        self.mensajes = list(mensajes or [])
        self.escrituras: list[tuple[str, dict]] = []
        self.actualizaciones: list[tuple[str, dict, dict]] = []
        self.revienta = set()

    async def select(self, table, params=None):
        params = params or {}
        if table in self.revienta:
            raise PostgrestError(500, f"PostgREST caído leyendo {table}")
        if table == "salons":
            return self.salones
        if table == "clients":
            # LOS FILTROS SE APLICAN DE VERDAD. Devolver la lista entera hacía
            # que `_buscar_cliente` cogiera la PRIMERA ficha de la tabla, fuera
            # de quien fuera: el test de «el correo va a la ficha del teléfono
            # que escribe» pasaba por accidente y habría seguido pasando con el
            # filtro por teléfono quitado del código.
            return [c for c in self.clientes if _pasa_los_filtros(c, params)]
        if table == "services":
            return [{"id": SERVICIO, "nombre": "Corte", "precio": 20, "duracion_min": 30}]
        if table == "employees":
            return [{"id": EMPLEADO, "nombre": "Lucho"}]
        if table == "bot_messages":
            # El filtro que usa `_intentos_de_email`. Se aplica de verdad: si el
            # código dejara de mandarlo, este doble devolvería otra cosa.
            patron = (params.get("content") or "").removeprefix("like.").rstrip("*")
            return [m for m in self.mensajes if m.get("content", "").startswith(patron)]
        return []

    async def insert(self, table, body):
        self.escrituras.append((table, body))
        return {"id": f"{table}-nueva", **body}

    async def update(self, table, params, body):
        self.actualizaciones.append((table, dict(params), dict(body)))
        if table != "clients":
            return [body]

        tocadas = []
        for fila in self.clientes:
            if fila["id"] != params.get("id", "").removeprefix("eq."):
                continue
            # LA CONDICIÓN, aplicada solo si la CONSULTA la pide. Es la parte
            # que hace que el test de «no se pisa» signifique algo.
            if params.get("email") == "is.null" and fila.get("email"):
                continue
            fila.update(body)
            tocadas.append(fila)
        return tocadas


def _pasa_los_filtros(fila: dict, params: dict) -> bool:
    """`id=eq.X` y el `or=(telefono_digitos.eq.A,…)` de `buscar_por_telefono`."""
    for campo, filtro in params.items():
        if campo in ("select", "limit", "order"):
            continue
        if campo == "or":
            alternativas = filtro.strip("()").split(",")
            if not any(
                str(fila.get(t.split(".")[0])) == t.split(".", 2)[2] for t in alternativas
            ):
                return False
            continue
        op, _, esperado = filtro.partition(".")
        if op == "eq" and str(fila.get(campo)) != esperado:
            return False
        if op == "is" and esperado == "null" and fila.get(campo) is not None:
            return False
    return True


class HuecoFalso:
    def __init__(self):
        self.inicio = INICIO
        self.fin = INICIO + timedelta(minutes=30)
        self.empleados = ()


@pytest.fixture()
def sin_carreras(monkeypatch):
    """El hueco sigue libre. La re-validación tiene sus propios tests."""

    async def _hueco_concreto(db, salon_id, **kw):
        return HuecoFalso()

    monkeypatch.setattr(bot_herramientas, "hueco_concreto", _hueco_concreto)

    async def _olvidar(ctx):
        return None

    monkeypatch.setattr(bot_herramientas, "_olvidar_propuesta", _olvidar)


def _salon(activo=True, horas=24) -> dict:
    return {"id": SALON, "recordatorios_email_activo": activo, "recordatorio_horas": horas}


def _ficha(**kw) -> dict:
    base = {
        "id": CLIENTE,
        # El salón, que no sobra: `buscar_por_telefono` filtra por él, y sin
        # esto el doble no devolvía ninguna ficha.
        "salon_id": SALON,
        "nombre": "Carlos",
        "telefono": TELEFONO,
        "telefono_digitos": "34600112233",
        "email": None,
        "recordatorios_email_rechazado_at": None,
        "recordatorios_baja_at": None,
    }
    base.update(kw)
    return base


def _propuesta() -> dict:
    return {
        "service_id": SERVICIO,
        "employee_id": EMPLEADO,
        "inicio": INICIO.isoformat(),
        "fin": (INICIO + timedelta(minutes=30)).isoformat(),
        "nombre_cliente": "Carlos",
        "client_id": CLIENTE,
        "servicio": "Corte",
        "profesional": "Lucho",
        "precio": 20,
        "duracion_min": 30,
    }


def _ctx(db: FakeDB) -> ContextoBot:
    return ContextoBot(zona=ZONA_SALON, 
        db=db, salon_id=SALON, session_id=SESION, telefono_cliente=TELEFONO
    )


# ═══════════════════════════════════════════ cuándo se ofrece y cuándo no


@pytest.mark.anyio
async def test_se_ofrece_al_confirmar_y_nunca_antes(sin_carreras):
    """EL CAMINO NORMAL: la cita se crea y el ofrecimiento viaja CON ella.

    Que vaya en el resultado de la reserva y no en un paso propio es lo que
    garantiza el «nunca antes»: el modelo no se entera de que hay algo que
    ofrecer hasta que la cita ya está insertada.
    """
    db = FakeDB(salon=_salon(), clientes=[_ficha()])
    resultado = await _ejecutar_reserva(_ctx(db), _propuesta())

    assert resultado["ok"] and resultado["cita_creada"]
    ofrecimiento = resultado["ofrecer_recordatorio"]
    assert ofrecimiento["cuando"] == "el día antes"
    # El guion, no un texto para repetir: la conversación puede ir en cualquier
    # idioma y una frase nuestra en español dentro de una en inglés canta.
    assert "con tus palabras" in ofrecimiento["guion"].lower()
    assert "opcional" in ofrecimiento["guion"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("caso", "salon", "ficha"),
    [
        ("el salón lo tiene apagado", _salon(activo=False), _ficha()),
        ("la ficha ya tiene correo", _salon(), _ficha(email="carlos@correo.es")),
        ("ya dijo que no una vez", _salon(), _ficha(recordatorios_email_rechazado_at="2026-01-01T00:00:00Z")),
        ("se dio de baja", _salon(), _ficha(recordatorios_baja_at="2026-01-01T00:00:00Z")),
    ],
)
async def test_a_quien_no_toca_no_se_le_pregunta(sin_carreras, caso, salon, ficha):
    """Y LA CITA SE CREA IGUAL, que es la otra mitad de cada caso."""
    db = FakeDB(salon=salon, clientes=[ficha])
    resultado = await _ejecutar_reserva(_ctx(db), _propuesta())

    assert resultado["ok"], caso
    assert db.escrituras[-1][0] == "appointments", caso
    assert "ofrecer_recordatorio" not in resultado, caso


@pytest.mark.anyio
@pytest.mark.parametrize("tabla_caida", ["salons", "clients"])
async def test_si_falla_mirarlo_la_cita_SIGUE_CREADA(sin_carreras, tabla_caida):
    """LA AFIRMACIÓN CENTRAL. La cita ya está insertada cuando esto se mira.

    Sin la red, un PostgREST caído aquí saldría por el `except` de `ejecutar()`
    como `no_disponible` y el bot le diría al cliente que no ha podido — con la
    cita hecha y el hueco ocupado.

    LAS DOS TABLAS Y NO UNA: son dos redes distintas y probar solo `salons`
    dejaba pasar un mutante. El fallo de `salons` lo recoge el `except` de
    `_salon_con_recordatorios`; el de `clients` tiene que llegar hasta el de
    `_ofrecimiento_de_recordatorio`, que es el que cubre la decisión entera.
    """
    db = FakeDB(salon=_salon(), clientes=[_ficha()])
    db.revienta.add(tabla_caida)
    resultado = await _ejecutar_reserva(_ctx(db), _propuesta())

    assert resultado["ok"] and resultado["cita_creada"]
    assert "ofrecer_recordatorio" not in resultado
    assert db.escrituras[-1][0] == "appointments"


@pytest.mark.anyio
async def test_un_dato_raro_del_salon_tampoco_tumba_la_cita(sin_carreras):
    """La red no es «por si falla una consulta», es por si falla CUALQUIER cosa.

    `recordatorio_horas` no puede ser basura viniendo de la columna —es `int not
    null` con su CHECK—, y aun así: la promesa de la docstring es que esto no
    lanza nunca, y una promesa que solo vale para los fallos que se anticiparon
    no es la promesa que hace falta cuando no hay nadie mirando.
    """
    db = FakeDB(salon={"id": SALON, "recordatorios_email_activo": True,
                       "recordatorio_horas": "el día antes"}, clientes=[_ficha()])
    resultado = await _ejecutar_reserva(_ctx(db), _propuesta())

    assert resultado["ok"] and resultado["cita_creada"]
    assert "ofrecer_recordatorio" not in resultado


@pytest.mark.anyio
async def test_el_guion_promete_lo_que_el_salon_va_a_mandar(sin_carreras):
    """4 horas de antelación no son «el día antes». Sale de la columna."""
    db = FakeDB(salon=_salon(horas=4), clientes=[_ficha()])
    resultado = await _ejecutar_reserva(_ctx(db), _propuesta())

    assert resultado["ofrecer_recordatorio"]["cuando"] == "unas 4 horas antes"


# ═══════════════════════════════════════════════════ guardar el correo


@pytest.mark.anyio
async def test_un_correo_valido_se_guarda_normalizado():
    db = FakeDB(salon=_salon(), clientes=[_ficha()])
    r = await ejecutar(_ctx(db), "guardar_email_cliente", {"email": "  Carlos@Correo.ES "})

    assert r == {"ok": True, "guardado": True}
    # Fuera los espacios del teclado del móvil y el dominio en minúsculas; la
    # parte de delante de la arroba, intacta.
    assert db.clientes[0]["email"] == "Carlos@correo.es"


@pytest.mark.anyio
async def test_no_se_pisa_el_correo_que_ya_tenia_el_CRM():
    """Lo puso el salón. Que una conversación de WhatsApp lo pudiera cambiar
    sería dejar que quien tenga ese teléfono redirija adónde escribe el salón."""
    db = FakeDB(salon=_salon(), clientes=[_ficha(email="el.bueno@correo.es")])
    r = await ejecutar(_ctx(db), "guardar_email_cliente", {"email": "otro@correo.es"})

    assert r == {"ok": True, "guardado": False}
    assert db.clientes[0]["email"] == "el.bueno@correo.es"
    # LA CONDICIÓN VIAJA EN EL UPDATE, no se comprueba antes: entre mirar y
    # escribir cabe otra petición entera.
    _, params, _ = db.actualizaciones[-1]
    assert params["email"] == "is.null"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "escrito", ["carlos@correo", "carlos arroba correo punto es", "", "carlos@.es", "no"]
)
async def test_lo_que_no_es_un_correo_no_llega_al_CRM(escrito):
    """El modelo redacta; quien decide si esto es un correo es el servidor."""
    db = FakeDB(salon=_salon(), clientes=[_ficha()])
    r = await ejecutar(_ctx(db), "guardar_email_cliente", {"email": escrito})

    assert r["ok"] is False and r["error"] == "email_invalido"
    assert db.clientes[0]["email"] is None
    assert db.actualizaciones == []


@pytest.mark.anyio
async def test_se_pide_otra_vez_UNA_vez_y_no_mas():
    """«No insistas» es una regla sobre lo que le pasa a una persona, así que no
    puede depender de que el modelo se acuerde: se cuenta en el servidor."""
    db = FakeDB(salon=_salon(), clientes=[_ficha()])

    primera = await ejecutar(_ctx(db), "guardar_email_cliente", {"email": "mal"})
    assert primera["reintentar"] is True

    # El bucle del webhook apunta cada llamada; el contador sale de ahí.
    db.mensajes.append({"content": "guardar_email_cliente({...}) → email_invalido"})
    segunda = await ejecutar(_ctx(db), "guardar_email_cliente", {"email": "peor"})
    assert segunda["reintentar"] is False


@pytest.mark.anyio
async def test_sin_poder_contar_los_intentos_se_calla():
    """Si no se sabe cuántas van, callarse es el lado seguro: como mucho se
    pierde un correo; al otro lado está perseguir a quien ya lo intentó."""
    db = FakeDB(salon=_salon(), clientes=[_ficha()])
    db.revienta.add("bot_messages")
    r = await ejecutar(_ctx(db), "guardar_email_cliente", {"email": "mal"})

    assert r["reintentar"] is False


@pytest.mark.anyio
async def test_con_los_recordatorios_APAGADOS_no_se_recoge_ningun_correo():
    """El prompt le pide al modelo que no lo saque por su cuenta (regla 10),
    pero una regla del prompt es una petición. Esto es una negativa."""
    db = FakeDB(salon=_salon(activo=False), clientes=[_ficha()])
    r = await ejecutar(_ctx(db), "guardar_email_cliente", {"email": "carlos@correo.es"})

    assert r == {"ok": False, "error": "no_procede"}
    assert db.clientes[0]["email"] is None
    assert db.actualizaciones == []


@pytest.mark.anyio
async def test_si_no_se_puede_leer_el_interruptor_NO_se_recoge_el_correo():
    """Y se contesta «no procede», no «ahora no puedo». La diferencia importa.

    `no_procede` le dice al modelo que lo deje estar; `no_disponible` le invita
    a volver a intentarlo, que es justo lo que no queremos por un dato opcional.
    Y el fondo es el mismo en los dos casos: sin saber si el salón quiere
    correos, no se guarda ninguno.
    """
    db = FakeDB(salon=_salon(), clientes=[_ficha()])
    db.revienta.add("salons")
    r = await ejecutar(_ctx(db), "guardar_email_cliente", {"email": "carlos@correo.es"})

    assert r == {"ok": False, "error": "no_procede"}
    assert db.clientes[0]["email"] is None


# ═══════════════════════════════════════════════════════════ el «no»


@pytest.mark.anyio
async def test_un_no_se_apunta_en_su_columna_y_NO_es_una_baja():
    """Las dos columnas dicen cosas distintas y por eso son dos.

    Un «no, gracias» de pasada por WhatsApp no puede valer como una baja que esa
    persona no ha pedido: el día que el salón le apuntara el correo a mano, no
    le llegaría nada y nadie sabría por qué.
    """
    db = FakeDB(salon=_salon(), clientes=[_ficha(email="carlos@correo.es")])
    r = await ejecutar(_ctx(db), "no_quiere_recordatorios", {})

    assert r == {"ok": True, "apuntado": True}
    _, _, body = db.actualizaciones[-1]
    assert list(body) == ["recordatorios_email_rechazado_at"]
    assert db.clientes[0]["recordatorios_baja_at"] is None
    # Y NO SE BORRA EL CORREO: es como el salón le escribe para todo lo demás.
    assert db.clientes[0]["email"] == "carlos@correo.es"


@pytest.mark.anyio
async def test_despues_de_un_no_no_se_vuelve_a_ofrecer(sin_carreras):
    """El bucle entero: dice que no, y en la SIGUIENTE reserva no se le pregunta."""
    db = FakeDB(salon=_salon(), clientes=[_ficha()])
    await ejecutar(_ctx(db), "no_quiere_recordatorios", {})

    resultado = await _ejecutar_reserva(_ctx(db), _propuesta())
    assert resultado["ok"]
    assert "ofrecer_recordatorio" not in resultado


@pytest.mark.anyio
async def test_con_los_recordatorios_APAGADOS_no_se_apunta_ningun_no():
    """Nunca se le debió preguntar, así que no hay ningún «no» que apuntar.

    Escribirlo sería peor que no hacer nada: dejaría a esa persona marcada, y el
    día que el salón encendiera los recordatorios ya no se le ofrecería nunca.
    """
    db = FakeDB(salon=_salon(activo=False), clientes=[_ficha()])
    r = await ejecutar(_ctx(db), "no_quiere_recordatorios", {})

    assert r == {"ok": False, "error": "no_procede"}
    assert db.actualizaciones == []


# ═════════════════════════════════════════════════ el correo va a SU ficha


@pytest.mark.anyio
async def test_el_correo_va_a_la_ficha_del_TELEFONO_que_escribe():
    """La misma defensa que las citas: el modelo no manda ningún identificador
    de cliente, así que no tiene forma de nombrar la ficha de otra persona."""
    otra = _ficha(id="otra-ficha", nombre="Ajena", telefono="+34699999999",
                  telefono_digitos="34699999999")
    db = FakeDB(salon=_salon(), clientes=[otra, _ficha()])
    await ejecutar(_ctx(db), "guardar_email_cliente", {"email": "carlos@correo.es"})

    tabla, params, _ = db.actualizaciones[-1]
    assert tabla == "clients"
    assert params["id"] == f"eq.{CLIENTE}"
    assert otra["email"] is None
