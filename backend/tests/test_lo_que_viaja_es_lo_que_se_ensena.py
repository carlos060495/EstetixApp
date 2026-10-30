"""LO QUE VIAJA A UNA PANTALLA SIN CORTINA ES LO QUE ESA PANTALLA ENSEÑA.

La Agenda y el Fichaje están fuera de la cortina del PIN de gestión: son la
tablet del mostrador, la que usa el equipo. La cortina es solo presentación;
lo que no se quiere que el equipo vea no puede LLEGAR a esa tablet, porque lo
que llega se lee en la pestaña de red. Pasó dos veces (19-10-2026): el buscador
de «Nueva cita» recibía la ficha entera de cada clienta —alergias, notas,
cumpleaños, lo gastado— para pintar un nombre, y la Agenda recibía de cada
empleada su teléfono, su correo y sus comisiones para pintar una columna.

CÓMO LO DESCUBRE, sin listas: lee `RUTAS_LIBRES` de la cortina, recorre los
ficheros de esas pantallas y los componentes que importan, encuentra cada
función de `@/lib/api` a la que llaman, saca de ella la dirección y el método,
busca esa ruta en la aplicación de verdad y recorre su `response_model`. Cada
campo que la respuesta trae tiene que aparecer en el código de la pantalla.
Una ruta de una pantalla libre sin `response_model` también es rojo: sin él no
se sabe qué viaja.
"""

from __future__ import annotations

import re
import types
import typing
from pathlib import Path

from fastapi.routing import APIRoute
from pydantic import BaseModel

from app.core.impersonation_policy import iterar_rutas

FRONT = Path(__file__).resolve().parents[2] / "frontend" / "src"

_IMPORT = re.compile(r'import\s+(type\s+)?\{([^}]*)\}\s+from\s+"([^"]+)"', re.S)
_IMPORT_DEFAULT = re.compile(r'import\s+(\w+)\s+from\s+"(\.[^"]+)"')


def rutas_libres(front: Path = FRONT) -> list[str]:
    texto = (front / "lib" / "cortina-gestion.ts").read_text(encoding="utf-8")
    m = re.search(r"RUTAS_LIBRES\s*=\s*\[([^\]]*)\]", texto)
    assert m, "no encuentro RUTAS_LIBRES en lib/cortina-gestion.ts"
    return re.findall(r'"([^"]+)"', m.group(1))


def _resolver(origen: Path, especificador: str, front: Path) -> Path | None:
    if especificador.startswith("@/"):
        base = front / especificador[2:]
    elif especificador.startswith("."):
        base = (origen.parent / especificador).resolve()
    else:
        return None
    for candidato in (base.with_suffix(".tsx"), base.with_suffix(".ts"), base / "index.tsx", base / "index.ts"):
        if candidato.is_file():
            return candidato
    return None


def ficheros_de_la_pantalla(ruta: str, front: Path = FRONT) -> list[Path]:
    """Los ficheros de la pantalla y todo lo que importan de la app (no de `lib/api`)."""
    pendientes = [p for p in (front / "app" / ruta.strip("/")).rglob("*.ts*") if ".test." not in p.name]
    vistos: set[Path] = set()
    while pendientes:
        f = pendientes.pop()
        if f in vistos:
            continue
        vistos.add(f)
        texto = f.read_text(encoding="utf-8")
        especs = [m.group(3) for m in _IMPORT.finditer(texto)] + [m.group(2) for m in _IMPORT_DEFAULT.finditer(texto)]
        for esp in especs:
            if esp.startswith("@/lib/api/"):
                continue
            destino = _resolver(f, esp, front)
            if destino and ".test." not in destino.name and destino not in vistos:
                pendientes.append(destino)
    return sorted(vistos)


def llamadas_a_la_api(ficheros: list[Path], front: Path = FRONT) -> set[tuple[str, str, str]]:
    """(función, MÉTODO, ruta) de cada función de `@/lib/api` que se importa sin `type`."""
    llamadas: set[tuple[str, str, str]] = set()
    for f in ficheros:
        for m in _IMPORT.finditer(f.read_text(encoding="utf-8")):
            if m.group(1) or not m.group(3).startswith("@/lib/api/"):
                continue
            modulo = front / "lib" / "api" / (m.group(3).removeprefix("@/lib/api/") + ".ts")
            fuente = modulo.read_text(encoding="utf-8")
            for nombre in m.group(2).split(","):
                nombre = nombre.strip()
                if not nombre or nombre.startswith("type "):
                    continue
                cuerpo = re.search(rf"export (?:async )?function {nombre}\b(.*?)(?=\nexport |\Z)", fuente, re.S)
                if not cuerpo:
                    continue  # una clase o una constante (ApiError…): no es una llamada
                for p in re.finditer(r"apiFetch(?:<[^>]*>)?\(([^;]*)", cuerpo.group(1), re.S):
                    # Todas las direcciones del primer argumento: también las dos de un `a ? "/x" : "/y"`.
                    metodo = re.search(r'method:\s*"(\w+)"', p.group(1))
                    for lit in re.findall(r'"(/[^"]*)"|`(/[^`]*)`', p.group(1)):
                        camino = re.sub(r"\$\{[^}]*\}", "{}", (lit[0] or lit[1]).split("?")[0])
                        llamadas.add((nombre, metodo.group(1) if metodo else "GET", camino))
    return llamadas


