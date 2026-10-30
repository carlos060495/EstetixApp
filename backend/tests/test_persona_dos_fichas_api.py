"""El enlace entre fichas, por la puerta por la que entra una persona.

`supabase/tests/persona_en_dos_centros.sql` prueba la GARANTIA -que la base no
deja agendarla dos veces- contra Postgres. Este fichero prueba el CAMINO: que
al dar de alta a alguien en el segundo centro se pueda decir «ya trabaja en el
otro», que el enlace se cree de verdad y que las horas se puedan sumar.

Contra la base local, no contra un doble, por la razon de siempre en esta parte
del proyecto: lo que hace el trabajo son un indice unico y tres triggers, y un
doble no tiene ninguno de los tres.
"""

from __future__ import annotations

import uuid

import pytest

from tests.test_camino_humano_dos_centros import (  # noqa: F401
    _cabeceras,
    anyio_backend,
    dos_centros,
)

# `base_real` y nada más. Que el stack esté o no lo decide `tests/stack_local.py`
# UNA sola vez por tirada, esperando de verdad; antes lo preguntaba cada fichero
# por su cuenta con dos segundos de paciencia, y los tests DESAPARECÍAN. En modo
# estricto (`ESTETIX_SIN_SALTOS=1`), que no esté es un error a gritos, nunca un salto.
pytestmark = [
    pytest.mark.base_real,
]


def _alta(cliente, centro, nombre, **extra):
    return cliente.post(
        "/employees",
        json={"nombre": nombre, "telefono": "600111222", **extra},
        headers=_cabeceras(centro),
    )


# ═══════════════════════ 1. crear el enlace al dar de alta


@pytest.mark.anyio
async def test_al_alta_en_el_segundo_centro_se_dice_que_ya_trabaja_en_el_otro(dos_centros):
    cliente = dos_centros["cliente"]
    centro_1 = dos_centros["salon_id"]
    centro_2 = dos_centros["centro_2"]

    eva_1 = _alta(cliente, centro_1, "Eva")
    assert eva_1.status_code == 201, eva_1.text
    assert eva_1.json()["persona_id"] is None, "una ficha sola no nace enlazada"

    eva_2 = _alta(cliente, centro_2, "Eva", vincular_con=eva_1.json()["id"])
    assert eva_2.status_code == 201, eva_2.text

    # Las DOS apuntan a la misma persona: la primera se sella tambien, si no el
    # enlace seria de ida y no de vuelta.
    persona = eva_2.json()["persona_id"]
    assert persona == eva_1.json()["id"]

    de_nuevo = cliente.get(f"/employees/{eva_1.json()['id']}", headers=_cabeceras(centro_1))
    assert de_nuevo.json()["persona_id"] == persona, "la ficha original quedo sin sellar"


@pytest.mark.anyio
async def test_sin_pedirlo_nadie_queda_enlazado(dos_centros):
    """LA EQUIVALENCIA. Es opcional y poco frecuente: quien no lo marque tiene
    exactamente la ficha de siempre, y hoy eso es toda la plantilla."""
    r = _alta(dos_centros["cliente"], dos_centros["centro_2"], "Nadie enlazada")

    assert r.status_code == 201
    assert r.json()["persona_id"] is None


@pytest.mark.anyio
async def test_no_se_puede_enlazar_con_una_ficha_de_ESTE_mismo_centro(dos_centros):
    cliente = dos_centros["cliente"]
    centro_2 = dos_centros["centro_2"]
    otra = _alta(cliente, centro_2, "Ya esta aqui")

    r = _alta(cliente, centro_2, "La misma", vincular_con=otra.json()["id"])

    assert r.status_code == 400, r.text
    assert "mismo centro" in r.json()["detail"]


@pytest.mark.anyio
async def test_no_se_le_puede_dar_DOS_fichas_en_el_mismo_centro(dos_centros):
    """El indice unico lo impediria igual; se adelanta para poder explicarlo."""
    cliente = dos_centros["cliente"]
    centro_1 = dos_centros["salon_id"]
    centro_2 = dos_centros["centro_2"]
    eva_1 = _alta(cliente, centro_1, "Eva")
    _alta(cliente, centro_2, "Eva", vincular_con=eva_1.json()["id"])

    repetida = _alta(cliente, centro_2, "Eva otra vez", vincular_con=eva_1.json()["id"])

    assert repetida.status_code == 409, repetida.text
    assert "ya tiene una ficha" in repetida.json()["detail"]


