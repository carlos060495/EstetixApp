"""Las tres herramientas del bot: `app/core/bot_herramientas.py`.

LO MÁS IMPORTANTE QUE SE FIJA AQUÍ es que la confirmación explícita del cliente
no depende de que el modelo se porte bien:

  · `confirmar_cita` NO acepta argumentos, así que no puede crear una cita con
    datos que se haya inventado.
  · Sin una propuesta guardada por el servidor, `confirmar_cita` se niega. Es
    decir: aunque el modelo la llamara de primeras, no hay cita.
  · Entre proponer y confirmar se REVALIDA todo contra la base de datos.

Y después, los casos que el salón va a vivir: servicio en lenguaje suelto,
profesional que no hace ese servicio, festivo, hora pasada, cliente nuevo vs.
cliente que ya está en el CRM (en formato local), y dos reservas peleándose por
el mismo hueco.

EL FAKE HACE CUMPLIR EL CANDADO de la migración 0030. Sin eso, el test de
concurrencia pasaría aunque el EXCLUDE no existiera, que es justo la trampa que
tiene probar la concurrencia contra un doble.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tests.zona_de_prueba import ZONA_SALON
from app.core.bot_herramientas import ContextoBot, ejecutar
from app.db.postgrest_client import PostgrestError

SALON = "11111111-1111-1111-1111-111111111111"
SALON_B = "22222222-2222-2222-2222-222222222222"
SESION = "5e551000-0000-0000-0000-000000000001"

CORTE = "c0000000-0000-0000-0000-000000000001"
CORTE_NINO = "c0000000-0000-0000-0000-000000000003"
COLOR = "c0000000-0000-0000-0000-000000000002"

ANA = "e0000000-0000-0000-0000-00000000000a"
BRUNO = "e0000000-0000-0000-0000-00000000000b"

CLIENTA = "a0000000-0000-0000-0000-00000000000a"

# El cliente escribe desde este número; Meta lo manda sin el más.
TELEFONO = "+34600112233"

# Todo se ancla a un lunes y se congela el reloj el viernes anterior, para que
# el lunes caiga dentro del horizonte y muy por encima de la antelación mínima.
LUNES = "2026-09-07"
DOMINGO = "2026-09-13"  # el salón cierra los domingos
AHORA = datetime(2026, 9, 4, 9, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------- fake


class FakePostgrest:
    """PostgREST de mentira, pero con los constraints de verdad.

    Implementa lo que usan las herramientas (`eq`, `in`, `not.in`, `lt`, `gt`,
    `or`, `limit`) y —lo importante— hace cumplir el EXCLUDE de `appointments`
    de la migración 0030 y la columna generada `telefono_digitos` de `clients`.
    Un fake que no hiciera esas dos cosas dejaría pasar exactamente los dos bugs
    que estos tests existen para cazar.
    """

    def __init__(self, tablas: dict[str, list[dict]]):
        self.tablas = tablas
        self.insertados: list[tuple[str, dict]] = []

    async def select_todo(self, table, params=None):
        """Como el real: exige un orden estable y trae todas; el doble no pagina."""
        assert "order" in (params or {}), f"select_todo({table!r}) sin order"
        return await self.select(table, params)

    async def select(self, tabla: str, params: dict | None = None) -> list[dict]:
        params = params or {}
        filas = [dict(f) for f in self.tablas.get(tabla, [])]
        for campo, filtro in params.items():
            if campo in ("select", "order", "limit"):
                continue
            if campo == "or":
                filas = [f for f in filas if _pasa_or(f, filtro)]
                continue
            filas = [f for f in filas if _pasa(f.get(campo), filtro)]
        orden = params.get("order")
        if orden:
            campo, _, sentido = orden.partition(".")
            filas.sort(key=lambda f: str(f.get(campo) or ""), reverse=sentido == "desc")
        if "limit" in params:
            filas = filas[: int(params["limit"])]
        return filas

    async def insert(self, tabla: str, body: dict) -> dict:
        fila = dict(body)
        fila.setdefault("id", f"{tabla}-{len(self.tablas.get(tabla, [])) + 1}")

        if tabla == "appointments":
            # EL TRIGGER `cita_apunta_a_la_persona` (0044), modelado: la cita
            # nace apuntando a la persona de su ficha. Sin esto, una cita creada
            # por el bot no ocuparía la hora de nadie y el candado de abajo
            # dejaría pasar la segunda.
            fila.setdefault("persona_id", self._persona_de_la_ficha(fila.get("employee_id")))
            self._exige_el_candado(fila)
        if tabla == "clients":
            # Columna generada: los dígitos del teléfono, sin más magia. La
            # reconciliación de formatos la hace `core/telefonos.py`.
            fila["telefono_digitos"] = (
                "".join(c for c in (fila.get("telefono") or "") if c.isdigit()) or None
            )

        self.tablas.setdefault(tabla, []).append(fila)
        self.insertados.append((tabla, fila))
        return fila

    async def update(self, tabla: str, params: dict, body: dict) -> list[dict]:
        tocadas = []
        for fila in self.tablas.get(tabla, []):
            if all(
                _pasa(fila.get(c), f) for c, f in params.items() if c not in ("select", "limit")
            ):
                fila.update(body)
                tocadas.append(fila)
        return tocadas

    def _persona_de_la_ficha(self, employee_id: str | None) -> str | None:
        if not employee_id:
            return None
        for e in self.tablas.get("employees", []):
            if e.get("id") == employee_id:
                return e.get("persona_id") or employee_id
        return employee_id

    @staticmethod
    def _clave_del_candado(cita: dict) -> str | None:
        """`coalesce(persona_id, employee_id)`, igual que la restricción.

        La 0044 cambió la clave del EXCLUDE de la FICHA a la PERSONA. Si este
        doble se quedara en `employee_id`, seguiría dando por buena una cita que
        la base de verdad rechaza — que es justo la clase de agujero que ya
        costó un 502 en producción.
        """
        return cita.get("persona_id") or cita.get("employee_id")

    def _exige_el_candado(self, nueva: dict) -> None:
        """El EXCLUDE de la 0030, con la clave que le puso la 0044: una PERSONA
        no puede estar en dos citas a la vez, ni aunque sean de centros
        distintos."""
        clave = self._clave_del_candado(nueva)
        if not clave or nueva.get("estado") in ("cancelada", "no_show"):
            return
        ini, fin = datetime.fromisoformat(nueva["inicio"]), datetime.fromisoformat(nueva["fin"])
        for otra in self.tablas.get("appointments", []):
            if self._clave_del_candado(otra) != clave:
                continue
            if otra.get("estado") in ("cancelada", "no_show"):
                continue
            if datetime.fromisoformat(otra["inicio"]) < fin and datetime.fromisoformat(
                otra["fin"]
            ) > ini:
                raise PostgrestError(
                    409,
                    'conflicting key value violates exclusion constraint '
                    '"appointments_sin_solape" (SQLSTATE 23P01)',
                )


def _pasa_or(fila: dict, filtro: str) -> bool:
    for trozo in filtro.strip("()").split(","):
        campo, _, resto = trozo.partition(".")
        if _pasa(fila.get(campo), resto):
            return True
    return False


def _texto(valor) -> str:
    # PostgREST habla en minúsculas para los booleanos («visible_online=eq.true»),
    # mientras que Python los imprime como «True». Sin esto, el filtro no casa
    # nunca y el fake devuelve cero servicios: un fallo del doble que parecería
    # un fallo del código.
    if isinstance(valor, bool):
        return "true" if valor else "false"
    return str(valor)


def _pasa(valor, filtro: str) -> bool:
    op, _, esperado = filtro.partition(".")
    if op == "eq":
        return _texto(valor) == esperado
    if op == "neq":
        return _texto(valor) != esperado
    if op == "gte":
        return datetime.fromisoformat(valor) >= datetime.fromisoformat(esperado)
    if op == "in":
        return _texto(valor) in esperado.strip("()").split(",")
    if op == "not":
        sub, _, resto = esperado.partition(".")
        assert sub == "in", f"filtro no soportado: {filtro}"
        return _texto(valor) not in resto.strip("()").split(",")
    if op == "lt":
        return datetime.fromisoformat(valor) < datetime.fromisoformat(esperado)
    if op == "gt":
        return datetime.fromisoformat(valor) > datetime.fromisoformat(esperado)
    if op == "is":
        return valor is None if esperado == "null" else False
    raise AssertionError(f"filtro no soportado: {filtro}")


def _tablas(**cambios) -> dict:
    base = {
        # El centro existe: su zona horaria (0060) la pone la base por defecto.
        "salons": [{"id": SALON, "zona_horaria": "Europe/Madrid"}, {"id": SALON_B, "zona_horaria": "Europe/Madrid"}],
        "services": [
            {"id": CORTE, "salon_id": SALON, "nombre": "Corte de caballero", "precio": 25, "duracion_min": 30, "visible_online": True},
            {"id": COLOR, "salon_id": SALON, "nombre": "Coloración", "precio": 60, "duracion_min": 90, "visible_online": True},
        ],
        "employees": [
            {"id": ANA, "salon_id": SALON, "nombre": "Ana", "estado": "activo"},
            {"id": BRUNO, "salon_id": SALON, "nombre": "Bruno", "estado": "activo"},
        ],
        "employee_services": [
            {"employee_id": ANA, "service_id": CORTE},
            {"employee_id": ANA, "service_id": COLOR},
            {"employee_id": BRUNO, "service_id": CORTE},
        ],
        "appointments": [],
        "clients": [],
        "salon_schedule": [],
        "salon_schedule_exceptions": [],
        "bot_sessions": [{"id": SESION, "salon_id": SALON, "propuesta_pendiente": None}],
    }
    base.update(cambios)
    return base


def _ctx(db: FakePostgrest, salon_id: str = SALON, telefono: str | None = TELEFONO) -> ContextoBot:
    return ContextoBot(zona=ZONA_SALON, db=db, salon_id=salon_id, session_id=SESION, telefono_cliente=telefono)


@pytest.fixture(autouse=True)
def reloj_congelado(monkeypatch):
    """El viernes anterior a las 11:00 locales. Sin esto, los tests caducarían
    solos: la política del bot mira la hora real."""

    class Reloj(datetime):
        @classmethod
        def now(cls, tz=None):
            return AHORA if tz else AHORA.replace(tzinfo=None)

    import app.core.bot_herramientas as mod
    import app.core.disponibilidad as disp

    monkeypatch.setattr(mod, "datetime", Reloj)
    monkeypatch.setattr(disp, "_ahora", lambda: AHORA)


# ══════════════════════════════════════════════ EL GATE DE CONFIRMACIÓN


@pytest.mark.anyio
async def test_confirmar_sin_haber_propuesto_no_crea_nada():
    # LA PRUEBA CENTRAL DE LA FASE. Aunque el modelo llame a confirmar_cita de
    # primeras —alucinando, o porque el cliente escribió «confírmame la cita»—,
    # no hay propuesta guardada y por tanto no hay cita. El gate lo impone el
    # servidor, no el prompt.
    db = FakePostgrest(_tablas())
    r = await ejecutar(_ctx(db), "confirmar", {})
    assert r == {"ok": False, "error": "sin_propuesta"}
    assert db.tablas["appointments"] == []


@pytest.mark.anyio
async def test_confirmar_ignora_los_argumentos_que_le_meta_el_modelo():
    # Si el modelo se inventa datos y los manda igualmente, la cita creada es la
    # que NOSOTROS resumimos, no la que él pidió.
    db = FakePostgrest(_tablas(clients=[_clienta()]))
    await ejecutar(_ctx(db), "proponer_cita", {"servicio": "corte", "fecha": LUNES, "hora": "11:00"})

    await ejecutar(
        _ctx(db),
        "confirmar",
        {"hora": "23:00", "servicio": "Coloración", "precio": 0, "profesional": "Nadie"},
    )

    cita = db.tablas["appointments"][0]
    assert cita["service_id"] == CORTE
    assert cita["inicio"] == "2026-09-07T09:00:00+00:00"  # 11:00 locales


@pytest.mark.anyio
async def test_una_propuesta_caducada_no_se_confirma():
    db = FakePostgrest(_tablas(clients=[_clienta()]))
    await ejecutar(_ctx(db), "proponer_cita", {"servicio": "corte", "fecha": LUNES, "hora": "11:00"})
    # El cliente contesta «sí» al día siguiente.
    db.tablas["bot_sessions"][0]["propuesta_pendiente"]["expira_en"] = (
        AHORA - timedelta(minutes=1)
    ).isoformat()

    r = await ejecutar(_ctx(db), "confirmar", {})
    assert r["error"] == "propuesta_caducada"
    assert db.tablas["appointments"] == []
    assert db.tablas["bot_sessions"][0]["propuesta_pendiente"] is None


@pytest.mark.anyio
async def test_proponer_no_crea_la_cita_todavia():
    db = FakePostgrest(_tablas(clients=[_clienta()]))
    r = await ejecutar(
        _ctx(db), "proponer_cita", {"servicio": "corte", "fecha": LUNES, "hora": "11:00"}
    )
    assert r["ok"] is True
    assert r["pendiente_de_confirmacion"] is True
    assert db.tablas["appointments"] == []  # nada creado
    assert db.tablas["bot_sessions"][0]["propuesta_pendiente"] is not None


# ═══════════════════════════════════════════════════════ RESERVA FELIZ


def _clienta() -> dict:
    """Una clienta que YA está en el CRM, con el teléfono en formato local
    español —sin prefijo de país—, que es como lo teclea la recepcionista."""
    return {
        "id": CLIENTA,
        "salon_id": SALON,
        "nombre": "Lucía",
        "telefono": "600 11 22 33",
        "telefono_digitos": "600112233",
    }


@pytest.mark.anyio
async def test_reserva_feliz_de_principio_a_fin():
    db = FakePostgrest(_tablas(clients=[_clienta()]))
    ctx = _ctx(db)

    huecos = await ejecutar(ctx, "consultar_disponibilidad", {"servicio": "un corte", "fecha": LUNES})
    assert huecos["ok"] is True
    assert huecos["servicio"] == "Corte de caballero"
    # Solo HORAS: quién atiende no viaja aquí, lo decide proponer_cita.
    assert "11:00" in huecos["horas"]
    assert "profesionales" not in str(huecos)

    resumen = await ejecutar(
        ctx, "proponer_cita", {"servicio": "un corte", "fecha": LUNES, "hora": "11:00"}
    )
    assert resumen["resumen"] == {
        "servicio": "Corte de caballero",
        "fecha": LUNES,
        "dia": "lunes 07/09",
        "hora": "11:00",
        "profesional": "Ana",
        "precio": 25,
        "duracion_min": 30,
        "cliente": "Lucía",
    }

    hecho = await ejecutar(ctx, "confirmar", {})
    assert hecho["ok"] is True and hecho["cita_creada"] is True

    cita = db.tablas["appointments"][0]
    assert cita["client_id"] == CLIENTA
    assert cita["service_id"] == CORTE
    assert cita["employee_id"] == ANA
    assert cita["estado"] == "confirmada"       # el salón no la aprueba
    assert cita["canal"] == "whatsapp"
    assert cita["bot_session_id"] == SESION     # el ORIGEN: vino del bot
    # La propuesta se consume: un segundo «sí» no puede duplicar la cita.
    assert db.tablas["bot_sessions"][0]["propuesta_pendiente"] is None


@pytest.mark.anyio
async def test_confirmar_dos_veces_no_duplica_la_cita():
    db = FakePostgrest(_tablas(clients=[_clienta()]))
    await ejecutar(_ctx(db), "proponer_cita", {"servicio": "corte", "fecha": LUNES, "hora": "11:00"})
    await ejecutar(_ctx(db), "confirmar", {})
    segundo = await ejecutar(_ctx(db), "confirmar", {})

    assert segundo["error"] == "sin_propuesta"
    assert len(db.tablas["appointments"]) == 1


@pytest.mark.anyio
async def test_se_puede_pedir_a_un_profesional_concreto():
    db = FakePostgrest(_tablas(clients=[_clienta()]))
    r = await ejecutar(
        _ctx(db),
        "proponer_cita",
        {"servicio": "corte", "fecha": LUNES, "hora": "11:00", "profesional": "Bruno"},
    )
    assert r["resumen"]["profesional"] == "Bruno"


@pytest.mark.anyio
async def test_sin_preferencia_se_asigna_al_menos_cargado():
    # Ana ya tiene dos citas ese día; le toca a Bruno aunque Ana vaya antes por
    # orden alfabético.
    db = FakePostgrest(
        _tablas(
            clients=[_clienta()],
            appointments=[
                _cita(ANA, "2026-09-07T08:00:00+00:00"),
                _cita(ANA, "2026-09-07T08:30:00+00:00"),
            ],
        )
    )
    r = await ejecutar(_ctx(db), "proponer_cita", {"servicio": "corte", "fecha": LUNES, "hora": "11:00"})
    assert r["resumen"]["profesional"] == "Bruno"


def _cita(employee_id: str, inicio: str, minutos: int = 30, estado: str = "confirmada") -> dict:
    ini = datetime.fromisoformat(inicio)
    return {
        "id": f"cita-{inicio}-{employee_id[-1]}",
        "salon_id": SALON,
        "employee_id": employee_id,
        # Lo pone el trigger de la 0044; sin enlace, la persona es la ficha.
        "persona_id": employee_id,
        "service_id": CORTE,
        "inicio": ini.isoformat(),
        "fin": (ini + timedelta(minutes=minutos)).isoformat(),
        "estado": estado,
    }


# ═══════════════════════════════════════════════════════════ EL CLIENTE


@pytest.mark.anyio
async def test_a_un_cliente_que_ya_existe_no_se_le_pregunta_el_nombre():
    # Y se le reconoce aunque el CRM lo tenga en formato local (600 11 22 33) y
    # WhatsApp mande el internacional. Este es el test del punto del teléfono.
    db = FakePostgrest(_tablas(clients=[_clienta()]))
    r = await ejecutar(_ctx(db), "proponer_cita", {"servicio": "corte", "fecha": LUNES, "hora": "11:00"})
    assert r["ok"] is True
    assert r["resumen"]["cliente"] == "Lucía"


@pytest.mark.anyio
async def test_a_un_cliente_nuevo_se_le_pide_el_nombre_y_luego_se_le_crea_la_ficha():
    db = FakePostgrest(_tablas())
    ctx = _ctx(db)

    falta = await ejecutar(ctx, "proponer_cita", {"servicio": "corte", "fecha": LUNES, "hora": "11:00"})
    assert falta == {"ok": False, "error": "falta_nombre"}
    assert db.tablas["bot_sessions"][0]["propuesta_pendiente"] is None  # nada guardado

    ok = await ejecutar(
        ctx,
        "proponer_cita",
        {"servicio": "corte", "fecha": LUNES, "hora": "11:00", "nombre_cliente": "Marta"},
    )
    assert ok["resumen"]["cliente"] == "Marta"
    assert db.tablas["clients"] == []  # todavía no: solo al confirmar

    await ejecutar(ctx, "confirmar", {})
    nueva = db.tablas["clients"][0]
    assert nueva["nombre"] == "Marta"
    # Se guarda en E.164, que es la forma canónica.
    assert nueva["telefono"] == TELEFONO
    assert db.tablas["appointments"][0]["client_id"] == nueva["id"]


@pytest.mark.anyio
async def test_si_la_ficha_aparece_entre_proponer_y_confirmar_no_se_duplica():
    # La recepcionista da de alta a esa misma persona desde el panel mientras la
    # conversación está a medias. Al confirmar hay que ENLAZAR, no crear otra.
    db = FakePostgrest(_tablas())
    ctx = _ctx(db)
    await ejecutar(
        ctx,
        "proponer_cita",
        {"servicio": "corte", "fecha": LUNES, "hora": "11:00", "nombre_cliente": "Marta"},
    )
    db.tablas["clients"].append(_clienta())

    await ejecutar(ctx, "confirmar", {})
    assert len(db.tablas["clients"]) == 1
    assert db.tablas["appointments"][0]["client_id"] == CLIENTA


# ════════════════════════════════════════════════ LO QUE NO PUEDE SER


# ════════════════════════════════════ LO QUE SE LE ENSEÑA AL MODELO
#
# Es el mando de coste del bot: todo lo que devuelva esta herramienta viaja en
# el contexto de cada mensaje de reserva. Antes se devolvía el día entero (~39
# huecos) con el array de empleados REPETIDO en cada uno, y el bot solo ofrece
# dos o tres horas. Aquí se fija que no vuelva a engordar.


@pytest.mark.anyio
async def test_no_se_devuelve_el_dia_entero_sino_una_ventana():
    db = FakePostgrest(_tablas())
    r = await ejecutar(_ctx(db), "consultar_disponibilidad", {"servicio": "corte", "fecha": LUNES})

    assert len(r["horas"]) == 8
    # Y se dice que hay más, con los extremos: si no, el bot diría «solo me
    # quedan estas ocho» con el día medio vacío.
    assert r["total_libres"] == 39
    assert r["primera_libre"] == "10:00"
    assert r["ultima_libre"] == "19:30"


@pytest.mark.anyio
async def test_no_se_repite_el_profesional_en_cada_hora():
    # Lo que más pesaba: ["Ana","Bruno"] en los 39 huecos. Quién atiende lo
    # decide proponer_cita, así que el modelo no lo necesita para nada.
    db = FakePostgrest(_tablas())
    r = await ejecutar(_ctx(db), "consultar_disponibilidad", {"servicio": "corte", "fecha": LUNES})

    assert "Ana" not in str(r) and "Bruno" not in str(r)


@pytest.mark.anyio
async def test_sin_hora_pedida_las_horas_van_repartidas_por_el_dia():
    # Petición ABIERTA («¿qué tenéis el lunes?»): lo útil es una oferta variada.
    # Las ocho primeras seguidas serían siempre la mañana, y el bot diría que no
    # tiene nada por la tarde con el día entero libre.
    db = FakePostgrest(_tablas())
    r = await ejecutar(_ctx(db), "consultar_disponibilidad", {"servicio": "corte", "fecha": LUNES})

    assert r["horas"] == ["10:00", "11:00", "12:00", "13:00", "14:00", "15:00", "16:00", "17:00"]


@pytest.mark.anyio
async def test_con_hora_pedida_se_devuelven_las_mas_cercanas_a_ella():
    # «El lunes sobre las cinco» → hora_preferida=17:00. Aquí NO se reparte: se
    # dan las más cercanas y ORDENADAS POR CERCANÍA, porque el modelo ofrece las
    # primeras y las primeras tienen que ser las que mejor le sirven.
    db = FakePostgrest(_tablas())
    r = await ejecutar(
        _ctx(db),
        "consultar_disponibilidad",
        {"servicio": "corte", "fecha": LUNES, "hora_preferida": "17:00"},
    )

    assert r["horas"][:4] == ["17:00", "17:15", "17:30", "17:45"]
    # SESGO HACIA DELANTE: primero lo que va de las cinco en adelante, y solo
    # después lo anterior. Adelantarle la cita a alguien que ha pedido una hora
    # es pedirle algo; retrasársela un rato, no.
    assert r["horas"].index("17:15") < r["horas"].index("16:45")


@pytest.mark.anyio
async def test_el_caso_de_la_una_los_cuartos_de_hora_de_al_lado_se_ofrecen():
    # EL MOTIVO DE QUE LA REGLA SEA CONDICIONAL: con el reparto fijo de una
    # hora, quien pedía la una recibía 13:00, 14:00 y 15:00, y las 13:15/13:30
    # —las que mejor le sirven, y las que peor se llenan— no se ofrecían nunca.
    db = FakePostgrest(_tablas())
    r = await ejecutar(
        _ctx(db),
        "consultar_disponibilidad",
        {"servicio": "corte", "fecha": LUNES, "hora_preferida": "13:00"},
    )

    assert r["horas"][:3] == ["13:00", "13:15", "13:30"]
    assert "14:00" not in r["horas"][:3]  # ni un reparto por horas...
    assert "12:45" not in r["horas"][:3]  # ...ni adelantarle la cita sin pedirlo


@pytest.mark.anyio
@pytest.mark.parametrize("como_la_escriba", ["17:00", "17", "17.00", "17h00"])
async def test_la_hora_preferida_se_entiende_escrita_de_varias_formas(como_la_escriba):
    db = FakePostgrest(_tablas())
    r = await ejecutar(
        _ctx(db),
        "consultar_disponibilidad",
        {"servicio": "corte", "fecha": LUNES, "hora_preferida": como_la_escriba},
    )
    assert "17:00" in r["horas"]


@pytest.mark.anyio
async def test_una_hora_preferida_ilegible_no_rompe_nada():
    # La escribe un modelo: puede llegar cualquier cosa. Se pierde el centrado,
    # no la respuesta.
    db = FakePostgrest(_tablas())
    r = await ejecutar(
        _ctx(db),
        "consultar_disponibilidad",
        {"servicio": "corte", "fecha": LUNES, "hora_preferida": "por la tardecita"},
    )
    assert r["horas"][0] == "10:00"


@pytest.mark.anyio
async def test_un_dia_con_pocas_horas_se_devuelve_entero_y_sin_avisos():
    # Sábado: 10:00–14:00 son 15 huecos... también se recorta. Se usa un día casi
    # lleno para ver el caso de «caben todas»: sin `total_libres`, que sobraría.
    db = FakePostgrest(
        _tablas(
            appointments=[
                _cita(e, f"2026-09-07T{h:02d}:00:00+00:00", 60)
                for h in range(8, 17)
                for e in (ANA, BRUNO)
            ]
        )
    )
    r = await ejecutar(_ctx(db), "consultar_disponibilidad", {"servicio": "corte", "fecha": LUNES})

    assert len(r["horas"]) < 8
    assert "total_libres" not in r
    assert "primera_libre" not in r


@pytest.mark.anyio
async def test_con_profesional_pedido_se_devuelven_sus_horas_y_su_nombre_una_vez():
    db = FakePostgrest(_tablas(appointments=[_cita(BRUNO, "2026-09-07T08:00:00+00:00")]))
    r = await ejecutar(
        _ctx(db),
        "consultar_disponibilidad",
        {"servicio": "corte", "fecha": LUNES, "profesional": "Bruno"},
    )

    assert r["profesional"] == "Bruno"          # una sola vez, no en cada hora
    assert str(r).count("Bruno") == 1
    assert r["horas"][0] == "10:30"             # las 10:00 las tiene ocupadas


@pytest.mark.anyio
async def test_las_alternativas_de_un_hueco_pedido_son_las_mas_cercanas_a_el():
    # Pidió las 07:00, que es antes de abrir: la hora PEDIDA es el ancla, así
    # que se le ofrece lo más parecido que hay —lo primero de la mañana—, y no
    # un reparto del día.
    db = FakePostgrest(_tablas(clients=[_clienta()]))
    r = await ejecutar(_ctx(db), "proponer_cita", {"servicio": "corte", "fecha": LUNES, "hora": "07:00"})

    assert [a["hora"] for a in r["alternativas"]] == ["10:00", "10:15", "10:30"]


@pytest.mark.anyio
async def test_si_la_hora_pedida_esta_cogida_se_ofrece_lo_de_justo_despues():
    # Quiere la una y la una está ocupada: se le corre la cita hacia delante lo
    # mínimo, no se le adelanta ni se le manda a media tarde.
    db = FakePostgrest(
        _tablas(
            clients=[_clienta()],
            # 13:00–13:30 locales ocupadas para los dos.
            appointments=[_cita(e, "2026-09-07T11:00:00+00:00") for e in (ANA, BRUNO)],
        )
    )
    r = await ejecutar(_ctx(db), "proponer_cita", {"servicio": "corte", "fecha": LUNES, "hora": "13:00"})

    horas = [a["hora"] for a in r["alternativas"]]
    assert horas == ["13:30", "13:45", "14:00"]
    # Y ninguna pisa la cita que hay: las 13:15 solaparían.
    assert "13:15" not in horas


@pytest.mark.anyio
async def test_si_no_hay_nada_despues_cerca_se_ofrece_lo_de_justo_antes():
    # Pide la una y está cogido hasta las tres. Ahí el sesgo hacia delante se
    # rinde: lo que le sirve son las 12:45, no las 15:00. Con un servicio de 15
    # min las 12:45 caben justo antes de la cita.
    tablas = _tablas(
        clients=[_clienta()],
        # 13:00–15:00 locales ocupadas para los dos.
        appointments=[_cita(e, "2026-09-07T11:00:00+00:00", 120) for e in (ANA, BRUNO)],
    )
    flequillo = "c0000000-0000-0000-0000-000000000004"
    tablas["services"].append(
        {"id": flequillo, "salon_id": SALON, "nombre": "Flequillo", "precio": 10,
         "duracion_min": 15, "visible_online": True}
    )
    tablas["employee_services"] += [
        {"employee_id": e, "service_id": flequillo} for e in (ANA, BRUNO)
    ]
    r = await ejecutar(
        _ctx(FakePostgrest(tablas)),
        "proponer_cita",
        {"servicio": "Flequillo", "fecha": LUNES, "hora": "13:00"},
    )

    horas = [a["hora"] for a in r["alternativas"]]
    assert horas == ["12:45", "12:30", "12:15"]
    assert "15:00" not in horas  # saltar dos horas hacia delante no es «cerca»


@pytest.mark.anyio
async def test_las_alternativas_nunca_incluyen_una_hora_ocupada():
    # Se llena de 11:00 a 12:00 para los dos. Ni la cercanía ni el reparto
    # pueden acabar ofreciendo una hora que no está libre.
    db = FakePostgrest(
        _tablas(
            clients=[_clienta()],
            appointments=[_cita(e, "2026-09-07T09:00:00+00:00", 60) for e in (ANA, BRUNO)],
        )
    )
    r = await ejecutar(_ctx(db), "proponer_cita", {"servicio": "corte", "fecha": LUNES, "hora": "07:00"})

    horas = [a["hora"] for a in r["alternativas"]]
    assert horas == ["10:00", "10:15", "10:30"]
    assert not any(h.startswith("11:") for h in horas)


@pytest.mark.anyio
async def test_con_el_dia_casi_lleno_se_ofrece_lo_mas_cercano_que_quede():
    # Una cita larga tapa el lunes entero salvo el último rato: solo quedan las
    # 19:00, 19:15 y 19:30. Anclado a las 11:00 del lunes, el final del lunes
    # queda muchísimo más cerca que el martes por la mañana, así que se ofrece
    # eso aunque sean tres cuartos de hora seguidos: con hora pedida manda la
    # cercanía, no el reparto.
    db = FakePostgrest(
        _tablas(
            clients=[_clienta()],
            appointments=[_cita(e, "2026-09-07T08:00:00+00:00", 9 * 60) for e in (ANA, BRUNO)],
        )
    )
    r = await ejecutar(_ctx(db), "proponer_cita", {"servicio": "corte", "fecha": LUNES, "hora": "11:00"})

    assert [(a["fecha"], a["hora"]) for a in r["alternativas"]] == [
        ("2026-09-07", "19:00"),
        ("2026-09-07", "19:15"),
        ("2026-09-07", "19:30"),
    ]


@pytest.mark.anyio
async def test_las_alternativas_no_prometen_un_profesional():
    # Quién atiende lo decide proponer_cita mirando la carga del día. Nombrar a
    # alguien aquí sería prometer una persona que puede no ser la asignada.
    db = FakePostgrest(_tablas())
    r = await ejecutar(_ctx(db), "consultar_disponibilidad", {"servicio": "corte", "fecha": DOMINGO})

    assert r["cerrado"] is True
    assert r["alternativas"]
    assert all(set(a) == {"fecha", "dia", "hora"} for a in r["alternativas"])


@pytest.mark.anyio
async def test_un_servicio_que_el_salon_no_ofrece_devuelve_los_que_si():
    db = FakePostgrest(_tablas())
    r = await ejecutar(_ctx(db), "consultar_disponibilidad", {"servicio": "masaje tailandés", "fecha": LUNES})
    assert r["error"] == "servicio_no_ofrecido"
    assert "Corte de caballero" in r["servicios_del_salon"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "como_lo_pide",
    [
        "un corte",
        "Corte de caballero",
        "corte de caballero",
        "quiero cita para un corte",
        "CORTE",
        "un corte de pelo de caballero",
    ],
)
async def test_el_servicio_se_entiende_aunque_lo_pidan_en_lenguaje_suelto(como_lo_pide):
    db = FakePostgrest(_tablas())
    r = await ejecutar(_ctx(db), "consultar_disponibilidad", {"servicio": como_lo_pide, "fecha": LUNES})
    assert r["ok"] is True, r
    assert r["servicio"] == "Corte de caballero"


@pytest.mark.anyio
async def test_el_mas_especifico_gana_al_ambiguo():
    # «corte» a secas es ambiguo con dos servicios de corte, pero «corte de
    # caballero» no lo es: comparte dos palabras y el otro solo una.
    tablas = _tablas()
    tablas["services"].append(
        {"id": CORTE_NINO, "salon_id": SALON, "nombre": "Corte infantil", "precio": 15, "duracion_min": 30, "visible_online": True}
    )
    db = FakePostgrest(tablas)
    r = await ejecutar(_ctx(db), "consultar_disponibilidad", {"servicio": "un corte de caballero", "fecha": LUNES})
    assert r["ok"] is True
    assert r["servicio"] == "Corte de caballero"


@pytest.mark.anyio
async def test_al_profesional_se_le_reconoce_por_el_nombre_de_pila():
    tablas = _tablas()
    tablas["employees"][0]["nombre"] = "Ana García"
    db = FakePostgrest(tablas)
    r = await ejecutar(
        _ctx(db), "consultar_disponibilidad", {"servicio": "corte", "fecha": LUNES, "profesional": "Ana"}
    )
    assert r["ok"] is True
    assert r["profesional"] == "Ana García"


@pytest.mark.anyio
async def test_un_servicio_ambiguo_no_se_elige_a_ciegas():
    # «corte» encaja con dos: elegir uno significaría agendar mal y cobrar otro
    # precio. Se devuelven los candidatos para que el bot pregunte.
    tablas = _tablas()
    tablas["services"].append(
        {"id": CORTE_NINO, "salon_id": SALON, "nombre": "Corte infantil", "precio": 15, "duracion_min": 30, "visible_online": True}
    )
    r = await ejecutar(_ctx(FakePostgrest(tablas)), "consultar_disponibilidad", {"servicio": "corte", "fecha": LUNES})
    assert r["error"] == "servicio_ambiguo"
    assert set(r["candidatos"]) == {"Corte de caballero", "Corte infantil"}


@pytest.mark.anyio
async def test_un_servicio_oculto_en_la_web_tampoco_lo_agenda_el_bot():
    tablas = _tablas()
    tablas["services"][0]["visible_online"] = False
    r = await ejecutar(_ctx(FakePostgrest(tablas)), "consultar_disponibilidad", {"servicio": "corte de caballero", "fecha": LUNES})
    assert r["error"] == "servicio_no_ofrecido"


@pytest.mark.anyio
async def test_un_profesional_que_no_hace_el_servicio_se_dice_con_claridad():
    # Bruno no colorea. El cliente tiene que oír eso, no un «no hay hueco».
    db = FakePostgrest(_tablas(clients=[_clienta()]))
    r = await ejecutar(
        _ctx(db),
        "proponer_cita",
        {"servicio": "Coloración", "fecha": LUNES, "hora": "11:00", "profesional": "Bruno"},
    )
    assert r["error"] == "profesional_no_realiza"
    assert r["profesional"] == "Bruno"
    assert r["profesionales_que_lo_hacen"] == ["Ana"]


@pytest.mark.anyio
async def test_un_profesional_que_no_existe_devuelve_los_que_si():
    db = FakePostgrest(_tablas())
    r = await ejecutar(
        _ctx(db), "consultar_disponibilidad", {"servicio": "corte", "fecha": LUNES, "profesional": "Jimena"}
    )
    assert r["error"] == "profesional_desconocido"
    assert r["profesionales_del_salon"] == ["Ana", "Bruno"]


@pytest.mark.anyio
async def test_un_dia_festivo_se_dice_con_su_motivo():
    tablas = _tablas(
        salon_schedule_exceptions=[
            {
                "id": "exc-1", "salon_id": SALON, "fecha_inicio": LUNES, "fecha_fin": LUNES,
                "cerrado": True, "apertura": None, "cierre": None, "tiene_pausa": False,
                "pausa_inicio": None, "pausa_fin": None, "motivo": "Fiesta local",
            }
        ]
    )
    r = await ejecutar(_ctx(FakePostgrest(tablas)), "consultar_disponibilidad", {"servicio": "corte", "fecha": LUNES})
    assert r["cerrado"] is True
    assert "Fiesta local" in r["motivo"]
    assert "horas" not in r
    assert r["alternativas"]  # y se ofrece otro día


@pytest.mark.anyio
async def test_fuera_del_horario_del_salon_no_se_propone():
    db = FakePostgrest(_tablas(clients=[_clienta()]))
    r = await ejecutar(_ctx(db), "proponer_cita", {"servicio": "corte", "fecha": LUNES, "hora": "07:00"})
    assert r["error"] == "hueco_no_disponible"
    assert r["alternativas"][0]["hora"] == "10:00"


@pytest.mark.anyio
async def test_una_hora_que_ya_ha_pasado_se_dice_como_tal():
    db = FakePostgrest(_tablas(clients=[_clienta()]))
    # AHORA es el viernes 04/09 a las 11:00 locales; se pide ese mismo día a las 09:00.
    r = await ejecutar(_ctx(db), "proponer_cita", {"servicio": "corte", "fecha": "2026-09-04", "hora": "09:00"})
    assert r["error"] == "hora_pasada"


@pytest.mark.anyio
async def test_el_bot_exige_una_hora_de_antelacion():
    db = FakePostgrest(_tablas(clients=[_clienta()]))
    # AHORA = 11:00 locales del viernes; a las 11:30 no llega al margen.
    r = await ejecutar(_ctx(db), "proponer_cita", {"servicio": "corte", "fecha": "2026-09-04", "hora": "11:30"})
    assert r["error"] == "demasiado_pronto"
    assert r["antelacion_minima_min"] == 60


@pytest.mark.anyio
async def test_mas_alla_de_tres_semanas_no_se_agenda():
    db = FakePostgrest(_tablas())
    r = await ejecutar(_ctx(db), "consultar_disponibilidad", {"servicio": "corte", "fecha": "2026-10-30"})
    assert r["error"] == "demasiado_lejos"
    assert r["horizonte_dias"] == 21


@pytest.mark.anyio
async def test_una_fecha_pasada_se_dice_como_tal():
    db = FakePostgrest(_tablas())
    r = await ejecutar(_ctx(db), "consultar_disponibilidad", {"servicio": "corte", "fecha": "2026-08-01"})
    assert r["error"] == "fecha_pasada"


@pytest.mark.anyio
async def test_un_hueco_ocupado_ofrece_las_alternativas_mas_cercanas():
    db = FakePostgrest(
        _tablas(
            clients=[_clienta()],
            appointments=[
                _cita(ANA, "2026-09-07T09:00:00+00:00"),
                _cita(BRUNO, "2026-09-07T09:00:00+00:00"),
            ],
        )
    )
    r = await ejecutar(_ctx(db), "proponer_cita", {"servicio": "corte", "fecha": LUNES, "hora": "11:00"})
    assert r["error"] == "hueco_no_disponible"
    # Las 11:00 están cogidas (11:00–11:30), así que se le corre hacia delante:
    # 11:30 en adelante, sin adelantarle nada a las 10:30.
    assert [a["hora"] for a in r["alternativas"]] == ["11:30", "11:45", "12:00"]


# ════════════════════════════════════════════════════════ CONCURRENCIA


@pytest.mark.anyio
async def test_si_el_hueco_se_ocupa_entre_proponer_y_confirmar_se_avisa():
    db = FakePostgrest(_tablas(clients=[_clienta()]))
    ctx = _ctx(db)
    await ejecutar(ctx, "proponer_cita", {"servicio": "corte", "fecha": LUNES, "hora": "11:00"})

    # Mientras el cliente lo piensa, el salón llena ese hueco desde el panel.
    db.tablas["appointments"] += [
        _cita(ANA, "2026-09-07T09:00:00+00:00"),
        _cita(BRUNO, "2026-09-07T09:00:00+00:00"),
    ]

    r = await ejecutar(ctx, "confirmar", {})
    assert r["error"] == "hueco_ocupado"
    assert r["alternativas"]
    # No se ha creado ninguna cita nueva: siguen siendo las dos del panel.
    assert len(db.tablas["appointments"]) == 2


@pytest.mark.anyio
async def test_dos_reservas_al_mismo_hueco_y_solo_una_gana():
    """El caso que solo cierra el candado de la base de datos.

    Las dos conversaciones proponen el mismo hueco y las dos lo ven libre —esa
    es la carrera—. Al confirmar, el EXCLUDE deja pasar a una y la otra recibe
    un 409 que se convierte en alternativas, no en una cita duplicada ni en un
    error crudo.
    """
    db = FakePostgrest(_tablas(clients=[_clienta()]))
    db.tablas["bot_sessions"].append(
        {"id": "sesion-B", "salon_id": SALON, "propuesta_pendiente": None}
    )
    uno = _ctx(db)
    otro = ContextoBot(zona=ZONA_SALON, db=db, salon_id=SALON, session_id="sesion-B", telefono_cliente="+34600999888")

    # Los dos proponen el mismo hueco con el mismo profesional: se le fuerza a
    # Ana en los dos casos para que peleen por la misma agenda.
    for ctx in (uno, otro):
        r = await ejecutar(
            ctx,
            "proponer_cita",
            {"servicio": "corte", "fecha": LUNES, "hora": "11:00", "profesional": "Ana", "nombre_cliente": "Quien sea"},
        )
        assert r["ok"] is True

    primera = await ejecutar(uno, "confirmar", {})
    segunda = await ejecutar(otro, "confirmar", {})

    assert primera["ok"] is True
    assert segunda["error"] == "hueco_ocupado"
    assert len(db.tablas["appointments"]) == 1


# ══════════════════════════════════════════════════════ MULTI-TENANT


@pytest.mark.anyio
async def test_el_bot_de_un_salon_no_ve_los_servicios_de_otro():
    # Aquí el fake filtra por salon_id como haría la RLS. La prueba de que la
    # política EXISTE se hace contra Postgres en supabase/tests/.
    db = FakePostgrest(_tablas())
    r = await ejecutar(_ctx(db, salon_id=SALON_B), "consultar_disponibilidad", {"servicio": "corte", "fecha": LUNES})
    assert r["error"] == "sin_servicios"


@pytest.mark.anyio
async def test_cada_salon_agenda_con_sus_propias_reglas():
    # Mismo día y mismo servicio, pero el salón B cierra los lunes: el bot de
    # cada uno contesta según SU horario, no según uno compartido.
    tablas = _tablas()
    tablas["services"].append(
        {"id": "svc-b", "salon_id": SALON_B, "nombre": "Corte de caballero", "precio": 30, "duracion_min": 30, "visible_online": True}
    )
    tablas["employees"].append({"id": "emp-b", "salon_id": SALON_B, "nombre": "Berta", "estado": "activo"})
    tablas["employee_services"].append({"employee_id": "emp-b", "service_id": "svc-b"})
    tablas["salon_schedule"] = [
        {"salon_id": SALON_B, "dia_semana": d, "cerrado": d == 1, "apertura": "10:00:00",
         "cierre": "20:00:00", "tiene_pausa": False, "pausa_inicio": None, "pausa_fin": None}
        for d in range(1, 8)
    ]
    db = FakePostgrest(tablas)

    en_a = await ejecutar(_ctx(db), "consultar_disponibilidad", {"servicio": "corte", "fecha": LUNES})
    en_b = await ejecutar(_ctx(db, salon_id=SALON_B), "consultar_disponibilidad", {"servicio": "corte", "fecha": LUNES})

    assert en_a["horas"]
    assert en_b["cerrado"] is True


# ════════════════════════════════════════════════════════════ ROBUSTEZ


@pytest.mark.anyio
async def test_una_herramienta_que_no_existe_se_contesta_en_vez_de_reventar():
    db = FakePostgrest(_tablas())
    assert await ejecutar(_ctx(db), "cancelar_cita", {}) == {
        "ok": False,
        "error": "herramienta_desconocida",
    }


@pytest.mark.anyio
async def test_un_fallo_de_base_de_datos_vuelve_como_resultado_no_como_excepcion():
    class Rota(FakePostgrest):
        async def select(self, tabla, params=None):
            raise PostgrestError(500, "la base de datos se ha caído")

    r = await ejecutar(_ctx(Rota(_tablas())), "consultar_disponibilidad", {"servicio": "corte", "fecha": LUNES})
    assert r == {"ok": False, "error": "no_disponible"}
