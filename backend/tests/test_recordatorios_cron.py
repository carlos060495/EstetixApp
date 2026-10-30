"""El cron de los recordatorios: la primera cosa del proyecto que ocurre sola.

LO QUE SE FIJA AQUÍ, por orden de gravedad si se rompiera:

  1. QUE NO SE MANDE DOS VECES. El sello es un UPDATE condicional; si dejara de
     serlo, dos réplicas mandarían dos correos por la misma cita.
  2. QUE UN FALLO NO DESAPAREZCA. Sellar antes de enviar evita el duplicado a
     cambio de poder perder un envío. Ese envío perdido tiene que quedar
     ANOTADO y contable, o el hueco es invisible.
  3. QUE LA PASADA DEJE LATIDO. Un cron que no se ejecuta no genera errores;
     genera silencio. El latido es lo único que distingue «no había nada que
     hacer» de «no se ha ejecutado».
  4. QUE NO SE ESCRIBA A QUIEN NO TOCA. Cancelada, ya pasada, sin correo, de
     baja, o reservada con prisa.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tests.zona_de_prueba import ZONA_SALON
from app.core.config import Settings
from app.core.emails import EnvioFallido
from app.tareas import recordatorios
from app.tareas.recordatorios import (
    MAX_INTENTOS_ENVIO,
    TOLERANCIA_ATRASO,
    esta_atrasado,
    momento_en_que_debio_salir,
)

SALON = "11111111-1111-1111-1111-111111111111"

# EL RELOJ DE ESTE FICHERO ES FIJO: un miércoles a mediodía en el salón.
#
# Era el reloj de la pared, y desde que el cron tiene horas de silencio
# (22:00-08:00 en Madrid) trece tests de aquí solo pasaban si se ejecutaban de
# día: de noche la pasada RETIENE en vez de mandar, y todo lo que esperaba un
# correo fallaba. En una máquina en UTC, eso es de 20:00 a 06:00 UTC en
# verano: diez horas de cada día.
#
# Peor que los trece que fallaban: uno pasaba EN VACÍO. «Un fallo no clasificado
# no suelta el sello» se cumple solo con que no se intente enviar nada, y de
# noche no se intentaba — medido con una mutación que suelta el sello: de día el
# test la caza y de noche la deja pasar.
#
# Mediodía y no «de día» a secas: lejos de los dos bordes del silencio, que
# tienen sus propios tests con su propio reloj.
AHORA = datetime(2026, 9, 9, 12, 0, tzinfo=ZONA_SALON).astimezone(timezone.utc)


def _settings() -> Settings:
    return Settings(
        supabase_url="http://local",
        supabase_secret_key="k",
        supabase_jwks_url="http://local/jwks",
        database_url="postgresql://x",
        frontend_base_url="https://estetixapp.es",
        resend_api_key="re_x",
        email_from="hola@estetixapp.es",
    )


class FakeDB:
    """Doble de PostgREST. Hace de cliente de servicio y de inquilino."""

    def __init__(self, salones=None, citas=None):
        self.salones = salones if salones is not None else [_salon()]
        self.citas = citas if citas is not None else []
        self.updates: list[tuple[str, dict, dict]] = []
        self.latidos: list[dict] = []
        # Simula la condición del UPDATE: una cita ya sellada no se deja
        # reclamar otra vez. Sin esto el doble sería más permisivo que Postgres
        # y el test de idempotencia no probaría nada.
        self.selladas: set[str] = set()
        self.revienta_citas = False

    async def select_todo(self, table, params=None):
        # Los salones se leen TODOS (no los mil primeros). El doble no pagina:
        # pasa por `select` para que lo que los tests le cuelgan ahí siga valiendo.
        assert table == "salons", table
        return await self.select(table, params)

    async def select(self, table, params=None):
        if table == "salons":
            return self.salones
        if table == "appointments":
            if self.revienta_citas:
                from app.db.postgrest_client import PostgrestError

                raise PostgrestError(500, "PostgREST caído")
            # LOS FILTROS SE APLICAN DE VERDAD, y esto no es un detalle: antes
            # se devolvía `self.citas` entera pasara lo que pasara, así que el
            # doble era más permisivo que PostgREST y NINGÚN test tocaba la
            # forma de la consulta. Comprobado volviendo a poner el suelo de la
            # banda vieja: el test del agujero seguía verde. Un doble que ignora
            # el `where` convierte en verde justo el fallo que se está
            # arreglando.
            return [c for c in self.citas if self._pasa_la_consulta(c, params or {})]
        return []

    def _pasa_la_consulta(self, cita: dict, params: dict) -> bool:
        """`inicio` con sus dos topes, el sello a null y el estado."""
        inicio = datetime.fromisoformat(cita["inicio"])

        for bruto in (params.get("inicio"), params.get("and")):
            if not bruto:
                continue
            # «gt.2026-…», «gte.2026-…» o «(inicio.lte.2026-…)»
            texto = bruto.strip("()").removeprefix("inicio.")
            op, _, valor = texto.partition(".")
            limite = datetime.fromisoformat(valor)
            if op == "gt" and not inicio > limite:
                return False
            if op == "gte" and not inicio >= limite:
                return False
            if op == "lte" and not inicio <= limite:
                return False

        if params.get("recordatorio_enviado_at") == "is.null" and cita["id"] in self.selladas:
            return False

        estados = params.get("estado", "")
        if estados.startswith("in."):
            if cita.get("estado") not in estados.removeprefix("in.").strip("()").split(","):
                return False
        return True

    async def update(self, table, params, body):
        self.updates.append((table, params, body))
        # SOLTAR EL SELLO va PRIMERO. Las dos escrituras llevan la misma clave y
        # la de sellar se llevaba también las de soltar: el doble volvía a
        # sellar la cita justo cuando el código la estaba liberando, y el test
        # del reintento fallaba por culpa del doble.
        if table == "appointments" and body.get("recordatorio_enviado_at", "x") is None:
            cid = params.get("id", "").removeprefix("eq.")
            self.selladas.discard(cid)
            for c in self.citas:
                if c["id"] == cid:
                    c["recordatorio_intentos"] = body.get("recordatorio_intentos", 0)
            return [{"id": cid}]

        if table == "appointments" and "recordatorio_enviado_at" in body:
            cid = params.get("id", "").removeprefix("eq.")
            # IMITA A POSTGRES, y esto importa: la fila solo se resiste si la
            # CONSULTA pide la condición. Antes el doble la aplicaba siempre,
            # así que quitarla del código no rompía nada y el test de
            # idempotencia no probaba lo que decía probar.
            condicional = params.get("recordatorio_enviado_at") == "is.null"
            if condicional and cid in self.selladas:
                return []
            self.selladas.add(cid)
            return [{"id": cid}]

        if table == "appointments":
            cid = params.get("id", "").removeprefix("eq.")
            for c in self.citas:
                if c["id"] == cid and "recordatorio_intentos" in body:
                    c["recordatorio_intentos"] = body["recordatorio_intentos"]
        return [{"id": "x"}]

    async def upsert_many(self, table, rows, on_conflict):
        if table == "tareas_latido":
            self.latidos.extend(rows)


def _salon(**kw) -> dict:
    base = {
        "zona_horaria": "Europe/Madrid",
        "id": SALON, "nombre_comercial": "Salón Demo", "direccion": "C/ Mayor 14",
        "telefono": "944 12 34 56", "recordatorio_horas": 24,
    }
    base.update(kw)
    return base


def _cita(cid="cita-1", **kw) -> dict:
    # A 20 h y no a 24, que es exactamente el techo (`inicio <= ahora + 24 h`).
    # Con el reloj de la pared daba igual: entre fijar AHORA al importar y leer
    # la hora dentro del cron pasaban unos milisegundos, y la cita quedaba un
    # pelo por DENTRO. Con el reloj congelado habría caído justo EN el borde, y
    # veinte tests habrían pasado a depender, sin decirlo, de si la consulta usa
    # `lte` o `lt`. El borde tiene su propio test (`test_la_consulta_es_TECHO…`).
    inicio = kw.pop("inicio", AHORA + timedelta(hours=20))
    creada = kw.pop("created_at", AHORA - timedelta(days=3))
    cliente = {
        "id": "cli-1", "nombre": "María", "email": "maria@correo.test",
        "recordatorios_baja_at": None,
        "recordatorios_token": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    }
    cliente.update(kw.pop("cliente", {}))
    base = {
        "id": cid,
        "inicio": inicio.isoformat(),
        "created_at": creada.isoformat(),
        "estado": "confirmada",
        "nombre_reserva": None,
        "clients": cliente,
        "services": {"nombre": "Corte"},
        "employees": {"nombre": "Lucho"},
    }
    base.update(kw)
    return base


@pytest.fixture()
def correo(monkeypatch):
    """Resend, simulado. `fallo` hace que reviente, para el caso que importa."""
    estado = {"enviados": [], "fallo": None, "intentos": 0, "falla_hasta": 10**6}

    async def _enviar(settings, to, subject, html, clave=None):
        estado["intentos"] += 1
        # `falla_hasta` deja fallar los primeros N intentos y luego funciona:
        # es como se prueba que el reintento de verdad recupera el envío.
        if estado["fallo"] and estado["intentos"] <= estado.get("falla_hasta", 10**6):
            raise estado["fallo"]
        estado["enviados"].append({"to": to, "subject": subject, "html": html, "clave": clave})

    monkeypatch.setattr(recordatorios, "enviar_email_branded", _enviar)
    return estado


@pytest.fixture()
def reloj(monkeypatch):
    """Congela `_ahora()`. Hace falta desde que existen horas de silencio: a qué
    hora del día ocurre una pasada dejó de dar igual."""

    def _fijar(momento: datetime) -> datetime:
        monkeypatch.setattr(recordatorios, "_ahora", lambda: momento)
        return momento

    return _fijar


@pytest.fixture(autouse=True)
def _reloj_fijo_por_defecto(monkeypatch):
    """Toda pasada de este fichero ocurre en AHORA salvo que el test diga otra
    cosa con `reloj(...)`, que la pisa.

    AUTOMÁTICO Y NO PEDIDO TEST A TEST, porque el fallo era justo ese: el
    fixture `reloj` existía, su docstring decía que hacía falta, y trece tests
    no lo pedían. Un test nuevo que se olvide de pedirlo tiene que heredar un
    reloj fijo, no el de la pared.
    """
    monkeypatch.setattr(recordatorios, "_ahora", lambda: AHORA)


@pytest.fixture()
def tenant(monkeypatch):
    """La clave de servicio, con la que el cron lee cada salón desde la 0086."""
    caja = {}
    monkeypatch.setattr(recordatorios, "get_service_postgrest_client", lambda s: caja["db"])
    return caja


async def _pasada(db, tenant) -> dict:
    tenant["db"] = db
    return await recordatorios.ejecutar(_settings())


# ═══════════════════════════════════════════════ el camino normal


@pytest.mark.anyio
async def test_manda_el_recordatorio_y_deja_latido(correo, tenant):
    db = FakeDB(citas=[_cita()])
    resumen = await _pasada(db, tenant)

    assert resumen["procesados"] == 1
    assert correo["enviados"][0]["to"] == "maria@correo.test"
    assert "Salón Demo" in correo["enviados"][0]["subject"]
    # EL LATIDO: sin esto, una pasada que no ocurre es indistinguible de una que
    # no encontró nada.
    assert db.latidos[-1]["tarea"] == "recordatorios_email"
    assert db.latidos[-1]["procesados"] == 1


@pytest.mark.anyio
async def test_el_correo_dice_lo_que_tiene_que_decir(correo, tenant):
    await _pasada(FakeDB(citas=[_cita()]), tenant)
    html = correo["enviados"][0]["html"]

    for esperado in ("Corte", "Lucho", "C/ Mayor 14", "Salón Demo"):
        assert esperado in html, esperado
    # Por qué lo recibe, y cómo salirse.
    assert "porque tienes una cita" in html
    assert "/recordatorios/baja/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa" in html
    # NADA DE PROMOCIONES: se escribe a quien no ha consentido publicidad.
    assert "oferta" not in html.lower() and "descuento" not in html.lower()


@pytest.mark.anyio
async def test_saluda_con_el_nombre_de_LA_RESERVA_si_lo_hay(correo, tenant):
    """La cita puede ser para otra persona. A quien va se le llama por su nombre,
    no por el de la ficha del teléfono."""
    db = FakeDB(citas=[_cita(nombre_reserva="Mariela Luna")])
    await _pasada(db, tenant)

    assert "Mariela Luna" in correo["enviados"][0]["html"]


# ═══════════════════════════════════════════════ idempotencia


@pytest.mark.anyio
async def test_dos_pasadas_seguidas_NO_mandan_dos_correos(correo, tenant):
    """LA AFIRMACIÓN CENTRAL. El sello es un UPDATE condicional: la segunda
    pasada pide la fila, no se la lleva, y se calla."""
    db = FakeDB(citas=[_cita()])
    await _pasada(db, tenant)
    await _pasada(db, tenant)

    assert len(correo["enviados"]) == 1


@pytest.mark.anyio
async def test_se_sella_ANTES_de_enviar(correo, tenant, monkeypatch):
    """El orden importa: si se sellara DESPUÉS, un proceso muerto entre el envío
    y el sello dejaría la cita lista para que otra pasada la mandara otra vez —
    y el cliente recibiría dos correos por la misma cita."""
    orden: list[str] = []

    async def _enviar(settings, to, subject, html, clave=None):
        orden.append("enviar")

    monkeypatch.setattr(recordatorios, "enviar_email_branded", _enviar)

    db = FakeDB(citas=[_cita()])
    update_original = db.update

    async def _update(table, params, body):
        if "recordatorio_enviado_at" in body:
            orden.append("sellar")
        return await update_original(table, params, body)

    db.update = _update
    await _pasada(db, tenant)

    assert orden == ["sellar", "enviar"]


# ═══════════════════════════════════════════════ el fallo NO desaparece


@pytest.mark.anyio
async def test_un_envio_fallido_queda_ANOTADO_y_contado(correo, tenant):
    """LA CONTRAPARTIDA DE SELLAR ANTES. La cita queda sellada y nadie recibió
    nada; sin esta anotación el hueco sería invisible."""
    correo["fallo"] = RuntimeError("Resend dice que no")
    db = FakeDB(citas=[_cita()])
    resumen = await _pasada(db, tenant)

    assert resumen["fallidos"] == 1
    # El sello LIMPIA el error del intento anterior, así que hay dos updates con
    # esa clave; la anotación es la que trae texto.
    anotaciones = [b for _t, _p, b in db.updates if b.get("recordatorio_error")]
    assert len(anotaciones) == 1
    assert "RuntimeError" in anotaciones[0]["recordatorio_error"]
    # Y el latido lo cuenta, para que se vea desde el panel.
    assert db.latidos[-1]["fallidos"] == 1


@pytest.mark.anyio
async def test_un_salon_que_falla_no_impide_los_demas(correo, tenant, monkeypatch):
    """Un salón al que no se puede entrar no puede dejar sin recordatorio a los
    otros, ni impedir que la pasada deje su latido.

    ANTES NO HACÍA FALLAR A NINGUNO. Montaba dos salones sanos y comprobaba que
    había dos y que había latido: seguía en verde QUITANDO el aislamiento por
    salón del cron —medido—, así que el nombre prometía algo que no se probaba,
    a ninguna hora. Ahora el primero revienta al leer sus citas, y lo que se
    comprueba es que el segundo manda su correo igualmente.
    """
    db = FakeDB(salones=[_salon(id="salon-roto"), _salon()], citas=[_cita()])
    tenant["db"] = db
    leer = recordatorios._citas_a_avisar

    async def _citas(db_, salon, ahora):
        if salon["id"] == "salon-roto":
            from app.db.postgrest_client import PostgrestError
            raise PostgrestError(500, "PostgREST caído")
        return await leer(db_, salon, ahora)

    monkeypatch.setattr(recordatorios, "_citas_a_avisar", _citas)
    resumen = await recordatorios.ejecutar(_settings())

    assert resumen["salones"] == 2
    assert resumen["fallidos"] == 1, "el salón roto tiene que quedar contado"
    assert len(correo["enviados"]) == 1, "el salón sano se quedó sin su recordatorio"
    assert db.latidos, "la pasada tiene que dejar latido igualmente"


@pytest.mark.anyio
async def test_si_no_se_pueden_leer_los_salones_NO_se_finge_un_latido(correo, tenant):
    """Decir «pasé» cuando no se hizo nada taparía justo lo que el latido existe
    para enseñar."""
    db = FakeDB()

    async def _revienta(table, params=None):
        from app.db.postgrest_client import PostgrestError

        raise PostgrestError(500, "caído")

    db.select = _revienta
    resumen = await _pasada(db, tenant)

    assert resumen.get("error") == "salones"
    assert db.latidos == []


# ═══════════════════════════════════════════════ a quién NO se escribe


@pytest.mark.parametrize(
    "cambio,motivo",
    [
        ({"cliente": {"email": None}}, "sin_email"),
        ({"cliente": {"email": "   "}}, "sin_email"),
        ({"cliente": {"recordatorios_baja_at": AHORA.isoformat()}}, "de_baja"),
        (
            {"created_at": AHORA - timedelta(hours=1),
             "inicio": AHORA + timedelta(hours=1, minutes=30)},
            "reservada_con_prisa",
        ),
    ],
)
@pytest.mark.anyio
async def test_hay_citas_a_las_que_no_se_escribe(correo, tenant, cambio, motivo):
    db = FakeDB(citas=[_cita(**cambio)])
    resumen = await _pasada(db, tenant)

    assert correo["enviados"] == []
    assert resumen["saltadas"].get(motivo) == 1
    # Y no se sella: si mañana el cliente da su correo, la cita sigue viva.
    assert db.selladas == set()


def _params_de_la_consulta(db):
    """Deja al descubierto los parámetros con los que se pidieron las citas."""
    capturado = {}
    original = db.select

    async def _select(table, params=None):
        if table == "appointments":
            capturado.update(params or {})
        return await original(table, params)

    db.select = _select
    return capturado


@pytest.mark.anyio
async def test_la_consulta_es_TECHO_y_no_banda(correo, tenant, reloj):
    """LA FORMA DE LA CONSULTA, que es donde estaba la avería.

    El único suelo es `inicio > ahora` —una cita empezada no se recuerda— y el
    techo es `ahora + recordatorio_horas`. Ni rastro de un suelo atado a las
    horas configuradas: ese era el que dejaba fuera para siempre lo reservado
    con poca antelación.
    """
    ahora = reloj(AHORA)
    db = FakeDB(citas=[])
    capturado = _params_de_la_consulta(db)
    await _pasada(db, tenant)

    assert capturado["estado"] == "in.(pendiente,confirmada)"
    assert capturado["recordatorio_enviado_at"] == "is.null"

    # El suelo: ahora, y ni un minuto antes.
    assert capturado["inicio"].startswith("gt.")
    assert datetime.fromisoformat(capturado["inicio"].removeprefix("gt.")) == ahora

    # El techo: exactamente las horas del salón.
    hasta = datetime.fromisoformat(
        capturado["and"].removeprefix("(inicio.lte.").rstrip(")")
    )
    assert hasta == ahora + timedelta(hours=24)


@pytest.mark.anyio
async def test_el_techo_lo_pone_el_SALON(correo, tenant, reloj):
    """Un salón a 3 h mira 3 h hacia delante, no 24. Y su suelo sigue siendo
    `ahora`: con banda, un salón así habría mirado 1 h hacia ATRÁS."""
    ahora = reloj(AHORA)
    db = FakeDB(salones=[_salon(recordatorio_horas=3)], citas=[])
    capturado = _params_de_la_consulta(db)
    await _pasada(db, tenant)

    desde = datetime.fromisoformat(capturado["inicio"].removeprefix("gt."))
    hasta = datetime.fromisoformat(
        capturado["and"].removeprefix("(inicio.lte.").rstrip(")")
    )
    assert desde == ahora
    assert hasta == ahora + timedelta(hours=3)


@pytest.mark.anyio
async def test_una_cuenta_suspendida_no_manda_correos_en_nombre_de_su_centro(
    correo, tenant
):
    """La suspensión dejó de ser del centro en la 0040: es de la CUENTA.

    Y esto se comprueba por lo que PASA, no por los parámetros de la consulta.
    El filtro ya no viaja en la consulta —viene por embed y se descarta en
    Python—, así que un test que mirara los `params` estaría fijando la
    implementación en vez del comportamiento: seguiría en verde con el filtro
    puesto y el descarte borrado.
    """
    db = FakeDB(
        salones=[_salon(accounts={"suspendido": True})],
        citas=[_cita()],
    )
    resumen = await _pasada(db, tenant)

    assert correo["enviados"] == []           # nadie recibe nada
    assert resumen["procesados"] == 0
    assert resumen["salones"] == 0


@pytest.mark.anyio
async def test_y_el_control_la_misma_cita_con_la_cuenta_al_corriente_SI_sale(
    correo, tenant
):
    """Sin este control, el test de arriba pasaría también si el cron estuviera
    roto del todo y no mandara nunca nada."""
    db = FakeDB(
        salones=[_salon(accounts={"suspendido": False})],
        citas=[_cita()],
    )
    resumen = await _pasada(db, tenant)

    assert len(correo["enviados"]) == 1
    assert resumen["procesados"] == 1


@pytest.mark.parametrize("estado", ["canceled", "unpaid", "incomplete_expired"])
@pytest.mark.anyio
async def test_una_cuenta_CANCELADA_sigue_mandando_los_recordatorios_de_lo_reservado(
    correo, tenant, estado
):
    """A PROPÓSITO, y es lo contrario que con la suspensión. Decidido el
    17-09-2026: cuando una cuenta deja de pagar, el bot se calla y el widget
    deja de admitir reservas, pero la clienta que ya reservó no es la que ha
    dejado de pagar. Dejarla sin aviso sería castigar a quien no debe.

    El estado viaja en la fila tal y como lo embebería PostgREST. Si mañana
    alguien añade aquí el mismo filtro que en el widget, este test se pone rojo
    antes de que una clienta se quede sin su recordatorio.
    """
    db = FakeDB(
        salones=[_salon(accounts={"suspendido": False, "subscriptions": [{"estado": estado}]})],
        citas=[_cita()],
    )
    resumen = await _pasada(db, tenant)

    assert len(correo["enviados"]) == 1
    assert resumen["procesados"] == 1


@pytest.mark.anyio
async def test_el_cron_ya_no_pide_suspendido_a_salons(correo, tenant):
    """La columna sigue existiendo en `salons` hasta la migración que contrae, y
    el trigger la mantiene sincronizada. Justo por eso pedirla ahí seguiría
    funcionando hoy y dejaría de funcionar el día que se quite, sin que nada
    avisara. Se fija que NO se pide."""
    capturado = {}
    db = FakeDB(citas=[])
    original = db.select

    async def _select(table, params=None):
        if table == "salons":
            capturado.update(params or {})
        return await original(table, params)

    db.select = _select
    await _pasada(db, tenant)

    assert capturado["recordatorios_email_activo"] == "eq.true"
    assert "suspendido" not in capturado
    assert "accounts(suspendido,es_demo)" in capturado["select"]  # 0080: y si es una demo


# ═══════════════════════════════════ EL AGUJERO: llegar tarde a tu propia banda


def _madrid(y, m, d, hh, mm=0) -> datetime:
    """Un instante escrito en la hora del salón, devuelto en UTC."""
    return datetime(y, m, d, hh, mm, tzinfo=ZONA_SALON).astimezone(timezone.utc)


@pytest.mark.anyio
async def test_una_cita_creada_TARDE_para_su_banda_SIGUE_recibiendo_aviso(
    correo, tenant, reloj
):
    """EL FALLO DE PRODUCCIÓN, tal cual ocurrió.

    Reserva por WhatsApp el 5-sep a las 20:11 UTC para el 6-sep a las 15:00 CEST
    (13:00 UTC). Antelación: 16,8 h. Con la banda de [ahora+20h, ahora+28h] esa
    cita estaba POR DEBAJO DEL SUELO en la pasada de las 20:30 — y en todas las
    demás: entre la reserva y la cita hubo 66 pasadas y en ninguna era elegible.
    El log decía `saltadas: {}` porque no llegaba ni a la consulta.

    Con techo y sin suelo entra a la primera. Aquí se comprueba en una pasada de
    día para aislar el arreglo del silencio, que tiene sus propios tests.
    """
    inicio = _madrid(2026, 9, 6, 15, 0)
    creada = inicio - timedelta(hours=16, minutes=49)
    ahora = _madrid(2026, 9, 6, 10, 0)  # 5 h antes: muy por debajo del viejo suelo
    reloj(ahora)

    db = FakeDB(citas=[_cita(inicio=inicio, created_at=creada)])
    resumen = await _pasada(db, tenant)

    assert resumen["procesados"] == 1, "la cita que el suelo dejaba fuera para siempre"
    assert len(correo["enviados"]) == 1
    assert resumen["saltadas"] == {}


@pytest.mark.anyio
async def test_lo_que_se_deja_QUEDA_CONTADO(correo, tenant, reloj):
    """«saltadas: {}» con una cita esperando era la mitad del problema.

    Ahora todo lo que entra en el techo pasa por el filtro de producto y sale
    contado. Una cita reservada con prisa se sigue saltando —eso no cambia— pero
    la tarea YA SABE que la está dejando, que es lo que no sabía antes.
    """
    ahora = _madrid(2026, 9, 6, 10, 0)
    reloj(ahora)
    inicio = ahora + timedelta(hours=2, minutes=30)

    db = FakeDB(citas=[_cita(inicio=inicio, created_at=ahora - timedelta(minutes=5))])
    resumen = await _pasada(db, tenant)

    assert correo["enviados"] == []
    assert resumen["saltadas"] == {"reservada_con_prisa": 1}


@pytest.mark.anyio
async def test_si_el_cron_ESTUVO_CAIDO_recupera_en_vez_de_saltarse(correo, tenant, reloj):
    """LA OTRA MITAD DEL CAMBIO, y no es un detalle de robustez.

    Con banda, una caída de más de ocho horas se llevaba por delante las citas
    de ese tramo: al volver ya estaban por debajo del suelo y no volvían a
    entrar. Con techo la elegibilidad es monótona —se entra y no se sale— así
    que una caída RETRASA y no PIERDE.

    Aquí el cron lleva 30 h sin correr y quedan tres citas de ese tramo: las
    tres salen en la primera pasada al volver.
    """
    ahora = _madrid(2026, 9, 6, 12, 0)
    reloj(ahora)
    hace_una_semana = ahora - timedelta(days=7)

    citas = [
        _cita(cid=f"cita-{i}", inicio=ahora + timedelta(hours=h), created_at=hace_una_semana)
        for i, h in enumerate((3, 9, 20))
    ]
    db = FakeDB(citas=citas)
    resumen = await _pasada(db, tenant)

    assert resumen["procesados"] == 3
    assert resumen["saltadas"] == {}
    assert len(correo["enviados"]) == 3


# ═════════════════════════════════════════════════ LAS HORAS DE SILENCIO


@pytest.mark.anyio
async def test_de_madrugada_se_RETIENE_y_no_se_sella(correo, tenant, reloj):
    """A las 23:30 no sale ningún correo con el nombre del salón.

    Y NO SE SELLA, que es lo que hace que retener sea retener: la cita vuelve a
    salir en la consulta tal cual en la primera pasada de la mañana, porque la
    elegibilidad ya no depende de a qué hora se mire.
    """
    reloj(_madrid(2026, 9, 5, 23, 30))
    inicio = _madrid(2026, 9, 6, 18, 0)

    db = FakeDB(citas=[_cita(inicio=inicio, created_at=inicio - timedelta(days=3))])
    resumen = await _pasada(db, tenant)

    assert correo["enviados"] == []
    assert resumen["retenidas"] == 1
    # Ni descartada ni contada como decisión: es una espera.
    assert resumen["saltadas"] == {}
    assert db.selladas == set()


@pytest.mark.anyio
async def test_RETENER_NO_PUEDE_ACABAR_EN_DESCARTAR(correo, tenant, reloj):
    """EL CHOQUE QUE HABÍA QUE RESOLVER, y cómo se resuelve.

    Reserva a las 23:30 para las 09:00 de la mañana siguiente. El aviso se
    retiene y se suelta a las 08:00, a UNA HORA de la cita. Si el suelo se
    midiera del envío a la cita, ese aviso moriría por haber esperado — nuestra
    espera, su recordatorio.

    No muere, y no por una excepción: el suelo se mide de la RESERVA a la cita
    (9,5 h aquí), y esa resta da lo mismo se mire cuando se mire. Retener cambia
    CUÁNDO, nunca SI.
    """
    creada = _madrid(2026, 9, 5, 23, 30)
    inicio = _madrid(2026, 9, 6, 9, 0)
    cita = _cita(inicio=inicio, created_at=creada)

    # Pasada de noche: se retiene.
    reloj(creada)
    db = FakeDB(citas=[cita])
    de_noche = await _pasada(db, tenant)
    assert de_noche["retenidas"] == 1 and correo["enviados"] == []

    # Pasada de las 08:00: sale, con una hora por delante.
    reloj(_madrid(2026, 9, 6, 8, 0))
    de_dia = await _pasada(db, tenant)
    assert de_dia["procesados"] == 1, "el aviso murió por haber esperado"
    assert de_dia["saltadas"] == {}
    assert len(correo["enviados"]) == 1


@pytest.mark.anyio
async def test_lo_que_no_llega_al_amanecer_sale_DE_MADRUGADA(correo, tenant, reloj):
    """La excepción que hace honesta la regla del silencio.

    Una cita a las 02:00 no aguanta hasta las 08:00: retenerla sería perderla,
    porque a las 08:00 ya ha pasado y la consulta ni la mira. Molestar de noche
    es peor que casi nada, y «casi nada» no incluye dejar a alguien sin el aviso
    de la cita que tiene dentro de tres horas.
    """
    reloj(_madrid(2026, 9, 5, 23, 0))
    inicio = _madrid(2026, 9, 6, 2, 0)

    db = FakeDB(citas=[_cita(inicio=inicio, created_at=inicio - timedelta(days=2))])
    resumen = await _pasada(db, tenant)

    assert resumen["procesados"] == 1
    assert resumen["retenidas"] == 0
    assert len(correo["enviados"]) == 1


@pytest.mark.anyio
async def test_a_las_08_00_ya_no_hay_silencio(correo, tenant, reloj):
    """El borde exacto de la franja, por los dos lados."""
    inicio = _madrid(2026, 9, 6, 20, 0)
    creada = inicio - timedelta(days=2)

    reloj(_madrid(2026, 9, 6, 7, 59))
    db = FakeDB(citas=[_cita(inicio=inicio, created_at=creada)])
    assert (await _pasada(db, tenant))["retenidas"] == 1

    reloj(_madrid(2026, 9, 6, 8, 0))
    db2 = FakeDB(citas=[_cita(inicio=inicio, created_at=creada)])
    assert (await _pasada(db2, tenant))["procesados"] == 1


@pytest.mark.anyio
async def test_a_las_22_00_empieza_el_silencio(correo, tenant, reloj):
    inicio = _madrid(2026, 9, 7, 12, 0)
    creada = inicio - timedelta(days=2)

    reloj(_madrid(2026, 9, 6, 21, 59))
    db = FakeDB(citas=[_cita(inicio=inicio, created_at=creada)])
    assert (await _pasada(db, tenant))["procesados"] == 1

    reloj(_madrid(2026, 9, 6, 22, 0))
    db2 = FakeDB(citas=[_cita(inicio=inicio, created_at=creada)])
    assert (await _pasada(db2, tenant))["retenidas"] == 1


# ═══════════════════════════════════ EL VIGÍA: qué cuenta como «llega tarde»
#
# `atrasadas` es una ALARMA DE SALUD, no un contador de calidad de los datos. En
# funcionamiento normal tiene que ser CERO: si no lo es, algo está roto. Todo lo
# que sigue existe para que eso sea verdad, porque un indicador que nunca baja a
# cero se aprende a ignorar y el día que signifique algo nadie lo mira.


def _cita_para_atraso(**kw) -> dict:
    """Una cita con lo que mira `momento_en_que_debio_salir`, y nada más."""
    inicio = kw.pop("inicio")
    return _cita(inicio=inicio, created_at=kw.pop("created_at", inicio - timedelta(days=3)), **kw)


def test_lo_que_acaba_de_entrar_en_el_techo_NO_esta_atrasado():
    """Una cadencia de espera es el funcionamiento correcto, no un retraso."""
    ahora = _madrid(2026, 9, 6, 12, 0)
    # Entró en el techo hace 10 minutos: la próxima pasada la coge.
    inicio = ahora + timedelta(hours=24) - timedelta(minutes=10)
    cita = _cita_para_atraso(inicio=inicio)

    assert momento_en_que_debio_salir(cita, 24, ahora, ZONA_SALON) is not None
    assert esta_atrasado(cita, 24, ahora, ZONA_SALON) is False


def test_tres_pasadas_sin_hacer_nada_SI_es_un_atraso():
    ahora = _madrid(2026, 9, 6, 12, 0)
    inicio = ahora + timedelta(hours=24) - (TOLERANCIA_ATRASO + timedelta(minutes=1))
    cita = _cita_para_atraso(inicio=inicio)

    assert esta_atrasado(cita, 24, ahora, ZONA_SALON) is True


def test_el_borde_exacto_de_la_tolerancia():
    """Justo en la tolerancia todavía no; un minuto más, sí."""
    ahora = _madrid(2026, 9, 6, 12, 0)
    justo = _cita_para_atraso(inicio=ahora + timedelta(hours=24) - TOLERANCIA_ATRASO)
    pasado = _cita_para_atraso(
        inicio=ahora + timedelta(hours=24) - TOLERANCIA_ATRASO - timedelta(minutes=1)
    )

    assert esta_atrasado(justo, 24, ahora, ZONA_SALON) is False
    assert esta_atrasado(pasado, 24, ahora, ZONA_SALON) is True


def test_lo_que_todavia_NO_ha_entrado_en_el_techo_no_cuenta():
    ahora = _madrid(2026, 9, 6, 12, 0)
    cita = _cita_para_atraso(inicio=ahora + timedelta(hours=40))

    assert momento_en_que_debio_salir(cita, 24, ahora, ZONA_SALON) is None
    assert esta_atrasado(cita, 24, ahora, ZONA_SALON) is False


@pytest.mark.parametrize(
    ("caso", "cliente"),
    [
        ("sin correo", {"email": None}),
        ("de baja", {"recordatorios_baja_at": "2026-01-01T00:00:00Z"}),
    ],
)
def test_a_quien_no_le_toca_NO_cuenta_como_atrasado(caso, cliente):
    """EL RUIDO QUE HABÍA QUE QUITAR. En Salón Demo, 9 de 11 citas son de
    clientes sin correo: contarlas dejaba el vigía distinto de cero para
    siempre."""
    ahora = _madrid(2026, 9, 6, 12, 0)
    cita = _cita_para_atraso(inicio=ahora + timedelta(hours=1), cliente=cliente)

    assert momento_en_que_debio_salir(cita, 24, ahora, ZONA_SALON) is None, caso
    assert esta_atrasado(cita, 24, ahora, ZONA_SALON) is False, caso


def test_quien_RECHAZO_dar_su_correo_pero_lo_TIENE_sigue_contando():
    """LA EXCEPCIÓN DE LA LISTA, y es deliberada.

    `recordatorios_email_rechazado_at` apaga la PREGUNTA del bot, no los envíos.
    Si el salón le apuntó el correo a mano, ese recordatorio sale — así que
    puede llegar tarde, y descontarlo aquí dejaría la alarma ciega justo para
    esa persona. La regla del vigía es la de la pasada, ni más ni menos.
    """
    ahora = _madrid(2026, 9, 6, 12, 0)
    cita = _cita_para_atraso(
        inicio=ahora + timedelta(hours=1),
        cliente={"recordatorios_email_rechazado_at": "2026-01-01T00:00:00Z"},
    )

    assert esta_atrasado(cita, 24, ahora, ZONA_SALON) is True


def test_una_cita_reservada_con_prisa_no_cuenta():
    ahora = _madrid(2026, 9, 6, 12, 0)
    inicio = ahora + timedelta(hours=2)
    cita = _cita_para_atraso(inicio=inicio, created_at=inicio - timedelta(hours=1))

    assert esta_atrasado(cita, 24, ahora, ZONA_SALON) is False


def test_una_cita_YA_EMPEZADA_no_cuenta():
    ahora = _madrid(2026, 9, 6, 12, 0)
    cita = _cita_para_atraso(inicio=ahora - timedelta(minutes=30))

    assert momento_en_que_debio_salir(cita, 24, ahora, ZONA_SALON) is None


def _reservada_de_noche() -> dict:
    """Reserva a las 23:30 para las 12:00 del día siguiente.

    Es la única forma realista de entrar en el techo DENTRO de la franja de
    silencio: con 24 h, una cita de día entra de día. Lo que la mete de noche es
    el `created_at`, no la hora de la cita — me equivoqué de escenario al
    escribir esto la primera vez y el test lo dijo.
    """
    return _cita_para_atraso(
        inicio=_madrid(2026, 9, 6, 12, 0), created_at=_madrid(2026, 9, 5, 23, 30)
    )


def test_lo_RETENIDO_por_la_noche_no_llega_tarde():
    """Espera su turno; no es un retraso. Sin esto, cada mañana el vigía
    enseñaría de golpe todo lo que se acumuló durante la noche."""
    cita = _reservada_de_noche()

    # Su turno empieza al abrirse la franja, no cuando entró en el techo.
    assert momento_en_que_debio_salir(cita, 24, _madrid(2026, 9, 6, 7, 30), ZONA_SALON) == _madrid(
        2026, 9, 6, 8, 0
    )
    assert esta_atrasado(cita, 24, _madrid(2026, 9, 6, 7, 30), ZONA_SALON) is False
    # Y a los 30 min de abrirse, todavía dentro de la tolerancia.
    assert esta_atrasado(cita, 24, _madrid(2026, 9, 6, 8, 30), ZONA_SALON) is False


def test_pero_si_pasa_el_amanecer_y_sigue_sin_salir_SI_llega_tarde():
    """El contrapeso del anterior: retener no puede tapar un cron muerto."""
    cita = _reservada_de_noche()

    assert esta_atrasado(cita, 24, _madrid(2026, 9, 6, 9, 0), ZONA_SALON) is True


def test_la_tolerancia_sale_de_la_CADENCIA_del_cron():
    """No es un número heredado: son tres pasadas de las de verdad."""
    assert TOLERANCIA_ATRASO == 3 * recordatorios.CADENCIA_CRON
    assert recordatorios.CADENCIA_CRON == timedelta(minutes=15)


# ═════════════════════════ UN ENVÍO CAÍDO NO SE PIERDE PARA SIEMPRE
#
# La cita se sella ANTES de enviar y eso no se toca: es lo que impide el correo
# duplicado. Lo que sí se puede hacer es SOLTAR el sello cuando sabemos que el
# correo no salió, y que la pasada siguiente lo reintente. La frontera entre
# «sabemos que no salió» y «puede haber salido» se decide en `core/emails.py` y
# tiene sus propios tests; aquí se prueba qué hace el cron con esa decisión.


def _sello_soltado(db) -> bool:
    """¿Alguna anotación devolvió `recordatorio_enviado_at` a null?"""
    return any(
        "recordatorio_enviado_at" in b and b["recordatorio_enviado_at"] is None
        for _t, _p, b in db.updates
        if b.get("recordatorio_error")
    )


@pytest.mark.anyio
async def test_un_fallo_SEGURO_suelta_el_sello_y_la_siguiente_pasada_lo_manda(
    correo, tenant
):
    """EL CASO QUE FALTABA. Resend no coge el teléfono en la primera pasada y a
    los 15 minutos sí: el recordatorio sale, no se pierde."""
    correo["fallo"] = EnvioFallido("ConnectError: nada", seguro=True)
    correo["falla_hasta"] = 1
    db = FakeDB(citas=[_cita()])

    primera = await _pasada(db, tenant)
    assert primera["fallidos"] == 1
    assert correo["enviados"] == []
    assert _sello_soltado(db), "sin soltar el sello, ese correo no sale nunca"

    segunda = await _pasada(db, tenant)
    assert segunda["procesados"] == 1
    assert len(correo["enviados"]) == 1


@pytest.mark.anyio
async def test_un_fallo_AMBIGUO_deja_el_sello_puesto(correo, tenant):
    """PUEDE HABER SALIDO. Mandar dos recordatorios es peor que no mandar uno,
    así que aquí no se reintenta ni una vez."""
    correo["fallo"] = EnvioFallido("ReadTimeout: se agotó leyendo", seguro=False)
    db = FakeDB(citas=[_cita()])

    primera = await _pasada(db, tenant)
    assert primera["fallidos"] == 1
    assert not _sello_soltado(db)

    # Y la segunda pasada ni lo intenta: la cita sigue sellada.
    segunda = await _pasada(db, tenant)
    assert segunda["procesados"] == 0
    assert segunda["fallidos"] == 0
    assert correo["enviados"] == []


@pytest.mark.anyio
async def test_un_fallo_NO_CLASIFICADO_se_trata_como_ambiguo(correo, tenant):
    """Lo que no se entiende no se reintenta. Es el lado por el que se pierde un
    recordatorio, nunca por el que se duplica."""
    correo["fallo"] = RuntimeError("algo que nadie previó")
    db = FakeDB(citas=[_cita()])
    await _pasada(db, tenant)

    # LA PRECONDICIÓN, escrita: sin un intento de envío, «no se soltó el sello»
    # se cumple en vacío. Pasaba justo así cada noche.
    assert correo["intentos"] == 1, "no se intentó enviar: el test no probaría nada"
    assert not _sello_soltado(db)


@pytest.mark.anyio
async def test_se_reintenta_hasta_el_TOPE_y_entonces_se_da_por_perdido(correo, tenant):
    """El tope existe para que un fallo PERMANENTE deje de contarse como «lo
    estamos intentando». Sin él, un correo que no existe se reintentaría cada 15
    minutos hasta la hora de la cita."""
    correo["fallo"] = EnvioFallido("ConnectError: nunca", seguro=True)
    db = FakeDB(citas=[_cita()])

    for _ in range(MAX_INTENTOS_ENVIO):
        assert (await _pasada(db, tenant))["fallidos"] == 1

    # En el último ya no se suelta el sello: se acabó.
    ultima = [b for _t, _p, b in db.updates if b.get("recordatorio_error")][-1]
    assert "definitivo" in ultima["recordatorio_error"]
    assert "recordatorio_enviado_at" not in ultima
    assert ultima["recordatorio_intentos"] == MAX_INTENTOS_ENVIO

    # Y una pasada más no vuelve a intentarlo.
    assert (await _pasada(db, tenant))["fallidos"] == 0
    assert correo["enviados"] == []


@pytest.mark.anyio
async def test_la_anotacion_dice_en_cual_de_los_dos_casos_estamos(correo, tenant):
    """La fila tiene que entenderse sola: quien la mire mañana necesita saber si
    ese correo va a volver a intentarse o si ya no."""
    correo["fallo"] = EnvioFallido("ConnectError: nada", seguro=True)
    db = FakeDB(citas=[_cita()])
    await _pasada(db, tenant)

    texto = [b for _t, _p, b in db.updates if b.get("recordatorio_error")][-1][
        "recordatorio_error"
    ]
    assert texto.startswith(f"[reintento 1/{MAX_INTENTOS_ENVIO}]")
    assert "ConnectError" in texto


@pytest.mark.anyio
async def test_al_reclamar_se_LIMPIA_el_error_del_intento_anterior(correo, tenant):
    """Si no, una cita que falló y luego salió se quedaría con el error puesto
    para siempre, y «sellada + con error» dejaría de significar «este correo se
    perdió» — que es justo lo que cuenta el vigía."""
    db = FakeDB(citas=[_cita()])
    await _pasada(db, tenant)

    sellos = [b for _t, _p, b in db.updates if b.get("recordatorio_enviado_at")]
    assert sellos and sellos[0]["recordatorio_error"] is None


@pytest.mark.anyio
async def test_la_configuracion_incompleta_es_un_fallo_SEGURO(correo, tenant, monkeypatch):
    """Ni se intentó salir: falta una variable de entorno. Y se arregla solo en
    cuanto alguien la ponga, así que reintentar es exactamente lo que toca."""
    from app.core.emails import EmailConfigError

    correo["fallo"] = EmailConfigError("falta RESEND_API_KEY")
    correo["falla_hasta"] = 1
    db = FakeDB(citas=[_cita()])

    await _pasada(db, tenant)
    assert _sello_soltado(db)

    assert (await _pasada(db, tenant))["procesados"] == 1

