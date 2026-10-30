"""Thin async wrapper over Supabase's PostgREST endpoint.

Deliberately not a generic SQL client: every call is scoped to a single
table and goes through PostgREST, which means every request is *also*
independently re-validated against RLS using the caller's own JWT — even
though the FastAPI layer already checked the token. Two independent checks,
neither trusting the other, per the "backend always revalidates" principle
in DIRECTRICES_PROYECTO.md §4.2/§4.4.
"""

from __future__ import annotations

import json
import logging

import httpx

from app.db.conexiones import cliente_compartido

logger = logging.getLogger("estetix.postgrest")

# Cuánto del cuerpo de error se registra cuando no es JSON reconocible. Lo justo
# para diagnosticar sin volcar una respuesta entera en cada línea de log.
MAX_DETALLE = 400


# UNA LISTA EN LA DIRECCIÓN TIENE TECHO. `in.(a,b,c…)` viaja en la URL, y la
# pasarela de Supabase corta en torno a 8 KB: con identificadores de 36
# caracteres eso son ~210. La pantalla del plazo de los datos reventó así en
# local con 211 cuentas («URI too long»). 100 por trozo son ~3,7 KB: la mitad
# del techo, para que quepan los demás filtros de la misma petición.
TROZO_EN = 100


def trozos_en(valores) -> list[str]:
    """`in.(…)` partido en trozos que caben en una URL, sin repetidos y en orden.

    Para las listas que crecen con la plataforma (todas las cuentas, todos los
    salones): cada trozo es una petición y quien llama junta los resultados.
    """
    unicos = sorted({str(v) for v in valores if v})
    return [f"in.({','.join(unicos[i:i + TROZO_EN])})" for i in range(0, len(unicos), TROZO_EN)]


class PostgrestError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def _motivo(resp: httpx.Response) -> str:
    """Lo que PostgREST dice que ha pasado, sin los valores de la fila.

    PostgREST contesta un JSON con `code`, `message`, `details` y `hint`. Se
    registran tres de los cuatro:

      · `code` y `message` son el diagnóstico —«42703: column employees.rol does
        not exist»— y no llevan datos de nadie.
      · `hint` suele traer la solución literal («GRANT SELECT ON …»).
      · `details` SE DEJA FUERA a propósito: en una violación de unique es donde
        va «Key (salon_id, customer_phone)=(…, 34600111222) already exists», o
        sea el dato de una persona. Un log se retiene, se replica y lo lee gente
        que no debería ver eso, y esa clase de error —el duplicado de una
        reentrega de Meta— es de los más frecuentes que hay aquí.
    """
    try:
        cuerpo = resp.json()
    except ValueError:
        return (resp.text or "")[:MAX_DETALLE].strip() or "(sin cuerpo)"
    if not isinstance(cuerpo, dict):
        return json.dumps(cuerpo)[:MAX_DETALLE]
    partes = [str(cuerpo[c]) for c in ("code", "message", "hint") if cuerpo.get(c)]
    return " · ".join(partes)[:MAX_DETALLE] or "(sin motivo)"


