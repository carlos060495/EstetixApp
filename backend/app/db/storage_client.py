"""Thin async wrapper over Supabase Storage's REST endpoint.

El frontend NUNCA habla con Storage: todo objeto entra y sale por el
backend, que usa la secret key y construye las rutas SIEMPRE a partir del
salon_id del JWT verificado (DIRECTRICES_PROYECTO.md §4.2). El bucket
'salon-assets' es privado: para leer se generan signed URLs de TTL corto
en el momento de la lectura (la BD guarda la ruta, nunca la URL firmada).
"""

from __future__ import annotations

import logging

import httpx

from app.db.conexiones import cliente_compartido

logger = logging.getLogger("estetix.storage")

# Lo justo del cuerpo de error para diagnosticar sin volcar la respuesta entera.
MAX_DETALLE = 400


class StorageError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


class StorageClient:
    def __init__(self, base_url: str, secret_key: str):
        self._base_url = base_url.rstrip("/") + "/storage/v1"
        self._headers = {
            "apikey": secret_key,
            "Authorization": f"Bearer {secret_key}",
        }

    async def _peticion(self, metodo: str, url: str, **kw) -> httpx.Response:
        """Una llamada a Storage, por la conexión compartida del proceso.

        Las cabeceras van en la llamada, no en el cliente: ver `conexiones.py`.

        Y UN FALLO DE TRANSPORTE ES UN `StorageError`, por el mismo motivo que
        en PostgREST: si Supabase tarda en aceptar la conexión, httpx lanza
        `ConnectTimeout`, que no lo recoge ningún `except StorageError` y llegaba
        arriba como un 500 pelado.
        """
        cliente = cliente_compartido()
        try:
            return await cliente.request(metodo, url, **kw)
        except httpx.HTTPError as e:
            logger.warning("Storage %s → sin respuesta · %s: %s", metodo, type(e).__name__, e)
            raise StorageError(
                502, f"No se pudo contactar con el almacenamiento ({type(e).__name__})."
            ) from e

    async def upload(self, bucket: str, path: str, content: bytes, content_type: str) -> None:
        """Sube (upsert) un objeto; sobreescribe si ya existe."""
        headers = {
            **self._headers,
            "Content-Type": content_type,
            "x-upsert": "true",
        }
        resp = await self._peticion(
            "POST", f"{self._base_url}/object/{bucket}/{path}", headers=headers, content=content
        )
        self._raise_for_status(resp)

    async def delete(self, bucket: str, path: str) -> None:
        resp = await self._peticion(
            "DELETE", f"{self._base_url}/object/{bucket}/{path}", headers=self._headers
        )
        # 404 = ya no existe: para un borrado es un resultado aceptable.
        if resp.status_code != 404:
            self._raise_for_status(resp)

    async def create_signed_url(self, bucket: str, path: str, expires_in: int) -> str:
        """Devuelve una URL firmada absoluta con TTL de `expires_in` segundos."""
        resp = await self._peticion(
            "POST",
            f"{self._base_url}/object/sign/{bucket}/{path}",
            headers={**self._headers, "Content-Type": "application/json"},
            json={"expiresIn": expires_in},
        )
        self._raise_for_status(resp)
        signed_path = resp.json()["signedURL"].lstrip("/")
        return f"{self._base_url}/{signed_path}"

    @staticmethod
    def _raise_for_status(resp: httpx.Response) -> None:
        """Convierte un error de Storage en excepción, DEJANDO CONSTANCIA.

        Único sitio donde nace un `StorageError`, así que el registro va aquí y
        no en los `except` de `salon.py`: cubre los que hay y los que vengan, y
        deja escrito el motivo en el momento en que existe. Antes, «no se pudo
        subir el logo» era todo lo que quedaba de un fallo de Storage.

        La RUTA sí se registra —es `<salon_id>/logo.png`, no lleva nada de
        ninguna persona— y sirve para saber a qué salón le pasó.
        """
        if resp.status_code < 400:
            return
        logger.warning(
            "Storage %s %s → %s · %s",
            resp.request.method,
            resp.request.url.path,
            resp.status_code,
            (resp.text or "")[:MAX_DETALLE].strip() or "(sin cuerpo)",
        )
        raise StorageError(resp.status_code, resp.text)
