"""¿Sabemos que ese correo NO salió? La frontera, probada donde se decide.

DE ESTO DEPENDE QUE NO HAYA DUPLICADOS. El recordatorio se sella antes de
enviar, así que soltar el sello y reintentar solo es legítimo cuando estamos
seguros de que el correo no salió. Clasificar de más un fallo como «seguro» es
mandarle a alguien dos recordatorios; clasificar de menos es perder uno.

LA REGLA, y se prueba entera aquí:

  · SEGURO  = la petición no llegó a existir para el servidor. Conexión que no
    se abre, tiempo agotado ABRIENDO, sin conexión en el pool, y un 4xx (que es
    el servidor contestando que la rechaza).
  · AMBIGUO = todo lo demás. Sobre todo el tiempo agotado LEYENDO —la petición
    se mandó entera y no llegamos a leer el «sí»— y los 5xx.

Se prueba contra el `httpx` de verdad, no contra un doble del cliente: lo que se
está fijando es cómo se traduce cada excepción y cada código suyo.
"""

from __future__ import annotations

import httpx
import pytest

from app.core.config import Settings
from app.core.emails import EnvioFallido, enviar_email_branded


def _settings() -> Settings:
    return Settings(
        supabase_url="http://local",
        supabase_secret_key="k",
        supabase_jwks_url="http://local/jwks",
        database_url="postgresql://x",
        frontend_base_url="https://estetixapp.es",
        resend_api_key="re_x",
        email_from="hola@estetixapp.es",
    )


async def _mandar():
    await enviar_email_branded(_settings(), "quien@correo.es", "Asunto", "<p>hola</p>")


def _peticion() -> httpx.Request:
    return httpx.Request("POST", "https://api.resend.com/emails")


@pytest.fixture()
def resend(monkeypatch):
    """Sustituye el `post` de httpx. Devuelve un estado para decidir qué pasa."""
    estado: dict = {"lanza": None, "codigo": 200, "cuerpo": '{"id":"re_1"}'}

    async def _post(self, url, **kw):
        if estado["lanza"]:
            raise estado["lanza"]
        return httpx.Response(estado["codigo"], text=estado["cuerpo"], request=_peticion())

    monkeypatch.setattr(httpx.AsyncClient, "post", _post)
    return estado


# ═══════════════════════════════════════════════════════ lo que sí salió


@pytest.mark.anyio
async def test_un_200_no_lanza_nada(resend):
    await _mandar()


# ═══════════════════════════════════════════ SEGURO: no llegó a existir


@pytest.mark.anyio
@pytest.mark.parametrize(
    "fallo",
    [
        httpx.ConnectError("no se pudo abrir", request=_peticion()),
        httpx.ConnectTimeout("se agotó abriendo", request=_peticion()),
        httpx.PoolTimeout("sin conexión libre", request=_peticion()),
    ],
)
async def test_si_la_conexion_NO_se_abrio_es_seguro(resend, fallo):
    """Ni un byte salió, así que reintentar no puede duplicar nada."""
    resend["lanza"] = fallo
    with pytest.raises(EnvioFallido) as e:
        await _mandar()

    assert e.value.seguro is True
    assert type(fallo).__name__ in str(e.value)


@pytest.mark.anyio
@pytest.mark.parametrize("codigo", [400, 401, 403, 404, 422, 429])
async def test_un_4xx_es_seguro(resend, codigo):
    """Resend contestó rechazando: una petición rechazada no es un correo
    enviado. El 429 entra aquí y por eso se reintenta, que es lo que se quiere
    con un límite de ritmo."""
    resend["codigo"] = codigo
    with pytest.raises(EnvioFallido) as e:
        await _mandar()

    assert e.value.seguro is True
    assert str(codigo) in str(e.value)


# ═════════════════════════════════════════ AMBIGUO: puede haber salido


@pytest.mark.anyio
@pytest.mark.parametrize(
    "fallo",
    [
        # EL CASO CLÁSICO: la petición se mandó ENTERA y se agotó el tiempo
        # esperando la respuesta. Resend puede haberla aceptado.
        httpx.ReadTimeout("se agotó leyendo", request=_peticion()),
        httpx.WriteTimeout("se cortó escribiendo", request=_peticion()),
        httpx.ReadError("se cayó leyendo", request=_peticion()),
        httpx.WriteError("se cayó escribiendo", request=_peticion()),
        httpx.RemoteProtocolError("el otro lado se portó raro", request=_peticion()),
    ],
)
async def test_si_algo_se_mando_es_AMBIGUO(resend, fallo):
    resend["lanza"] = fallo
    with pytest.raises(EnvioFallido) as e:
        await _mandar()

    assert e.value.seguro is False


@pytest.mark.anyio
@pytest.mark.parametrize("codigo", [500, 502, 503, 504])
async def test_un_5xx_es_AMBIGUO(resend, codigo):
    """Aunque intuitivamente parezca un «no salió».

    Un 5xx es el servidor diciendo que algo se rompió DENTRO, y si el correo ya
    estaba encolado cuando se rompió es justo lo que no se puede saber desde
    fuera. Se paga con recordatorios perdidos durante una caída de Resend; la
    alternativa se paga con correos duplicados.
    """
    resend["codigo"] = codigo
    with pytest.raises(EnvioFallido) as e:
        await _mandar()

    assert e.value.seguro is False


@pytest.mark.anyio
async def test_el_limite_esta_EXACTAMENTE_en_500(resend):
    """El borde, por los dos lados: 499 seguro, 500 ambiguo."""
    resend["codigo"] = 499
    with pytest.raises(EnvioFallido) as e:
        await _mandar()
    assert e.value.seguro is True

    resend["codigo"] = 500
    with pytest.raises(EnvioFallido) as e:
        await _mandar()
    assert e.value.seguro is False


@pytest.mark.anyio
async def test_un_fallo_de_transporte_NUEVO_se_da_por_ambiguo(resend):
    """LO NO CLASIFICADO ES AMBIGUO, y es el valor por defecto a propósito.

    Si mañana `httpx` añade una excepción que aquí no está enumerada, lo peor
    que puede pasar es perder un recordatorio. Nunca duplicarlo.
    """

    class FalloDeManana(httpx.TransportError):
        pass

    resend["lanza"] = FalloDeManana("algo que todavía no existe")
    with pytest.raises(EnvioFallido) as e:
        await _mandar()

    assert e.value.seguro is False


@pytest.mark.anyio
async def test_sigue_siendo_un_RuntimeError(resend):
    """Los otros correos del proyecto —alta, recuperación, handoff— capturan
    `Exception` o `RuntimeError`. Cambiar el tipo no puede romperlos."""
    resend["codigo"] = 500
    with pytest.raises(RuntimeError):
        await _mandar()
