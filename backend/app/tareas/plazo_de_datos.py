"""Los avisos del plazo de los datos (0064). NO BORRA NADA.

Una cuenta cuya suscripción ha terminado de verdad tiene sus datos 90 días; esta
tarea se lo recuerda por correo tres veces —al terminar, quedando 30 días y
quedando 7— con la FECHA EXACTA y el enlace para llevárselos. El borrado no es
esta tarea: es una función de la base (0068) que nace apagada y se ejecuta a mano.

DÓNDE CORRE: en la misma ejecución del cron de los recordatorios
(`python -m app.tareas.recordatorios`, cada 15 minutos), DESPUÉS de ellos y
aislada: si esto falla, los recordatorios ya salieron, y al revés. Deja su
PROPIO latido (`plazo_de_datos`), porque un silencio de esta tarea no lo
delataría el de los recordatorios.

LO QUE SE APLICA DE LOS RECORDATORIOS, porque ya costó aprenderlo:
  · SELLAR ANTES DE MANDAR con un UPDATE condicional: dos pasadas a la vez no
    mandan dos correos.
  · ELEGIBILIDAD MONÓTONA (`core/plazo_de_datos.aviso_que_toca`): un cron caído
    retrasa un aviso, no lo pierde.
  · UN FALLO SEGURO SUELTA EL SELLO y se reintenta en la pasada siguiente; uno
    AMBIGUO lo deja puesto: repetir un correo así molesta más que no mandarlo.
  · SIN EL TOPE DE MIL: se lee con `select_todo`.
"""

from __future__ import annotations

import asyncio
import html
import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone

from app.core import plazo_de_datos as plazos
from app.core.config import Settings, get_settings
from app.core.emails import (
    EmailConfigError,
    EnvioFallido,
    clave_idempotencia,
    construir_email_branded,
    enviar_email_branded,
)
from app.core.security import get_service_postgrest_client
from app.core.simulacro import LO_QUE_QUEDA, LO_QUE_SE_BORRA
from app.core.zona_de_la_cuenta import COLUMNAS as COLUMNAS_ZONA
from app.core.zona_de_la_cuenta import zona_de_la_cuenta
from app.db.postgrest_client import PostgrestClient, PostgrestError, trozos_en

logger = logging.getLogger("estetix.plazo_de_datos")

NOMBRE_TAREA = "plazo_de_datos"

def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def _fecha(valor: object) -> datetime | None:
    if not isinstance(valor, str) or not valor:
        return None
    try:
        d = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else None


# Los sellos de las cuentas que nunca pagaron viven en `accounts` (0065): no hay
# suscripción donde ponerlos.
SELLO_SIN_PAGO: dict = {
    "terminada": "aviso_plazo_terminada_at", "30": "aviso_plazo_30_at", "7": "aviso_plazo_7_at",
}


@dataclass(frozen=True)
class Reloj:
    """Una cuenta con el plazo de los datos en marcha.

    DOS ORÍGENES Y UN SOLO RELOJ (04-10-2026):
      · «suscripcion»: la suscripción terminó de verdad (0064);
      · «sin_pago»: se dio de alta y nunca activó la prueba; cuenta desde el día
        en que la prueba habría terminado (0065). Sin esto sus datos se
        guardaban para siempre: se borraba a quien pagó y no a quien no.
    """

    account_id: str
    origen: str  # "suscripcion" | "impago" (0086) | "sin_pago"
    terminada_at: datetime
    enviados: dict
    tabla: str  # dónde se sellan sus avisos
    fila_id: str
    sellos: dict
    zona_horaria: str
    plazo: plazos.Plazo
    fuera_de_plazo: bool


