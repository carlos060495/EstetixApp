"""Entre dos tablas que el código une con un embed tiene que haber UN camino.

═══ LA CONSECUENCIA, QUE ES PEOR QUE LA DE B2a ═══

Cuando esto pasó la primera vez, la consulta ambigua era la de `/auth/me` y unas
cuantas más. Ahora el embed vive en la consulta de SESIÓN —
`core/security.py::_exigir_sesion_valida`, que corre en CADA petición del panel
antes que nada—, así que un segundo camino entre `salons` y `accounts` ya no
tumba una pantalla: **no responde ni una sola petición de la aplicación**. Nadie
entra, nadie ve la agenda, nadie cobra. Y no da un error de columna que se lea:
da un 300 con un JSON que, hasta que se arregló el cliente, se devolvía como si
fueran filas.

═══ POR QUÉ ESTO NO ES UNA LISTA ═══

El guardián anterior comparaba dos `select` escritos a mano. Un dato que vive en
dos sitios se separa: B3a añadió TRES embeds y ninguno entró en esa lista, que
además no cubría la dirección `accounts → salons`. Aquí no hay lista. Los embeds
se descubren recorriendo `app/` (`tests/embeds_del_codigo.py`) y se le piden a
PostgREST uno a uno: el que se prueba es siempre el que corre.

`limit=0` a propósito: valida el `select` sin traer una fila y sin depender de
que haya datos. Un embed ambiguo contesta 300 igualmente, porque PostgREST
resuelve la relación antes de mirar la tabla.

═══ EL CONTROL, QUE AQUÍ NO HAY QUE INVENTAR ═══

«Ninguno da 300» pasaría igual de verde si el detector no encontrara nada o si
PostgREST contestara 200 a cualquier cosa. Hace falta enseñar un 300 DE VERDAD, y
resulta que el esquema ya tiene uno: desde la 0039, `users` llega a `salons` por
TRES caminos —su propia columna, `ultimo_centro_id` y `user_salons`—. Pedir ese
embed sin pista contesta 300 hoy, en esta misma base. Por eso la consulta de
sesión escribe `salons!users_salon_id_fkey(...)`.

Así que el control no se fabrica: se usa la ambigüedad que ya existe, con y sin
pista sobre el MISMO par. Sin pista, 300. Con pista, 200.
"""

from __future__ import annotations

import httpx
import pytest

from tests.embeds_del_codigo import Salto, embeds, pares, saltos_de
from tests.test_el_segundo_centro_atiende import H, SUPA  # noqa: F401

pytestmark = [
    pytest.mark.base_real,
]

# Las dos tablas cuya relación ya nos costó una tarde, y las dos direcciones en
# que el código las une hoy. No es la lista de lo que se comprueba —eso se
# descubre— sino lo que este test EXIGE encontrar antes de fiarse del detector.
PAR_CRITICO = ("salons", "accounts")


def _pedir(tabla: str, select: str) -> httpx.Response:
    return httpx.get(
        f"{SUPA}/rest/v1/{tabla}", params={"select": select, "limit": "0"}, headers=H, timeout=30
    )


# ═══════════════════════════════════════════════════════ lo que se afirma


@pytest.mark.parametrize("embed", embeds(), ids=lambda e: e.donde)
def test_ningun_embed_del_codigo_puede_recibir_un_300(embed):
    """Cada consulta con embed que hay en `app/`, contra PostgREST de verdad.

    Si alguien añade una clave ajena que deja dos caminos entre dos tablas que
    ya se embeben, esto se pone rojo señalando el fichero y la línea — y el
    cuerpo del 300 trae la solución literal de PostgREST en su `hint`.
    """
    r = _pedir(embed.tabla, embed.select)

    assert r.status_code == 200, (
        f"{embed.donde}\n"
        f"  {embed.tabla}?select={embed.select}\n"
        f"  saltos: {', '.join(str(s) for s in embed.saltos)}\n\n"
        f"PostgREST contesta {r.status_code}: {r.text[:400]}"
    )


