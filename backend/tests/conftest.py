"""Configuración común de los tests.

Las variables de entorno se fijan ANTES de importar nada de `app`: Settings las
exige al construirse y, en pydantic-settings, las variables de entorno ganan al
fichero .env — así los tests no dependen del .env de cada desarrollador ni tocan
credenciales reales.
"""

from __future__ import annotations

import os

ADMIN_JWT_SECRET_TEST = "secreto-de-test-para-el-panel-super-admin"
IMPERSONATION_SECRET_TEST = "secreto-de-test-para-la-sesion-de-soporte-32+"
ORIGEN_PERMITIDO = "http://localhost:3000"

os.environ.setdefault("SUPABASE_URL", "https://tests.supabase.invalid")
os.environ.setdefault("SUPABASE_SECRET_KEY", "sb_secret_tests")
os.environ.setdefault("SUPABASE_JWKS_URL", "https://tests.supabase.invalid/jwks")
os.environ["ADMIN_JWT_SECRET"] = ADMIN_JWT_SECRET_TEST
os.environ["ADMIN_ALLOWED_ORIGINS"] = ORIGEN_PERMITIDO
# También el del salón: la defensa CSRF del modo soporte lo compara contra el
# Origin de cada mutación. Sin fijarlo aquí, `allowed_origins` saldría del .env
# del desarrollador y el test mediría su configuración, no la guardia.
os.environ["ALLOWED_ORIGINS"] = ORIGEN_PERMITIDO
os.environ["JWT_IMPERSONATION_SECRET"] = IMPERSONATION_SECRET_TEST

from collections.abc import Iterator  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402

import jwt  # noqa: E402
import pytest  # noqa: E402

ADMIN_ID = "11111111-1111-1111-1111-111111111111"


def admin_cookie(
    rol: str = "owner",
    admin_id: str = ADMIN_ID,
    email: str = "owner@estetixapp.test",
) -> str:
    """JWT válido para el guard de /admin/*, con el rol pedido."""
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": admin_id,
            "email": email,
            "rol": rol,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(hours=1)).timestamp()),
            "iss": "estetixapp-admin",
            "aud": "estetixapp-admin-panel",
        },
        ADMIN_JWT_SECRET_TEST,
        algorithm="HS256",
    )


SID = "55555555-0000-0000-0000-000000000001"
CODE_ID = "cccccccc-0000-0000-0000-000000000001"


def impersonation_cookie(
    salon_id: str,
    sid: str = SID,
    admin_id: str = ADMIN_ID,
    email: str = "owner@estetixapp.test",
    minutos: int = 30,
    code_id: str = CODE_ID,
) -> str:
    """Cookie de sesión de soporte válida. `minutos` negativo = ya caducada.

    Lleva los mismos claims que emite `crear_token_impersonacion`: un doble que
    se quede corto haría pasar tests a código que lee un claim inexistente.
    """
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": admin_id,
            "email": email,
            "salon_id": salon_id,
            "sid": sid,
            "code_id": code_id,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=minutos)).timestamp()),
            "iss": "estetixapp-impersonation",
            "aud": "estetixapp-tenant-support",
        },
        IMPERSONATION_SECRET_TEST,
        algorithm="HS256",
    )


