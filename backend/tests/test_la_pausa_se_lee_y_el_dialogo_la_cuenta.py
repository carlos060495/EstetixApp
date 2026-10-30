"""La pausa existe de verdad, y el diálogo se lo cuenta al salón. Las dos.

ESTE FICHERO ES EL SUCESOR DE `test_sigue_al_bajar_todavia_no_se_lee.py`, que
afirmaba lo contrario: que `salons.sigue_al_bajar` se escribía y no la leía
nadie. Se puso rojo el día que llegó B3a, que es exactamente para lo que estaba
escrito, y lo que hizo falta ese día fue devolverle al diálogo de bajar de plan
la frase de la pausa. Hecho eso, el espejo sigue vivo — solo que ahora mira al
otro lado.

POR QUÉ SIGUE HACIENDO FALTA. Es el mismo tipo de test que el de los límites
(`test_limites_espejo.py`): un dato que vive en dos sitios y que puede separarse
en silencio. Los dos sitios son el USO de la columna en el código y lo que el
diálogo le PROMETE al salón. Nada obliga a que cambien juntos, así que lo obliga
esto, y ahora en las dos direcciones:

  · si alguien quita la congelación y el diálogo se queda prometiendo una pausa
    que ya no pasa —que es el estado del que venimos, y del que costó salir—;
  · si alguien cambia el texto del diálogo y le quita la frase mientras el
    código sí pausa centros, el salón se encontraría un centro apagado sin que
    nadie se lo hubiera dicho.

QUÉ CUENTA COMO ESCRITURA: la columna aparece como valor que se guarda
(`{"sigue_al_bajar": True}`) o como filtro de ese mismo `update` —desmarcar al
anterior—. Todo lo demás —pedirla en un `select`, leerla de una fila, mirarla en
un `if`, usarla en el frontend— es una LECTURA.

Los comentarios no cuentan: hablar de la columna no es usarla.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
BACKEND = RAIZ / "backend" / "app"
FRONTEND = RAIZ / "frontend" / "src"

COLUMNA = "sigue_al_bajar"

# El ÚNICO sitio que puede escribirla (0088): `billing/el_que_sigue.py`. Lo usan
# la bajada de plan, el cambio de centro desde Ajustes y el webhook que borra la
# marca al subir a un plan que cubre todos los centros.
FUNCION_QUE_LA_ESCRIBE = "marcar_el_que_sigue"

# Las dos formas que usa ese guardado: marcar o desmarcar (valor), y acotar el
# UPDATE al que estaba marcado (filtro).
_ESCRITURAS = (
    f'"{COLUMNA}": True',
    f'"{COLUMNA}": False',
    f'"{COLUMNA}": "is.true"',
)

_ARRANQUES = ("async def ", "def ", "export function ", "function ")


@dataclass(frozen=True)
class Aparicion:
    fichero: str
    linea: int
    texto: str
    funcion: str

    @property
    def es_escritura(self) -> bool:
        return any(forma in self.texto for forma in _ESCRITURAS)

    def __str__(self) -> str:
        return f"{self.fichero}:{self.linea}  {self.texto.strip()}"


def _sin_comentarios(texto: str, sufijo: str) -> list[str]:
    """El fichero con lo comentado quitado, SIN mover ni una línea.

    Se recorta lo comentado y se devuelve una entrada por línea, para que el
    número que salga en el fallo siga siendo el de verdad. No es un analizador:
    reconoce las formas que usa este repositorio —almohadilla y docstring en
    Python, barras y bloque en TypeScript— y nada más.
    """
    fuera: list[str] = []
    dentro_de_bloque = False
    for linea in texto.splitlines():
        limpia = linea
        if sufijo == ".py":
            if dentro_de_bloque:
                # Dentro de un docstring, la línea que lo cierra empieza igual
                # que la que lo abre: lo que decide es que aparezcan las tres
                # comillas, no dónde.
                dentro_de_bloque = '"""' not in limpia
                limpia = ""
            elif limpia.lstrip().startswith('"""'):
                dentro_de_bloque = limpia.count('"""') < 2
                limpia = ""
            corte = limpia.find("#")
            if corte >= 0:
                limpia = limpia[:corte]
        else:
            if dentro_de_bloque:
                fin = limpia.find("*/")
                limpia = limpia[fin + 2 :] if fin >= 0 else ""
                dentro_de_bloque = fin < 0
            inicio = limpia.find("/*")
            if inicio >= 0:
                fin = limpia.find("*/", inicio)
                if fin >= 0:
                    limpia = limpia[:inicio] + limpia[fin + 2 :]
                else:
                    limpia = limpia[:inicio]
                    dentro_de_bloque = True
            corte = limpia.find("//")
            if corte >= 0:
                limpia = limpia[:corte]
        fuera.append(limpia)
    return fuera


