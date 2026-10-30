"""Qué incluye el plan de esta cuenta AHORA MISMO. Un solo sitio, una sola vez.

Hasta aquí el plan se comprobaba al CREAR —abrir un centro, dar de alta a una
empleada— y nunca al USAR. De ahí salían los dos agujeros que este módulo cierra
juntos, porque son el mismo: una cuenta en Pro con dos centros usaba los dos, y
un Básico con WhatsApp configurado seguía teniendo asistente. Partirlo en dos
comprobaciones sería escribir el mecanismo dos veces y acabar con dos sitios que
pueden decir cosas distintas.

Es el hermano de `estado_suscripcion.py`, y por el mismo motivo: aquel contesta
«¿esta cuenta está viva?» para los tres caminos por los que se usa la aplicación
—la sesión, el widget y el bot—; este contesta «¿y qué incluye?».

══ FUNCIONES PURAS SOBRE DATOS YA LEÍDOS ══

Aquí no hay base de datos. Quien llama ya tiene el plan y los centros de la
cuenta colgados de la fila que estaba leyendo de todas formas, así que esto no
añade ni una consulta ni un viaje por petición. Medido el 18-09-2026 contra el
PostgREST local, con los tres embeds que lo traen:

    users                  → salons → accounts(plan, salons(...))
    salons                 → accounts(plan, salons(...))
    salon_whatsapp_config  → salons → accounts(plan, salons(...))

y la consulta de sesión, que corre en CADA petición, costaba 7,51 ms sin los
centros y 7,71 ms con ellos: dentro del ruido.

══════════════════════════════════════════════════════════════════════════
  LA DIRECCIÓN DEL FALLO. Son DOS preguntas distintas y fallan al revés.
══════════════════════════════════════════════════════════════════════════

  · ANTE LA DUDA SOBRE QUÉ INCLUYE EL PLAN —esto—, todo sigue funcionando y
    salta un aviso ruidoso. Un salón que no puede trabajar es mucho peor que un
    salón que usa algo gratis diez minutos.

  · ANTE LA DUDA SOBRE SI LA CUENTA ESTÁ VIVA —suspendida, cancelada, impagada:
    `estado_suscripcion.py` y la suspensión de `core/security.py`— no se toca
    nada: eso sigue fallando CERRADO. Una cuenta cancelada que falla abierta es
    servicio gratis indefinido; un plan mal resuelto son diez minutos de una
    función de más.

Y «no se sabe» no es lo mismo que «dice que no». Solo lo primero concede: una
consulta que falla, un embed que no viene, un plan que no conocemos. Un plan que
sí conocemos y que no incluye algo, niega.

UN PLAN DESCONOCIDO NO ES BÁSICO. `plan_rank` trata cualquier cadena que no esté
en la jerarquía como el plan más restrictivo, que es lo correcto para decidir un
límite pero sería desastroso aquí: el día que se cree un precio nuevo en Stripe y
el webhook escriba un nombre que `plans.py` no tiene, una cuenta que paga se
quedaría sin su segundo centro y con el bot callado. Aquí eso es «no resuelto».

APUNTADO, NO CONSTRUIDO: el sitio natural para enterarse de que Stripe tiene un
precio que nosotros no conocemos es el vigilante de divergencia de B2a
(`test_precios_espejo`/`billing/precios.py`), que ya compara lo que vendemos con
lo que hay dado de alta. Este módulo solo chilla cuando ya ha pasado.

══ LO QUE ESTE MÓDULO NO ES ══

No sustituye a `plans.py`. Aquel es la lista de precios —`CENTROS_POR_PLAN`,
`EMPLOYEE_LIMITS`, `FEATURE_MIN_PLAN`, con su espejo vigilado en el frontend— y
sigue siendo la única copia de esos números. Este es el portero: lee esa lista y
contesta sobre UNA cuenta concreta en ESTE momento.

Tampoco es la frontera de aislamiento. `salones_visibles()` sigue contestando de
quién son los centros y nada más. El límite de plan es una frontera COMERCIAL:
si se metiera en la RLS, un fallo de facturación pasaría a ser un fallo de
acceso a datos y las suites de aislamiento pasarían a depender de Stripe. Y no
serviría de nada, porque media docena de caminos escriben con `service_role`,
que se salta la RLS por definición.

══ EL CENTRO QUE SE QUEDA ══

Por orden, y sin escribir nada:

  1. el que la titular marcó al bajar de plan (`salons.sigue_al_bajar`, 0049).
     Como mucho hay uno por cuenta y lo garantiza el índice único parcial de la
     base, no este código;
  2. si no hay marca —cuentas que ya estaban en Pro con dos centros, o bajadas
     anteriores a B2a—, el MÁS ANTIGUO por `created_at`, y a igualdad, el `id`
     menor. El primero es el que tiene la historia: las clientas, las citas
     viejas, el slug que la gente conoce, el QR impreso;
  3. nunca cero. Si el tope saliera 0 se fuerza 1.

NO SE ESCRIBE LA ELECCIÓN CALCULADA. Convertiría un camino de lectura —el
widget, el bot— en uno de escritura con `service_role`, tendría carrera con el
índice único, y crearía un SEGUNDO escritor de esa columna. Una respuesta que se
calcula no puede desincronizarse de sus datos.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.billing.plans import CENTROS_POR_PLAN, FEATURE_MIN_PLAN, plan_allows

logger = logging.getLogger("estetix.plan")

# El orden de las claves de `centros`: lo que hace falta para repartir.
CAMPOS_DEL_CENTRO = ("id", "created_at", "sigue_al_bajar")

# Lo que se le dice a quien mira desde fuera. Nunca nombra el plan ni la
# suscripción: la clienta no tiene por qué enterarse de nuestra facturación.
# Ver `routers/publico.py` y `routers/webhooks_whatsapp.py`.
SIN_RESERVAS_ONLINE = "Ahora mismo no se puede reservar por aquí."


def _clave(centro: dict) -> tuple[int, int, str, str]:
    """Con qué orden se reparten. NO depende del orden en que lleguen las filas.

    Es lo que hace la regla determinista: dos peticiones con las mismas filas en
    distinto orden tienen que pausar el mismo centro. Una fila sin `created_at`
    —solo pasa en dobles de tests— se va al final para que decida el dato real.
    """
    nacido = str(centro.get("created_at") or "")
    return (
        0 if centro.get("sigue_al_bajar") else 1,
        0 if nacido else 1,
        nacido,
        str(centro.get("id") or ""),
    )


@dataclass(frozen=True)
class Incluido:
    """La respuesta entera para una cuenta. Se construye con `lo_que_incluye`."""

    plan: str | None
    # False = NO SE HA PODIDO SABER. Entonces todo está concedido y ya se ha
    # hecho ruido al construirlo. No es «el plan no incluye nada».
    resuelto: bool
    centros_activos: tuple[str, ...]
    centros_en_pausa: tuple[str, ...]

    def alcanza(self, plan_minimo: str) -> bool:
        """¿Llega este plan al mínimo que pide una función?

        Sin resolver, SÍ. Es la primera mitad de la regla de arriba, y está aquí
        —y no repetida en cada `require_plan`— para que no haya dos criterios.
        """
        if not self.resuelto:
            return True
        return plan_allows(self.plan or "", plan_minimo)

    @property
    def asistente(self) -> bool:
        """¿Contesta el bot de WhatsApp? Sale de `FEATURE_MIN_PLAN`, no de una
        segunda lista que se separaría de la primera."""
        return self.alcanza(FEATURE_MIN_PLAN["asistente"])

    def esta_activo(self, centro_id: str | None) -> bool:
        """¿Se puede trabajar y atender en este centro?

        SE PREGUNTA POR LOS PAUSADOS Y NO POR LOS ACTIVOS, a propósito: un centro
        del que no sabemos nada —porque la lista no vino— cuenta como activo. Es
        la misma dirección de fallo de todo el módulo.
        """
        if not centro_id:
            return True
        return str(centro_id) not in self.centros_en_pausa

    @property
    def hay_centros_en_pausa(self) -> bool:
        return bool(self.centros_en_pausa)


def lo_que_incluye(plan: str | None, centros: list[dict] | None = None) -> Incluido:
    """Qué incluye este plan para esta cuenta, ahora.

    `centros` son las filas de `salons` de la CUENTA con `id`, `created_at` y
    `sigue_al_bajar`. Sin ellas se contesta igual sobre las funciones —el
    asistente— y no se pausa ningún centro: no se pausa lo que no se ha visto.
    """
    filas = [c for c in (centros or ()) if c and c.get("id")]

    resuelto = bool(plan) and plan in CENTROS_POR_PLAN
    if not resuelto:
        # RUIDOSO A PROPÓSITO, y a nivel ERROR: si esto pasa, alguien está
        # usando gratis algo que no ha contratado, o —peor— a una cuenta que
        # paga se le ha estado dando de menos. Las dos cosas hay que verlas.
        logger.error(
            "PLAN SIN RESOLVER (%r): se concede TODO y no se pausa ningún centro. "
            "Si es un plan nuevo de Stripe, falta en billing/plans.py.",
            plan,
        )
        return Incluido(
            plan=plan,
            resuelto=False,
            centros_activos=tuple(str(c["id"]) for c in sorted(filas, key=_clave)),
            centros_en_pausa=(),
        )

    # NUNCA CERO CENTROS ACTIVOS. El `max(1, ...)` no es defensa contra el
    # diccionario de hoy —el más bajo ya es 1— sino contra el de mañana: una
    # cuenta sin ningún centro activo no puede trabajar, y eso no lo puede
    # provocar un cambio de precios.
    tope = max(1, CENTROS_POR_PLAN[plan])
    ordenados = [str(c["id"]) for c in sorted(filas, key=_clave)]
    return Incluido(
        plan=plan,
        resuelto=True,
        centros_activos=tuple(ordenados[:tope]),
        centros_en_pausa=tuple(ordenados[tope:]),
    )


def centro_que_se_queda(centros: list[dict] | None) -> dict | None:
    """EL CENTRO DE REFERENCIA DE LA CUENTA: el primero del reparto, o sea el
    que se queda al bajar de plan (el marcado; si no hay marca, el más
    antiguo). La misma regla que la congelación, no una segunda. `None` solo si
    la cuenta no tiene ningún centro."""
    filas = [c for c in (centros or ()) if c and c.get("id")]
    return min(filas, key=_clave) if filas else None


def centros_de_la_cuenta(cuenta: dict) -> list[dict]:
    """Los centros que PostgREST cuelga de `accounts(...,salons(...))`.

    Igual que `estados_embebidos`: el doble de los tests puede no traer la clave
    —eso es «no se sabe», no un error— y PostgREST devuelve el lado «uno a
    muchos» como lista.
    """
    filas = (cuenta or {}).get("salons") or []
    if isinstance(filas, dict):
        filas = [filas]
    return [f for f in filas if isinstance(f, dict)]