def _ruta_de(app, metodo: str, camino: str):
    for r in iterar_rutas(app.routes):
        if not isinstance(r, APIRoute) or metodo not in r.methods:
            continue
        patron = "^" + "[^/]+".join(map(re.escape, re.split(r"\{[^}]*\}", r.path))) + "$"
        if re.match(patron, camino.replace("{}", "x")):
            return r
    return None


def campos(tipo, prefijo: str = "") -> list[str]:
    """Los nombres de campo de un modelo de respuesta, entrando en listas y anidados."""
    origen = typing.get_origin(tipo)
    if origen in (list, set, tuple, typing.Union, types.UnionType):
        return [c for a in typing.get_args(tipo) for c in campos(a, prefijo)]
    if isinstance(tipo, type) and issubclass(tipo, BaseModel):
        salida = []
        for nombre, info in tipo.model_fields.items():
            salida.append(f"{prefijo}{nombre}")
            salida += campos(info.annotation, f"{prefijo}{nombre}.")
        return salida
    return []


def lo_que_viaja_y_no_se_ensena(app, front: Path = FRONT) -> list[str]:
    fuera: list[str] = []
    for ruta in rutas_libres(front):
        ficheros = ficheros_de_la_pantalla(ruta, front)
        fuente = "\n".join(f.read_text(encoding="utf-8") for f in ficheros)
        for funcion, metodo, camino in sorted(llamadas_a_la_api(ficheros, front)):
            if metodo != "GET":
                continue  # una escritura devuelve lo que se acaba de escribir desde esa pantalla
            r = _ruta_de(app, metodo, camino)
            if r is None:
                fuera.append(f"{ruta}: {funcion}() → {metodo} {camino}: no encuentro la ruta en el backend")
                continue
            if r.response_model is None:
                fuera.append(f"{ruta}: {metodo} {r.path} no declara response_model: no se sabe qué viaja")
                continue
            for campo in campos(r.response_model):
                hoja = campo.rsplit(".", 1)[-1]
                if not re.search(rf"\b{re.escape(hoja)}\b", fuente):
                    fuera.append(f"{ruta}: {metodo} {r.path} trae «{campo}» y la pantalla no lo usa")
    return fuera


def _app_real():
    from app.main import app

    return app


# LO QUE EL RECORRIDO ENCONTRÓ EL 19-10-2026 Y NO SE HA TOCADO: ninguno es dato
# de una persona (clienta o empleada) —es el marco de la aplicación, que viaja
# en todas las pantallas, y un servicio del catálogo—, así que no es el mismo
# patrón que los dos arreglados. No crece sin que
# se vea en el diff, y una entrada que deja de ser cierta pone rojo el test de
# abajo para que se quite.
SABIDO_Y_CONTADO = {
    "/agenda: GET /salon/centros trae «centros.slug» y la pantalla no lo usa",
    "/agenda: GET /salon/centros trae «cambio_de_centro_desde» y la pantalla no lo usa",
    "/agenda: GET /auth/me trae «centro_activo» y la pantalla no lo usa",
    "/agenda: GET /services trae «visible_online» y la pantalla no lo usa",
    "/fichaje: GET /auth/me trae «rol» y la pantalla no lo usa",
    "/fichaje: GET /auth/me trae «centro_activo» y la pantalla no lo usa",
    "/fichaje: GET /auth/me trae «centro_rechazado» y la pantalla no lo usa",
}


def test_las_pantallas_sin_cortina_solo_reciben_lo_que_ensenan():
    assert sorted(set(lo_que_viaja_y_no_se_ensena(_app_real())) - SABIDO_Y_CONTADO) == []


def test_lo_sabido_sigue_siendo_cierto():
    assert sorted(SABIDO_Y_CONTADO - set(lo_que_viaja_y_no_se_ensena(_app_real()))) == []