def _funcion_que_la_contiene(lineas: list[str], numero: int) -> str:
    """El `def` más cercano por encima. Vale para Python y para TypeScript."""
    for anterior in reversed(lineas[: numero - 1]):
        limpio = anterior.strip()
        for arranque in _ARRANQUES:
            if limpio.startswith(arranque):
                return limpio[len(arranque) :].split("(")[0]
    return "<fuera de toda función>"


def _nombre(fichero: Path) -> str:
    """La ruta desde la raíz del repositorio; solo el nombre si viene de fuera
    (los árboles inventados de los controles)."""
    try:
        return str(fichero.relative_to(RAIZ)).replace("\\", "/")
    except ValueError:
        return fichero.name


def apariciones(raices: tuple[Path, ...] = (BACKEND, FRONTEND)) -> list[Aparicion]:
    """Cada línea de código que nombra la columna, con la función que la contiene."""
    fuera: list[Aparicion] = []
    for raiz in raices:
        if not raiz.exists():
            continue
        for fichero in sorted(raiz.rglob("*")):
            if fichero.suffix not in (".py", ".ts", ".tsx") or "__pycache__" in fichero.parts:
                continue
            lineas = _sin_comentarios(fichero.read_text(encoding="utf-8"), fichero.suffix)
            for numero, texto in enumerate(lineas, start=1):
                if COLUMNA in texto:
                    fuera.append(
                        Aparicion(
                            fichero=_nombre(fichero),
                            linea=numero,
                            texto=texto,
                            funcion=_funcion_que_la_contiene(lineas, numero),
                        )
                    )
    return fuera


# ═══════════════════════════════════════════════════════ el espejo


# Los sitios que pueden LEER la columna, y por qué cada uno. Una lectura nueva
# fuera de esta lista no es necesariamente un error — es algo que nadie ha
# decidido, y hay que decidirlo.
QUIEN_PUEDE_LEERLA = {
    # El reparto: la única pieza que decide qué centro se queda.
    ("backend/app/billing/lo_que_incluye_el_plan.py", "<fuera de toda función>"),
    ("backend/app/billing/lo_que_incluye_el_plan.py", "_clave"),
    # Y las cuatro consultas que le traen los centros ya colgados de la fila que
    # estaban leyendo de todas formas. Son `select`, no decisiones: si alguna
    # dejara de pedirla, el reparto miraría una clave que nunca llega y no
    # pausaría nada —en silencio—.
    ("backend/app/core/security.py", "_exigir_sesion_valida"),
    ("backend/app/routers/publico.py", "_salon_por_slug"),
    ("backend/app/routers/webhooks_whatsapp.py", "_salon_del_numero"),
    ("backend/app/routers/billing.py", "_avisar_si_deja_centros_en_pausa"),
    # LA ZONA DE LA CUENTA en el panel (02-10-2026): las fechas de la
    # cuenta van en la hora del centro que se queda al bajar de plan. No decide
    # nada nuevo: pregunta al reparto (`centro_que_se_queda`) y solo lee la
    # marca para DECIR en pantalla si ese centro está marcado o es el más
    # antiguo. `COLUMNAS` es el `select`; `_cargar` le trae los centros a
    # Facturación.
    ("backend/app/core/zona_de_la_cuenta.py", "<fuera de toda función>"),
    ("backend/app/core/zona_de_la_cuenta.py", "zona_de_la_cuenta"),
    ("backend/app/routers/admin_plataforma.py", "_cargar"),
    # ELEGIR QUÉ CENTRO SE QUEDA (0088). `SELECT_CENTROS` es el `select`;
    # `marcado` dice si la titular ya eligió (el aviso «se ha quedado X» y
    # devolver la marca si Stripe rechaza); `en_pausa_si_se_queda` pone la
    # marca donde se va a poner y PREGUNTA AL REPARTO, no decide por su cuenta.
    ("backend/app/billing/el_que_sigue.py", "<fuera de toda función>"),
    ("backend/app/billing/el_que_sigue.py", "marcado"),
    ("backend/app/billing/el_que_sigue.py", "en_pausa_si_se_queda"),
    # Y el listado de Ajustes › Centros la pide para decir si ya eligió.
    ("backend/app/routers/centros.py", "<fuera de toda función>"),
}

