"""LA REGLA, vigilada: una columna nueva y obligatoria no puede romper el alta.

    UNA COLUMNA NUEVA SOBRE UNA TABLA EN LA QUE EL CÓDIGO DESPLEGADO INSERTA
    ES OPCIONAL O TIENE VALOR POR DEFECTO.

POR QUÉ EXISTE ESTE FICHERO. Ha pasado dos veces seguidas, con el mismo
mecanismo y una semana de diferencia:

  · 0039 — `salons.account_id not null`. El backend desplegado insertaba el
    salón sin cuenta. Nadie pudo registrarse hasta la 0040.
  · 0040 — `subscriptions.account_id not null`. El backend desplegado insertaba
    la suscripción sin cuenta. El cliente pagaba en Stripe, el insert reventaba
    y se quedaba fuera de la aplicación.

Las dos veces el razonamiento fue el mismo y el mismo error: «el backend nuevo
la rellena siempre». La rellena, sí — a partir de que se despliegue. Entre la
migración y el despliegue quien inserta es el código ANTERIOR, que no sabe de
esa columna; y si hay que revertir, vuelve a serlo.

Escrito también en la cabecera de la 0041.
Un comentario no para una migración; este test sí.

SI ESTE TEST FALLA, hay dos salidas y ninguna es tocar la lista a la ligera:

  1. Que la columna sea opcional o tenga `default`. Casi siempre vale.
  2. Si de verdad tiene que ser obligatoria: un trigger `before insert` que la
     rellene, EN LA MISMA MIGRACIÓN, y su entrada en `RELLENADAS_POR_TRIGGER`
     de abajo. Escribir la entrada sin escribir el trigger no cuela: hay un
     segundo test que comprueba que el trigger existe.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.esquema import MIGRACIONES, _sin_ruido

RAIZ = Path(__file__).resolve().parents[1]

# Columnas obligatorias que SÍ pueden existir porque un trigger `before insert`
# las rellena cuando quien inserta no las trae. El valor es el nombre del
# trigger, y se comprueba que exista de verdad.
RELLENADAS_POR_TRIGGER = {
    ("salons", "account_id"): "salon_entra_en_una_cuenta",
    ("subscriptions", "account_id"): "suscripcion_entra_en_una_cuenta",
}

_ADD_COLUMN = re.compile(
    r"\badd\s+column\s+(?:if\s+not\s+exists\s+)?(\w+)\b([^,;]*)", re.I
)
_ALTER = re.compile(r"alter\s+table\s+(?:if\s+exists\s+)?public\.(\w+)\b(.*?);", re.I | re.S)
_SET_NOT_NULL = re.compile(
    r"alter\s+table\s+(?:if\s+exists\s+)?public\.(\w+)\s+alter\s+column\s+(\w+)\s+set\s+not\s+null",
    re.I,
)


def columnas_obligatorias_nuevas() -> set[tuple[str, str]]:
    """`(tabla, columna)` de cada columna AÑADIDA que quedó obligatoria sin
    valor por defecto.

    Cuenta las dos formas de dejarla obligatoria: en el propio `add column ...
    not null` y en un `alter column ... set not null` posterior, que es como lo
    hicieron la 0039 y la 0040 y por eso hay que mirar las dos.
    """
    encontradas: set[tuple[str, str]] = set()
    for fichero in sorted(MIGRACIONES.glob("*.sql")):
        sql = _sin_ruido(fichero.read_text(encoding="utf-8"))

        for tabla, cuerpo in _ALTER.findall(sql):
            for columna, resto in _ADD_COLUMN.findall(cuerpo):
                tiene_default = re.search(r"\bdefault\b", resto, re.I)
                es_not_null = re.search(r"\bnot\s+null\b", resto, re.I)
                if es_not_null and not tiene_default:
                    encontradas.add((tabla.lower(), columna.lower()))

        for tabla, columna in _SET_NOT_NULL.findall(sql):
            encontradas.add((tabla.lower(), columna.lower()))
    return encontradas


def tablas_en_las_que_el_backend_inserta() -> set[str]:
    """Las tablas que el backend crea filas en. Salen del código, no de una
    lista a mano: una lista a mano se queda vieja justo cuando importa."""
    tablas: set[str] = set()
    patron = re.compile(r"\.(?:insert|insert_many|upsert_many)\(\s*[\"'](\w+)[\"']", re.S)
    for fichero in (RAIZ / "app").rglob("*.py"):
        tablas |= set(patron.findall(fichero.read_text(encoding="utf-8")))
    return tablas


def test_el_backend_inserta_en_bastantes_tablas():
    """El control del test de abajo.

    Si el detector de inserts dejara de encontrar nada —un cambio de estilo, un
    ayudante nuevo—, la regla pasaría a no vigilar nada y seguiría en verde.
    """
    tablas = tablas_en_las_que_el_backend_inserta()
    assert len(tablas) >= 10, f"solo se detectaron inserts en {sorted(tablas)}"
    assert "subscriptions" in tablas
    assert "salons" in tablas


def test_ninguna_columna_obligatoria_nueva_rompe_a_quien_inserta():
    """LA REGLA.

    Una columna obligatoria sin default sobre una tabla en la que el backend
    inserta rompe al backend anterior durante la ventana de despliegue —y otra
    vez si hay que revertir—. O es opcional, o tiene default, o la rellena un
    trigger declarado en `RELLENADAS_POR_TRIGGER`.
    """
    insertadas = tablas_en_las_que_el_backend_inserta()
    culpables = [
        (tabla, columna)
        for tabla, columna in sorted(columnas_obligatorias_nuevas())
        if tabla in insertadas and (tabla, columna) not in RELLENADAS_POR_TRIGGER
    ]
    assert not culpables, (
        "Columnas obligatorias nuevas sobre tablas en las que el backend "
        f"inserta: {culpables}. O la haces opcional / con default, o escribes "
        "un trigger `before insert` en la MISMA migración y lo declaras en "
        "RELLENADAS_POR_TRIGGER."
    )


def test_los_triggers_declarados_existen_de_verdad():
    """Declarar el trigger en la lista sin escribirlo sería peor que nada:
    apagaría la alarma dejando el fallo puesto."""
    sql = "\n".join(
        f.read_text(encoding="utf-8") for f in sorted(MIGRACIONES.glob("*.sql"))
    ).lower()
    for (tabla, columna), trigger in RELLENADAS_POR_TRIGGER.items():
        assert f"create trigger {trigger}" in sql, (
            f"{tabla}.{columna} dice que la rellena el trigger «{trigger}», "
            "y ninguna migración lo crea."
        )