def _coincide(valor: object, expresion: str) -> bool:
    operador, _, objetivo = expresion.partition(".")
    if operador == "eq":
        # PostgREST escribe los booleanos en minúscula (`id=eq.true`) y Python
        # los imprime como `True`. Sin este caso, un filtro sobre una columna
        # booleana no casaría NUNCA en el doble aunque sí case en PostgREST, y
        # el test fallaría por un defecto del doble, no del código.
        if isinstance(valor, bool):
            return str(valor).lower() == objetivo.lower()
        return str(valor) == objetivo
    if operador == "neq":
        # `id=neq.X` — el solape al MOVER una cita se busca sin la propia cita.
        return str(valor) != objetivo
    if operador == "in":
        # `in.(a,b,c)` — PostgREST no pone comillas cuando los valores no las
        # necesitan, que es el caso de los uuid y de los roles del bot.
        return str(valor) in objetivo.strip("()").split(",")
    if operador == "ilike":
        return objetivo.strip("*").lower() in str(valor or "").lower()
    if operador == "is":
        return valor is None if objetivo == "null" else valor is not None
    if operador == "not":
        # `not.is.null` — la negación va delante del operador de verdad.
        return not _coincide(valor, objetivo)
    if operador in ("gt", "gte", "lt", "lte"):
        # Rangos de fecha: los usa el vigía de recordatorios para acotar qué
        # citas mira. Se comparan como instantes y no como cadenas porque
        # «...+00:00» y «...Z» son la misma hora escrita de dos formas.
        izq, der = _instante(valor), _instante(objetivo)
        if izq is None or der is None:
            return False
        return {
            "gt": izq > der, "gte": izq >= der,
            "lt": izq < der, "lte": izq <= der,
        }[operador]
    raise AssertionError(f"FakePostgrest no soporta el operador '{operador}'")


def _instante(valor: object) -> datetime | None:
    """Una marca ISO en `datetime` con zona. `None` si no lo es."""
    if isinstance(valor, datetime):
        return valor if valor.tzinfo else valor.replace(tzinfo=timezone.utc)
    if not isinstance(valor, str) or not valor:
        return None
    try:
        d = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _partes(filtro: str) -> list[str]:
    """Lo de dentro de `(...)` partido por las comas de PRIMER nivel.

    `and=(or(a,b),or(c,d))` son DOS condiciones, no cuatro: partir por todas
    las comas convertiría la lista de clientas «dormidas que se llamen Ana» en
    una mezcla sin sentido.
    """
    cuerpo = filtro[1:-1] if filtro.startswith("(") and filtro.endswith(")") else filtro
    partes, profundidad, actual = [], 0, ""
    for ch in cuerpo:
        if ch == "(":
            profundidad += 1
        elif ch == ")":
            profundidad -= 1
        if ch == "," and profundidad == 0:
            partes.append(actual)
            actual = ""
        else:
            actual += ch
    if actual:
        partes.append(actual)
    return partes


def _cumple(row: dict, parte: str) -> bool:
    """Una condición suelta, o un `or(...)`/`and(...)` anidado."""
    if parte.startswith("or("):
        return _coincide_or(row, parte[2:])
    if parte.startswith("and("):
        return _coincide_and(row, parte[3:])
    columna, _, expresion = parte.partition(".")
    return _coincide(row.get(columna), expresion)


def _cumple_todo(row: dict, params: dict[str, str]) -> bool:
    """Los filtros de un UPDATE o un DELETE, con `or`/`and` igual que al leer:
    PostgREST los acepta en los tres (la 0072 escribe la copia de Stripe solo si
    es más nueva, con un `or`)."""
    for col, exp in params.items():
        if col == "or":
            ok = _coincide_or(row, exp)
        elif col == "and":
            ok = _coincide_and(row, exp)
        else:
            ok = _coincide(row.get(col), exp)
        if not ok:
            return False
    return True


def _coincide_and(row: dict, and_filter: str) -> bool:
    """`and=(inicio.lte.X)` — mismo formato que el `or`, con la otra semántica.

    PostgREST lo usa para poner DOS condiciones sobre la misma columna, que en
    la cadena de consulta no cabrían de otra forma.
    """
    return all(_cumple(row, parte) for parte in _partes(and_filter))


def _coincide_or(row: dict, or_filter: str) -> bool:
    return any(_cumple(row, parte) for parte in _partes(or_filter))