@pytest.mark.anyio
async def test_una_ficha_inventada_no_enlaza_con_nada(dos_centros):
    """LA FRONTERA. El `select` va con el token del inquilino, asi que una
    ficha que la RLS no deja ver no existe para este endpoint. Se comprueba con
    un id que no esta en ninguna cuenta; que uno de OTRA cuenta tampoco entre
    lo prueba `supabase/tests/account_isolation.sql` contra la base."""
    r = _alta(
        dos_centros["cliente"],
        dos_centros["centro_2"],
        "Fantasma",
        vincular_con=str(uuid.uuid4()),
    )

    assert r.status_code == 400, r.text


# ═══════════════════════ 2. a quien se ofrece enlazar


@pytest.mark.anyio
async def test_el_desplegable_ofrece_al_equipo_del_otro_centro(dos_centros):
    cliente = dos_centros["cliente"]
    centro_1 = dos_centros["salon_id"]
    centro_2 = dos_centros["centro_2"]
    eva_1 = _alta(cliente, centro_1, "Eva")

    ofrecidos = cliente.get("/employees/vinculables", headers=_cabeceras(centro_2))

    assert ofrecidos.status_code == 200, ofrecidos.text
    assert eva_1.json()["id"] in {o["id"] for o in ofrecidos.json()}
    # Y trae el nombre del centro, que es lo que desambigua a dos «Eva».
    fila = next(o for o in ofrecidos.json() if o["id"] == eva_1.json()["id"])
    assert fila["centro"] == "Centro Uno"


@pytest.mark.anyio
async def test_no_se_ofrece_a_quien_YA_tiene_ficha_aqui(dos_centros):
    """Ofrecer algo que al pulsarlo da 409 es peor que no ofrecerlo."""
    cliente = dos_centros["cliente"]
    centro_1 = dos_centros["salon_id"]
    centro_2 = dos_centros["centro_2"]
    eva_1 = _alta(cliente, centro_1, "Eva")
    _alta(cliente, centro_2, "Eva", vincular_con=eva_1.json()["id"])

    ofrecidos = cliente.get("/employees/vinculables", headers=_cabeceras(centro_2))

    assert eva_1.json()["id"] not in {o["id"] for o in ofrecidos.json()}


@pytest.mark.anyio
async def test_el_equipo_de_ESTE_centro_no_se_ofrece_a_si_mismo(dos_centros):
    """El control del test de arriba: si el listado estuviera vacio por otro
    motivo, aquel pasaria sin demostrar nada."""
    cliente = dos_centros["cliente"]
    centro_2 = dos_centros["centro_2"]
    de_aqui = _alta(cliente, centro_2, "De este centro")

    ofrecidos = cliente.get("/employees/vinculables", headers=_cabeceras(centro_2))

    assert de_aqui.json()["id"] not in {o["id"] for o in ofrecidos.json()}


# ═══════════════════════ 3. el tope de empleados


@pytest.mark.anyio
async def test_la_persona_enlazada_OCUPA_CUPO_en_los_dos_centros(dos_centros):
    """LA DECISION, fijada.

    El plan es de la cuenta; el limite, de cada centro. Una persona que trabaja
    en los dos ocupa un puesto en cada uno, porque en los dos hay que atenderla,
    pagarla y ponerla en cuadro. Enlazar NO es una via para meter gente gratis
    en el segundo local.
    """
    cliente = dos_centros["cliente"]
    centro_1 = dos_centros["salon_id"]
    centro_2 = dos_centros["centro_2"]
    eva_1 = _alta(cliente, centro_1, "Eva")
    eva_2 = _alta(cliente, centro_2, "Eva", vincular_con=eva_1.json()["id"])
    assert eva_2.status_code == 201

    equipo_2 = cliente.get("/employees", headers=_cabeceras(centro_2))

    assert eva_2.json()["id"] in {e["id"] for e in equipo_2.json()}, (
        "la ficha enlazada no cuenta en el equipo del centro 2: el cupo no la veria"
    )


