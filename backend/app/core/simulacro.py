"""El simulacro del borrado a 90 días. LEE; NO BORRA, Y NO PUEDE.

Pregunta a la base (`simulacro_de_borrado`, 0065) qué se llevaría por delante un
borrado de la cuenta —desde la 0068, con el MISMO plan que ejecuta el borrado
(`plan_de_borrado`)—, y lo pone al lado de LO DECIDIDO (04-10-2026)
que se va y que se queda. Lo que importa son las DISCREPANCIAS: algo que se
borraría y habíamos decidido conservar, algo que se quedaría y debería irse, o
algo que haría fallar el borrado. El simulacro existe para verlas ANTES de que
exista el borrado.

La función de la base es `stable`: Postgres no le deja ejecutar un DELETE. Y
este módulo solo llama a esa función.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.db.postgrest_client import PostgrestClient

# ── LO QUE SE QUEDA (04-10-2026). Todo lo demás de la cuenta se va. ──
# LA CUENTA Y SU SUSCRIPCIÓN SE VAN (revisado el mismo 04-10, 0068): «la cuenta
# se queda» era la regla de la BAJA —puede volver—, no la del borrado. Si
# sobreviviera con su nombre, su CIF y su cliente de Stripe, el borrado no
# borraría a la persona, solo su salón. Lo que se queda es el registro contable
# y de abuso: dinero, descargas, soporte y el registro del panel.
SE_QUEDA: dict[str, str] = {
    "public.decisiones_de_dinero": "nuestro registro comercial: quién decidió cada cosa de dinero",
    "public.descargas_de_datos": "nuestro registro: la prueba de que se llevó sus datos",
    "public.impersonation_sessions": "nuestra auditoría de soporte",
    "public.support_access_codes": "nuestra auditoría de soporte",
    "public.admin_logs": "nuestro registro del panel",
    "public.cuentas_borradas": "nuestra constancia de que el borrado ocurrió (0068)",
    "public.altas_atribuidas": "quién trajo cada cuenta: de aquí salen comisiones a perpetuidad (0070)",
    "public.codigos_que_no_valieron": "la constancia de un código de referido que no valió (0070)",
    "public.referidos": "el programa de referidos: no es de ninguna cuenta (0029)",
    "public.comisiones": "lo que se debe a los referidos: una comisión devengada no se va con el salón (0075)",
    "public.cambios_de_porcentaje": "quién cambió el % de un salón, cuándo y por qué (0075)",
    "public.pagos_a_referidos": "lo que se les ha pagado a los referidos, y quién lo ordenó (0076)",
    "public.pagos_a_referidos_resultados": "lo que pasó con cada pago: enviado, rechazado o revertido (0076)",
    "public.cambios_de_minimo": "quién cambió el mínimo de pago a los referidos, cuándo y por qué (0077)",
}

# ── LO QUE LE DECIMOS A LA PERSONA QUE SE VA (19-10-2026). ──────────
# Cada tabla que se queda está NOMBRADA en el texto, o está aquí abajo diciendo
# por qué no lleva nada de la cuenta. No se invoca la ley donde la decisión es
# nuestra: el rastro de soporte lo guardamos porque lo queremos, no porque nos
# obliguen. `tests/test_lo_que_queda_se_dice.py` se pone rojo si aparece una
# tabla que se queda sin estar en ninguno de los dos sitios, y comprueba que el
# diálogo, los correos y la política de privacidad dicen ESTE texto.
LO_QUE_QUEDA_DICHO: dict[str, set[str]] = {
    # Con el nombre y el correo de quien lo hizo.
    "de los cambios de plan y de pago": {"public.decisiones_de_dinero"},
    "de las descargas de datos": {"public.descargas_de_datos"},
    "de quién entró a darte soporte": {"public.impersonation_sessions", "public.support_access_codes"},
    # Sin correo: el id del centro, o el nombre de la cuenta.
    "lo que nuestro equipo hizo en tu cuenta desde el panel": {"public.admin_logs"},
    "si llegaste por un comercial, tu alta y lo que se le paga por ella": {
        "public.altas_atribuidas", "public.codigos_que_no_valieron", "public.comisiones",
        "public.cambios_de_porcentaje",
    },
}

# Se quedan, y no llevan nada de la cuenta que se va: no hay nada que decirle.
SIN_NADA_DE_LA_CUENTA: dict[str, str] = {
    "public.cuentas_borradas": "solo el id de la cuenta, el día y cuántas filas se borraron de cada tabla",
    "public.referidos": "los datos del comercial, no los de la cuenta",
    "public.pagos_a_referidos": "lo que se le pagó al comercial, sin cuentas",
    "public.pagos_a_referidos_resultados": "lo que pasó con cada pago al comercial, sin cuentas",
    "public.cambios_de_minimo": "el mínimo de pago a los comerciales, que no es de ninguna cuenta",
}

LO_QUE_SE_BORRA = "centros, clientas, citas, equipo, fichajes y tu cuenta"
LO_QUE_QUEDA = (
    "Queda constancia, con tu nombre y tu correo, de los cambios de plan y de pago, de las descargas de datos "
    "y de quién entró a darte soporte. Queda también, sin tu correo, lo que nuestro equipo hizo en tu cuenta "
    "desde el panel y, si llegaste por un comercial, tu alta y lo que se le paga por ella. Las facturas las "
    "guarda Stripe."
)
TEXTO_DEL_BORRADO = f"Tus datos se conservan 90 días y después se borran: {LO_QUE_SE_BORRA}. {LO_QUE_QUEDA}"


def como_lo_dice_la_baja(tabla: str) -> str | None:
    """La frase del texto de la baja que nombra esta tabla, o por qué no hace falta."""
    for frase, tablas in LO_QUE_QUEDA_DICHO.items():
        if tabla in tablas:
            return f"En la baja: «{frase}»"
    if tabla in SIN_NADA_DE_LA_CUENTA:
        return f"No lleva nada de la cuenta: {SIN_NADA_DE_LA_CUENTA[tabla]}"
    return None


# Lo que se va aunque no cuelgue de nada (sin clave ajena): el registro de
# jornada es del salón y se va con él.
SE_VA_SIN_CLAVE: dict[str, str] = {
    "public.correcciones_de_fichaje": "registro de jornada: es del salón, se va con él",
    "public.correcciones_de_caja": "registro de la caja: es del salón, se va con él",
}


@dataclass
class Fila:
    tabla: str
    filas: int | None
    que_haria: str  # borra · queda · queda_sin_referencia · impide_el_borrado · trigger_impide_borrar · queda_en_auth · no_comprobada
    detalle: str
    decidido: str  # «se va» · «se queda»
    problema: str | None = None
    # Lo que se queda: cómo lo nombra el texto de la baja (o por qué no hace falta).
    en_la_baja: str | None = None


@dataclass
class Simulacro:
    filas: list[Fila] = field(default_factory=list)

    @property
    def problemas(self) -> list[Fila]:
        return [f for f in self.filas if f.problema]

    @property
    def filas_a_borrar(self) -> int:
        # Las filas de la BASE (Auth incluida, que se borra en la misma
        # transacción). Lo de fuera —Stripe y Storage— no son filas de aquí.
        return sum(f.filas or 0 for f in self.filas
                   if f.que_haria == "borra" and not f.tabla.startswith(("storage.", "stripe")))


def _decidido(tabla: str) -> str:
    return "se queda" if tabla in SE_QUEDA else "se va"


def clasificar(crudas: list[dict]) -> Simulacro:
    """Lo que dice la base, contra lo decidido. Función PURA: se prueba sola."""
    sim = Simulacro()
    con_filas = {r["tabla"]: (r.get("filas") or 0) for r in crudas if r.get("accion") in ("borra", "queda")}
    for r in sorted(crudas, key=lambda r: (r.get("paso") or 0, r.get("tabla") or "")):
        tabla, accion, filas = r["tabla"], r["accion"], r.get("filas")
        decidido = _decidido(tabla)
        problema = None
        if accion == "borra" and tabla in SE_QUEDA and (filas or 0) > 0:
            problema = f"Se borraría, y está decidido que se queda: {SE_QUEDA[tabla]}."
        elif accion == "borra" and tabla in SE_QUEDA:
            problema = (f"Hoy no tiene filas, pero se borraría en cuanto las tuviera ({r['detalle']}), "
                        f"y está decidido que se queda: {SE_QUEDA[tabla]}.")
        elif accion == "queda" and tabla not in SE_QUEDA:
            motivo = SE_VA_SIN_CLAVE.get(tabla, "no está entre lo que se queda")
            problema = f"Se quedaría, y debería irse ({motivo}): no cuelga de nada de lo que se borra."
        elif accion == "impide_el_borrado":
            problema = f"Impediría el borrado: hay filas que apuntan a lo que se borra ({r['detalle']})."
        elif accion == "queda_sin_referencia" and tabla in SE_QUEDA:
            problema = None  # se queda, solo pierde la referencia: es lo esperado
        elif accion == "queda_sin_referencia":
            # Lo de `auth.scim_users` hasta la 0083: la fila sobrevive sin dueño,
            # con lo que tuviera dentro. No es «lo nuestro»: debería irse.
            problema = f"Se quedaría sin dueño, y debería irse ({r['detalle']}): sus datos sobrevivirían al borrado."
        elif accion == "no_comprobada":
            # 0084: una tabla que no se ha podido mirar no es «nada»: es «no lo sé».
            problema = f"No se ha podido comprobar si guarda algo de esta cuenta ({r['detalle']})."
        elif accion == "queda_en_auth":
            problema = f"Quedaría en Auth algo que la nombra ({r['detalle']}): el borrado se negaría."
        elif accion == "trigger_impide_borrar":
            if tabla in SE_QUEDA or con_filas.get(tabla, 0) == 0:
                continue  # protegido y se queda, o no hay nada suyo: no estorba
            problema = f"El borrado fallaría: {r['detalle']}, y esta tabla tiene filas de la cuenta que deberían irse."
        sim.filas.append(Fila(tabla, filas, accion, r.get("detalle") or "", decidido, problema,
                              como_lo_dice_la_baja(tabla) if decidido == "se queda" else None))
    return sim


async def simular(servicio: PostgrestClient, account_id: str) -> Simulacro:
    """Pregunta a la base. No escribe nada: la función es `stable`."""
    return clasificar(await servicio.rpc("simulacro_de_borrado", {"p_cuenta": account_id}))
