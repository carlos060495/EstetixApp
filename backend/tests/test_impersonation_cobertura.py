"""Ninguna ruta mutable puede quedar sin clasificar para el modo soporte.

Si este test falla es porque alguien ha añadido un endpoint y no ha decidido si
soporte puede usarlo. No lo "arregles" metiendo la ruta en PERMITIDOS sin
pensar: la pregunta es si el dueño del salón esperaría que un agente de soporte
pudiera hacer eso con su consentimiento por código.

NOTA sobre el recorrido de rutas: NO se itera `app.routes` en plano. En
FastAPI 0.139 / Starlette 1.3.1, `include_router` no aplana las rutas dentro
de `app.routes`: mete un objeto `_IncludedRouter` que las guarda en
`.original_router.routes` y que no tiene ni `.path` ni `.methods`. Un
recorrido plano de `app.routes` devuelve CERO rutas mutables de negocio, así
que este test pasaría en vacío sin comprobar nada. Por eso se reutiliza
`rutas_mutables()` de `app.core.impersonation_policy`, que ya resuelve el
recorrido recursivo correcto (ver `iterar_rutas` en ese módulo).
"""

from __future__ import annotations

from app.core.impersonation_policy import BLOQUEADOS, PERMITIDOS, PUBLICOS, rutas_mutables
from app.main import app

# /admin/* se excluye: el panel se autentica con __Host-sa-access-token, nunca con la
# cookie de soporte, así que la guardia de impersonación no lo mira y no
# clasificamos sus rutas aquí (ver PREFIJO_PANEL en impersonation_policy.py).
_PREFIJO_PANEL = "/admin"


def _rutas_mutables_de_tenant() -> set[str]:
    return {
        ruta
        for ruta in rutas_mutables(app)
        if not ruta.split(" ", 1)[1].startswith(_PREFIJO_PANEL)
    }


def test_toda_ruta_mutable_esta_clasificada() -> None:
    clasificadas = PERMITIDOS | BLOQUEADOS | PUBLICOS
    sin_clasificar = _rutas_mutables_de_tenant() - clasificadas
    assert not sin_clasificar, (
        "Endpoints sin clasificar para el modo soporte: "
        + ", ".join(sorted(sin_clasificar))
        + ". Añádelos a PERMITIDOS, BLOQUEADOS o PUBLICOS en "
        "app/core/impersonation_policy.py."
    )


def test_no_hay_rutas_clasificadas_que_ya_no_existan() -> None:
    """Al revés: una entrada huérfana suele significar que se renombró una ruta
    y la lista negra dejó de aplicarse sin que nadie se enterase."""
    huerfanas = (PERMITIDOS | BLOQUEADOS | PUBLICOS) - _rutas_mutables_de_tenant()
    assert not huerfanas, f"Rutas clasificadas que ya no existen: {sorted(huerfanas)}"


def test_las_listas_no_se_solapan() -> None:
    assert not (PERMITIDOS & BLOQUEADOS)
    assert not (PERMITIDOS & PUBLICOS)
    assert not (BLOQUEADOS & PUBLICOS)


def test_la_facturacion_esta_bloqueada() -> None:
    """Ancla explícita: si alguien mueve estas rutas de lista, que se vea."""
    for ruta in ("POST /billing/checkout", "POST /billing/checkout/trial", "POST /billing/portal"):
        assert ruta in BLOQUEADOS


def test_generar_otro_codigo_esta_bloqueado() -> None:
    assert "POST /soporte/codigo" in BLOQUEADOS
