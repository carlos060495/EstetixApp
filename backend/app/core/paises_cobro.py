"""EN QUÉ PAÍSES PUEDE COBRAR UN REFERIDO. El ÚNICO sitio donde está la lista.

05-10-2026, comprobado en el panel de Stripe país por país: desde una
plataforma española, Stripe deja enviar fondos a cuentas de los 27 de la Unión
Europea, Noruega, Liechtenstein, Reino Unido, Suiza y Gibraltar. 32 países.

NO están, y no es un descuido: Estados Unidos, Canadá, México y Tailandia
(Stripe no nos deja enviarles fondos desde España), ni Islandia, aunque sea del
Espacio Económico Europeo.

De aquí la sacan la página pública (`GET /referido/paises`) y el portal, que
frena a quien sea de fuera ANTES de mandarlo a Stripe; y el backend la usa para
rechazar cualquier otro país al crear la cuenta (`core/cobros_referido.py`).
`tests/test_paises_cobro.py` se pone rojo si la lista aparece escrita en otro
sitio, del backend o del frontend.
"""

from __future__ import annotations

# Código ISO 3166-1 alfa-2 → nombre, en el orden en que se enseña.
PAISES_COBRO: dict[str, str] = {
    "DE": "Alemania",
    "AT": "Austria",
    "BE": "Bélgica",
    "BG": "Bulgaria",
    "CY": "Chipre",
    "HR": "Croacia",
    "DK": "Dinamarca",
    "SK": "Eslovaquia",
    "SI": "Eslovenia",
    "ES": "España",
    "EE": "Estonia",
    "FI": "Finlandia",
    "FR": "Francia",
    "GI": "Gibraltar",
    "GR": "Grecia",
    "HU": "Hungría",
    "IE": "Irlanda",
    "IT": "Italia",
    "LV": "Letonia",
    "LI": "Liechtenstein",
    "LT": "Lituania",
    "LU": "Luxemburgo",
    "MT": "Malta",
    "NO": "Noruega",
    "NL": "Países Bajos",
    "PL": "Polonia",
    "PT": "Portugal",
    "GB": "Reino Unido",
    "CZ": "República Checa",
    "RO": "Rumanía",
    "SE": "Suecia",
    "CH": "Suiza",
}

POR_QUE_NO = (
    "Por ahora solo podemos pagar a cuentas de la Unión Europea, Noruega, Liechtenstein, "
    "Reino Unido, Suiza y Gibraltar: Stripe, que es quien hace los pagos, no nos deja "
    "enviar dinero a otros países desde España."
)


def se_puede_cobrar_en(pais: str | None) -> bool:
    return bool(pais) and pais.upper() in PAISES_COBRO