# ═══ LA LISTA DEL MOSTRADOR (0090) ═══════════════════════════════════════════
#
# Un dispositivo mostrador solo usa sin PIN las rutas de `RUTAS_DEL_MOSTRADOR`.
# Esa lista tiene que ser EXACTAMENTE lo que piden las pantallas libres: ni una
# ruta que ninguna llame (sería abrir algo sin motivo), ni una llamada de esas
# pantallas sin decidir. Lo que llaman y NO está en la lista va aquí, con su
# porqué: en el mostrador pide el PIN.
PIDEN_GESTION_EN_EL_MOSTRADOR = {
    ("GET", "/billing/baja"): "el muro de pago: darse de baja es de la dueña",
    ("POST", "/billing/baja"): "el muro de pago: darse de baja es de la dueña",
    ("POST", "/billing/checkout"): "el muro de pago: pagar es de la dueña",
    ("POST", "/billing/checkout/trial"): "el muro de pago: pagar es de la dueña",
    ("POST", "/billing/portal"): "el muro de pago: la tarjeta es de la dueña",
    ("POST", "/billing/sincronizar"): "el muro de pago: vuelta de Stripe, de la dueña",
    ("GET", "/cuenta/datos"): "el muro de pago: llevarse los datos es de la dueña",
    ("POST", "/demo/plan"): "elegir plan en una demo es de la dueña",
    ("PUT", "/salon/centros/activo"): "el mostrador es de un centro: no se cambia desde él",
}


def lo_que_llaman_las_pantallas_libres(app, front: Path = FRONT) -> set[tuple[str, str]]:
    llamadas: set[tuple[str, str]] = set()
    for ruta in rutas_libres(front):
        for _funcion, metodo, camino in llamadas_a_la_api(ficheros_de_la_pantalla(ruta, front), front):
            r = _ruta_de(app, metodo, camino)
            # Las puertas del propio mostrador (/mostrador/…) las autoriza su
            # cookie, no la sesión: no son rutas de la lista ni de la gestión.
            if not (r and r.path.startswith("/mostrador")):
                llamadas.add((metodo, r.path if r else f"?{camino}"))
    return llamadas


def test_la_lista_del_mostrador_es_lo_que_llaman_las_pantallas_libres():
    from app.core.mostrador import RUTAS_DEL_MOSTRADOR

    llamadas = lo_que_llaman_las_pantallas_libres(_app_real())
    assert sorted(RUTAS_DEL_MOSTRADOR - llamadas) == [], "en la lista sin que ninguna pantalla libre la llame"
    assert sorted(llamadas - RUTAS_DEL_MOSTRADOR - set(PIDEN_GESTION_EN_EL_MOSTRADOR)) == [], (
        "una pantalla libre llama a una ruta que no está decidida: o a la lista del mostrador, o a "
        "PIDEN_GESTION_EN_EL_MOSTRADOR con su porqué"
    )
    assert sorted(set(PIDEN_GESTION_EN_EL_MOSTRADOR) - llamadas) == [], "decidida y ya nadie la llama"


# ── CONTROLES: la guarda caza los dos casos que se arreglaron ─────────────────


def _pantalla_falsa(tmp_path: Path, importa: str, llama: str) -> Path:
    """Un `src/` con la cortina de verdad, los módulos de la API de verdad y una
    Agenda que llama a `importa` y solo pinta el nombre."""
    import shutil

    front = tmp_path / "src"
    (front / "lib" / "api").mkdir(parents=True)
    shutil.copy(FRONT / "lib" / "cortina-gestion.ts", front / "lib" / "cortina-gestion.ts")
    for m in (FRONT / "lib" / "api").glob("*.ts"):
        shutil.copy(m, front / "lib" / "api" / m.name)
    (front / "app" / "agenda").mkdir(parents=True)
    modulo = "clients" if "Client" in importa else "employees"
    (front / "app" / "agenda" / "page.tsx").write_text(
        f'import {{ {importa} }} from "@/lib/api/{modulo}";\n'
        f"export default function P() {{ const filas = {llama}; return filas.map((f) => f.nombre); }}\n",
        encoding="utf-8",
    )
    return front


def test_CONTROL_la_agenda_pidiendo_el_equipo_entero_se_detecta(tmp_path):
    rojo = lo_que_viaja_y_no_se_ensena(_app_real(), _pantalla_falsa(tmp_path, "listEmployees", "listEmployees({ conBajas: true })"))
    for campo in ("telefono", "email", "comision_servicios", "tiene_pin"):
        assert f"/agenda: GET /employees trae «{campo}» y la pantalla no lo usa" in rojo


def test_CONTROL_el_buscador_pidiendo_la_ficha_entera_se_detecta(tmp_path):
    rojo = lo_que_viaja_y_no_se_ensena(_app_real(), _pantalla_falsa(tmp_path, "listClients", "listClients({ q })"))
    for campo in ("alergias", "notas", "cumpleanos", "telefono", "total_gastado"):
        assert f"/agenda: GET /clients trae «{campo}» y la pantalla no lo usa" in rojo