class PostgrestClient:
    # El de httpx, dicho en voz alta en vez de heredado sin mirarlo: si PostgREST
    # tarda más de esto, la petición se corta. Para una API web es un techo
    # sano —una petición colgada es peor que una que falla— y por eso NO se
    # cambia. Se hace configurable solo para que los tests contra la base local,
    # que hacen decenas de altas completas seguidas, puedan ser más pacientes
    # sin tocar lo que corre en producción.
    TIMEOUT_POR_DEFECTO = 5.0

    def __init__(
        self,
        base_url: str,
        api_key: str,
        user_jwt: str | None = None,
        timeout: float | None = None,
    ):
        self._timeout = timeout or self.TIMEOUT_POR_DEFECTO
        self._base_url = base_url.rstrip("/") + "/rest/v1"
        self._headers = {
            "apikey": api_key,
            "Authorization": f"Bearer {user_jwt or api_key}",
            "Content-Type": "application/json",
        }


    async def _peticion(self, metodo: str, url: str, **kw) -> httpx.Response:
        """Una petición a PostgREST, por la conexión compartida del proceso.

        LAS CABECERAS VAN AQUÍ, EN LA LLAMADA. El cliente lo comparte todo el
        proceso, así que pegarle el `Authorization` de un salón sería dárselo a
        los demás. `app/db/conexiones.py` lo crea sin cabeceras, sin `auth` y
        sin memoria de galletas, y hay un guarda que lo comprueba.

        Y EL PLAZO TAMBIÉN VA AQUÍ: antes vivía en el cliente de usar y tirar.

        UN FALLO DE TRANSPORTE TAMBIÉN ES UN `PostgrestError`. Si Supabase tarda
        en aceptar la conexión, httpx lanza `ConnectTimeout`, que no era
        `PostgrestError` y por tanto NO lo recogía ninguno de los ~95 manejadores
        que contestan 502: se escapaba hasta arriba y el salón veía un 500 pelado
        sin explicación. Ahora sale por el mismo sitio que el resto.
        """
        cliente = cliente_compartido()
        try:
            return await cliente.request(
                metodo, url, headers=kw.pop("headers", None) or self._headers,
                timeout=self._timeout, **kw
            )
        except httpx.HTTPError as e:
            raise self._fallo_de_transporte(metodo, url, e) from e

    @staticmethod
    def _fallo_de_transporte(metodo: str, url: str, e: Exception) -> "PostgrestError":
        """Ni una respuesta: no se pudo hablar con la base. Se deja constancia.

        502 y no 500: la avería no es nuestra lógica, es la pasarela de abajo, y
        es lo mismo que contesta el resto del código cuando PostgREST falla.
        """
        tabla = url.rsplit("/", 1)[-1] or url
        logger.warning("PostgREST %s %s → sin respuesta · %s: %s", metodo, tabla, type(e).__name__, e)
        return PostgrestError(502, f"No se pudo contactar con la base de datos ({type(e).__name__}).")

    async def select(self, table: str, params: dict[str, str] | None = None) -> list[dict]:
        resp = await self._peticion("GET", f"{self._base_url}/{table}", params=params or {})
        self._raise_for_status(resp)
        return resp.json()

    async def count(self, table: str, params: dict[str, str] | None = None) -> int:
        """CUÁNTAS filas hay, sin traérselas. Y sin el tope de mil.

        POR QUÉ NO VALE `len(await select(...))`. PostgREST devuelve como mucho
        `db-max-rows` filas (mil en Supabase) y NO avisa de que ha recortado, así
        que contar lo devuelto da un número que parece exacto y no lo es. Ya nos
        mordió una vez en el aviso de impacto.

        Se pide `Range: 0-0` —una fila— y `Prefer: count=exact`, y el total sale
        de la cabecera `Content-Range` («0-0/137»). El recuento lo hace Postgres
        y no viaja ni una fila de más.
        """
        headers = {**self._headers, "Prefer": "count=exact", "Range-Unit": "items", "Range": "0-0"}
        resp = await self._peticion(
            "GET", f"{self._base_url}/{table}", headers=headers, params=params or {}
        )
        self._raise_for_status(resp)
        rango = resp.headers.get("content-range", "")
        total = rango.partition("/")[2]
        if not total.isdigit():
            # PostgREST contesta «*» cuando no puede contar. Devolver 0 diría
            # «no hay ninguna», que es una respuesta distinta y falsa.
            raise PostgrestError(
                resp.status_code,
                f"PostgREST no devolvió un recuento para {table}: {rango!r}",
            )
        return int(total)

    # Lo que PostgREST devuelve como mucho por petición (`db-max-rows`, mil en
    # Supabase). `select_todo` pide páginas de este tamaño.
    PAGINA = 1000

    async def select_todo(self, table: str, params: dict[str, str] | None = None) -> list[dict]:
        """TODAS las filas, pasen o no de mil. Para sumar, no para enseñar listas.

        `select` se queda en mil sin avisar, y una suma hecha sobre lo devuelto
        da un número que parece exacto y no lo es: Estadísticas dejaba de contar
        a partir de la cita mil. Aquí se pide por páginas con `Prefer:
        count=exact`, como en `count`, y el total de `Content-Range` dice cuántas
        filas hay que reunir. Si al final no salen todas, se LANZA: un total a
        medias es justo lo que no puede pasar en silencio.

        `order` es obligatorio: paginar sin un orden estable puede repetir o
        saltarse filas entre una página y la siguiente.
        """
        params = dict(params or {})
        if "order" not in params:
            raise ValueError(f"select_todo({table!r}) necesita un `order` estable para paginar")
        if "limit" in params or "offset" in params:
            raise ValueError(f"select_todo({table!r}) pagina sola: no admite limit ni offset")
        filas: list[dict] = []
        total: int | None = None
        while total is None or len(filas) < total:
            desde = len(filas)
            headers = {**self._headers, "Prefer": "count=exact", "Range-Unit": "items",
                       "Range": f"{desde}-{desde + self.PAGINA - 1}"}
            resp = await self._peticion("GET", f"{self._base_url}/{table}", headers=headers, params=params)
            self._raise_for_status(resp)
            rango = resp.headers.get("content-range", "")
            declarado = rango.partition("/")[2]
            if not declarado.isdigit():
                raise PostgrestError(resp.status_code, f"PostgREST no devolvió un recuento para {table}: {rango!r}")
            total = int(declarado)
            lote = resp.json()
            if not lote and len(filas) < total:
                raise PostgrestError(
                    502, f"{table}: PostgREST dice {total} filas y solo se reunieron {len(filas)}"
                )
            filas += lote
        return filas

    async def insert(self, table: str, body: dict, select: str | None = None) -> dict:
        # `select`: las columnas de la fila que se devuelve. Sin él PostgREST
        # devuelve `*`, y en una tabla con permisos por columna (`employees` y
        # `users` desde la 0047) eso es un 403 aunque el INSERT sea legítimo.
        headers = {**self._headers, "Prefer": "return=representation"}
        resp = await self._peticion(
            "POST",
            f"{self._base_url}/{table}",
            headers=headers,
            params={"select": select} if select else None,
            json=body,
        )
        self._raise_for_status(resp)
        rows = resp.json()
        return rows[0] if isinstance(rows, list) else rows

    async def insert_many(self, table: str, rows: list[dict]) -> None:
        # PostgREST inserts a JSON array as a single statement in a single
        # transaction: either every row lands or none does. return=minimal
        # avoids serializing the inserted rows back over the wire.
        headers = {**self._headers, "Prefer": "return=minimal"}
        resp = await self._peticion("POST", f"{self._base_url}/{table}", headers=headers, json=rows)
        self._raise_for_status(resp)

    async def upsert_many(self, table: str, rows: list[dict], on_conflict: str) -> None:
        # Inserta o actualiza en una sola sentencia contra la clave única de
        # `on_conflict`. Se usa para guardar conjuntos que van de una pieza (la
        # semana de horario): con delete + insert habría un instante en el que
        # el salón se queda sin horario, y si el insert fallara se quedaría así.
        headers = {**self._headers, "Prefer": "resolution=merge-duplicates,return=minimal"}
        resp = await self._peticion(
            "POST",
            f"{self._base_url}/{table}",
            headers=headers,
            params={"on_conflict": on_conflict},
            json=rows,
        )
        self._raise_for_status(resp)

    async def update(
        self, table: str, params: dict[str, str], body: dict, select: str | None = None
    ) -> list[dict]:
        # `select`, igual que en `insert`: sin él la fila vuelve con `*`.
        headers = {**self._headers, "Prefer": "return=representation"}
        resp = await self._peticion(
            "PATCH",
            f"{self._base_url}/{table}",
            headers=headers,
            params={**params, "select": select} if select else params,
            json=body,
        )
        self._raise_for_status(resp)
        return resp.json()

    async def rpc(self, function: str, body: dict) -> list[dict]:
        # Una función de la base (`POST /rpc/<nombre>`). Para lo que tiene que ser
        # atómico en Postgres y no cabe en un select + update desde aquí, como
        # contar un intento de PIN antes de comprobarlo (0046).
        resp = await self._peticion("POST", f"{self._base_url}/rpc/{function}", json=body)
        self._raise_for_status(resp)
        datos = resp.json()
        return datos if isinstance(datos, list) else [datos]

    async def delete(self, table: str, params: dict[str, str]) -> None:
        resp = await self._peticion("DELETE", f"{self._base_url}/{table}", params=params)
        self._raise_for_status(resp)

    @staticmethod
    def _raise_for_status(resp: httpx.Response) -> None:
        """Convierte un error de PostgREST en excepción, DEJANDO CONSTANCIA.

        ES UNO DE LOS DOS SITIOS DONDE NACE UN `PostgrestError` —el otro es
        `_fallo_de_transporte`, para cuando no llega a haber respuesta— y por eso
        el registro va aquí y no en cada `except`. Lo que se ganó con esto:

          · Cubre los ~95 manejadores que responden 502 sin registrar nada Y los
            ~79 que se lo tragan en silencio. Un `except PostgrestError: return`
            deja de ser un agujero negro.
          · Cubre el código que todavía no existe. Nadie tiene que acordarse de
            nada al escribir el siguiente endpoint, que es exactamente de lo que
            no se acordó nadie durante cuatro migraciones.
          · Registra en el momento en que la causa EXISTE, con la petición
            delante. Un `except` de más arriba ya no la tiene: solo tiene un
            texto suelto y un endpoint que devolver.

        El que motivó todo esto —«column employees.rol does not exist»— salía de
        aquí y se perdía dos marcos más arriba, dejando un 502 mudo que parecía
        un cuelgue.

        NIVEL WARNING y no ERROR: no todos los 4xx son averías. El 409 de un
        unique es la idempotencia del webhook funcionando, y quien decide si esto
        importa es quien lo captura, no este método. Lo que este método garantiza
        es que el motivo esté escrito.

        QUÉ SE REGISTRA Y QUÉ NO: método, tabla y estado, más el motivo de
        PostgREST ya filtrado por `_motivo()`. NO se registran ni la query (sus
        filtros llevan teléfonos, correos e ids de cliente) ni el cuerpo enviado
        (lleva, entre otras cosas, hashes de PIN).

        UN 3xx TAMBIÉN ES UN FALLO. PostgREST contesta 300 Multiple Choices
        cuando un embed es ambiguo (PGRST201: dos caminos entre las mismas dos
        tablas), y con el corte en 400 ese cuerpo de error se devolvía COMO SI
        FUERAN FILAS. El endpoint recibía una cadena donde esperaba una lista y
        reventaba con «'str' object has no attribute 'get'» tres marcos más
        abajo, sin una sola línea de registro que dijera que la consulta ni
        siquiera se había ejecutado. Aquí no hay ninguna respuesta 3xx legítima:
        no se siguen redirecciones y no se usan peticiones condicionales.
        """
        if 200 <= resp.status_code < 300:
            return
        peticion = resp.request
        logger.warning(
            "PostgREST %s %s → %s · %s",
            peticion.method,
            peticion.url.path.rsplit("/", 1)[-1] or peticion.url.path,
            resp.status_code,
            _motivo(resp),
        )
        raise PostgrestError(resp.status_code, resp.text)