def _vista_clientas_con_actividad(tables: dict[str, list[dict]]) -> list[dict]:
    """La vista de la 0058, calculada como la calcula Postgres.

    Un doble que guardara `visitas` en la fila de la clienta sería otra vez la
    columna que no escribía nadie: aquí se cuentan las citas COMPLETADAS de cada
    clienta, se suma el precio ACTUAL de su servicio y se toma la última. Una
    fixture que siembre citas ve cambiar la ficha, igual que en producción.
    """
    precios = {s["id"]: s.get("precio") for s in tables.get("services", [])}
    completadas: dict[str, list[dict]] = {}
    for cita in tables.get("appointments", []):
        if cita.get("estado") == "completada" and cita.get("client_id"):
            completadas.setdefault(cita["client_id"], []).append(cita)
    filas = []
    for clienta in tables.get("clients", []):
        suyas = completadas.get(clienta.get("id"), [])
        inicios = [c["inicio"] for c in suyas if c.get("inicio")]
        filas.append({
            **{k: v for k, v in clienta.items() if k not in ("visitas", "total_gastado")},
            "visitas": len(suyas),
            "total_gastado": round(sum(float(precios.get(c.get("service_id")) or 0) for c in suyas), 2),
            "ultima_visita": max(inicios, key=_instante) if inicios else None,
        })
    return filas


# Las nueve columnas que la migración 0040 subió de `salons` a `accounts`.
COLUMNAS_QUE_SUBIERON = (
    "plan", "pending_plan", "stripe_customer_id", "suspendido", "suspendido_at",
    "suspendido_por", "cif", "logo_url", "accent_color",
)


def _aplicar_la_0040(tables: dict[str, list[dict]]) -> None:
    """Le aplica la migración 0040 a los datos sembrados por la fixture.

    POR QUÉ AQUÍ Y NO EN TREINTA FICHEROS. Las fixtures siembran salones con su
    plan y su suspensión, que es donde vivían hasta la 0040. En vez de reescribir
    cada una a mano —treinta ocasiones de dejarse una y no enterarse—, el doble
    hace con ellas exactamente lo que la migración hizo con los datos de verdad:
    crea la cuenta, le pasa esas nueve columnas y LAS QUITA del salón.

    Quitarlas es la mitad importante. Si se quedaran, un endpoint que siguiera
    pidiendo `salons.plan` seguiría encontrándolo aquí y pasando en verde
    mientras en producción lee null: que es justo cómo `employees.rol` llegó a
    producción con la suite entera contenta (ver tests/esquema.py).

    Una fixture que siembre `accounts` a mano manda sobre esto: la cuenta ya
    existe y no se toca.
    """
    salones = tables.get("salons")
    if not salones:
        return
    cuentas = tables.setdefault("accounts", [])
    por_id = {c["id"]: c for c in cuentas}

    for salon in salones:
        account_id = salon.get("account_id") or f"cuenta-de-{salon.get('id')}"
        salon["account_id"] = account_id
        cuenta = por_id.get(account_id)
        if cuenta is None:
            cuenta = {"id": account_id, "nombre": salon.get("nombre_comercial") or "Cuenta"}
            por_id[account_id] = cuenta
            cuentas.append(cuenta)
        for columna in COLUMNAS_QUE_SUBIERON:
            if columna in salon:
                cuenta.setdefault(columna, salon.pop(columna))

    # Y la suscripción pasa a colgar de la cuenta. Aquí `salon_id` SÍ se queda:
    # la 0040 lo conserva durante la ventana de despliegue, y la columna sigue
    # siendo not null.
    cuenta_de = {s["id"]: s["account_id"] for s in salones if s.get("id")}
    for suscripcion in tables.get("subscriptions", []):
        if not suscripcion.get("account_id"):
            suscripcion["account_id"] = cuenta_de.get(suscripcion.get("salon_id"))


