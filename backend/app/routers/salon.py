from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status

from app.core import demo, publico
from app.core.errores_visibles import visible
from app.core.images import MAX_LOGO_BYTES, InvalidImageError, reencode_logo
from app.core.security import (
    CurrentUser,
    get_current_user,
    get_postgrest_client,
    get_storage_client,
)
from app.db.postgrest_client import PostgrestClient, PostgrestError
from app.db.storage_client import StorageClient, StorageError
from app.core.cuenta import con_la_cuenta_dentro, id_de_cuenta
from app.core.telefonos import (
    TelefonoNoValido,
    region_del_salon,
    revisar_telefono,
    telefono_legible,
)
from app.schemas.salon import SalonLogoOut, SalonProfileOut, SalonProfileUpdate

router = APIRouter(prefix="/salon", tags=["salon"], dependencies=[publico.ADMINISTRADORA])

LOGO_BUCKET = "salon-assets"
LOGO_SIGNED_URL_TTL = 3600  # 1 h: se firma en cada lectura, nunca se persiste


async def sign_logo_url(storage: StorageClient, raw: str | None) -> str | None:
    """Ruta de objeto → signed URL fresca; URLs http legadas pasan tal cual.

    Si el objeto ya no existe en el bucket no se rompe la lectura del
    perfil: se devuelve null y el frontend cae al avatar de iniciales.
    """
    if not raw:
        return None
    if raw.startswith("http"):
        return raw
    try:
        return await storage.create_signed_url(LOGO_BUCKET, raw, LOGO_SIGNED_URL_TTL)
    except StorageError:
        return None


# Lo que el formulario de Ajustes edita y sigue siendo del centro, frente a lo
# que desde la 0040 es de la cuenta. La pantalla no cambia: sigue viendo una
# ficha sola.
_CAMPOS_DE_LA_CUENTA = {"cif", "accent_color"}


