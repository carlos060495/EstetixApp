"""Ninguna hora llega a una pantalla, un correo, un PDF o la base sin pasar por
`app/core/zona.py`.

POR QUÉ. Hasta la 0060 había tres `ZoneInfo("Europe/Madrid")` escritos por
separado, el historial de fichajes convertía a UTC, el bot sacaba «hoy» de la
fecha del servidor y la reposición de stock fechaba el gasto en UTC. Cada uno
convertía por su cuenta, y dos sitios convirtiendo acaban discrepando.

QUÉ VIGILA, recorriendo el árbol sintáctico de todo `app/` salvo `core/zona.py`:

  · `ZoneInfo(...)`          — una zona se pide por su nombre a `zona.zona()`.
  · `.astimezone(...)`       — convertir es cosa de `zona.local` / `zona.a_utc`.
  · `.strftime(...)` y los `{x:%H:%M}` de los f-strings — formatear una hora.
  · `.date()` / `.time()`    — partir un instante en día y hora es convertir.
  · `.replace(tzinfo=...)`   — ponerle una zona a mano a una hora.
  · `datetime.combine(...)`  — montar una hora de pared.
  · `date.today()`, `datetime.now()` sin zona, `utcnow()`, `today()` — la fecha
    del SERVIDOR, que vive en UTC, no es la de ningún centro.
  · `fromtimestamp(...)` sin `tz=` — un epoch de Stripe es UTC y hay que decirlo.
  · `.hour` / `.minute`      — leer la hora de un instante sin convertirlo.

No hay lista de pendientes: hoy el código está limpio, y cualquier caso nuevo
se pone rojo.
"""

from __future__ import annotations

import ast
import re
import typing
from pathlib import Path

from app.core import zona as zonas
from app.schemas.salon import ZonaHoraria

APP = Path(__file__).resolve().parents[1] / "app"
MODULO = APP / "core" / "zona.py"
MIGRACION = Path(__file__).resolve().parents[2] / "supabase" / "migrations" / "0060_la_zona_horaria_del_centro.sql"


def infracciones(codigo: str) -> list[tuple[int, str]]:
    fuera: list[tuple[int, str]] = []
    for n in ast.walk(ast.parse(codigo)):
        if isinstance(n, ast.Call):
            f = n.func
            nombre = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else None)
            claves = {k.arg for k in n.keywords}
            if nombre == "ZoneInfo":
                fuera.append((n.lineno, "ZoneInfo()"))
            elif nombre in ("astimezone", "strftime", "combine", "utcnow", "today", "utcfromtimestamp") or (
                # `.date()` / `.time()` sin argumentos: partir un instante en su día y su hora.
                nombre in ("date", "time") and isinstance(f, ast.Attribute) and not n.args and not n.keywords
            ):
                fuera.append((n.lineno, f".{nombre}()"))
            elif nombre == "replace" and "tzinfo" in claves:
                fuera.append((n.lineno, ".replace(tzinfo=)"))
            elif nombre == "now" and isinstance(f, ast.Attribute) and not n.args and "tz" not in claves:
                fuera.append((n.lineno, "now() sin zona"))
            elif nombre == "fromtimestamp" and "tz" not in claves and len(n.args) < 2:
                fuera.append((n.lineno, "fromtimestamp() sin tz"))
        elif isinstance(n, ast.FormattedValue) and n.format_spec is not None:
            spec = ast.unparse(n.format_spec)
            if "%" in spec:
                fuera.append((n.lineno, "f-string con formato de fecha"))
        elif isinstance(n, ast.Attribute) and n.attr in ("hour", "minute") and isinstance(n.ctx, ast.Load):
            fuera.append((n.lineno, f".{n.attr}"))
    return fuera


# ═══════════════════════════════════════════════ controles


def test_CONTROL_encuentra_cada_forma_de_convertir_por_su_cuenta():
    codigo = '''
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
def mal(x, t):
    a = ZoneInfo("Europe/Madrid")
    b = x.astimezone(a)
    c = x.strftime("%H:%M")
    d = f"{x:%d/%m}"
    e = x.date()
    f = x.time()
    g = x.replace(tzinfo=timezone.utc)
    h = datetime.combine(date.today(), t)
    i = datetime.now()
    j = datetime.fromtimestamp(12)
    k = x.hour
    l = datetime.utcnow()
'''
    tipos = [que for _, que in infracciones(codigo)]
    for esperado in ("ZoneInfo()", ".astimezone()", ".strftime()", "f-string con formato de fecha", ".date()",
                     ".time()", ".replace(tzinfo=)", ".combine()", ".today()", "now() sin zona",
                     "fromtimestamp() sin tz", ".hour", ".utcnow()"):
        assert esperado in tipos, esperado


def test_CONTROL_deja_pasar_lo_que_esta_bien():
    codigo = '''
from datetime import datetime, timezone
from app.core import zona as zonas
def bien(x, z, epoch, valor):
    a = datetime.now(timezone.utc)
    b = zonas.hhmm(x, z)
    c = datetime.fromtimestamp(epoch, tz=timezone.utc)
    d = f"{valor:.2f} €"
    e = x.isoformat()
    f = valor.replace("Z", "+00:00")
'''
    assert infracciones(codigo) == []


def test_CONTROL_el_propio_modulo_si_convierte():
    """Si el detector no viera nada en el único sitio donde SÍ se convierte,
    estaría ciego y el test de abajo pasaría por no mirar."""
    assert len(infracciones(MODULO.read_text(encoding="utf-8"))) >= 5


# ═══════════════════════════════════════════════ el código


def test_ninguna_hora_se_convierte_fuera_de_zona_py():
    encontradas = []
    for f in sorted(APP.rglob("*.py")):
        if f == MODULO:
            continue
        for linea, que in infracciones(f.read_text(encoding="utf-8")):
            encontradas.append(f"{f.relative_to(APP.parent)}:{linea} {que}")
    assert not encontradas, (
        "Horas convertidas o formateadas fuera de app/core/zona.py:\n  " + "\n  ".join(encontradas)
        + "\nUsa las funciones de zona.py: una sola conversión para todo."
    )


# ═══════════════════════════════════════════════ la lista cerrada, en un solo sentido


def test_la_lista_de_zonas_es_la_misma_en_la_base_el_backend_y_la_api():
    """Tres sitios nombran las zonas permitidas: el `check` de la 0060, `ZONAS`
    y el `Literal` de la API. Si uno ganara una zona sin los otros, la base
    rechazaría lo que la API acepta, o al revés."""
    en_la_base = set(re.findall(r"'([A-Za-z_]+/[A-Za-z_]+)'", MIGRACION.read_text(encoding="utf-8").split("check", 1)[1]))
    assert en_la_base == set(zonas.ZONAS) == set(typing.get_args(ZonaHoraria))
    assert zonas.POR_DEFECTO in zonas.ZONAS


def test_y_la_misma_en_las_pantallas():
    """La cuarta copia: `ZONAS` de `frontend/src/lib/horas.ts`, la del selector."""
    horas_ts = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "horas.ts").read_text(encoding="utf-8")
    bloque = horas_ts.split("export const ZONAS = {", 1)[1].split("}", 1)[0]
    assert set(re.findall(r'"([A-Za-z_]+/[A-Za-z_]+)"\s*:', bloque)) == set(zonas.ZONAS)
