"""La agenda del salón.

LAS REGLAS DE NEGOCIO NO ESTÁN AQUÍ: viven en `app/core/citas.py`, porque el bot
de WhatsApp crea citas sin pasar por estos endpoints y las reglas no pueden
depender de por qué puerta se entre. Este módulo solo transporta: lee el token,
llama a las reglas y traduce `CitaNoValida` a la respuesta HTTP que le
corresponde.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core import publico
from app.core import zona as zonas
from app.core.errores_visibles import visible
from app.core.citas import (
    MENSAJE_CARRERA_PERDIDA,
    MENSAJE_OCUPADA_EN_OTRO_CENTRO_SIN_NOMBRE,
    CitaNoValida,
    asegurar_dentro_del_horario,
    asegurar_empleado_realiza_servicio,
    asegurar_en_su_horario,
    asegurar_refs_del_salon,
    asegurar_sin_solape,
    es_violacion_de_exclusion,
    esta_enlazada,
)
from app.core.disponibilidad import POLITICA_PANEL, huecos_del_dia
from app.core.security import admite_cuenta_cortada, CurrentUser, get_current_user, get_postgrest_client
from app.db.postgrest_client import PostgrestClient, PostgrestError
from app.schemas.appointments import AppointmentCreate, AppointmentOut, AppointmentUpdate
from app.schemas.disponibilidad import DisponibilidadOut

router = APIRouter(prefix="/appointments", tags=["appointments"], dependencies=[publico.ADMINISTRADORA])

_SELECT = "id,client_id,employee_id,service_id,inicio,fin,estado,notas,canal,nombre_reserva"

# clients(nombre,alergias) es un embed de PostgREST: la agenda pinta el
# nombre del cliente y el aviso de alergia sin una segunda consulta.
_SELECT_EMBED = _SELECT + ",clients(nombre,alergias)"


def _http(e: CitaNoValida) -> HTTPException:
    """`CitaNoValida` -> la respuesta HTTP de siempre.

    El status y el texto los trae ya la excepción, así que aquí no se decide
    nada: son exactamente los mismos que cuando estas reglas vivían dentro de
    este fichero.
    """
    return HTTPException(status_code=e.status, detail=e.mensaje)


@router.get("", response_model=list[AppointmentOut])
@admite_cuenta_cortada("agenda")
async def list_appointments(
    desde: str = Query(..., description="ISO datetime, inicio del rango"),
    hasta: str = Query(..., description="ISO datetime, fin del rango"),
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> list[dict]:
    # Semántica de solape con el rango (inicio<hasta y fin>desde): una cita
    # que cruce un borde del día también se devuelve.
    solo_agenda = user.cuenta_cortada or user.viendo_centro_en_pausa
    # TODAS, no las mil primeras: `select` se queda en mil sin avisar y esta lista crece con el tiempo.
    citas = await db.select_todo(
        "appointments",
        {
            # CON LA CUENTA CORTADA (0086) la ficha de la clienta no se lee: la
            # base contestaría 403 al embed. El nombre y las alergias —lo que
            # protege a quien se sienta en la silla— llegan de
            # `clientas_de_la_agenda`, que no devuelve nada más.
            # Lo mismo en la agenda de un CENTRO EN PAUSA (0088): la base no
            # devuelve sus fichas, y el embed llegaría vacío.
            "select": _SELECT if solo_agenda else _SELECT_EMBED,
            "salon_id": f"eq.{user.salon_id}",
            "inicio": f"lt.{hasta}",
            "fin": f"gt.{desde}",
            "order": "inicio.asc,id.asc",
        },
    )
    if solo_agenda:
        ids = sorted({c["client_id"] for c in citas if c.get("client_id")})
        seguras = {
            f["id"]: {"nombre": f.get("nombre"), "alergias": f.get("alergias")}
            for f in (await db.rpc("clientas_de_la_agenda", {"p_ids": ids}) if ids else [])
        }
        for c in citas:
            c["clients"] = seguras.get(c.get("client_id"))
    return citas


@router.get("/disponibilidad", response_model=DisponibilidadOut)
async def disponibilidad(
    service_id: str = Query(..., description="Servicio que se quiere reservar"),
    fecha: str = Query(..., description="Día a consultar, YYYY-MM-DD"),
    employee_id: str | None = Query(None, description="Limitar a un profesional"),
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> dict:
    """Los huecos REALES de un día para un servicio.

    Mismo motor que usa el bot de WhatsApp (`core/disponibilidad.py`): una sola
    cuenta para que el panel y el bot no puedan discrepar sobre si algo está
    libre. Hoy lo consume el bot; queda expuesto para que la rejilla de la
    agenda y el futuro widget de reserva online lo usen sin reimplementarlo.

    LA POLÍTICA ES LA DEL PANEL, no la del bot: sin antelación mínima, porque
    una recepcionista tiene que poder agendar a la clienta que tiene delante
    ahora mismo. Al bot se le aplica una hora de margen, y eso se decide en el
    bot, no aquí.
    """
    try:
        return await huecos_del_dia(
            db,
            user.salon_id,
            service_id=service_id,
            fecha=fecha,
            employee_id=employee_id,
            politica=POLITICA_PANEL,
        )
    except CitaNoValida as e:
        raise _http(e)


async def _por_que_choco(db: PostgrestClient, employee_id: str | None) -> str:
    """El candado ha rechazado la cita. ¿Qué se le dice a quien la escribía?

    El candado sabe QUE hay choque, no DÓNDE: solo ve que la clave (persona,
    rango) ya existe. Antes se contestaba siempre «ese hueco acaba de ocuparse»,
    y eso es cierto únicamente cuando el choque está en esta misma agenda. Con
    una persona que trabaja en dos centros, esa frase manda a mirar una agenda
    que está vacía a esa hora — y quien la lea concluirá que la app falla.

    Se distingue por lo único que se puede saber sin ver la fila ajena: si la
    ficha está enlazada. Si no lo está, un choque solo puede ser de aquí y es una
    carrera perdida. Si lo está y la comprobación previa no vio nada, o es una
    carrera o es una fila que la RLS le esconde; entre decir de más y decir algo
    falso, se dice lo que sabemos con certeza y nada más.
    """
    try:
        if await esta_enlazada(db, employee_id):
            return MENSAJE_OCUPADA_EN_OTRO_CENTRO_SIN_NOMBRE
    except PostgrestError:
        # Si ni siquiera se puede leer la ficha, el mensaje de siempre. Un fallo
        # al AFINAR un mensaje no puede cambiar el código de respuesta.
        pass
    return MENSAJE_CARRERA_PERDIDA


def _mismo_instante(a: object, b: object) -> bool:
    """«2026-09-14T08:00:00.000Z» y «2026-09-14T08:00:00+00:00» son la misma hora."""
    try:
        return datetime.fromisoformat(str(a)) == datetime.fromisoformat(str(b))
    except ValueError:
        return str(a) == str(b)


@router.post("", response_model=AppointmentOut, status_code=status.HTTP_201_CREATED)
async def create_appointment(
    payload: AppointmentCreate,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> dict:
    body = payload.model_dump(mode="json", exclude_none=True)
    try:
        await asegurar_refs_del_salon(db, user.salon_id, body)
        await asegurar_empleado_realiza_servicio(db, payload.employee_id, payload.service_id)
        await asegurar_dentro_del_horario(db, user.salon_id, body["inicio"], body["fin"])
        await asegurar_en_su_horario(
            db, payload.employee_id, body["inicio"], body["fin"],
            z=await zonas.zona_del_centro(db, user.salon_id),
        )
        await asegurar_sin_solape(
            db, user.salon_id, payload.employee_id, body["inicio"], body["fin"]
        )
    except CitaNoValida as e:
        raise _http(e)

    body["salon_id"] = user.salon_id
    try:
        return await db.insert("appointments", body)
    except PostgrestError as e:
        # Se ha perdido una carrera: la comprobación de arriba lo vio libre y
        # para cuando llegó el INSERT ya no lo estaba. Lo caza el candado de la
        # migración 0030. Sin este caso, la recepcionista veía el texto crudo de
        # Postgres sobre un constraint del que no ha oído hablar.
        if es_violacion_de_exclusion(e):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=await _por_que_choco(db, payload.employee_id),
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=visible("crear la cita")
        )


@router.patch("/{appointment_id}", response_model=AppointmentOut)
async def update_appointment(
    appointment_id: str,
    payload: AppointmentUpdate,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> dict:
    body = payload.model_dump(mode="json", exclude_none=True)
    if not body:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nada que actualizar")

    try:
        await asegurar_refs_del_salon(db, user.salon_id, body)

        if "inicio" in body or "fin" in body or "employee_id" in body or "service_id" in body:
            current = await db.select(
                "appointments",
                {
                    "select": _SELECT,
                    "id": f"eq.{appointment_id}",
                    "salon_id": f"eq.{user.salon_id}",
                },
            )
            if not current:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Cita no encontrada"
                )
            merged = {**current[0], **body}

            # HABILIDAD: solo si la pareja empleado/servicio CAMBIA de verdad.
            # «Viene en el body» no significa «se ha cambiado»: el modal manda la
            # cita entera en cada guardado. Si bastara con que apareciera, una cita
            # heredada cuyo empleado ya no realiza el servicio no se podría ni
            # cancelar —mismo criterio que el horario en 16C: se bloquea el cambio,
            # nunca el dato que ya estaba—.
            pareja = (merged.get("employee_id"), merged.get("service_id"))
            if pareja != (current[0].get("employee_id"), current[0].get("service_id")):
                await asegurar_empleado_realiza_servicio(db, pareja[0], pareja[1])

            # «SE MUEVE» SE MIRA EN LOS VALORES, NO EN SI EL CAMPO VIENE. El modal
            # manda la cita entera en cada guardado, así que `"inicio" in body` es
            # cierto también al cancelar. Con esa condición, una cita que se
            # quedó fuera de horario —y desde la 0045 el aviso de citas afectadas
            # promete que se quedan en la agenda «hasta que alguien las cambie»—
            # no se podía ni cancelar desde la agenda.
            se_mueve = not (
                _mismo_instante(merged["inicio"], current[0]["inicio"])
                and _mismo_instante(merged["fin"], current[0]["fin"])
            )

            # Solo si se MUEVE la cita en el tiempo. Cambiar el empleado o el estado
            # de una cita que ya estaba fuera de horario no debe quedar bloqueado:
            # si no, una cita antigua sería imposible de cancelar o reasignar.
            if se_mueve:
                await asegurar_dentro_del_horario(
                    db, user.salon_id, str(merged["inicio"]), str(merged["fin"])
                )
            # SU HORARIO: al moverla, y también al dársela a OTRA persona. Dar a
            # Ana una cita del martes que libra es el mismo error que crearla.
            # Cambiar el estado o las notas no lo exige: una cita que se quedó
            # fuera de su horario tiene que poder cancelarse.
            if se_mueve or merged.get("employee_id") != current[0].get("employee_id"):
                await asegurar_en_su_horario(
                    db, merged.get("employee_id"), str(merged["inicio"]), str(merged["fin"]),
                    z=await zonas.zona_del_centro(db, user.salon_id),
                )
            # Sin `service_id`: cambiar solo el servicio no mueve la cita ni cambia
            # de empleado, así que no puede crear un solape que no existiera ya.
            if "inicio" in body or "fin" in body or "employee_id" in body:
                await asegurar_sin_solape(
                    db,
                    user.salon_id,
                    merged.get("employee_id"),
                    str(merged["inicio"]),
                    str(merged["fin"]),
                    exclude_id=appointment_id,
                )
    except CitaNoValida as e:
        raise _http(e)

    try:
        rows = await db.update(
            "appointments",
            {"id": f"eq.{appointment_id}", "salon_id": f"eq.{user.salon_id}"},
            body,
        )
    except PostgrestError as e:
        # Mover una cita encima de otra que acaba de entrar: mismo caso que en
        # el alta, y el candado también lo cubre en el UPDATE.
        if es_violacion_de_exclusion(e):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=await _por_que_choco(db, merged.get("employee_id")),
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=visible("guardar los cambios de la cita"),
        )

    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cita no encontrada")
    return rows[0]


@router.delete("/{appointment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_appointment(
    appointment_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> None:
    await db.delete(
        "appointments", {"id": f"eq.{appointment_id}", "salon_id": f"eq.{user.salon_id}"}
    )