class FakePostgrest:
    """PostgREST en memoria con el subconjunto de filtros que usan los endpoints
    del panel: `eq.`, `in.`, `ilike.`, `is.`, `not.` como prefijo, `gt./gte./lt./lte.`
    sobre fechas,
    `or=(...)`, `and=(...)`, `select`, `order`, `limit` y `offset`. No pretende
    ser general: si un endpoint necesita otro filtro, se añade aquí
    explícitamente —y el operador desconocido LANZA— para que el doble nunca
    finja soportar algo que no soporta.
    """

    TOPE = 1000

    def __init__(self, tables: dict[str, list[dict]] | None = None):
        self.tables: dict[str, list[dict]] = tables or {}
        _aplicar_la_0040(self.tables)
        # La 0060: `salons.zona_horaria` es `not null default 'Europe/Madrid'`.
        # Un salón sembrado sin ella la tiene en la base de verdad, igual aquí.
        for salon in self.tables.get("salons", []):
            salon.setdefault("zona_horaria", "Europe/Madrid")
        # La 0029 deja UNA fila en `plataforma_config`, y la 0068 le pone
        # `dias_de_prueba not null default 14`. En la base siempre está.
        self.tables.setdefault("plataforma_config", [{"id": True, "comision_referido_pct": 10}])
        for fila in self.tables["plataforma_config"]:
            fila.setdefault("dias_de_prueba", 14)
        self.inserts: list[tuple[str, dict]] = []
        self.updates: list[tuple[str, dict, dict]] = []
        # Las funciones de la base que un test necesita, con lo que devuelven.
        # Una que el test no ha dado LANZA: el doble no finge saber calcularla.
        self.rpcs: dict = {"clientas_de_la_agenda": self._clientas_de_la_agenda}

    def _clientas_de_la_agenda(self, body: dict) -> list[dict]:
        """La de la 0086: nombre y alergias de las clientas con alguna cita."""
        ids = {str(i) for i in body.get("p_ids") or []}
        con_cita = {str(a.get("client_id")) for a in self.tables.get("appointments", [])}
        return [{"id": c["id"], "nombre": c.get("nombre"), "alergias": c.get("alergias")}
                for c in self.tables.get("clients", []) if str(c.get("id")) in ids & con_cita]

    async def rpc(self, function: str, body: dict) -> list[dict]:
        if function not in self.rpcs:
            raise AssertionError(f"el doble no sabe calcular la función {function!r}: dásela en `rpcs`")
        return self.rpcs[function](body)

    async def count(self, table: str, params: dict[str, str] | None = None) -> int:
        """El recuento exacto, como el de verdad: los mismos filtros y sin tope.

        Se ignoran `limit` y `offset` A PROPÓSITO, que es justo lo que hace
        PostgREST con `count=exact`: si el doble los aplicara, un test pasaría
        con un número recortado y en producción saldría otro.
        """
        params = {k: v for k, v in (params or {}).items() if k not in ("limit", "offset")}
        return len(await self._todas(table, params))

    async def _todas(self, table: str, params: dict[str, str]) -> list[dict]:
        self._sin_tope = True
        try:
            return await self.select(table, params)
        finally:
            self._sin_tope = False

    async def select_todo(self, table: str, params: dict[str, str] | None = None) -> list[dict]:
        """Todas las filas, como el cliente real: exige `order` y no admite
        limit ni offset (pagina él)."""
        params = dict(params or {})
        if "order" not in params:
            raise ValueError(f"select_todo({table!r}) necesita un `order` estable para paginar")
        if "limit" in params or "offset" in params:
            raise ValueError(f"select_todo({table!r}) pagina sola: no admite limit ni offset")
        return await self._todas(table, params)

    async def select(self, table: str, params: dict[str, str] | None = None) -> list[dict]:
        params = dict(params or {})
        if table == "clientas_con_actividad":
            rows = _vista_clientas_con_actividad(self.tables)
        else:
            rows = list(self.tables.get(table, []))

        select = params.pop("select", None)
        order = params.pop("order", None)
        limit = params.pop("limit", None)
        offset = params.pop("offset", None)
        or_filter = params.pop("or", None)
        and_filter = params.pop("and", None)

        for columna, expresion in params.items():
            rows = [r for r in rows if _coincide(r.get(columna), expresion)]

        for nombre, valor in (("or", or_filter), ("and", and_filter)):
            # PostgREST exige `or=(a,b)` con sus paréntesis y contesta 400 sin
            # ellos. Un doble que lo tragara dejó pasar un 500 en la lista de
            # clientas «dormidas» con toda la suite en verde.
            if valor and not (valor.startswith("(") and valor.endswith(")")):
                raise AssertionError(f"FakePostgrest: `{nombre}={valor}` sin paréntesis; PostgREST daría 400")
        if or_filter:
            rows = [r for r in rows if _coincide_or(r, or_filter)]
        if and_filter:
            rows = [r for r in rows if _coincide_and(r, and_filter)]

        if order:
            # Varias columnas, como PostgREST (`created_at.desc,id.asc`): se
            # ordena de la última a la primera, que con un orden estable deja
            # la primera mandando.
            for parte in reversed(order.split(",")):
                campo, _, direccion = parte.partition(".")
                rows.sort(
                    key=lambda r, c=campo: (r.get(c) is None, r.get(c)),
                    reverse=direccion.startswith("desc"),
                )

        # Mismo orden que PostgREST: primero se descarta el offset y sobre lo
        # que queda se aplica el limit.
        if offset:
            rows = rows[int(offset) :]

        if limit:
            rows = rows[: int(limit)]

        # EL TOPE DE POSTGREST. El de verdad devuelve como mucho mil filas
        # (`db-max-rows`) y no avisa. Un doble sin tope dejaba que una suma
        # sobre 1 500 citas pasara los tests y en producción contara mil.
        # `count` y `select_todo` no lo tienen, igual que en el cliente real.
        if not getattr(self, "_sin_tope", False):
            rows = rows[: self.TOPE]

        # UN `select` CON EMBED NO SE PROYECTA. «clients(email,baja_at)» lleva
        # comas dentro, así que partir por comas lo destrozaría. Se devuelve la
        # fila entera, que es más de lo que daría PostgREST pero nunca menos,
        # así que ningún test pasa por una columna que no exista.
        if select and select != "*" and "(" not in select:
            columnas = [c.strip() for c in select.split(",")]
            return [{c: r.get(c) for c in columnas} for r in rows]

        rows = [dict(r) for r in rows]

        if select and "salons!" in select:
            # EL EMBED ANIDADO de `get_current_user`: desde `users`, su salón, y
            # colgando de él la suspensión de la cuenta. Una sola consulta
            # responde a dos cosas —que la persona siga existiendo y que su
            # cuenta esté al corriente—, y si el doble se la saltara la
            # suspensión dejaría de probarse por el camino que de verdad corre.
            # La clave ajena va nombrada porque `users` llega a `salons` por dos
            # caminos desde la 0039.
            salones = {x["id"]: x for x in self.tables.get("salons", [])}
            cuentas = {c["id"]: c for c in self.tables.get("accounts", [])}
            for fila in rows:
                salon = salones.get(fila.get("salon_id"))
                if salon is None:
                    fila["salons"] = None
                    continue
                cuenta = cuentas.get(salon.get("account_id"))
                embebida = dict(cuenta) if cuenta else None
                if embebida is not None and "subscriptions(" in select:
                    # LA SUSCRIPCIÓN DE LA CUENTA (0086): la sesión corta a la
                    # que no tiene una viva. El lado «uno a muchos» es una lista.
                    embebida["subscriptions"] = [
                        {"estado": s.get("estado")} for s in self.tables.get("subscriptions", [])
                        if s.get("account_id") == cuenta["id"]
                    ]
                fila["salons"] = {"accounts": embebida}
            return rows

        if select and "accounts(" in select:
            # ESTE EMBED SÍ SE RESUELVE. Desde la 0040 el plan, la marca y la
            # suspensión se leen así en casi todos los endpoints, y un doble que
            # se lo saltara dejaría sin probar el camino que de verdad corre.
            # PostgREST devuelve el lado «muchos a uno» como objeto —medido
            # contra el PostgREST local—, y eso es lo que se devuelve aquí.
            por_id = {c["id"]: c for c in self.tables.get("accounts", [])}
            # SE PROYECTA A LAS COLUMNAS PEDIDAS, como hace PostgREST. Devolver
            # la cuenta entera colaba su `id` en el embed, y al aplanarla en el
            # router pisaba el `id` del salón: el listado del panel empezó a
            # devolver ids de cuenta donde el panel espera ids de salón.
            pedidas = select.split("accounts(", 1)[1].split(")", 1)[0]
            columnas = [c.strip() for c in pedidas.split(",") if c.strip()]
            for fila in rows:
                cuenta = por_id.get(fila.get("account_id"))
                fila["accounts"] = (
                    {c: cuenta.get(c) for c in columnas} if cuenta else None
                )

        return rows

    async def insert(self, table: str, body: dict, select=None) -> dict:
        self.inserts.append((table, dict(body)))
        self.tables.setdefault(table, []).append(dict(body))
        return dict(body)

    async def insert_many(self, table: str, rows: list[dict]) -> None:
        for row in rows:
            await self.insert(table, row)

    async def upsert_many(self, table: str, rows: list[dict], on_conflict: str) -> None:
        claves = [c.strip() for c in on_conflict.split(",")]
        destino = self.tables.setdefault(table, [])
        for fila in rows:
            existente = next(
                (r for r in destino if all(r.get(k) == fila.get(k) for k in claves)), None
            )
            if existente is not None:
                existente.update(fila)
            else:
                destino.append(dict(fila))
                self.inserts.append((table, dict(fila)))

    async def update(self, table: str, params: dict[str, str], body: dict, select=None) -> list[dict]:
        self.updates.append((table, dict(params), dict(body)))
        afectadas: list[dict] = []
        for row in self.tables.get(table, []):
            if _cumple_todo(row, params):
                row.update(body)
                afectadas.append(dict(row))
        return afectadas

    async def delete(self, table: str, params: dict[str, str]) -> None:
        self.tables[table] = [
            r
            for r in self.tables.get(table, [])
            if not _cumple_todo(r, params)
        ]