def test_entre_salons_y_accounts_sigue_habiendo_UN_camino_en_las_dos_direcciones():
    """El par que nos costó la tarde, dicho aparte y en las dos direcciones.

    Va separado del recorrido de arriba aunque esté incluido en él, porque una
    clave ajena nueva rompe las DOS a la vez y quiero que el fallo diga eso y no
    «una consulta de un router cualquiera».
    """
    ida = _pedir("salons", "id,accounts(id)")
    vuelta = _pedir("accounts", "id,salons(id)")

    assert ida.status_code == 200, f"salons → accounts: {ida.text[:300]}"
    assert vuelta.status_code == 200, f"accounts → salons: {vuelta.text[:300]}"


def test_los_tres_embeds_de_B3a_estan_entre_los_descubiertos():
    """CONTROL: que el detector encuentre lo que se sabe que existe.

    Son los tres que la lista escrita a mano no tenía. Si el lector dejara de
    reconocerlos —una consulta reescrita de otra forma, un `select` que pase a
    construirse en una variable— el recorrido de arriba pasaría sin mirarlos.
    """
    donde = {e.donde.rsplit(":", 1)[0] for e in embeds() if any(
        s.padre == "accounts" and s.hijo == "salons" for s in e.saltos
    )}

    assert donde == {
        "app/core/security.py",      # la sesión: corre en CADA petición
        "app/routers/publico.py",    # el widget
        "app/routers/webhooks_whatsapp.py",  # el bot
    }, donde


def test_y_tambien_los_que_ya_habia():
    """El otro lado del control: los embeds viejos siguen encontrándose."""
    encontrados = pares()

    assert ("salons", "accounts") in encontrados, "se perdió el embed de siempre"
    assert ("accounts", "salons") in encontrados, "se perdió la dirección nueva"


# ═══════════════════════════════════ que no pase en vacío


def test_CONTROL_un_par_con_DOS_caminos_SI_da_300():
    """EL CONTROL QUE IMPORTA, y no hace falta inventarlo.

    `users` llega a `salons` por tres caminos desde la 0039. Si este embed
    contestara 200, querría decir que PostgREST ha dejado de quejarse de las
    ambigüedades y que todo lo de arriba pasa por el motivo equivocado.
    """
    r = _pedir("users", "id,salons(id)")

    assert r.status_code == 300, f"un par ambiguo ya no da 300: {r.status_code} {r.text[:200]}"
    assert "PGRST201" in r.text


def test_CONTROL_el_mismo_par_CON_pista_si_vale():
    """El contrapeso: la pista es la salida, y es lo que usa la consulta de
    sesión. Sin este control, el de arriba se podría «arreglar» rompiendo el
    embed de cualquier manera y seguiría en verde."""
    r = _pedir("users", "id,salons!users_salon_id_fkey(id)")

    assert r.status_code == 200, r.text[:300]


def test_CONTROL_el_detector_encuentra_bastantes_y_no_cero():
    """Si `embeds()` devolviera una lista vacía —una ruta cambiada, un método
    renombrado en el cliente— la parametrización de arriba no ejecutaría ni un
    caso y el fichero entero saldría en verde sin comprobar nada."""
    assert len(embeds()) >= 20


def test_CONTROL_el_lector_de_saltos_entiende_el_anidamiento():
    """Y los cuenta bien: el de B3a mete `salons` DENTRO de `accounts`, que es
    el salto que la lista vieja no tenía. Un lector que se quedara en el primer
    paréntesis no lo vería."""
    saltos = saltos_de(
        "id,rol,salons!users_salon_id_fkey(accounts(suspendido,plan,salons(id,created_at)))",
        "users",
    )

    assert saltos == (
        Salto("users", "salons", "users_salon_id_fkey"),
        Salto("salons", "accounts", None),
        Salto("accounts", "salons", None),
    ), saltos


def test_CONTROL_una_pista_no_se_confunde_con_una_tabla():
    """`salons!users_salon_id_fkey(...)` embebe `salons`, no una tabla llamada
    `users_salon_id_fkey`. Si se confundieran, el par crítico dejaría de
    encontrarse y nadie lo notaría."""
    (salto,) = saltos_de("id,salons!employees_salon_id_fkey(nombre)", "employees")

    assert salto.hijo == "salons"
    assert salto.pista == "employees_salon_id_fkey"