# ═══════════════════════ 4. las horas de la persona


@pytest.mark.anyio
async def test_las_horas_de_una_ficha_sola_no_traen_suma_de_persona(dos_centros):
    """LA EQUIVALENCIA otra vez: para el 99% de la plantilla la respuesta es
    exactamente la de antes."""
    cliente = dos_centros["cliente"]
    centro_1 = dos_centros["salon_id"]
    sola = _alta(cliente, centro_1, "Trabaja en un sitio")

    r = cliente.get(f"/employees/{sola.json()['id']}/fichajes", headers=_cabeceras(centro_1))

    assert r.status_code == 200, r.text
    assert r.json()["persona"] is None


@pytest.mark.anyio
async def test_las_horas_de_la_persona_se_suman_entre_centros(dos_centros):
    cliente = dos_centros["cliente"]
    centro_1 = dos_centros["salon_id"]
    centro_2 = dos_centros["centro_2"]
    eva_1 = _alta(cliente, centro_1, "Eva")
    eva_2 = _alta(cliente, centro_2, "Eva", vincular_con=eva_1.json()["id"])

    r = cliente.get(f"/employees/{eva_1.json()['id']}/fichajes", headers=_cabeceras(centro_1))

    assert r.status_code == 200, r.text
    persona = r.json()["persona"]
    assert persona is not None, "enlazada y sin suma: el enlace no sirve para lo segundo"
    assert {c["salon_id"] for c in persona["centros"]} == {centro_1, centro_2}
    assert persona["total_min"] == sum(c["minutos"] for c in persona["centros"])
    # Y las horas de ESTA ficha siguen siendo las de este centro: la nomina del
    # local no cambia por haber enlazado a nadie.
    assert r.json()["horas_mes_min"] == next(
        c["minutos"] for c in persona["centros"] if c["salon_id"] == centro_1
    )
    assert eva_2.json()["persona_id"] is not None


# ═══════════════════════ 5. lo que se copia al enlazar


@pytest.mark.anyio
async def test_el_desplegable_trae_lo_que_se_puede_copiar(dos_centros):
    """Copiar UNA VEZ para empezar: movil, email, puesto y servicios.

    Viajan en el mismo listado a proposito. Pedir la ficha despues con
    `GET /employees/{id}` daria 404: ese endpoint filtra por el centro activo y
    la ficha es del OTRO.
    """
    cliente = dos_centros["cliente"]
    centro_1 = dos_centros["salon_id"]
    centro_2 = dos_centros["centro_2"]

    puesto = cliente.post(
        "/puestos", json={"nombre": "Estilista senior"}, headers=_cabeceras(centro_1)
    )
    assert puesto.status_code in (200, 201), puesto.text
    servicio = cliente.post(
        "/services",
        json={"nombre": "Corte", "precio": 20, "duracion_min": 30},
        headers=_cabeceras(centro_1),
    )
    assert servicio.status_code in (200, 201), servicio.text

    eva = cliente.post(
        "/employees",
        json={
            "nombre": "Eva Navarro",
            "telefono": "600999888",
            "email": "eva@ejemplo.com",
            "puesto_id": puesto.json()["id"],
            "service_ids": [servicio.json()["id"]],
            "comision_servicios": 42,
        },
        headers=_cabeceras(centro_1),
    )
    assert eva.status_code == 201, eva.text

    fila = next(
        o
        for o in cliente.get("/employees/vinculables", headers=_cabeceras(centro_2)).json()
        if o["id"] == eva.json()["id"]
    )

    # De la PERSONA: se copian siempre. Nadie tiene dos moviles segun el local.
    assert fila["telefono"] == "600999888"
    assert fila["email"] == "eva@ejemplo.com"
    # Del TRABAJO: por NOMBRE, porque los ids son de cada centro.
    assert fila["puesto"] == "Estilista senior"
    assert fila["servicios"] == ["Corte"]