@pytest.fixture(autouse=True)
def _freno_de_origen_limpio() -> Iterator[None]:
    """El freno del 403 por Origin vive en memoria del PROCESO, así que sin esto
    un test se comería las filas del siguiente: todos usan el mismo `sid`."""
    from app.core.impersonation_policy import _freno_de_origen

    _freno_de_origen.limpiar()
    yield
    _freno_de_origen.limpiar()


@pytest.fixture()
def auditoria_de_soporte(monkeypatch: pytest.MonkeyPatch) -> FakePostgrest:
    """El rastro de la sesión de soporte, en memoria, para el test que lo mire.

    Las filas quedan en `auditoria_de_soporte.tables["admin_logs"]`, aparte del
    doble con los datos del salón: en producción también son dos clientes
    distintos, porque `admin_logs` se escribe con la secret key del panel y no
    con el token del inquilino.
    """
    espia = FakePostgrest({"admin_logs": []})
    monkeypatch.setattr("app.core.impersonation_policy._db_auditoria", lambda _s: espia)
    return espia


class _AuditoriaSinVigilar(FakePostgrest):
    """Doble por defecto: apunta cada intento de auditar para delatarlo luego.

    No basta con reventar aquí: el middleware se traga a propósito cualquier
    excepción de la auditoría (anotar mal no puede tumbar una acción que YA
    ocurrió), así que la excepción no haría fallar al test — solo acabaría en el
    log de aplicación, que es justo donde nadie mira. Lo que falla de verdad es
    la comprobación al desmontar el fixture.
    """

    def __init__(self) -> None:
        super().__init__({"admin_logs": []})
        self.intentos: list[dict] = []

    async def insert(self, table: str, body: dict, select=None) -> dict:
        self.intentos.append({"table": table, **body})
        raise AssertionError("auditoría de soporte sin vigilar")