def _reloj(f: dict) -> Reloj | None:
    fin = _fecha(f.get("terminada_at"))
    if fin is None or not f.get("account_id"):
        return None
    # El impago (0086) también vive en la fila de la suscripción.
    suscripcion = f.get("origen") != "sin_pago"
    enviados = {"terminada": bool(f.get("aviso_terminada")), "30": bool(f.get("aviso_30")),
                "7": bool(f.get("aviso_7"))}
    return Reloj(
        str(f["account_id"]), str(f["origen"]), fin, enviados,
        "subscriptions" if suscripcion else "accounts", str(f["fila_id"]),
        dict(plazos.SELLO if suscripcion else SELLO_SIN_PAGO), str(f["zona_horaria"]),
        plazos.Plazo(terminada_el=date.fromisoformat(f["terminada_el"]),
                     se_borra_el=date.fromisoformat(f["se_borran_el"]),
                     dias_restantes=int(f["dias_restantes"])),
        bool(f.get("fuera_de_plazo")),
    )


async def relojes(servicio: PostgrestClient, ahora: datetime, cuenta: str | None = None) -> list[Reloj]:
    """QUIÉN ESTÁ EN PLAZO, y cuándo se borra. NO SE DECIDE AQUÍ: lo decide la
    base (`relojes_del_plazo`, 0068), que es la MISMA selección que leen el
    simulacro y el borrado. Así el correo, el muro, el panel y el borrado no
    pueden decir fechas distintas. Esto solo la lee.
    """
    cuerpo: dict = {"p_ahora": ahora.isoformat()}
    if cuenta is not None:
        cuerpo["p_cuenta"] = cuenta
    return [r for r in (_reloj(f) for f in await servicio.rpc("relojes_del_plazo", cuerpo)) if r is not None]


async def datos_de_las_cuentas(servicio: PostgrestClient, cuentas: list[str]) -> tuple[dict, dict, dict]:
    """Nombre, zona y correos de la administradora de cada cuenta."""
    if not cuentas:
        return {}, {}, {}
    # EN TROZOS: son TODAS las cuentas con el reloj en marcha, y crecen con la
    # plataforma. En una sola URL reventaba a partir de ~210 (`trozos_en`).
    filas_cuentas, salones, admins = [], [], []
    for en in trozos_en(cuentas):
        filas_cuentas += await servicio.select_todo("accounts", {"select": "id,nombre", "id": en, "order": "id.asc"})
        salones += await servicio.select_todo(
            # Las columnas las pone `zona_de_la_cuenta`: es quien decide qué
            # centro manda, y la lista no se copia.
            "salons", {"select": COLUMNAS_ZONA, "account_id": en, "order": "id.asc"}
        )
        admins += await servicio.select_todo(
            "users", {"select": "id,account_id,email", "account_id": en, "rol": "eq.admin", "order": "id.asc"}
        )
    nombres = {str(f["id"]): f.get("nombre") for f in filas_cuentas}
    por_cuenta: dict[str, list[dict]] = {}
    for s in salones:
        por_cuenta.setdefault(str(s["account_id"]), []).append(s)
    zona = {c: zona_de_la_cuenta(por_cuenta.get(c, [])) for c in cuentas}
    correos: dict[str, list[str]] = {}
    for u in admins:
        if (u.get("email") or "").strip():
            correos.setdefault(str(u["account_id"]), []).append(u["email"].strip())
    return nombres, zona, correos


# EL REGISTRO DE JORNADA (04-10-2026): es del salón, y la obligación de
# conservarlo es suya como empleador, el tiempo que diga la ley de su país (sin
# número: cada país tiene el suyo, 19-10-2026). Se borra con lo demás, así que
# cada aviso lo dice con todas las letras: que no lo pierda sin enterarse.
REGISTRO_DE_JORNADA = (
    "<br><br><strong>La descarga incluye el registro de jornada de tu equipo</strong> "
    "(sus fichajes y sus correcciones). Tienes que conservarlo el tiempo que exija la ley de tu país: "
    "guárdalo tú, porque aquí se borrará con todo lo demás."
)