@pytest.mark.anyio
async def test_la_COMISION_no_viaja_siquiera(dos_centros):
    """EL DINERO NO SE HEREDA POR DESCUIDO.

    No es que la pantalla no lo pinte: es que el dato no sale de la API. Lo que
    no viaja no se puede copiar por error el dia que alguien toque el
    formulario.
    """
    cliente = dos_centros["cliente"]
    centro_1 = dos_centros["salon_id"]
    centro_2 = dos_centros["centro_2"]

    cliente.post(
        "/employees",
        json={"nombre": "Eva", "telefono": "600111222", "comision_servicios": 42},
        headers=_cabeceras(centro_1),
    )

    ofrecidos = cliente.get("/employees/vinculables", headers=_cabeceras(centro_2)).json()

    assert ofrecidos
    # SE MIRAN LAS CLAVES, no el texto de la fila. La primera version de esto
    # comprobaba `"42" not in str(fila)`, y eso falla en cuanto un uuid lleva un
    # «42» dentro: paso en la suite completa, con la comision perfectamente
    # fuera. Un control que salta por el contenido de un identificador no
    # controla nada.
    for fila in ofrecidos:
        assert not [k for k in fila if k.startswith("comision")], fila


@pytest.mark.anyio
async def test_el_hash_del_PIN_no_sale_de_la_api_nunca(dos_centros):
    """Solo si lo tiene o no, que es lo que el formulario necesita saber."""
    cliente = dos_centros["cliente"]
    centro_1 = dos_centros["salon_id"]
    centro_2 = dos_centros["centro_2"]

    cliente.post(
        "/employees",
        json={"nombre": "Eva", "telefono": "600111222", "pin_fichaje": "1234"},
        headers=_cabeceras(centro_1),
    )

    fila = cliente.get("/employees/vinculables", headers=_cabeceras(centro_2)).json()[0]

    assert fila["tiene_pin"] is True
    assert "pin_fichaje_hash" not in fila
    assert "$argon2" not in str(fila)


# ═══════════════════════ 6. el PIN al dar de alta a alguien ya enlazada


@pytest.mark.anyio
async def test_la_ficha_nueva_HEREDA_el_PIN_y_no_se_genera_otro(dos_centros):
    """EL FORMULARIO NO PUEDE CONTRADECIR A LA BASE.

    El trigger `la_ficha_adopta_el_pin_de_la_persona` impone el PIN de ella se
    mande lo que se mande, asi que un PIN propuesto al azar en el alta no
    llegaba a existir en ninguna parte: se enseñaba un numero que no servia
    para fichar.
    """
    cliente = dos_centros["cliente"]
    centro_1 = dos_centros["salon_id"]
    centro_2 = dos_centros["centro_2"]

    eva = cliente.post(
        "/employees",
        json={"nombre": "Eva", "telefono": "600111222", "pin_fichaje": "1234"},
        headers=_cabeceras(centro_1),
    )
    nueva = cliente.post(
        "/employees",
        json={"nombre": "Eva", "telefono": "600111222", "vincular_con": eva.json()["id"]},
        headers=_cabeceras(centro_2),
    )

    assert nueva.status_code == 201, nueva.text
    assert nueva.json()["tiene_pin"] is True, "la ficha nueva nacio sin PIN: no podria fichar"