DIALOGO = RAIZ / "frontend" / "src" / "app" / "ajustes" / "confirmar-cambio-plan.tsx"

# Lo que el diálogo TIENE que seguir diciendo mientras el código pause centros.
# No es el texto entero —eso se rompería al cambiar una coma— sino lo que no se
# puede dejar de prometer: que se pausa, y que no se borra.
LO_QUE_NO_SE_PUEDE_CALLAR = ("en pausa", "No se borra nada")


def test_la_columna_SE_LEE_de_verdad():
    """LA MITAD QUE VIGILA QUE LA PAUSA NO DESAPAREZCA.

    Si alguien retirase la congelación, esto se pondría rojo antes de que el
    diálogo se quedara prometiendo algo que ya no pasa. Es el estado del que
    venimos: la columna se escribía, no la leía nadie, y el texto prometía una
    pausa que no existía.
    """
    lecturas = [a for a in apariciones() if not a.es_escritura]

    assert lecturas, (
        "NADIE LEE YA `salons.sigue_al_bajar`. Si se ha retirado la congelación "
        "(B3a), hay que quitarle al diálogo de bajar de plan la frase de la "
        "pausa en el acto: está en "
        "frontend/src/app/ajustes/confirmar-cambio-plan.tsx, en el bloque "
        "«¿Qué centro se queda?». No se puede prometer lo que no se hace."
    )


def test_y_la_lee_SOLO_quien_debe():
    """El reparto, y las consultas que le dan de comer. Nadie más.

    Una lectura en otro sitio casi siempre significa que alguien ha vuelto a
    decidir por su cuenta qué centro se queda, y entonces habría dos respuestas
    posibles a la misma pregunta.
    """
    sitios = {(a.fichero, a.funcion) for a in apariciones() if not a.es_escritura}

    assert sitios == QUIEN_PUEDE_LEERLA, (
        "Lee la columna quien no debería, o ha dejado de leerla quien sí.\n"
        f"  sobran: {sorted(sitios - QUIEN_PUEDE_LEERLA)}\n"
        f"  faltan: {sorted(QUIEN_PUEDE_LEERLA - sitios)}"
    )


def test_solo_la_escribe_el_guardado_del_cambio_programado():
    """Y SIGUE HABIENDO UN SOLO ESCRITOR. La congelación CALCULA el centro que se
    queda y no lo escribe: si lo escribiera, la invariante de «un elegido por
    cuenta» pasaría a depender de dos caminos y de una carrera contra el índice
    único de la 0049."""
    sitios = {(a.fichero, a.funcion) for a in apariciones() if a.es_escritura}

    assert sitios == {("backend/app/billing/el_que_sigue.py", FUNCION_QUE_LA_ESCRIBE)}, sitios