def cuerpo_del_aviso(
    settings: Settings, aviso: plazos.Aviso, p: plazos.Plazo, cuenta: str | None, origen: str = "suscripcion"
) -> tuple[str, str]:
    """Asunto y HTML. Dice la FECHA EXACTA, nunca «pronto»."""
    fecha = plazos.fecha_larga(p.se_borra_el)
    quien = html.escape(cuenta or "tu salón")
    if aviso == "terminada" and origen == "sin_pago":
        asunto = f"No llegaste a activar EstetixApp: tus datos se borrarán el {fecha}"
        titulo = "No llegaste a activar tu prueba"
        entrada = (
            f"<strong>{quien}</strong> se dio de alta en EstetixApp pero la prueba no llegó a "
            f"empezar. Conservamos lo que guardaste hasta el <strong>{fecha}</strong>. Ese día "
            f"se borrará y no se podrá recuperar."
        )
    elif aviso == "terminada" and origen == "impago":
        # EL PRIMER COBRO FALLIDO (0086): la suscripción aún no ha terminado
        # —Stripe está reintentando—, así que no se dice que terminó.
        asunto = f"No hemos podido cobrar tu cuota: si no se resuelve, tus datos se borrarán el {fecha}"
        titulo = "No hemos podido cobrar tu cuota"
        entrada = (
            f"El cobro de la suscripción de <strong>{quien}</strong> falló el "
            f"{plazos.fecha_larga(p.terminada_el)}. Si no se resuelve, conservaremos tus datos "
            f"—{LO_QUE_SE_BORRA}— hasta el <strong>{fecha}</strong>. Ese día se "
            f"borrarán y no se podrán recuperar. Actualiza tu tarjeta en EstetixApp y no se borrará nada."
        )
    elif aviso == "terminada":
        asunto = f"Tu suscripción ha terminado: tus datos se borrarán el {fecha}"
        titulo = "Tu suscripción ha terminado"
        entrada = (
            f"La suscripción de <strong>{quien}</strong> terminó el "
            f"{plazos.fecha_larga(p.terminada_el)}. Conservamos tus datos —{LO_QUE_SE_BORRA}— "
            f"hasta el <strong>{fecha}</strong>. Ese día se borrarán y no se "
            f"podrán recuperar."
        )
    else:
        dias = int(aviso)
        asunto = f"Quedan {dias} días: tus datos de EstetixApp se borrarán el {fecha}"
        titulo = f"Quedan {dias} días para que se borren tus datos"
        entrada = (
            f"Los datos de <strong>{quien}</strong> se borrarán el <strong>{fecha}</strong>. "
            f"Después no se podrán recuperar."
        )
    entrada += (
        "<br><br>Si quieres conservarlos, descárgalos antes de esa fecha: entra en EstetixApp "
        "con tu cuenta y pulsa «Descargar mis datos». Si activas tu plan antes, no se "
        "borra nada."
    ) + REGISTRO_DE_JORNADA + f"<br><br>{html.escape(LO_QUE_QUEDA)}"
    cuerpo = construir_email_branded(
        title=titulo,
        intro=entrada,
        cta_label="Descargar mis datos",
        action_link=f"{settings.frontend_base_url.rstrip('/')}/ajustes",
        footer_note=f"Recibes este correo porque administras {quien} en EstetixApp.",
    )
    return asunto, cuerpo


async def _sellar(servicio: PostgrestClient, r: Reloj, aviso) -> bool:
    """UPDATE condicional: solo si no está sellado. En una suscripción, además,
    solo si el reloj sigue siendo el mismo: si la cuenta vuelve justo ahora, el
    webhook pone `terminada_at` a NULL, esto ya no encaja y el correo no sale."""
    sello = r.sellos[aviso]
    filtro = {"id": f"eq.{r.fila_id}", sello: "is.null"}
    cuerpo = {sello: _ahora().isoformat()}
    try:
        # LA TABLA, ESCRITA. El censo de columnas (`test_columnas_leidas_que_nadie_escribe`)
        # solo ve las llamadas con el nombre de la tabla a la vista.
        if r.tabla == "subscriptions":
            filtro["terminada_at"] = f"eq.{r.terminada_at.isoformat()}"
            filas = await servicio.update("subscriptions", filtro, cuerpo, select="id")
        else:
            filas = await servicio.update("accounts", filtro, cuerpo, select="id")
    except PostgrestError:
        logger.exception("Plazo de datos: no se pudo sellar %s.", r.account_id)
        return False
    return bool(filas)