@pytest.mark.anyio
async def test_cambiar_el_PIN_al_enlazar_lo_cambia_en_LOS_DOS_centros(dos_centros):
    """Y si se pide cambiarlo, cambia en los dos. Es lo que el formulario avisa
    en la misma frase en la que lo ofrece."""
    cliente = dos_centros["cliente"]
    centro_1 = dos_centros["salon_id"]
    centro_2 = dos_centros["centro_2"]

    eva = cliente.post(
        "/employees",
        json={"nombre": "Eva", "telefono": "600111222", "pin_fichaje": "1234"},
        headers=_cabeceras(centro_1),
    )
    nueva = cliente.post(
        "/employees",
        json={
            "nombre": "Eva",
            "telefono": "600111222",
            "vincular_con": eva.json()["id"],
            "pin_fichaje": "9876",
        },
        headers=_cabeceras(centro_2),
    )
    assert nueva.status_code == 201, nueva.text
    assert nueva.json()["tiene_pin"] is True

    # Se comprueba fichando DE VERDAD en el centro 1 con el PIN nuevo: es la
    # unica forma de demostrar que viajo, porque el hash no sale de la API.
    ficho = cliente.post(
        "/fichaje",
        json={"employee_id": eva.json()["id"], "pin": "9876", "accion": "entrada"},
        headers=_cabeceras(centro_1),
    )
    assert ficho.status_code in (200, 201), (
        f"el PIN nuevo no llego a la ficha del centro 1: {ficho.status_code} {ficho.text[:120]}"
    )

    # Y el control: el viejo ya no vale en ninguno de los dos.
    viejo = cliente.post(
        "/fichaje",
        json={"employee_id": eva.json()["id"], "pin": "1234", "accion": "salida"},
        headers=_cabeceras(centro_1),
    )
    assert viejo.status_code == 422, f"el PIN viejo sigue abriendo: {viejo.status_code}"


# ═══════════════════════ 7. sellar la primera ficha puede chocar


@pytest.mark.anyio
async def test_si_sellar_la_ficha_choca_se_dice_con_palabras():
    """UN GUARDA, Y DIGO HASTA DONDE LLEGA.

    Sellar la primera ficha dispara `repuntar_citas_de_la_persona`, que apunta
    sus citas a la persona; si eso choca, la restriccion rechaza el sellado
    entero. Es correcto: la garantia no se afloja para dejar entrar un enlace.
    Lo que no puede ser es que salga como un 500 con el texto crudo de Postgres,
    porque `_resolver_persona` corre FUERA del `try/except` del alta.

    HONESTAMENTE: hoy esto NO es alcanzable por ningun endpoint. Al dar de alta,
    la ficha nueva no tiene ni una cita, y la que se sella se apunta a si misma,
    asi que no hay con que chocar. Lo destape haciendo el PATCH a mano montando
    las capturas. Pasa a ser alcanzable el dia que se pueda enlazar una ficha
    QUE YA EXISTE —lo que quedo anotado como fuera de alcance—, y por eso el
    guarda se queda puesto y probado en vez de esperar a que alguien lo
    descubra en produccion.
    """
    from fastapi import HTTPException

    from app.db.postgrest_client import PostgrestError
    from app.routers.employees import _resolver_persona

    class DobleQueChoca:
        async def select(self, tabla, params=None):
            if tabla == "employees" and "persona_id" in (params or {}):
                return []  # nadie de esa persona en este centro
            return [{"id": "e-otra", "salon_id": "otro-centro", "persona_id": None}]

        async def update(self, tabla, filtros, cuerpo, select=None):
            raise PostgrestError(
                400,
                '{"code":"23P01","message":"conflicting key value violates '
                'exclusion constraint \"appointments_sin_solape\""}',
            )

    with pytest.raises(HTTPException) as e:
        await _resolver_persona(DobleQueChoca(), "este-centro", "e-otra")

    assert e.value.status_code == 409
    assert "se pisan" in e.value.detail
    # Lo que NO puede leer nadie: el vocabulario de Postgres.
    assert "exclusion" not in e.value.detail.lower()
    assert "constraint" not in e.value.detail.lower()


@pytest.mark.anyio
async def test_y_cualquier_OTRO_fallo_de_base_sigue_subiendo():
    """EL CONTROL. Un `except` que se tragara todo convertiria una averia de
    verdad en «tiene citas que se pisan», que es mentira y manda a buscar donde
    no hay nada."""
    from app.db.postgrest_client import PostgrestError
    from app.routers.employees import _resolver_persona

    class DobleQueRevienta:
        async def select(self, tabla, params=None):
            if tabla == "employees" and "persona_id" in (params or {}):
                return []
            return [{"id": "e-otra", "salon_id": "otro-centro", "persona_id": None}]

        async def update(self, tabla, filtros, cuerpo, select=None):
            raise PostgrestError(502, "PostgREST caido")

    with pytest.raises(PostgrestError):
        await _resolver_persona(DobleQueRevienta(), "este-centro", "e-otra")
