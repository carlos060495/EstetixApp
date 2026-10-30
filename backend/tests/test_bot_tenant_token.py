"""Acceso del webhook de WhatsApp a los datos de un salón.

Lo que se fija aquí:

  1. Que el webhook entra con un token REAL del salón —el mismo mecanismo que
     la sesión de soporte— y no con service_role. Se comprueba mirando la
     cabecera Authorization del cliente que sale.
  2. Que la comprobación de IDENTIDAD del broker se le aplica también: si
     GoTrue devolviera un `sub` que no es el esperado, no se entra. Es el
     motivo de compartir `acunar_token_tenant` en vez de copiarlo.
  3. Que dos CUENTAS no comparten token JAMÁS. Un fallo aquí sería un salón
     escribiendo en la conversación de otro.
  4. Que un fallo de configuración (sin admin enlazado) y uno pasajero (GoTrue
     caído) se distinguen: de eso depende que Meta reintente o no.
  5. Que un SEGUNDO CENTRO entra con el admin de su cuenta. Se buscaba un admin
     con `salon_id` = ese centro, que no existe: la ficha del titular apunta al
     primero. Su bot callaba, su widget daba 503 y sus recordatorios fallaban.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import app.core.bot_tenant_token as btt
from app.core.bot_tenant_token import (
    BotSinAcceso,
    SalonSinBot,
    cliente_del_salon,
    reiniciar_custodia,
)
from app.core.config import Settings
from app.db.postgrest_client import PostgrestError
from app.db.supabase_auth_client import SupabaseAuthError

SALON_A = "11111111-1111-1111-1111-111111111111"
SALON_B = "22222222-2222-2222-2222-222222222222"
# El segundo centro de la cuenta de A. Nadie tiene su `salon_id` en la ficha.
SALON_A2 = "11111111-2222-2222-2222-222222222222"
CUENTA_A = "aaaaaaaa-0000-0000-0000-00000000000a"
CUENTA_B = "bbbbbbbb-0000-0000-0000-00000000000b"
AUTH_A = "99999999-0000-0000-0000-00000000000a"
AUTH_B = "99999999-0000-0000-0000-00000000000b"

AJUSTES = Settings(
    supabase_url="http://local",
    supabase_secret_key="secret",
    supabase_jwks_url="http://local/jwks",
    database_url="postgresql://x",
)

USUARIOS = [
    {"salon_id": SALON_A, "account_id": CUENTA_A, "rol": "admin",
     "email": "a@salon.test", "auth_user_id": AUTH_A},
    {"salon_id": SALON_B, "account_id": CUENTA_B, "rol": "admin",
     "email": "b@salon.test", "auth_user_id": AUTH_B},
]

SALONES = [
    {"id": SALON_A, "account_id": CUENTA_A},
    {"id": SALON_A2, "account_id": CUENTA_A},
    {"id": SALON_B, "account_id": CUENTA_B},
]


class ServicioFalso:
    """El cliente de service_role. Solo se le piden `salons` y `users`.

    APLICA LOS FILTROS QUE RECIBE, y no devuelve lo que toque: un filtro que el
    doble no aplica es un filtro que los tests no prueban. Si el código volviera
    a buscar al admin por `salon_id`, el segundo centro se quedaría sin nadie.
    """

    def __init__(self, filas=None, salones=None):
        self.filas = USUARIOS if filas is None else filas
        self.salones = SALONES if salones is None else salones
        self.consultas = 0
        self.revienta = False

    async def select(self, table, params=None):
        assert table in ("users", "salons"), f"el bot no debe leer '{table}' con service_role"
        if self.revienta:
            raise PostgrestError(500, "PostgREST caído")
        self.consultas += 1
        p = params or {}
        filas = self.salones if table == "salons" else self.filas
        for clave, valor in p.items():
            if clave in ("select", "order", "limit"):
                continue
            assert valor.startswith("eq."), f"filtro que el doble no entiende: {clave}={valor}"
            filas = [f for f in filas if f.get(clave) == valor.removeprefix("eq.")]
        return filas


class AuthFalso:
    """GoTrue. Devuelve un token distinto por email y el `sub` que le digan."""

    def __init__(self, sub_forzado=None, revienta=False):
        self.acunados = 0
        self.cerrados: list[str] = []
        self.sub_forzado = sub_forzado
        self.revienta = revienta
        self.por_email = {"a@salon.test": AUTH_A, "b@salon.test": AUTH_B}
        self._ultimo_email = None

    async def generate_magiclink(self, email):
        if self.revienta:
            raise SupabaseAuthError(503, "GoTrue no responde")
        self._ultimo_email = email
        return {"properties": {"hashed_token": f"hash-{email}"}}

    async def verify_magiclink(self, token_hash):
        self.acunados += 1
        sub = self.sub_forzado or self.por_email[self._ultimo_email]
        return {"access_token": f"token-de-{self._ultimo_email}", "user": {"id": sub}}

    async def logout_local(self, access_token):
        self.cerrados.append(access_token)


@pytest.fixture()
def auth(monkeypatch):
    reiniciar_custodia()
    falso = AuthFalso()
    monkeypatch.setattr(btt, "_cliente_auth", lambda _s: falso)
    yield falso
    reiniciar_custodia()


def _token(cliente) -> str:
    return cliente._headers["Authorization"].removeprefix("Bearer ")


# ------------------------------------------------------- se entra CON RLS


@pytest.mark.anyio
async def test_el_cliente_lleva_un_token_del_salon_y_no_la_secret_key(auth):
    servicio = ServicioFalso()
    db = await cliente_del_salon(AJUSTES, servicio, SALON_A)
    # Si aquí saliera la secret key, PostgREST evaluaría la RLS como
    # service_role y el webhook escribiría en cualquier salón.
    assert _token(db) == "token-de-a@salon.test"
    assert _token(db) != AJUSTES.supabase_secret_key


@pytest.mark.anyio
async def test_dos_cuentas_nunca_comparten_token(auth):
    servicio = ServicioFalso()
    a = await cliente_del_salon(AJUSTES, servicio, SALON_A)
    b = await cliente_del_salon(AJUSTES, servicio, SALON_B)
    assert _token(a) != _token(b)
    assert _token(a) == "token-de-a@salon.test"
    assert _token(b) == "token-de-b@salon.test"


# ----------------------------------------------------------------- custodia


@pytest.mark.anyio
async def test_el_token_se_reaprovecha_en_lugar_de_acunar_por_mensaje(auth):
    servicio = ServicioFalso()
    for _ in range(5):
        await cliente_del_salon(AJUSTES, servicio, SALON_A)
    # Una ráfaga de mensajes del mismo salón no puede abrir cinco sesiones en
    # GoTrue ni releer `salons` y `users` cinco veces.
    assert auth.acunados == 1
    assert servicio.consultas == 2


@pytest.mark.anyio
async def test_cuando_caduca_se_vuelve_a_acunar(auth, monkeypatch):
    servicio = ServicioFalso()
    await cliente_del_salon(AJUSTES, servicio, SALON_A)
    assert auth.acunados == 1

    despues = datetime.now(timezone.utc) + timedelta(minutes=btt._MINUTOS_CUSTODIA + 1)
    monkeypatch.setattr(btt, "_ahora", lambda: despues)
    await cliente_del_salon(AJUSTES, servicio, SALON_A)
    assert auth.acunados == 2


@pytest.mark.anyio
async def test_lo_caducado_se_retira_de_la_custodia_y_no_solo_se_ignora(auth, monkeypatch):
    servicio = ServicioFalso()
    await cliente_del_salon(AJUSTES, servicio, SALON_A)
    assert SALON_A in btt._custodia

    despues = datetime.now(timezone.utc) + timedelta(minutes=btt._MINUTOS_CUSTODIA + 1)
    monkeypatch.setattr(btt, "_ahora", lambda: despues)
    # Se pide OTRO salón: la purga perezosa debe barrer el de A igualmente, o
    # un salón que deje de escribir queda residente para siempre.
    await cliente_del_salon(AJUSTES, servicio, SALON_B)
    assert SALON_A not in btt._custodia


# ------------------------------------------------------------- identidad


@pytest.mark.anyio
async def test_si_gotrue_acuna_otra_identidad_no_se_entra(monkeypatch):
    reiniciar_custodia()
    # El caso feo: `users.email` diverge y GoTrue acuña un usuario que no es el
    # que elegimos. Sin esta comprobación el backend creería estar en el salón
    # A mientras PostgREST devolvería los datos de otro.
    falso = AuthFalso(sub_forzado="00000000-0000-0000-0000-000000000000")
    monkeypatch.setattr(btt, "_cliente_auth", lambda _s: falso)
    with pytest.raises(BotSinAcceso):
        await cliente_del_salon(AJUSTES, ServicioFalso(), SALON_A)
    # Y la credencial que llegó a acuñarse se cierra, no se queda viva.
    assert falso.cerrados == ["token-de-a@salon.test"]
    assert SALON_A not in btt._custodia
    reiniciar_custodia()


# --------------------------------------------------------- segundo centro


@pytest.mark.anyio
async def test_el_segundo_centro_entra_con_el_admin_de_su_cuenta(auth):
    """EL FALLO MEDIDO. Nadie tiene `salon_id` = SALON_A2 en su ficha, y aun así
    su bot tiene que poder atender: lo hace la titular, que ve los dos centros."""
    db = await cliente_del_salon(AJUSTES, ServicioFalso(), SALON_A2)
    assert _token(db) == "token-de-a@salon.test"


@pytest.mark.anyio
async def test_un_centro_de_OTRA_cuenta_nunca_entra_con_ese_admin(auth):
    """El contrapeso: buscar por cuenta no puede acabar en «el primer admin que
    haya». La cuenta B no tiene admin, y su centro NO se lleva el de A."""
    servicio = ServicioFalso(filas=[USUARIOS[0]])
    with pytest.raises(SalonSinBot):
        await cliente_del_salon(AJUSTES, servicio, SALON_B)
    assert auth.acunados == 0


@pytest.mark.anyio
async def test_si_hay_varios_admins_entra_siempre_el_mas_antiguo(auth):
    """Determinista: el orden lo pide la consulta, y el doble respeta el orden de
    las filas tal y como vendrían ordenadas."""
    servicio = ServicioFalso(
        filas=[
            {"salon_id": SALON_A, "account_id": CUENTA_A, "rol": "admin",
             "email": "a@salon.test", "auth_user_id": AUTH_A},
            {"salon_id": SALON_A2, "account_id": CUENTA_A, "rol": "admin",
             "email": "b@salon.test", "auth_user_id": AUTH_B},
        ]
    )
    pedido = {}
    original = servicio.select

    async def _select(table, params=None):
        if table == "users":
            pedido.update(params or {})
        return await original(table, params)

    servicio.select = _select
    a2 = await cliente_del_salon(AJUSTES, servicio, SALON_A2)
    assert pedido.get("order") == "created_at.asc"
    # Aunque haya un admin cuya ficha apunta justo a SALON_A2, entra el primero.
    assert _token(a2) == "token-de-a@salon.test"


@pytest.mark.anyio
async def test_un_salon_que_no_existe_es_un_fallo_permanente(auth):
    with pytest.raises(SalonSinBot):
        await cliente_del_salon(AJUSTES, ServicioFalso(salones=[]), SALON_A)
    assert auth.acunados == 0


# ------------------------------------------------------- fallos distinguidos


@pytest.mark.anyio
async def test_salon_sin_admin_enlazado_es_un_fallo_permanente(auth):
    servicio = ServicioFalso(
        [{"salon_id": SALON_A, "account_id": CUENTA_A, "rol": "admin",
          "email": "a@salon.test", "auth_user_id": None}]
    )
    with pytest.raises(SalonSinBot):
        await cliente_del_salon(AJUSTES, servicio, SALON_A)


@pytest.mark.anyio
async def test_salon_sin_ningun_admin_es_un_fallo_permanente(auth):
    with pytest.raises(SalonSinBot):
        await cliente_del_salon(AJUSTES, ServicioFalso([]), SALON_A)


@pytest.mark.anyio
async def test_gotrue_caido_es_un_fallo_pasajero(monkeypatch):
    reiniciar_custodia()
    monkeypatch.setattr(btt, "_cliente_auth", lambda _s: AuthFalso(revienta=True))
    with pytest.raises(BotSinAcceso):
        await cliente_del_salon(AJUSTES, ServicioFalso(), SALON_A)
    reiniciar_custodia()


@pytest.mark.anyio
async def test_postgrest_caido_es_un_fallo_pasajero(auth):
    servicio = ServicioFalso()
    servicio.revienta = True
    with pytest.raises(BotSinAcceso):
        await cliente_del_salon(AJUSTES, servicio, SALON_A)


@pytest.mark.anyio
async def test_un_fallo_no_deja_nada_a_medias_en_la_custodia(auth):
    servicio = ServicioFalso([])
    with pytest.raises(SalonSinBot):
        await cliente_del_salon(AJUSTES, servicio, SALON_A)
    assert SALON_A not in btt._custodia