async def _fetch_salon(db: PostgrestClient, salon_id: str) -> dict:
    rows = await db.select(
        "salons",
        {
            "select": (
                "nombre_comercial,direccion,telefono,email,account_id,zona_horaria,"
                "accounts(cif,plan,accent_color,logo_url)"
            ),
            "id": f"eq.{salon_id}",
            "limit": "1",
        },
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Salón no encontrado")
    return con_la_cuenta_dentro(rows[0])


@router.get("/profile", response_model=SalonProfileOut)
async def get_salon_profile(
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
    storage: StorageClient = Depends(get_storage_client),
) -> dict:
    try:
        salon = await _fetch_salon(db, user.salon_id)
    except PostgrestError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudo leer el perfil del salón") from e
    salon["logo_url"] = await sign_logo_url(storage, salon.get("logo_url"))
    salon["telefono_aviso"] = await _aviso_del_telefono(db, user.salon_id, salon.get("telefono"))
    salon["telefono_legible"] = await _legible(db, user.salon_id, salon.get("telefono"))
    return salon


async def _region(db: PostgrestClient, salon_id: str, telefono: str | None) -> str:
    """De qué país es este salón, para leer un teléfono escrito en local.

    Se le pregunta a `region_del_salon`, que es el punto único, y se le da la
    mejor pista que haya: el número de WhatsApp del salón si lo tiene conectado
    —lo trae Meta en internacional, así que su prefijo es fiable— y si no, el
    teléfono que ya estuviera guardado.

    UN NÚMERO ESCRITO EN INTERNACIONAL NO NECESITA NADA DE ESTO: trae su
    prefijo. La región solo decide cómo leer un «944 12 34 56», que es como lo
    teclea la gente de su propio país.
    """
    try:
        filas = await db.select(
            "salon_whatsapp_config",
            {"select": "display_phone_number", "salon_id": f"eq.{salon_id}",
             "display_phone_number": "not.is.null", "limit": "1"},
        )
    except PostgrestError:
        filas = []
    de_whatsapp = (filas[0].get("display_phone_number") if filas else None) or None
    return region_del_salon(de_whatsapp or telefono)


async def _legible(db: PostgrestClient, salon_id: str, telefono: str | None) -> str | None:
    """El número guardado, escrito para leerlo. `None` si no hay o no vale."""
    if not (telefono or "").strip():
        return None
    return telefono_legible(telefono, await _region(db, salon_id, telefono))


async def _aviso_del_telefono(
    db: PostgrestClient, salon_id: str, telefono: str | None
) -> str | None:
    """Lo que hay que decirle al salón sobre el teléfono que YA tiene guardado.

    Los que se guardaron antes de que esto se validara siguen ahí, y uno de
    ellos tenía un dígito de más: su botón «Llamar al salón» llevaba muerto en
    todos los recordatorios. Enterarse por un cliente que no pudo llamar es la
    peor forma de enterarse; enterarse al abrir Ajustes es la barata.
    """
    if not (telefono or "").strip():
        return None
    try:
        revisar_telefono(telefono, await _region(db, salon_id, telefono))
    except TelefonoNoValido as e:
        return str(e)
    return None


@router.patch("/profile", response_model=SalonProfileOut)
async def update_salon_profile(
    payload: SalonProfileUpdate,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
    storage: StorageClient = Depends(get_storage_client),
) -> dict:
    body = payload.model_dump(exclude_unset=True)
    if not body:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nada que actualizar")

    if "telefono" in body:
        # SE VALIDA AQUÍ Y NO EN EL ESQUEMA porque hace falta saber de qué país
        # es el salón, y eso está en la base de datos: un esquema de Pydantic no
        # la tiene delante. El resto de campos sí se validan allí.
        try:
            body["telefono"] = revisar_telefono(
                body["telefono"], await _region(db, user.salon_id, None)
            )
        except TelefonoNoValido as e:
            # 422 con el motivo Y con un código: «campo inválido» no le dice a
            # nadie qué corregir, y el código deja que la pantalla ponga el
            # mensaje DEBAJO DEL CAMPO en vez de en el aviso general de la
            # esquina — que en un móvil queda lejos de lo que hay que arreglar.
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"codigo": "telefono_no_valido", "mensaje": str(e)},
            ) from e

    # DOS TABLAS, UNA PANTALLA. El nombre y la dirección son del centro; el CIF
    # y el color son de la cuenta, porque un dueño con dos locales tiene un CIF
    # y una marca, no dos. Se escribe cada cosa donde vive y se devuelve la
    # ficha entera, que es lo que la pantalla espera.
    de_la_cuenta = {k: v for k, v in body.items() if k in _CAMPOS_DE_LA_CUENTA}
    del_centro = {k: v for k, v in body.items() if k not in _CAMPOS_DE_LA_CUENTA}

    # CADA MITAD DICE LO SUYO. La pantalla tiene dos botones —«Guardar cambios»
    # y «Guardar datos fiscales»— y contestarle «no se ha podido guardar el
    # perfil del salón» a quien acaba de pulsar el segundo no le dice qué se
    # quedó sin guardar.
    if de_la_cuenta:
        try:
            account_id = await id_de_cuenta(db, user.salon_id)
            if not account_id:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Salón no encontrado"
                )
            await db.update("accounts", {"id": f"eq.{account_id}"}, de_la_cuenta)
        except PostgrestError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=visible("guardar los datos fiscales"),
            ) from e
    if del_centro:
        try:
            rows = await db.update("salons", {"id": f"eq.{user.salon_id}"}, del_centro)
        except PostgrestError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=visible("guardar los datos del local"),
            ) from e
        if not rows:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Salón no encontrado"
            )

    # Se relee en vez de devolver lo que dijo el update: son dos escrituras y la
    # respuesta tiene que enseñar el resultado de las dos.
    salon = await _fetch_salon(db, user.salon_id)
    salon["logo_url"] = await sign_logo_url(storage, salon.get("logo_url"))
    # Acaba de validarse, así que no hay aviso que dar. Se manda explícito para
    # que la pantalla borre el que tuviera puesto sin tener que recargar.
    salon["telefono_aviso"] = None
    salon["telefono_legible"] = await _legible(db, user.salon_id, salon.get("telefono"))
    return salon