def test_el_dialogo_le_dice_al_salon_lo_que_va_a_pasar():
    """LA OTRA MITAD DEL ESPEJO, y la que le importa a quien paga.

    El código pausa centros. Si el texto no lo dice, el salón se encuentra un
    local apagado sin que nadie se lo hubiera avisado — que es peor que la
    promesa vacía de antes, porque ahora sí pasa algo.
    """
    texto = DIALOGO.read_text(encoding="utf-8")
    sin_comentar = "\n".join(_sin_comentarios(texto, ".tsx"))

    faltan = [f for f in LO_QUE_NO_SE_PUEDE_CALLAR if f not in sin_comentar]

    assert not faltan, (
        f"El diálogo de bajar de plan ya no dice: {faltan}.\n\n"
        "Mientras el código pause centros, ese bloque tiene que decir QUÉ les "
        "pasa y que no se borra nada. Si se ha retirado la pausa, quita también "
        "la frase — y este test entero."
    )


# ═══════════════════════════════════════════ que no pase en vacío


def test_el_lector_encuentra_las_escrituras_que_SI_existen():
    """El control de siempre: si el lector dejara de encontrar nada —una ruta
    cambiada, un fichero movido— el test de arriba pasaría sin mirar nada."""
    encontradas = [a for a in apariciones() if a.es_escritura]

    assert len(encontradas) == 3, [str(a) for a in encontradas]
    assert all(a.fichero == "backend/app/billing/el_que_sigue.py" for a in encontradas)


def test_y_detecta_una_lectura_DE_VERDAD_si_la_hubiera(tmp_path):
    """EL CONTROL QUE IMPORTA. No basta con no encontrar lecturas: hay que
    demostrar que las encontraría. Se le da un árbol inventado con las formas en
    las que B3a leería la columna: pedirla en un `select`, mirarla en una fila y
    filtrar por ella en una pantalla."""
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "congelacion.py").write_text(
        "async def centros_congelados(db, cuenta):\n"
        '    filas = await db.select("salons", {"select": "id,sigue_al_bajar"})\n'
        '    return [f for f in filas if not f["sigue_al_bajar"]]\n',
        encoding="utf-8",
    )
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "centros.tsx").write_text(
        "export function Centros({ centros }) {\n"
        "  return centros.filter((c) => c.sigue_al_bajar);\n"
        "}\n",
        encoding="utf-8",
    )

    encontradas = apariciones((tmp_path / "app", tmp_path / "web"))
    lecturas = [a for a in encontradas if not a.es_escritura]

    assert len(encontradas) == 3, [str(a) for a in encontradas]
    assert len(lecturas) == 3, "ninguna de las tres es una escritura"
    assert {a.funcion for a in lecturas} == {"centros_congelados", "Centros"}


def test_nombrarla_en_un_COMENTARIO_no_cuenta(tmp_path):
    """El contrapeso del control anterior: hablar de la columna no es leerla, y
    si contara, el propio código que la nombra dejaría el test rojo."""
    (tmp_path / "comentada.py").write_text(
        "# Aquí se leerá sigue_al_bajar cuando llegue B3a.\n"
        "def nada():\n"
        '    """Todavía no se mira sigue_al_bajar."""\n'
        "    return None\n",
        encoding="utf-8",
    )
    (tmp_path / "comentada.tsx").write_text(
        "/* Cuando exista la pausa, este bloque leerá sigue_al_bajar. */\n"
        "export function Centros() {\n"
        "  // ni aquí: sigue_al_bajar\n"
        "  return null;\n"
        "}\n",
        encoding="utf-8",
    )

    assert apariciones((tmp_path,)) == []


def test_una_escritura_NO_se_toma_por_una_lectura(tmp_path):
    """Y el detector no puede chillar por las escrituras legítimas: si lo hiciera,
    el día que B3a llegue nadie sabría distinguir la señal del ruido."""
    (tmp_path / "escribe.py").write_text(
        "async def marcar(db, centro):\n"
        '    await db.update("salons", {"id": f"eq.{centro}"}, {"sigue_al_bajar": True})\n',
        encoding="utf-8",
    )

    encontradas = apariciones((tmp_path,))

    assert len(encontradas) == 1 and encontradas[0].es_escritura