@pytest.fixture(autouse=True)
def _auditoria_de_soporte_sin_red(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """Barrera doble alrededor del rastro de soporte. No es comodidad.

    (1) NINGÚN test habla con Supabase. El middleware de auditoría se construye
        su propio cliente de PostgREST —un middleware no puede usar `Depends`,
        así que `dependency_overrides` no le llega—, y las credenciales de este
        fichero entran con `os.environ.setdefault`: en una máquina con
        `SUPABASE_URL`/`SUPABASE_SECRET_KEY` exportadas, esos inserts irían al
        proyecto REAL, a una tabla que nadie puede borrar después.

    (2) El test que audite sin querer se entera. Si un test provoca escrituras
        de auditoría y no ha pedido `auditoria_de_soporte`, no las está mirando:
        el caso peligroso es el que comprueba "aquí NO se audita" contra su
        propio doble y pasa en vacío porque las filas se fueron a otro sitio.
    """
    if "auditoria_de_soporte" in request.fixturenames:
        # El test ya tiene su propio doble y responde de lo que se escriba.
        yield
        return

    centinela = _AuditoriaSinVigilar()
    monkeypatch.setattr("app.core.impersonation_policy._db_auditoria", lambda _s: centinela)
    yield
    assert not centinela.intentos, (
        "Este test provocó "
        f"{len(centinela.intentos)} escritura(s) de auditoría de soporte que nadie "
        "está mirando. Pide el fixture `auditoria_de_soporte` y compruébalas: "
        f"{centinela.intentos}"
    )


@pytest.fixture(autouse=True)
def _la_sesion_de_soporte_sin_red(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Apuntar el `session_id` de soporte (0086) habla con PostgREST con la clave
    de servicio. Fuera de la base real no hay a quién: se queda en nada, como la
    auditoría de arriba. La de verdad la prueban los tests de base real."""
    if request.node.get_closest_marker("base_real") is None:
        async def _nada(settings, sid, token):
            return None

        monkeypatch.setattr("app.core.tenant_token_broker._apuntar_la_sesion_de_soporte", _nada)


# UN TEST SALTADO NO PUEDE SER UN VERDE. Los ganchos viven en su propio módulo
# para poder probarlos desde fuera (`test_un_salto_no_es_un_verde.py`); importarlos
# aquí es lo que los registra. Con `ESTETIX_SIN_SALTOS=1` —lo pone el CI— un
# test saltado hace fallar la ejecución. Ver `tests/sin_saltos.py`.
from tests.sin_saltos import (  # noqa: E402,F401
    pytest_configure,
    pytest_runtest_logreport,
    pytest_terminal_summary,
)
from tests.sin_saltos import pytest_sessionfinish as _sin_saltos_al_terminar  # noqa: E402

# ¿ESTÁ EL STACK LOCAL? Una sola sonda por tirada, y esperando de verdad. Si en
# esta tirada no corre ningún test de base real, no pregunta nada. Ver
# `tests/stack_local.py`; importar el gancho es lo que lo registra.
from tests.stack_local import pytest_collection_modifyitems  # noqa: E402,F401

# LO COMPARTIDO VUELVE COMO ESTABA después de cada test, y el que lo deja
# cambiado se pone rojo. Importarla aquí es lo que la registra. Ver
# `tests/lo_compartido.py`.
from tests.lo_compartido import _a_lo_compartido_vuelve_como_estaba  # noqa: E402,F401

# LO QUE UNA PRUEBA DEJA, SE LO LLEVA (12-10-2026): lo que GoTrue apunta mientras
# corre un fichero de base real se borra al acabarlo. Ver `tests/lo_que_deja_una_prueba.py`.
from tests.lo_que_deja_una_prueba import comprobar_al_terminar as _filas_al_terminar  # noqa: E402
from tests.lo_que_deja_una_prueba import pytest_collection_finish, registro_de_auth  # noqa: E402,F401


def pytest_sessionfinish(session, exitstatus):
    # Dos ganchos con el mismo nombre no pueden vivir en un conftest: aquí se llaman los dos.
    _sin_saltos_al_terminar(session, exitstatus)
    _filas_al_terminar(session)


def servicio_del_me(db: "FakePostgrest") -> "FakePostgrest":
    """El cliente de servicio que `/auth/me` usa desde la 0068: lee los días de
    prueba de `plataforma_config` y el reloj de `relojes_del_plazo`. Estas
    pruebas no van del plazo: la cuenta no tiene reloj, que es lo que la base
    contestaría para una cuenta viva."""
    db.rpcs.setdefault("relojes_del_plazo", lambda body: [])
    return db
