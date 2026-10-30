"""El teléfono del salón: se valida al guardarlo y se avisa de lo que ya está mal.

POR QUÉ EXISTE ESTE FICHERO. El teléfono de un salón tenía diez dígitos
nacionales —uno de más para España— y el sistema lo guardó sin chistar. El botón
«Llamar al salón» de TODOS sus recordatorios llevaba muerto desde el primer
correo, en móvil también, y no se supo hasta que alguien fue a formatear ese
número para otra cosa. Un campo que solo se comprueba cuando se usa es un campo
sin comprobar.

Lo que se fija, por orden:

  1. Que un número que no se puede marcar NO se guarda.
  2. Que el motivo sea el de VERDAD —le sobra un dígito, le falta, ese prefijo
     no existe—: «campo inválido» no le dice a nadie qué corregir.
  3. Que lo que ya estaba guardado mal se vea al abrir Ajustes, y no por un
     cliente que no pudo llamar.
  4. Que un número escrito en local se guarde en su forma canónica, que es la
     única que se puede marcar desde cualquier sitio.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.core.config import Settings, get_settings
from app.core.security import (
    CurrentUser,
    get_current_user,
    get_postgrest_client,
    get_storage_client,
)
from app.routers import salon as router_salon

SALON = "11111111-1111-1111-1111-111111111111"

# El de Salón Demo, tal cual estaba en producción: un dígito de más.
ROTO = "+346587231649"


def _settings() -> Settings:
    return Settings(
        supabase_url="http://local",
        supabase_secret_key="k",
        supabase_jwks_url="http://local/jwks",
        database_url="postgresql://x",
        frontend_base_url="https://estetixapp.es",
    )


class FakeDB:
    """El salón y —si lo tiene— su número de WhatsApp, que da el país."""

    def __init__(self, telefono: str | None = None, whatsapp: str | None = None):
        self.salon = {
            "zona_horaria": "Europe/Madrid",
            "nombre_comercial": "Salón Demo",
            "cif": None,
            "direccion": "C/ Mayor 14",
            "telefono": telefono,
            "email": None,
            "plan": "pro",
            "accent_color": "#C9A84C",
            "logo_url": None,
        }
        self.whatsapp = whatsapp
        self.updates: list[dict] = []

    async def select(self, table, params=None):
        if table == "salons":
            return [dict(self.salon)]
        if table == "salon_whatsapp_config":
            return [{"display_phone_number": self.whatsapp}] if self.whatsapp else []
        return []

    async def update(self, table, params, body):
        self.updates.append(dict(body))
        self.salon.update(body)
        return [dict(self.salon)]


class FakeStorage:
    async def signed_url(self, *a, **kw):
        return None


def _cliente(db: FakeDB, rol: str = "admin") -> TestClient:
    app = FastAPI()
    app.include_router(router_salon.router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        sub="u1", salon_id=SALON, rol=rol, email="duena@salon.test"
    )
    app.dependency_overrides[get_postgrest_client] = lambda: db
    app.dependency_overrides[get_storage_client] = lambda: FakeStorage()
    app.dependency_overrides[get_settings] = _settings
    return TestClient(app)


# ═══════════════════════════════════════════════════ guardar


def test_el_numero_de_produccion_YA_NO_se_puede_guardar():
    """EL CASO. Diez dígitos nacionales: uno de más para España."""
    db = FakeDB()
    r = _cliente(db).patch("/salon/profile", json={"telefono": ROTO})

    assert r.status_code == 422
    detalle = r.json()["detail"]
    assert detalle["codigo"] == "telefono_no_valido"
    assert "de más" in detalle["mensaje"]
    # Y NO SE ESCRIBE. Un 422 que además guarda sería lo peor de los dos mundos.
    assert db.updates == []


@pytest.mark.parametrize(
    ("escrito", "trozo_del_motivo"),
    [
        (ROTO, "de más"),
        ("60011", "de menos"),
        ("+999123456789", "prefijo de país no existe"),
        ("no tengo", "no se parece a un teléfono"),
        # Nueve dígitos, la longitud correcta, pero ningún operador español
        # empieza por ahí. Es el número inventado clásico.
        ("+34 111 11 11 11", "no existe"),
    ],
)
def test_el_motivo_dice_QUE_esta_mal(escrito, trozo_del_motivo):
    """«Campo inválido» no le dice a nadie qué corregir. Que le sobre un dígito
    y que ese prefijo no exista son dos problemas con dos arreglos distintos."""
    r = _cliente(FakeDB()).patch("/salon/profile", json={"telefono": escrito})

    assert r.status_code == 422
    assert trozo_del_motivo in r.json()["detail"]["mensaje"]


def test_el_motivo_trae_un_ejemplo_DE_ESE_PAIS():
    """Un salón tailandés no necesita un ejemplo español. El ejemplo sale de
    `phonenumbers` para la región del salón, no de una cadena escrita a mano."""
    db = FakeDB(whatsapp="+66812345678")
    r = _cliente(db).patch("/salon/profile", json={"telefono": "123"})

    assert "+66" in r.json()["detail"]["mensaje"]


@pytest.mark.parametrize(
    ("escrito", "guardado"),
    [
        ("+34658723149", "+34658723149"),
        ("658723149", "+34658723149"),
        ("944 12 34 56", "+34944123456"),
        ("  600 11 22 33  ", "+34600112233"),
    ],
)
def test_un_numero_valido_se_guarda_en_su_forma_CANONICA(escrito, guardado):
    """E.164, que es la única forma que se puede marcar desde cualquier sitio.

    Es también lo que hace que el correo pueda formatearlo: `region_del_salon`
    saca el país del prefijo, y un número en formato local no lo lleva.
    """
    db = FakeDB()
    r = _cliente(db).patch("/salon/profile", json={"telefono": escrito})

    assert r.status_code == 200
    assert db.updates[-1]["telefono"] == guardado
    assert r.json()["telefono"] == guardado


def test_el_pais_sale_del_WHATSAPP_del_salon_si_lo_tiene():
    """Un fijo de Londres escrito en local. Sin la pista del país, «020 7946
    0958» no es un número español y se rechazaría."""
    db = FakeDB(whatsapp="+442079460958")
    r = _cliente(db).patch("/salon/profile", json={"telefono": "020 7946 0958"})

    assert r.status_code == 200
    assert db.updates[-1]["telefono"] == "+442079460958"


def test_dejar_el_telefono_vacio_es_valido():
    """Es un campo opcional. Lo que no vale es tener algo que parece un teléfono
    y no lo es."""
    db = FakeDB(telefono="+34600112233")
    r = _cliente(db).patch("/salon/profile", json={"telefono": ""})

    assert r.status_code == 200
    assert db.updates[-1]["telefono"] is None


def test_guardar_OTRA_cosa_no_toca_el_telefono():
    """El control: un número guardado que ya no valida no impide cambiar la
    dirección. Sería absurdo bloquear el perfil entero por un campo opcional."""
    db = FakeDB(telefono=ROTO)
    r = _cliente(db).patch("/salon/profile", json={"direccion": "C/ Nueva 2"})

    assert r.status_code == 200
    assert "telefono" not in db.updates[-1]


# ═══════════════════════════════════════════════════ el aviso al leer


def test_al_abrir_ajustes_se_ve_lo_que_ya_estaba_mal():
    """Enterarse por un cliente que no pudo llamar es la peor forma de
    enterarse. Esta es la barata."""
    r = _cliente(FakeDB(telefono=ROTO)).get("/salon/profile")

    assert r.status_code == 200
    assert "de más" in r.json()["telefono_aviso"]


@pytest.mark.parametrize("telefono", ["+34658723149", "944 12 34 56", None, ""])
def test_un_telefono_que_vale_NO_lleva_aviso(telefono):
    r = _cliente(FakeDB(telefono=telefono)).get("/salon/profile")

    assert r.json()["telefono_aviso"] is None


def test_tras_guardarlo_bien_el_aviso_desaparece():
    """Sin recargar: el PATCH devuelve el aviso a null explícitamente."""
    db = FakeDB(telefono=ROTO)
    cliente = _cliente(db)
    assert cliente.get("/salon/profile").json()["telefono_aviso"] is not None

    r = cliente.patch("/salon/profile", json={"telefono": "658723149"})
    assert r.json()["telefono_aviso"] is None


# ═══════════════════════════════ guardado canónico, mostrado legible


@pytest.mark.parametrize(
    ("guardado", "legible"),
    [
        ("+34944123456", "+34 944 12 34 56"),
        ("+34658723149", "+34 658 72 31 49"),
        ("+442079460958", "+44 20 7946 0958"),
        ("+66812345678", "+66 81 234 5678"),
    ],
)
def test_el_perfil_devuelve_el_numero_ESCRITO_PARA_LEERLO(guardado, legible):
    """E.164 en `telefono`, legible en `telefono_legible`. Guardado canónico,
    mostrado legible: el salón teclea «944 12 34 56», recarga, y no se encuentra
    una cadena pegada."""
    r = _cliente(FakeDB(telefono=guardado)).get("/salon/profile")

    assert r.json()["telefono"] == guardado
    assert r.json()["telefono_legible"] == legible


def test_al_guardar_ya_vuelve_legible_sin_recargar():
    db = FakeDB()
    r = _cliente(db).patch("/salon/profile", json={"telefono": "944 12 34 56"})

    assert r.json()["telefono"] == "+34944123456"
    assert r.json()["telefono_legible"] == "+34 944 12 34 56"


@pytest.mark.parametrize("telefono", [None, ""])
def test_sin_telefono_no_hay_nada_que_leer(telefono):
    assert _cliente(FakeDB(telefono=telefono)).get("/salon/profile").json()[
        "telefono_legible"
    ] is None


def test_un_numero_guardado_que_NO_VALE_no_se_maquilla():
    """Los que se guardaron antes de que esto se validara siguen ahí. Enseñarlos
    bonitos escondería que ese botón de llamar no llama a nadie — la misma
    decisión que se tomó para el correo."""
    r = _cliente(FakeDB(telefono=ROTO)).get("/salon/profile")

    assert r.json()["telefono_legible"] is None
    assert r.json()["telefono"] == ROTO
    assert r.json()["telefono_aviso"] is not None