@router.post(
    "/logo",
    response_model=SalonLogoOut,
    status_code=status.HTTP_201_CREATED,
    summary="Sube (o reemplaza) el logo del salón",
    description=(
        "Multipart admin-only. Valida tamaño ≤ 2 MB y tipo real por magic bytes "
        "(PNG/JPEG/WebP; SVG no permitido), re-encodea la imagen con Pillow "
        "(elimina EXIF/metadata, cap 512×512) y la sube al bucket privado "
        "'salon-assets' en {salon_id}/logo.<ext> — ruta derivada SIEMPRE del "
        "JWT, nunca del nombre de archivo del cliente. Devuelve una signed URL "
        "de ~1 h para pintar el logo al instante."
    ),
)
async def upload_salon_logo(
    file: UploadFile = File(...),
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
    storage: StorageClient = Depends(get_storage_client),
) -> dict:
    # UNA DEMO NO SUBE FICHEROS (0080): el barrido no podría llevárselos.
    demo.no_en_una_demo(user.es_demo, "ficheros")
    # +1 para detectar el exceso sin cargar archivos arbitrariamente grandes.
    data = await file.read(MAX_LOGO_BYTES + 1)
    if len(data) > MAX_LOGO_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="El archivo supera el máximo de 2 MB.",
        )
    if not data:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="El archivo está vacío."
        )

    try:
        content, ext, mime = reencode_logo(data)
    except InvalidImageError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e

    # Ruta construida desde el salon_id del JWT verificado (anti path-traversal:
    # el nombre de archivo del cliente no participa en la ruta).
    path = f"{user.salon_id}/logo.{ext}"

    try:
        salon = await _fetch_salon(db, user.salon_id)
        await storage.upload(LOGO_BUCKET, path, content, mime)
        await db.update(
            "accounts", {"id": f"eq.{salon['account_id']}"}, {"logo_url": path}
        )
    except StorageError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudo subir el logo."
        ) from e
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudo guardar el logo."
        ) from e

    # Si cambió la extensión, retira el objeto anterior (best-effort: un
    # huérfano en el bucket no debe hacer fallar una subida correcta).
    old_path = salon.get("logo_url")
    if old_path and old_path != path and old_path.startswith(f"{user.salon_id}/"):
        try:
            await storage.delete(LOGO_BUCKET, old_path)
        except StorageError:
            pass

    signed = await storage.create_signed_url(LOGO_BUCKET, path, LOGO_SIGNED_URL_TTL)
    return {"logo_url": signed}


@router.delete(
    "/logo",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Elimina el logo del salón",
    description=(
        "Admin-only. Borra el objeto del bucket 'salon-assets' y pone "
        "accounts.logo_url a null. El tenant sale del salon_id del JWT."
    ),
)
async def delete_salon_logo(
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
    storage: StorageClient = Depends(get_storage_client),
) -> Response:
    try:
        salon = await _fetch_salon(db, user.salon_id)
        path = salon.get("logo_url")
        # Solo se borran objetos del prefijo del propio salón; los valores
        # http legados no apuntan al bucket y se limpian solo en BD.
        if path and path.startswith(f"{user.salon_id}/"):
            await storage.delete(LOGO_BUCKET, path)
        await db.update(
            "accounts", {"id": f"eq.{salon['account_id']}"}, {"logo_url": None}
        )
    except StorageError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudo eliminar el logo."
        ) from e
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudo eliminar el logo."
        ) from e
    return Response(status_code=status.HTTP_204_NO_CONTENT)