async def _soltar(servicio: PostgrestClient, r: Reloj, aviso) -> None:
    filtro, cuerpo = {"id": f"eq.{r.fila_id}"}, {r.sellos[aviso]: None}
    try:
        if r.tabla == "subscriptions":
            await servicio.update("subscriptions", filtro, cuerpo, select="id")
        else:
            await servicio.update("accounts", filtro, cuerpo, select="id")
    except PostgrestError:
        logger.exception("Plazo de datos: no se pudo soltar el sello de %s.", r.account_id)


async def ejecutar(settings: Settings | None = None, servicio: PostgrestClient | None = None) -> dict:
    """Una pasada. Devuelve el resumen (para el log y los tests)."""
    settings = settings or get_settings()
    servicio = servicio or get_service_postgrest_client(settings)
    ahora = _ahora()
    resumen: dict = {"en_plazo": 0, "enviados": 0, "fallidos": 0, "sin_correo": 0}

    try:
        filas = await relojes(servicio, ahora)
        nombres, zona, correos = await datos_de_las_cuentas(servicio, sorted({r.account_id for r in filas}))
    except PostgrestError:
        logger.exception("Plazo de datos: no se pudo leer quién está en plazo.")
        return {**resumen, "error": "lectura"}

    resumen["en_plazo"] = len(filas)
    for r in filas:
        cuenta = r.account_id
        p = r.plazo
        aviso = plazos.aviso_que_toca(p, r.enviados)
        if aviso is None:
            continue
        destinos = correos.get(cuenta, [])
        if not destinos:
            resumen["sin_correo"] += 1
            logger.warning("Plazo de datos: la cuenta %s no tiene correo de administradora.", cuenta)
            continue
        if not await _sellar(servicio, r, aviso):
            continue
        asunto, cuerpo = cuerpo_del_aviso(settings, aviso, p, nombres.get(cuenta), r.origen)
        salio, seguro = False, True
        for destino in destinos:
            try:
                # Un aviso por cuenta, plazo y buzón: los reintentos llevan la misma.
                await enviar_email_branded(settings, destino, asunto, cuerpo, clave=clave_idempotencia(
                    "plazo", cuenta, aviso, p.terminada_el, destino))
                salio = True
            except EmailConfigError:
                logger.error("Plazo de datos: configuración de correo incompleta.")
            except EnvioFallido as e:
                seguro = seguro and e.seguro
                logger.warning("Plazo de datos: aviso %s de %s no salió (%s).", aviso, cuenta,
                               "seguro" if e.seguro else "ambiguo")
            except Exception:  # noqa: BLE001 — no clasificado = ambiguo
                seguro = False
                logger.exception("Plazo de datos: aviso %s de %s.", aviso, cuenta)
        if salio:
            resumen["enviados"] += 1
        else:
            resumen["fallidos"] += 1
            if seguro:
                await _soltar(servicio, r, aviso)

    try:
        await servicio.upsert_many(
            "tareas_latido",
            [{"tarea": NOMBRE_TAREA, "ultima_at": _ahora().isoformat(),
              "procesados": resumen["enviados"], "fallidos": resumen["fallidos"]}],
            on_conflict="tarea",
        )
    except PostgrestError:
        logger.exception("Plazo de datos: no se pudo dejar el latido.")
    logger.info("Plazo de datos: %s", resumen)
    return resumen


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    print(f"plazo_de_datos: {asyncio.run(ejecutar())}")


if __name__ == "__main__":
    main()
