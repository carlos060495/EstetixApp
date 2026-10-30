"""LAS CLAVES CON LAS QUE SE COMPRUEBA UNA SESIÓN, SIN PARAR A NADIE.

QUÉ PASABA (medido el 14-10-2026 contra el backend real y un Supabase de
laboratorio al que se le pide el retraso). La comprobación de cada sesión usaba
`jwt.PyJWKClient`, que descarga las claves con `urllib` —una llamada SÍNCRONA—
desde dentro de una función `async`. Mientras duraba la descarga NO AVANZABA
NINGUNA PETICIÓN DE NINGÚN SALÓN:

  · Supabase tarda 2 s   → la petición tarda 2 s y TODO el proceso se para 2 s.
  · Supabase se cuelga   → 30 s de proceso muerto (el plazo de PyJWKClient), y
                           al final la sesión, que era buena, se rechaza con 401.
  · Supabase da un error → 401 a una sesión buena: el navegador la da por
                           caducada y manda a la persona al login.

Pasaba cada cinco minutos (al caducar la caché) y con cada clave desconocida.

LO QUE HACE ESTO, y las tres reglas que lo sostienen:

  1. LA DESCARGA ES ASÍNCRONA Y TIENE PLAZO (`PLAZO`). Va por el cliente
     compartido de `app/db/conexiones.py`, así que mientras espera, el resto
     del mundo sigue trabajando.

  2. AL CADUCAR NO ESPERA NADIE. Las claves que ya tenemos se siguen usando y la
     renovación se hace por detrás, una sola a la vez. La petición que coincide
     con la caducidad tarda lo mismo que cualquier otra.

  3. UN MAL MINUTO DE SUPABASE NO ECHA A NADIE. Si la renovación falla, se
     siguen usando las claves de antes (hasta `VIDA_MAXIMA_S`). Y si de verdad
     no hay con qué comprobar una sesión —arranque en frío con Supabase caído—
     se contesta 503 con un motivo, NUNCA 401: un 401 es «tu sesión no vale» y
     el navegador manda al login a quien no ha hecho nada mal.

Solo se ESPERA a Supabase cuando llega una clave que no conocemos (Supabase ha
rotado las suyas, o es el primer arranque), y entonces como mucho `PLAZO`.

LA VERIFICACIÓN NO SALE A LA RED. `security._usuario_desde_bearer` lee las
claves de memoria (`clave`); quien llama hace antes `await preparar(...)`. Así
la parte síncrona no puede volver a bloquear el bucle: no tiene red que tocar.
Lo vigila `tests/test_nada_de_fuera_para_la_aplicacion.py`.
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx
import jwt

from app.db.conexiones import cliente_compartido

logger = logging.getLogger("estetix.claves_de_sesion")

# Cada cuánto se pregunta a Supabase si hay claves nuevas. Las mismas cinco
# minutos que tenía PyJWKClient: rotar claves es raro y se anuncia antes.
FRESCAS_S = 300.0

# EL PLAZO DE LA DESCARGA. Son un JSON de menos de 1 KB servido por el borde de
# Supabase: lo normal son decenas de milisegundos. Tres segundos es holgura de
# sobra para un mal día, y es también lo máximo que puede llegar a esperar una
# petición (solo la que trae una clave nueva; las demás no esperan nunca).
PLAZO = httpx.Timeout(3.0)

# CUÁNTO SE FÍA DE LAS CLAVES QUE YA TIENE sin poder renovarlas. Una hora: más
# que la vida de un token de Supabase. Si Supabase revoca una clave, la
# siguiente renovación que salga bien la quita; esto solo cubre el rato en que
# no se puede preguntar.
VIDA_MAXIMA_S = 3600.0

# Tras un intento (bien o mal) no se vuelve a preguntar antes de esto si quien
# lo pide es una clave desconocida. Sin este suelo, una ristra de tokens con
# claves inventadas sería una ristra de descargas.
ENTRE_INTENTOS_S = 1.0

# Una clave que se buscó y NO estaba no se vuelve a buscar en este rato.
NO_ESTABA_S = 30.0


def _kid(token: str) -> str | None:
    try:
        kid = jwt.get_unverified_header(token).get("kid")
    except jwt.PyJWTError:
        return None
    return kid if isinstance(kid, str) and kid else None


class _Claves:
    """Las claves de UNA dirección de JWKS. Si cambia, se empieza de cero."""

    def __init__(self) -> None:
        self._reiniciar(None)

    def _reiniciar(self, url: str | None) -> None:
        self._url = url
        self._claves: dict[str, jwt.PyJWK] = {}
        self._leidas_en: float | None = None   # último éxito
        self._intentada_en: float | None = None  # último intento, saliera como saliera
        self._fallo = False                     # el último intento falló
        self._no_estaban: dict[str, float] = {}
        self._en_curso: asyncio.Task | None = None

    # ── lo que lee la verificación (síncrono, sin red) ──────────────────────

    def clave(self, url: str, kid: str | None) -> jwt.PyJWK | None:
        if url != self._url or kid is None or self._leidas_en is None:
            return None
        if time.monotonic() - self._leidas_en > VIDA_MAXIMA_S:
            return None
        return self._claves.get(kid)

    def sin_respuesta(self, url: str) -> bool:
        """No hay con qué comprobar porque Supabase NO CONTESTA (no porque la
        clave sea mala). Es lo que separa un 503 de un 401."""
        return url == self._url and self._fallo

    # ── lo que prepara la petición (asíncrono, con plazo) ───────────────────

    async def preparar(self, token: str, url: str) -> None:
        """Deja en memoria la clave de este token, si se puede. Nunca lanza."""
        if url != self._url:
            self._reiniciar(url)
        kid = _kid(token)
        if kid is None:
            return  # no es un token con clave: lo rechazará la verificación
        ahora = time.monotonic()
        if self.clave(url, kid) is not None:
            if ahora - (self._leidas_en or 0) > FRESCAS_S:
                self._renovar_por_detras(url)  # nadie espera por esto
            return
        if ahora - self._no_estaban.get(kid, -NO_ESTABA_S) < NO_ESTABA_S:
            return
        if self._en_curso_aqui() is not None:
            await asyncio.shield(self._en_curso)  # ya va una: se espera a esa
            if self.clave(url, kid) is not None:
                return
        # SUPABASE NO CONTESTA: NO SE VUELVE A ESPERAR EN ESTA PETICIÓN (19-10-2026).
        # El suelo de abajo es para una clave recién rotada (la descarga SALIÓ BIEN
        # y la clave aún no estaba). Si lo que acaba de fallar es la descarga, otra
        # en esta misma petición solo duplicaba la espera: medido con 50 sesiones a
        # la vez y Supabase colgado, 7,4 s en vez de un plazo. Se contesta ya (503).
        if self._fallo and self._intentada_en is not None \
                and time.monotonic() - self._intentada_en < ENTRE_INTENTOS_S:
            return
        # SE ESPERA EL SUELO, NO SE RECHAZA. Una clave nueva que llega justo
        # después de una descarga puede haber nacido después de ella (Supabase
        # acaba de rotar): rechazarla sería un 401 a una sesión buena. Lo
        # destapó la medida del 14-10: la primera petición con la clave nueva
        # salía 401.
        espera = ENTRE_INTENTOS_S - (time.monotonic() - (self._intentada_en or -ENTRE_INTENTOS_S))
        if espera > 0:
            await asyncio.sleep(espera)
            if self.clave(url, kid) is not None:
                return
        if self._en_curso_aqui() is not None:
            await asyncio.shield(self._en_curso)  # otra que esperaba se adelantó
        else:
            await self._descargar_una_vez(url)
        if url == self._url and not self._fallo and kid not in self._claves:
            self._no_estaban[kid] = time.monotonic()

    def _en_curso_aqui(self) -> asyncio.Task | None:
        t = self._en_curso
        if t is None or t.done():
            return None
        # Una tarea es de su bucle de eventos; en los tests hay más de uno.
        if t.get_loop() is not asyncio.get_running_loop():
            return None
        return t

    def _renovar_por_detras(self, url: str) -> None:
        if self._en_curso_aqui() is not None:
            return
        if self._intentada_en is not None and time.monotonic() - self._intentada_en < ENTRE_INTENTOS_S:
            return
        self._en_curso = asyncio.get_running_loop().create_task(self._descargar(url))

    async def _descargar_una_vez(self, url: str) -> None:
        self._en_curso = asyncio.get_running_loop().create_task(self._descargar(url))
        await asyncio.shield(self._en_curso)

    async def _descargar(self, url: str) -> None:
        try:
            r = await cliente_compartido().get(url, timeout=PLAZO)
            r.raise_for_status()
            juego = jwt.PyJWKSet.from_dict(r.json())
            claves = {k.key_id: k for k in juego.keys if k.key_id}
            if not claves:
                raise ValueError("el JWKS no trae ninguna clave con identificador")
        except Exception as e:  # noqa: BLE001 — cualquier fallo: se sigue con las de antes
            if url == self._url:
                self._intentada_en = time.monotonic()
                self._fallo = True
            logger.warning(
                "No se pudieron renovar las claves de sesión (%s: %s). Se siguen usando las de antes.",
                type(e).__name__, e,
            )
            return
        if url != self._url:
            return  # mientras tanto cambió la dirección: esto ya no es de nadie
        ahora = time.monotonic()
        self._claves, self._leidas_en, self._intentada_en, self._fallo = claves, ahora, ahora, False
        self._no_estaban = {k: t for k, t in self._no_estaban.items() if k not in claves}

    # ── solo para medir y para los tests ────────────────────────────────────

    def envejecer(self, segundos: float) -> None:
        """Hace como si las claves se hubieran leído hace `segundos` más."""
        if self._leidas_en is not None:
            self._leidas_en -= segundos
        if self._intentada_en is not None:
            self._intentada_en -= segundos


_CLAVES = _Claves()


async def preparar(token: str, url: str) -> None:
    await _CLAVES.preparar(token, url)


def clave(url: str, token: str) -> jwt.PyJWK | None:
    return _CLAVES.clave(url, _kid(token))


def sin_respuesta(url: str) -> bool:
    return _CLAVES.sin_respuesta(url)


def olvidar() -> None:
    """Empieza de cero. Para los tests."""
    _CLAVES._reiniciar(None)
