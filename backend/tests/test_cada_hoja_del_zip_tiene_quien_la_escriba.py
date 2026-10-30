"""CADA HOJA DEL ZIP TIENE QUIEN LA ESCRIBA.

El ZIP de «Tus datos» prometía «los consentimientos firmados por tus clientas,
con la fecha de firma», y la hoja salía vacía en todas las cuentas: la tabla
`consents` existía, el volcado la leía, y nada en la aplicación escribía en
ella (19-10-2026). Una promesa así no la caza ningún test del formato: el
fichero está, la cabecera está, las filas no llegan nunca.

CÓMO LO DESCUBRE, sin listas: saca de `routers/datos.py` cada tabla que lee el
volcado y busca quién la escribe: un `insert`/`update`/`upsert` sobre ella en
`app/`, o un `insert into`/`update` en una función o disparador de las
migraciones. Una vista cuenta como escrita si lo está la tabla de la que lee.
"""

from __future__ import annotations

import re
from pathlib import Path

BACK = Path(__file__).resolve().parents[1]
MIGRACIONES = BACK.parent / "supabase" / "migrations"


def tablas_que_lee_el_volcado(fuente: str) -> set[str]:
    cuerpo = fuente[fuente.index("async def leer_datos"):]
    return set(re.findall(r'(?:\bt|_todas|\.select)\(\s*(?:\w+,\s*)?"(\w+)"', cuerpo))


def _escrituras_en_la_app() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in (BACK / "app").rglob("*.py"))


def _sql() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(MIGRACIONES.glob("*.sql")))


def quien_la_escribe(tabla: str, app: str, sql: str, _visto: frozenset = frozenset()) -> bool:
    # El nombre a pelo o por una constante (`_TABLA = "salon_schedule_exceptions"`); también `insert_many`.
    nombres = [f'"{tabla}"'] + re.findall(rf'^\s*(\w+)\s*=\s*"{tabla}"\s*$', app, re.M)
    if re.search(rf"\.(?:insert|update|upsert)\w*\(\s*(?:{'|'.join(map(re.escape, nombres))})(?!\w)", app):
        return True
    if re.search(rf"\b(?:insert\s+into|update)\s+(?:public\.)?{tabla}\b", sql, re.I):
        return True
    vista = re.search(rf"create\s+(?:or\s+replace\s+)?view\s+public\.{tabla}\b.*?\bfrom\s+public\.(\w+)", sql, re.I | re.S)
    return bool(vista) and vista.group(1) not in _visto and quien_la_escribe(vista.group(1), app, sql, _visto | {tabla})


def hojas_sin_escritor(fuente: str) -> list[str]:
    app, sql = _escrituras_en_la_app(), _sql()
    return sorted(t for t in tablas_que_lee_el_volcado(fuente) if not quien_la_escribe(t, app, sql))


def _datos() -> str:
    return (BACK / "app" / "routers" / "datos.py").read_text(encoding="utf-8")


def test_cada_tabla_que_lee_el_volcado_tiene_quien_la_escriba():
    leidas = tablas_que_lee_el_volcado(_datos())
    assert {"clients", "appointments", "employees", "time_entries"} <= leidas  # antivacío: el recorrido encuentra
    assert hojas_sin_escritor(_datos()) == []


def test_CONTROL_la_hoja_de_consentimientos_de_antes_se_detecta():
    con_consents = _datos().replace(
        't("expenses",', 't("consents", "id,salon_id,client_id,tipo,firmado_at", "firmado_at.asc"),\n        t("expenses",', 1
    )
    assert hojas_sin_escritor(con_consents) == ["consents"]
