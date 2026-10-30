"""LA LISTA DE PAÍSES DONDE COBRA UN REFERIDO ESTÁ EN UN SOLO SITIO.

05-10-2026: `app/core/paises_cobro.py`, y nada más. La página pública
y el portal la piden a `GET /referido/paises`; el backend la usa al crear la
cuenta. Si aparece escrita en otro sitio —del backend, del frontend o de las
migraciones—, este guarda se pone rojo.

CÓMO SE RECONOCE LA LISTA, recorriendo todos los ficheros de texto del repo:

  · por sus NOMBRES: cuatro o más países de la lista escritos con su nombre
    («Noruega, Liechtenstein, Reino Unido, Suiza…»). Es lo que lee una persona
    y lo que se copiaría en una página;
  · por sus CÓDIGOS, con lo que la hace suya: Gibraltar o Liechtenstein (`"GI"`,
    `"LI"`) junto a tres o más códigos más de la lista. Así NO se denuncia la
    lista de países que ven euros (`frontend/src/proxy.ts`), que es otra cosa y
    se parece mucho: zona euro y vecinos, sin Gibraltar ni Liechtenstein;
  · y en cualquier fichero de lo de los referidos (`referido` en la ruta), tres
    o más códigos de la lista, tengan Gibraltar o no.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from app.core.paises_cobro import PAISES_COBRO, POR_QUE_NO, se_puede_cobrar_en

RAIZ = pathlib.Path(__file__).resolve().parents[2]
SU_SITIO = RAIZ / "backend" / "app" / "core" / "paises_cobro.py"
ESTE = pathlib.Path(__file__).resolve()

_DONDE = ("backend/app", "backend/tests", "frontend/src", "frontend/public", "supabase")
_SALTAR = {"node_modules", ".venv", ".next", "__pycache__", "coverage", "dist", "build"}
_TEXTO = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".json", ".sql", ".md", ".mdx", ".html", ".txt"}
_SUYOS = {"GI", "LI"}

_CODIGO = re.compile(r"""["'`](%s)["'`]""" % "|".join(PAISES_COBRO))


def lista_en(texto: str, ruta: str) -> list[str]:
    """Qué hay de la lista en este texto; vacío si no la hay."""
    nombres = sorted(n for n in PAISES_COBRO.values() if re.search(r"(?<!\w)%s(?!\w)" % re.escape(n), texto))
    codigos = set(_CODIGO.findall(texto))
    fuera = []
    if len(nombres) >= 4:
        fuera.append(f"{ruta}: {len(nombres)} nombres ({', '.join(nombres[:5])}…)")
    if codigos & _SUYOS and len(codigos - _SUYOS) >= 3:
        fuera.append(f"{ruta}: códigos con Gibraltar/Liechtenstein ({', '.join(sorted(codigos))})")
    elif "referido" in ruta.lower() and len(codigos) >= 3:
        fuera.append(f"{ruta}: {len(codigos)} códigos en lo de los referidos ({', '.join(sorted(codigos))})")
    return fuera


def _ficheros():
    for donde in _DONDE:
        for p in sorted((RAIZ / donde).rglob("*")):
            if p.suffix in _TEXTO and p.is_file() and not (_SALTAR & set(p.parts)):
                yield p


def test_la_lista_no_esta_escrita_en_otro_sitio():
    hallado, mirados = [], 0
    for p in _ficheros():
        if p.resolve() in (SU_SITIO, ESTE):
            continue
        mirados += 1
        hallado += lista_en(p.read_text(encoding="utf-8", errors="ignore"), p.relative_to(RAIZ).as_posix())
    assert mirados > 500  # antivacío: recorre de verdad el backend y el frontend
    assert hallado == [], "La lista de países de cobro está escrita fuera de su sitio:\n  " + "\n  ".join(hallado)


def test_en_su_sitio_si_se_ve():
    """Antivacío: el detector reconoce la lista donde está."""
    assert len(lista_en(SU_SITIO.read_text(encoding="utf-8"), "paises_cobro.py")) == 2


def test_la_lista_es_la_de_carlos():
    """Los 27 de la UE, Noruega, Liechtenstein, Reino Unido, Suiza y Gibraltar."""
    assert len(PAISES_COBRO) == 32
    assert {"NO", "LI", "GB", "CH", "GI"} <= PAISES_COBRO.keys()
    for fuera in ("US", "CA", "MX", "TH", "IS"):
        assert not se_puede_cobrar_en(fuera)
    assert se_puede_cobrar_en("es") and not se_puede_cobrar_en("") and not se_puede_cobrar_en(None)
    assert "Stripe" in POR_QUE_NO


# ── LOS CONTROLES ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("texto, ruta", [
    ("<p>Puedes cobrar si vives en España, Francia, Noruega o Suiza.</p>", "frontend/src/app/referidos/page.tsx"),
    ('const PAISES = ["ES", "FR", "PT", "GI"];', "frontend/src/lib/cualquiera.ts"),
    ('PAISES = {"ES", "FR", "LI", "NO"}', "backend/app/core/otro.py"),
    ('const opciones = ["ES", "FR", "IT"];', "frontend/src/components/referido/selector.tsx"),
], ids=["nombres_en_una_pagina", "codigos_con_gibraltar", "codigos_con_liechtenstein", "codigos_en_referidos"])
def test_EL_CONTROL_la_lista_copiada_se_pone_roja(texto, ruta):
    assert lista_en(texto, ruta) != []


def test_EL_CONTROL_la_lista_de_los_euros_no_se_denuncia():
    """La de `proxy.ts` es otra lista: zona euro y vecinos, sin GI ni LI."""
    euros = ('const PAISES_EUR = new Set(["ES", "PT", "FR", "IT", "DE", "AT", "BE", "NL", "LU", "IE", "AD", '
             '"MC", "CH", "NO", "DK", "SE"]);')
    assert lista_en(euros, "frontend/src/proxy.ts") == []
    # Y está de verdad en el repo: si se fuera, este control dejaría de probar nada.
    assert "PAISES_EUR" in (RAIZ / "frontend" / "src" / "proxy.ts").read_text(encoding="utf-8")
