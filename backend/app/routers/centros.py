"""Los centros de una cuenta: cuáles son los tuyos y cómo se abre el segundo.

EL LISTADO NO LO FILTRA ESTE FICHERO, lo filtra la RLS. `salons` tiene
`salon_id = any(salones_visibles())` desde la Fase A, así que un `select` sin
`where` con el token del inquilino devuelve EXACTAMENTE los centros a los que
esa persona puede entrar: los de su cuenta si es administradora, aquellos donde
tenga ficha si no. El selector no puede enseñar de más porque no hay ningún
filtro mío que quitar por error — no hay ninguno.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status

from app.core import publico
from app.billing.el_que_sigue import (
    DIAS_ENTRE_CAMBIOS,
    centros_de,
    en_pausa_si_se_queda,
    marcado,
    marcar_el_que_sigue,
    se_puede_cambiar_desde,
)
from app.billing.lo_que_incluye_el_plan import lo_que_incluye
from app.billing.plans import centros_permitidos
from app.billing.rastro import apuntar_decision, usuario_de_la_sesion
from app.core import zona as zonas
from app.core.aviso_centro_en_pausa import fecha_legible
from app.core.citas_en_pausa import TOPE_QUE_SE_PINTA, pendientes_en_los_centros_en_pausa
from app.core.cuenta import leer_cuenta
from app.core.telefonos import TelefonoNoValido, region_del_salon, revisar_telefono
from app.core.security import (
    admite_cuenta_cortada,
    CurrentUser,
    get_current_user,
    get_postgrest_client,
    get_service_postgrest_client,
)
from app.db.postgrest_client import PostgrestClient, PostgrestError
from app.schemas.centros import (
    CentroOut,
    CentrosOut,
    CitasEnPausaOut,
    CrearCentroIn,
    ElegirCentroIn,
    ElegirCentroOut,
)

router = APIRouter(prefix="/salon/centros", tags=["centros"], dependencies=[publico.ADMINISTRADORA])
logger = logging.getLogger("estetix.centros")

# `direccion` y `telefono` NO se devuelven: solo se miran aquí para decir si
# faltan. Pedirlos cuesta cero —es el mismo `select`— y devolverlos sería
# engordar una respuesta que la barra de centros pide en cada carga.
_SELECT = "id,nombre_comercial,slug,direccion,telefono,account_id,sigue_al_bajar"


async def _region_de_la_cuenta(db: PostgrestClient, salon_id: str) -> str:
    """El país de la cuenta, para poder leer un teléfono escrito «600 11 22 33».

    Sale del centro desde el que se está creando el nuevo: un dueño que abre su
    segundo local lo abre en su país, y si algún día no fuera así, el número
    escrito con prefijo internacional se lee igual.
    """
    filas = await db.select(
        "salons", {"select": "telefono", "id": f"eq.{salon_id}", "limit": "1"}
    )
    return region_del_salon(filas[0].get("telefono") if filas else None)


def _slug(nombre: str) -> str:
    """Un slug legible a partir del nombre. La unicidad la impone la base."""
    limpio = re.sub(r"[^a-z0-9]+", "-", nombre.lower().strip()).strip("-")
    return limpio[:40] or "centro"


@router.get("", response_model=CentrosOut)
@admite_cuenta_cortada("agenda")
async def listar_centros(
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
    service_db: PostgrestClient = Depends(get_service_postgrest_client),
) -> CentrosOut:
    """Los centros en los que esta persona puede trabajar, y en cuál está.

    CON UN SOLO CENTRO ESTO DEVUELVE UNO y la pantalla no enseña selector: la
    equivalencia se decide aquí, con datos, no con una bandera de configuración.
    """
    try:
        filas = await db.select("salons", {"select": _SELECT, "order": "nombre_comercial.asc"})
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudieron leer tus centros."
        ) from e

    # LA MARCA NO ES UN FILTRO. La consulta de arriba sigue sin `where` y lo que
    # vuelve es lo que `salones_visibles()` deja pasar, tal cual; aquí solo se
    # anota cuáles el plan deja fuera. Ese reparto viene ya resuelto en la
    # sesión, así que esto no cuesta una consulta más.
    en_pausa = set(user.centros_en_pausa)
    # CON CENTROS EN PAUSA, hasta cuándo no puede volver a cambiar el que se
    # queda (0088). Solo entonces: es una consulta más al registro.
    cambio_desde = None
    if en_pausa and filas:
        try:
            desde = await se_puede_cambiar_desde(
                service_db, filas[0]["account_id"], datetime.now(timezone.utc)
            )
            cambio_desde = desde.isoformat() if desde else None
        except PostgrestError:
            # Es un aviso: el límite lo comprueba el cambio, no esta lista.
            logger.exception("No se pudo leer cuándo puede volver a cambiar de centro.")
    return CentrosOut(
        activo=user.salon_id,
        centros=[
            CentroOut(
                id=f["id"],
                nombre_comercial=f["nombre_comercial"],
                slug=f.get("slug"),
                en_pausa=str(f["id"]) in en_pausa,
                falta_contacto=not (f.get("direccion") or "").strip()
                or not (f.get("telefono") or "").strip(),
            )
            for f in filas
        ],
        centro_rechazado=user.centro_rechazado,
        centro_en_pausa=user.centro_en_pausa,
        sigue_elegido=marcado(filas) is not None,
        cambio_de_centro_desde=cambio_desde,
    )


@router.get("/en-pausa/citas", response_model=CitasEnPausaOut)
async def citas_de_los_centros_en_pausa(
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> CitasEnPausaOut:
    """Lo que sigue pendiente en los centros que el plan ha dejado fuera.

    VA EN UN ENDPOINT APARTE Y NO DENTRO DEL LISTADO porque el listado lo pide
    la barra de centros en cada carga de la aplicación y esto son tres consultas
    más. Casi ninguna cuenta tiene centros en pausa; las que no los tienen no
    pagan nada por esto.

    Sin centros en pausa contesta cero sin tocar la base, y quien llama no tiene
    que saber si preguntar o no.
    """
    try:
        pendientes = await pendientes_en_los_centros_en_pausa(db, user.centros_en_pausa)
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudieron leer las citas de los centros en pausa.",
        ) from e

    return CitasEnPausaOut(
        total=pendientes.total,
        ultima=pendientes.ultima,
        citas=pendientes.citas,
        se_pintan_como_mucho=TOPE_QUE_SE_PINTA,
    )


@router.put("/activo", response_model=CentrosOut)
async def recordar_centro(
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
    service_db: PostgrestClient = Depends(get_service_postgrest_client),
) -> CentrosOut:
    """Recuerda el centro en el que esta persona está trabajando.

    NO RECIBE CUERPO: el centro que se guarda es el que la petición acaba de
    usar, o sea el de la cabecera YA COMPROBADA. Aceptarlo en el cuerpo sería
    abrir un segundo camino que no pasa por esa comprobación, y de nada sirve
    validar la cabecera si al lado hay una puerta sin validar.

    Se guarda en `users.ultimo_centro_id` y no en el navegador porque «por
    usuario» no es «por navegador»: quien trabaja desde el móvil y desde el
    mostrador abre los dos donde lo dejó.
    """
    try:
        # Por `auth_user_id`: una identidad tiene una ficha. NO por `salon_id`,
        # que ahora es el centro ACTIVO y no el de su ficha — filtrar por él no
        # encontraría nada en cuanto alguien cambie de centro, que es
        # exactamente cuando esto se llama.
        await service_db.update(
            "users",
            {"auth_user_id": f"eq.{user.sub}"},
            {"ultimo_centro_id": user.salon_id},
        )
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo recordar el centro.",
        ) from e
    return await listar_centros(user=user, db=db, service_db=service_db)


@router.put("/el-que-sigue", response_model=ElegirCentroOut)
async def elegir_el_que_sigue(
    payload: ElegirCentroIn,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
    service_db: PostgrestClient = Depends(get_service_postgrest_client),
) -> ElegirCentroOut:
    """Cambia qué centro se queda activo, sin pasar por Stripe (0088).

    UNA VEZ CADA 30 DÍAS, comprobado aquí y no solo en la pantalla (ver
    `billing/el_que_sigue.py`). Si lo intenta antes, se le dice QUÉ DÍA podrá,
    no un error seco. Confirmar el que ya se queda no es cambiar: no mueve
    ningún centro, no gasta los 30 días y no se apunta.
    """
    cuenta = await leer_cuenta(db, user.salon_id, "plan")
    if not cuenta:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Salón no encontrado.")
    account_id, plan = cuenta["account_id"], cuenta.get("plan")
    elegido = payload.centro_que_sigue
    ahora = datetime.now(timezone.utc)
    try:
        centros = await centros_de(service_db, account_id)
        if elegido not in {str(c["id"]) for c in centros}:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ese centro no es de tu cuenta.")
        if len(centros) <= centros_permitidos(plan):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "codigo": "nada_que_elegir",
                    "mensaje": "Tu plan incluye todos tus centros: no hay que elegir ninguno.",
                },
            )

        desde = await se_puede_cambiar_desde(service_db, account_id, ahora)
        if elegido in lo_que_incluye(plan, centros).centros_activos:
            if not marcado(centros):
                await marcar_el_que_sigue(service_db, account_id, elegido)
            return ElegirCentroOut(
                centro_que_sigue=elegido, se_puede_cambiar_desde=desde.isoformat() if desde else None
            )

        if desde:
            z = await zonas.zona_del_centro(db, user.salon_id)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "codigo": "cambio_de_centro_reciente",
                    "mensaje": (
                        "El centro que se queda solo se puede cambiar una vez cada 30 días. "
                        f"Podrás volver a cambiarlo el {fecha_legible(desde.isoformat(), z)}."
                    ),
                    "se_puede_cambiar_desde": desde.isoformat(),
                },
            )

        marca_de_antes = marcado(centros)
        await marcar_el_que_sigue(service_db, account_id, elegido)
        nombre = next((c.get("nombre_comercial") for c in centros if str(c["id"]) == elegido), None)
        try:
            # EL REGISTRO ES EL CONTADOR DE LOS 30 DÍAS: si no se escribe, el
            # cambio no vale. Por eso lanza (`desde_el_webhook`) y se deshace.
            await apuntar_decision(
                service_db,
                account_id=account_id,
                salon_id=elegido,
                suceso="elige_centro",
                origen="app",
                usuario_id=await usuario_de_la_sesion(service_db, user.sub),
                motivo=f"Se queda {nombre}." if nombre else None,
                plan_antes=plan,
                plan_despues=plan,
                centros_en_pausa=en_pausa_si_se_queda(plan, centros, elegido),
                desde_el_webhook=True,
            )
        except PostgrestError:
            await marcar_el_que_sigue(service_db, account_id, marca_de_antes)
            raise
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudo cambiar el centro que se queda."
        ) from e

    logger.info("La cuenta %s deja activo el centro %s.", account_id, elegido)
    return ElegirCentroOut(
        centro_que_sigue=elegido,
        se_puede_cambiar_desde=(ahora + timedelta(days=DIAS_ENTRE_CAMBIOS)).isoformat(),
    )


@router.post("", response_model=CentroOut, status_code=status.HTTP_201_CREATED)
async def crear_centro(
    payload: CrearCentroIn,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
    service_db: PostgrestClient = Depends(get_service_postgrest_client),
) -> CentroOut:
    """Abre un segundo centro en la cuenta. Solo Elite, y solo dos.

    EL LÍMITE SE CUENTA SOBRE LA CUENTA, no sobre lo que ve quien pregunta: una
    administradora ve todos los suyos, pero contar lo que ella ve sería contar
    permisos en vez de centros. Se cuenta con la clave de servicio.
    """
    cuenta = await leer_cuenta(db, user.salon_id, "plan")
    if not cuenta:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Salón no encontrado.")
    account_id = cuenta["account_id"]
    tope = centros_permitidos(cuenta.get("plan"))

    try:
        existentes = await service_db.select(
            "salons", {"select": "id", "account_id": f"eq.{account_id}"}
        )
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo comprobar cuántos centros tienes.",
        ) from e

    if len(existentes) >= tope:
        # 409 y no 402: no es «te falta pagar algo», es «tu plan ya está al
        # tope». El mensaje dice el número porque «has alcanzado el límite» sin
        # cifra no le dice a nadie qué hacer.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Tu plan permite {tope} "
                f"{'centro' if tope == 1 else 'centros'} y ya "
                f"{'tienes uno' if tope == 1 else f'tienes {len(existentes)}'}. "
                "Cambia a Elite para abrir un segundo local."
            ),
        )

    # EL TELÉFONO, MARCABLE DE VERDAD. Se valida aquí y no en el esquema por el
    # mismo motivo que en el perfil del salón: hace falta saber de qué país es la
    # cuenta, y eso está en la base. Se toma la región de un centro que ya
    # existe — el que se está creando todavía no tiene teléfono.
    try:
        telefono = revisar_telefono(payload.telefono, await _region_de_la_cuenta(db, user.salon_id))
    except TelefonoNoValido as e:
        # 422 con CÓDIGO además del motivo, para que el formulario ponga el
        # mensaje debajo del campo y no en el aviso de la esquina, que en un
        # móvil queda lejos de lo que hay que arreglar.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"codigo": "telefono_no_valido", "mensaje": str(e)},
        ) from e

    nuevo = {
        "nombre_comercial": payload.nombre_comercial,
        "slug": _slug(payload.nombre_comercial),
        "account_id": account_id,
        "direccion": payload.direccion.strip(),
        "telefono": telefono,
    }
    try:
        creado = await service_db.insert("salons", nuevo)
    except PostgrestError as e:
        if e.status_code == 409:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Ya hay un centro con una dirección web parecida. Cámbiale el nombre.",
            ) from e
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudo crear el centro."
        ) from e

    if payload.copiar_servicios_de:
        await _copiar_servicios(
            service_db,
            origen=payload.copiar_servicios_de,
            destino=creado["id"],
            account_id=account_id,
        )

    logger.info("Centro nuevo %s en la cuenta %s.", creado["id"], account_id)
    return CentroOut(**{k: creado.get(k) for k in ("id", "nombre_comercial", "slug")})


async def _copiar_servicios(
    db: PostgrestClient, *, origen: str, destino: str, account_id: str
) -> None:
    """Copia el catálogo UNA VEZ. No sincroniza, y eso es la decisión.

    A partir de aquí cada centro lleva su catálogo y sus precios: el del barrio
    caro sube y el otro no, y nadie tiene que acordarse de desvincular nada. Lo
    contrario —mantenerlos atados— es lo que obliga a inventar excepciones el
    primer día que dos locales cobran distinto.

    El centro de origen se comprueba contra la CUENTA: copiar desde el catálogo
    de un salón ajeno sería llevarse los precios de otro.
    """
    try:
        del_mismo_dueno = await db.select(
            "salons", {"select": "id", "id": f"eq.{origen}", "account_id": f"eq.{account_id}", "limit": "1"}
        )
        if not del_mismo_dueno:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Ese centro no es de tu cuenta.",
            )

        # Las categorías van primero: un servicio apunta a la suya, y copiar los
        # servicios sin ellas dejaría el catálogo nuevo sin ordenar.
        categorias = await db.select(
            "service_categories", {"select": "id,nombre,tipo", "salon_id": f"eq.{origen}"}
        )
        equivalencia: dict[str, str] = {}
        for c in categorias:
            nueva = await db.insert(
                "service_categories",
                {"salon_id": destino, "nombre": c["nombre"], "tipo": c["tipo"]},
            )
            equivalencia[c["id"]] = nueva["id"]

        servicios = await db.select(
            "services",
            {"select": "nombre,precio,duracion_min,visible_online,category_id",
             "salon_id": f"eq.{origen}"},
        )
        if servicios:
            await db.insert_many(
                "services",
                [
                    {
                        "salon_id": destino,
                        "nombre": s["nombre"],
                        "precio": s["precio"],
                        "duracion_min": s["duracion_min"],
                        "visible_online": s["visible_online"],
                        "category_id": equivalencia.get(s.get("category_id")),
                    }
                    for s in servicios
                ],
            )
        logger.info(
            "Copiados %s servicios y %s categorías de %s a %s.",
            len(servicios), len(categorias), origen, destino,
        )
    except PostgrestError as e:
        # EL CENTRO YA ESTÁ CREADO y no se deshace: quedarse sin catálogo es
        # molesto y se arregla a mano; perder el centro recién creado, no. Se
        # avisa con un 502 para que la pantalla lo diga.
        logger.exception("No se pudieron copiar los servicios al centro nuevo")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "El centro se ha creado, pero no se pudieron copiar los servicios. "
                "Añádelos desde su catálogo."
            ),
        ) from e
