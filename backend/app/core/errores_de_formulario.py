"""Un dato que no vale, dicho para la persona que lo escribió.

FastAPI contesta un 422 con la lista de errores de Pydantic, en inglés y con
prefijos de programador: «Value error, no se pueden crear citas en el pasado»,
«String should have at least 10 characters», «value is not a valid email
address: The part after the @-sign…». Las pantallas enseñaban eso tal cual.

Aquí se convierte en UNA frase en castellano que dice qué pasa y qué hacer, y
el 422 sale con `detail` como texto, que es lo que leen todas las pantallas.
Los mensajes propios (los `ValueError` de los validadores) ya están escritos
para la persona: solo se les quita el prefijo.
"""

from __future__ import annotations

# Cómo se llama cada campo para quien rellena el formulario. Lo que no está
# aquí se nombra «uno de los datos»: nunca se enseña el nombre técnico.
_CAMPOS: dict[str, str] = {
    "email": "el correo",
    "password": "la contraseña",
    "nueva": "la contraseña",
    "telefono": "el teléfono",
    "telefono_aviso": "el teléfono de aviso",
    "nombre": "el nombre",
    "nombre_comercial": "el nombre del centro",
    "admin_nombre": "tu nombre",
    "salon_nombre": "el nombre del salón",
    "direccion": "la dirección",
    "cif": "la identificación fiscal",
    "motivo": "el motivo",
    "importe": "el importe",
    "precio": "el precio",
    "precio_coste": "el precio de coste",
    "coste_unitario": "el coste por unidad",
    "cantidad": "la cantidad",
    "stock": "el stock",
    "stock_min": "el stock mínimo",
    "fecha": "la fecha",
    "fecha_inicio": "la fecha de inicio",
    "fecha_fin": "la fecha de fin",
    "inicio": "la hora de inicio",
    "fin": "la hora de fin",
    "concepto": "el concepto",
    "categoria": "la categoría",
    "notas": "las notas",
    "alergias": "las alergias",
    "pin": "el PIN",
    "codigo": "el código",
    "duracion_min": "la duración",
    "apertura": "la hora de apertura",
    "cierre": "la hora de cierre",
    "accent_color": "el color",
    "marca": "la marca",
    "mensaje": "el mensaje",
}

_POR_DEFECTO = "Revisa los datos del formulario e inténtalo de nuevo."


def _campo(loc: tuple | list) -> str | None:
    for parte in reversed(list(loc or ())):
        if isinstance(parte, str) and parte not in ("body", "query", "path", "header"):
            return _CAMPOS.get(parte)
    return None


def _mayuscula(texto: str) -> str:
    texto = texto.strip()
    if not texto:
        return texto
    texto = texto[0].upper() + texto[1:]
    return texto if texto.endswith((".", "?", "!")) else texto + "."


def un_error(err: dict) -> str:
    """La frase para UN error de Pydantic."""
    tipo = err.get("type") or ""
    msg = str(err.get("msg") or "")
    ctx = err.get("ctx") or {}
    campo = _campo(err.get("loc") or ())
    nombre = campo or "uno de los datos"

    if tipo in ("value_error", "assertion_error"):
        # El correo: el validador de Pydantic habla en inglés.
        if "email address" in msg:
            return "El correo no es válido. Revísalo: tiene que ser como nombre@dominio.es."
        for prefijo in ("Value error, ", "Assertion failed, "):
            if msg.startswith(prefijo):
                msg = msg[len(prefijo):]
        return _mayuscula(msg) if msg else f"Revisa {nombre}."
    if tipo == "missing":
        return f"Falta {nombre}." if campo else _POR_DEFECTO
    if tipo == "string_too_short":
        minimo = ctx.get("min_length")
        if minimo in (None, 1):
            return _mayuscula(f"{nombre} no puede estar vacío")
        return _mayuscula(f"{nombre} es demasiado corto: escribe al menos {minimo} caracteres")
    if tipo == "string_too_long":
        return _mayuscula(f"{nombre} es demasiado largo: como mucho {ctx.get('max_length')} caracteres")
    if tipo in ("greater_than", "greater_than_equal", "less_than", "less_than_equal"):
        limite = ctx.get("gt", ctx.get("ge", ctx.get("lt", ctx.get("le"))))
        que = {"greater_than": "mayor que", "greater_than_equal": "como mínimo",
               "less_than": "menor que", "less_than_equal": "como mucho"}[tipo]
        return _mayuscula(f"{nombre} tiene que ser {que} {limite}")
    if tipo in ("int_parsing", "int_type", "float_parsing", "float_type", "decimal_parsing", "int_from_float"):
        return _mayuscula(f"{nombre} tiene que ser un número")
    if tipo.startswith(("date", "datetime", "time")):
        return _mayuscula(f"{nombre} no es una fecha u hora válida")
    if tipo == "json_invalid":
        return "No hemos podido leer lo que se ha enviado. Recarga la página e inténtalo de nuevo."
    return _mayuscula(f"Revisa {nombre}: no es un valor válido") if campo else _POR_DEFECTO


def mensaje_de_validacion(errores: list[dict]) -> str:
    """Una sola frase, sin repetir: la primera de cada distinta, en orden."""
    frases: list[str] = []
    for err in errores:
        frase = un_error(err)
        if frase not in frases:
            frases.append(frase)
    return " ".join(frases) or _POR_DEFECTO
