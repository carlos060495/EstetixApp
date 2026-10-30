"""EL VOLCADO DE LA CUENTA: los datos del salón, en un ZIP que se abre en Excel.

Es el derecho de portabilidad del RGPD (art. 20) y, además, una promesa: el
diálogo de la baja dice que los datos se conservan 90 días y después se
borran. Sin una forma de llevárselos, esa frase no se sostiene.

QUÉ ENTRA: todo lo que es del salón (el criterio, 01-10-2026: si el
dato es del salón, se lo lleva) —centros, clientas, citas, servicios,
empleadas, fichajes, ventas, stock, conversaciones, recordatorios, gastos,
proveedores, horarios y usuarios— de TODOS los centros de la cuenta, con una
columna «Centro» en cada fila. Y un LEEME.txt que explica cada fichero.

QUÉ NO ENTRA, a propósito: hashes de PIN, tokens (el de darse de baja de los
recordatorios), identificadores de Stripe, el `payload` interno del bot y sus
mensajes de herramienta, las cuentas de acceso (`users`) y todo lo de
plataforma. Aquí se construye con una LISTA DE COLUMNAS por fichero: una
columna nueva en una tabla no entra en el volcado hasta que alguien la añada a
mano, que es el sentido seguro del fallo.

EL FORMATO, MEDIDO CON EXCEL (30-09-2026), no elegido de memoria:

  · UTF-16 con BOM y TABULADOR. Es lo único que se abre en columnas en
    cualquier Excel: el separador «;» de un CSV español se lee como una sola
    columna en un Excel con formato regional de otro país (y el «,», al revés
    en España), porque Excel usa el separador de lista del sistema. El UTF-8
    con BOM acierta los acentos pero no el separador, y añadir `sep=;` arregla
    el separador y rompe los acentos.
  · Importes con COMA decimal y fechas dd/mm/aaaa hh:mm, en hora de Madrid:
    lo que Excel en España reconoce como número y como fecha.
  · NINGUNA CELDA EMPIEZA POR = + - @: Excel ejecuta como fórmula lo que
    empieza así (medido: «=1+1» se abre como 2). Un nombre de clienta escrito
    con mala idea se convertiría en una fórmula en el ordenador del salón. Se
    le antepone un apóstrofo, que Excel enseña tal cual y no ejecuta.
  · Los teléfonos, agrupados («+34 600 12 34 56»): así Excel los deja como
    texto. En E.164 pegado los convierte en 3,46E+10.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from zoneinfo import ZoneInfo

from app.core import zona as zonas
from app.core.telefonos import region_del_salon, telefono_legible

# ── Las traducciones de los valores internos ───────────────────────────────

ESTADO_CITA = {
    "pendiente": "Pendiente",
    "confirmada": "Confirmada",
    "completada": "Completada",
    "cancelada": "Cancelada",
    "no_show": "No se presentó",
}
CANAL_CITA = {
    "manual": "En el salón",
    "whatsapp": "WhatsApp",
    "instagram": "Instagram",
    "google": "Google",
    "online": "Reserva online",
}
TIPO_FICHAJE = {"entrada": "Entrada", "pausa": "Pausa", "reanudacion": "Vuelta de la pausa", "salida": "Salida"}
ACCION_CORRECCION = {"nueva": "Añadida", "cambia": "Hora cambiada", "anula": "Anulada"}
TIPO_VENTA = {"producto": "Producto", "otro": "Otro"}
ESTADO_EMPLEADA = {"activo": "Activa", "pausa": "En pausa", "baja": "De baja"}
TIPO_COMISION = {"porcentaje": "Porcentaje", "fijo": "Importe fijo"}
# Quién escribe en una conversación. `tool` NO está: es la caja negra con la
# que el asistente calculó huecos, no algo que nadie haya dicho.
QUIEN_ESCRIBE = {"user": "Clienta", "model": "Asistente", "agente": "Salón", "system": "Aviso"}
TIPO_GASTO = {"fijo": "Fijo", "variable": "Variable"}
ESTADO_GASTO = {"pendiente": "Pendiente", "pagado": "Pagado"}
# Solo la administradora tiene cuenta (0069): es el único rol que puede salir.
ROL = {"admin": "Administradora"}
# ISO-8601, como `dia_semana` en la base (0022): 1 = lunes … 7 = domingo.
DIA = {1: "Lunes", 2: "Martes", 3: "Miércoles", 4: "Jueves", 5: "Viernes", 6: "Sábado", 7: "Domingo"}


# ── Las celdas ─────────────────────────────────────────────────────────────

_EMPIEZA_PELIGROSO = ("=", "+", "-", "@", "\t", "\r")
_TELEFONO_AGRUPADO = re.compile(r"^\+[0-9 ]+$")


def texto(v) -> str:
    """Texto libre, sin nada que Excel pueda ejecutar."""
    if v is None:
        return ""
    s = str(v).replace("\t", " ")
    if s.startswith(_EMPIEZA_PELIGROSO):
        return "'" + s
    return s


def importe(v) -> str:
    """12,50 — con coma, como lo lee Excel en España."""
    if v is None or v == "":
        return ""
    try:
        d = Decimal(str(v)).quantize(Decimal("0.01"))
    except InvalidOperation:
        return texto(v)
    return f"{d}".replace(".", ",")


def numero(v) -> str:
    if v is None or v == "":
        return ""
    return str(v).replace(".", ",")


def si_no(v) -> str:
    return "Sí" if v else "No"


def fecha_hora(v, z: ZoneInfo | None = None) -> str:
    """Un instante, en la hora de pared del centro al que pertenece la fila."""
    if not v:
        return ""
    return zonas.fecha_y_hora(v, z)


def fecha(v, z: ZoneInfo | None = None) -> str:
    """Un día: de calendario tal cual («1990-03-04»), o el del centro si es un
    instante («…T22:30:00+00:00» es el día siguiente en Madrid)."""
    if not v:
        return ""
    if isinstance(v, datetime) or "T" in str(v):
        return zonas.fecha_dd_mm_aaaa(v, z)
    d = v if isinstance(v, date) else date.fromisoformat(str(v)[:10])
    return zonas.dia_dd_mm_aaaa(d)


# CON MEMORIA, POR NÚMERO. `phonenumbers` es lo caro del volcado: medido con
# una cuenta sintética de 212.000 filas, dos tercios del tiempo de armar el ZIP
# se iban aquí, porque cada mensaje de una conversación repite el teléfono de
# su clienta. Un número se formatea una vez.
def hora(v) -> str:
    """«09:30:00» → «09:30»."""
    return str(v)[:5] if v else ""


@lru_cache(maxsize=65536)
def telefono(v, region: str) -> str:
    if not v:
        return ""
    legible = telefono_legible(v, region)
    if legible and _TELEFONO_AGRUPADO.match(legible):
        return legible
    # No es un número válido: se enseña lo guardado, tal cual pero inerte.
    return texto(v)


def enum(v, tabla: dict[str, str]) -> str:
    return tabla.get(v, texto(v)) if v else ""


# ── El fichero ─────────────────────────────────────────────────────────────


def _campo(v: str) -> str:
    if any(c in v for c in ('"', "\n", "\r")):
        return '"' + v.replace('"', '""') + '"'
    return v


_CAMPO_DE_CAJA = {
    "fecha": "fecha", "concepto": "concepto", "importe": "importe", "cantidad": "cantidad",
    "categoria": "categoría", "tipo": "tipo", "estado": "estado", "provider_id": "proveedor",
}


def _json_legible(v) -> str:
    """«importe: 120 · fecha: 2026-10-29» en vez de un JSON. Solo los campos
    que la persona reconoce; al anular, `antes` es la fila entera."""
    if not isinstance(v, dict):
        return ""
    return " · ".join(f"{_CAMPO_DE_CAJA[k]}: {v[k]}" for k in _CAMPO_DE_CAJA if k in v and v[k] is not None)


def _stock_legible(c: dict) -> str:
    movido = c.get("stock_movido")
    if isinstance(movido, dict):
        u = movido.get("unidades") or 0
        return f"{'+' if u > 0 else ''}{u} uds de {movido.get('producto')}"
    return c.get("stock_sin_ajustar_detalle") or ""


def tabla_csv(cabecera: list[str], filas: list[list[str]]) -> bytes:
    """UTF-16 con BOM, tabulador y CRLF: ver la cabecera del módulo."""
    lineas = ["\t".join(_campo(c) for c in cabecera)]
    lineas += ["\t".join(_campo(c) for c in f) for f in filas]
    return ("\r\n".join(lineas) + "\r\n").encode("utf-16")


@dataclass(frozen=True)
class Datos:
    """Lo leído de la base, por tabla, ya limitado a los centros de la cuenta."""

    centros: list[dict]
    clientas: list[dict]
    citas: list[dict]
    servicios: list[dict]
    categorias: list[dict]
    empleadas: list[dict]
    puestos: list[dict]
    fichajes: list[dict]
    ventas: list[dict]
    productos: list[dict]
    conversaciones: list[dict]
    mensajes: list[dict]
    rechazos_recordatorio: list[dict] = field(default_factory=list)
    gastos: list[dict] = field(default_factory=list)
    proveedores: list[dict] = field(default_factory=list)
    horario_centro: list[dict] = field(default_factory=list)
    excepciones_centro: list[dict] = field(default_factory=list)
    horario_empleadas: list[dict] = field(default_factory=list)
    excepciones_empleadas: list[dict] = field(default_factory=list)
    empleada_servicios: list[dict] = field(default_factory=list)
    usuarios: list[dict] = field(default_factory=list)
    bot: list[dict] = field(default_factory=list)
    cuenta: dict = field(default_factory=dict)
    correcciones_fichaje: list[dict] = field(default_factory=list)
    correcciones_caja: list[dict] = field(default_factory=list)

    def filas(self) -> int:
        return sum(len(v) for v in vars(self).values() if isinstance(v, list))


def dichos_de_recordatorio(d: Datos) -> list[tuple[str, dict, str]]:
    """LO QUE SÍ SE GUARDA de lo que cada clienta ha dicho de los recordatorios:
    la baja desde el pie de un correo y el «no» al asistente de WhatsApp (0037).
    (cuándo, la clienta, qué dijo), del más antiguo al más reciente. No hay
    consentimientos firmados: la aplicación no los recoge (19-10-2026)."""
    clienta = {c["id"]: c for c in d.clientas}
    return sorted(
        [(c["recordatorios_baja_at"], c, "Se dio de baja de los recordatorios por correo")
         for c in d.clientas if c.get("recordatorios_baja_at")]
        + [(r["recordatorios_email_rechazado_at"], clienta.get(r["id"]) or r,
            "Dijo que no cuando el asistente de WhatsApp le ofreció recordatorios por correo")
           for r in d.rechazos_recordatorio if r.get("recordatorios_email_rechazado_at")],
        key=lambda x: x[0],
    )


def construir_zip(d: Datos, generado: datetime) -> bytes:
    centro = {c["id"]: c.get("nombre_comercial") or "" for c in d.centros}
    region = {c["id"]: region_del_salon(c.get("telefono")) for c in d.centros}
    clienta = {c["id"]: c for c in d.clientas}
    servicio = {s["id"]: s for s in d.servicios}
    categoria = {c["id"]: c.get("nombre") or "" for c in d.categorias}
    empleada = {e["id"]: e.get("nombre") or "" for e in d.empleadas}
    puesto = {p["id"]: p.get("nombre") or "" for p in d.puestos}
    producto = {p["id"]: p.get("nombre") or "" for p in d.productos}
    conversacion = {c["id"]: c for c in d.conversaciones}
    centro_de_empleada = {e["id"]: e.get("salon_id") for e in d.empleadas}
    proveedor = {p["id"]: p.get("nombre") or "" for p in d.proveedores}
    bot = {b.get("salon_id"): b for b in d.bot}
    servicios_de: dict[str, list[str]] = {}
    for es in d.empleada_servicios:
        nombre = (servicio.get(es.get("service_id")) or {}).get("nombre")
        if nombre:
            servicios_de.setdefault(es.get("employee_id"), []).append(nombre)

    def c_de(f):
        return texto(centro.get(f.get("salon_id"), ""))

    # La zona de cada centro (0060): una hora de Las Palmas no sale en la de
    # Madrid porque la cuenta tenga también un local en Madrid.
    zona_de_centro = {c["id"]: zonas.zona(c.get("zona_horaria")) for c in d.centros}

    def z_de(f):
        return zona_de_centro[f.get("salon_id") or f.get("id")]

    def r_de(f):
        return region.get(f.get("salon_id"), "ES")

    ficheros: dict[str, bytes] = {}

    ficheros["centros.csv"] = tabla_csv(
        ["Centro", "Dirección", "Teléfono", "Email", "Alta", "Titular", "Identificación fiscal",
         "Reservas online", "Recordatorios por email", "Antelación del recordatorio (horas)",
         "Tono del asistente de WhatsApp", "Normas del asistente de WhatsApp"],
        [
            [texto(c.get("nombre_comercial")), texto(c.get("direccion")),
             telefono(c.get("telefono"), region[c["id"]]), texto(c.get("email")), fecha(c.get("created_at"), z_de(c)),
             texto(d.cuenta.get("nombre")), texto(d.cuenta.get("cif")),
             si_no(c.get("reservas_online_activo")), si_no(c.get("recordatorios_email_activo")),
             numero(c.get("recordatorio_horas")), texto((bot.get(c["id"]) or {}).get("bot_tono")),
             texto((bot.get(c["id"]) or {}).get("bot_politicas"))]
            for c in d.centros
        ],
    )

    ficheros["clientas.csv"] = tabla_csv(
        ["Centro", "Nombre", "Teléfono", "Email", "Cumpleaños", "Alergias", "Notas", "VIP",
         "Total gastado (€)", "Visitas", "Alta", "Recordatorios por email", "Id"],
        [
            [c_de(c), texto(c.get("nombre")), telefono(c.get("telefono"), r_de(c)), texto(c.get("email")),
             fecha(c.get("cumpleanos"), z_de(c)), texto(c.get("alergias")), texto(c.get("notas")), si_no(c.get("vip")),
             importe(c.get("total_gastado")), numero(c.get("visitas")), fecha(c.get("created_at"), z_de(c)),
             f"Se dio de baja el {fecha(c['recordatorios_baja_at'], z_de(c))}" if c.get("recordatorios_baja_at") else "",
             c["id"]]
            for c in d.clientas
        ],
    )

    def nombre_clienta(cita):
        c = clienta.get(cita.get("client_id"))
        return texto(c.get("nombre") if c else cita.get("nombre_reserva"))

    ficheros["citas.csv"] = tabla_csv(
        ["Centro", "Inicio", "Fin", "Estado", "Clienta", "Servicio", "Empleada", "Canal", "Notas",
         "Creada", "Id", "Id clienta"],
        [
            [c_de(a), fecha_hora(a.get("inicio"), z_de(a)), fecha_hora(a.get("fin"), z_de(a)), enum(a.get("estado"), ESTADO_CITA),
             nombre_clienta(a), texto((servicio.get(a.get("service_id")) or {}).get("nombre")),
             texto(empleada.get(a.get("employee_id"))), enum(a.get("canal"), CANAL_CITA), texto(a.get("notas")),
             fecha_hora(a.get("created_at"), z_de(a)), a["id"], a.get("client_id") or ""]
            for a in d.citas
        ],
    )

    ficheros["servicios.csv"] = tabla_csv(
        ["Centro", "Servicio", "Categoría", "Precio (€)", "Duración (min)", "Reservable online", "Id"],
        [
            [c_de(s), texto(s.get("nombre")), texto(categoria.get(s.get("category_id"))), importe(s.get("precio")),
             numero(s.get("duracion_min")), si_no(s.get("visible_online")), s["id"]]
            for s in d.servicios
        ],
    )

    ficheros["empleadas.csv"] = tabla_csv(
        ["Centro", "Nombre", "Puesto", "Teléfono", "Email", "Estado", "Tipo de comisión",
         "Comisión en servicios", "Comisión en productos", "Servicios que hace", "Id"],
        [
            [c_de(e), texto(e.get("nombre")), texto(puesto.get(e.get("puesto_id"))),
             telefono(e.get("telefono"), r_de(e)), texto(e.get("email")), enum(e.get("estado"), ESTADO_EMPLEADA),
             enum(e.get("comision_tipo"), TIPO_COMISION), numero(e.get("comision_servicios")),
             numero(e.get("comision_productos")), texto(", ".join(sorted(servicios_de.get(e["id"], [])))),
             e["id"]]
            for e in d.empleadas
        ],
    )

    ficheros["fichajes.csv"] = tabla_csv(
        ["Centro", "Empleada", "Tipo", "Fecha y hora", "Id empleada"],
        [
            [c_de(t), texto(empleada.get(t.get("employee_id"))), enum(t.get("tipo"), TIPO_FICHAJE),
             fecha_hora(t.get("ts"), z_de(t)), t.get("employee_id") or ""]
            for t in d.fichajes
        ],
    )

    # LO QUE SE CORRIGIÓ (0061), aparte de lo que se fichó: `fichajes.csv` es lo
    # que marcó el reloj, tal cual, y esto es cada corrección con su motivo.
    ficheros["correcciones_de_fichaje.csv"] = tabla_csv(
        ["Centro", "Empleada", "Corregido el", "Quién corrigió", "Qué", "Tipo", "Antes", "Después", "Motivo",
         "Sus propias horas", "Id marca"],
        [
            [c_de(c), texto(empleada.get(c.get("employee_id")) or c.get("empleada_nombre")),
             fecha_hora(c.get("ocurrido_at"), z_de(c)), texto(c.get("usuario_nombre") or c.get("usuario_email")),
             enum(c.get("accion"), ACCION_CORRECCION), enum(c.get("tipo"), TIPO_FICHAJE),
             fecha_hora(c.get("ts_antes"), z_de(c)) if c.get("ts_antes") else "",
             fecha_hora(c.get("ts_despues"), z_de(c)) if c.get("ts_despues") else "",
             texto(c.get("motivo")), "Sí" if c.get("sobre_si_misma") else "No", c.get("marca_id") or ""]
            for c in d.correcciones_fichaje
        ],
    )

    ficheros["ventas.csv"] = tabla_csv(
        ["Centro", "Fecha", "Concepto", "Tipo", "Producto", "Cantidad", "Importe (€)", "Anulado el",
         "Motivo de la anulación", "Id"],
        [
            [c_de(v), fecha(v.get("fecha"), z_de(v)), texto(v.get("concepto")), enum(v.get("tipo"), TIPO_VENTA),
             texto(producto.get(v.get("product_id"))), numero(v.get("cantidad")), importe(v.get("importe")),
             fecha_hora(v.get("anulado_at"), z_de(v)) if v.get("anulado_at") else "",
             texto(v.get("anulado_motivo")), v.get("id") or ""]
            for v in d.ventas
        ],
    )

    ficheros["stock.csv"] = tabla_csv(
        ["Centro", "Producto", "Marca", "Código de barras", "Categoría", "Precio de venta (€)",
         "Precio de coste (€)", "Stock", "Stock mínimo", "Se vende al público", "Consumo interno"],
        [
            [c_de(p), texto(p.get("nombre")), texto(p.get("marca")), texto(p.get("codigo_barras")),
             texto(categoria.get(p.get("category_id"))), importe(p.get("precio")), importe(p.get("precio_coste")),
             numero(p.get("stock")), numero(p.get("stock_min")), si_no(p.get("venta_publico")),
             si_no(p.get("consumo_interno"))]
            for p in d.productos
        ],
    )

    def de_la_conversacion(m, campo):
        return (conversacion.get(m.get("session_id")) or {}).get(campo)

    ficheros["conversaciones.csv"] = tabla_csv(
        ["Centro", "Conversación", "Teléfono de la clienta", "Fecha y hora", "Quién escribe", "Mensaje"],
        [
            [c_de(m), m.get("session_id") or "", telefono(de_la_conversacion(m, "customer_phone"), r_de(m)),
             fecha_hora(m.get("created_at"), z_de(m)), enum(m.get("role"), QUIEN_ESCRIBE), texto(m.get("content"))]
            for m in d.mensajes
            if m.get("role") in QUIEN_ESCRIBE
        ],
    )

    ficheros["recordatorios.csv"] = tabla_csv(
        ["Centro", "Clienta", "Qué dijo", "Cuándo", "Id clienta"],
        [[c_de(c), texto(c.get("nombre")), que, fecha_hora(cuando, z_de(c)), c.get("id") or ""]
         for cuando, c, que in dichos_de_recordatorio(d)],
    )

    ficheros["gastos.csv"] = tabla_csv(
        ["Centro", "Fecha", "Categoría", "Proveedor", "Tipo", "Estado", "Importe (€)", "Producto", "Cantidad",
         "Anulado el", "Motivo de la anulación", "Id"],
        [
            [c_de(g), fecha(g.get("fecha"), z_de(g)), texto(g.get("categoria")), texto(proveedor.get(g.get("provider_id"))),
             enum(g.get("tipo"), TIPO_GASTO), enum(g.get("estado"), ESTADO_GASTO), importe(g.get("importe")),
             texto(producto.get(g.get("product_id"))), numero(g.get("cantidad")) if g.get("cantidad") else "",
             fecha_hora(g.get("anulado_at"), z_de(g)) if g.get("anulado_at") else "",
             texto(g.get("anulado_motivo")), g.get("id") or ""]
            for g in d.gastos
        ],
    )

    # LO QUE SE CORRIGIÓ O ANULÓ EN CAJA (0092): cada cambio con su motivo y lo
    # que pasó con el stock. Si el stock no se movió, dice por qué.
    ficheros["correcciones_de_caja.csv"] = tabla_csv(
        ["Centro", "Cuándo", "Quién", "Qué", "Movimiento", "Antes", "Después", "Motivo", "Stock",
         "Id movimiento"],
        [
            [c_de(c), fecha_hora(c.get("ocurrido_at"), z_de(c)), texto(c.get("usuario_nombre") or c.get("usuario_email")),
             "Anulación" if c.get("accion") == "anula" else "Corrección",
             "Ingreso" if c.get("movimiento") == "ingreso" else "Gasto",
             texto(_json_legible(c.get("antes"))), texto(_json_legible(c.get("despues"))), texto(c.get("motivo")),
             texto(_stock_legible(c)), c.get("movimiento_id") or ""]
            for c in d.correcciones_caja
        ],
    )

    ficheros["proveedores.csv"] = tabla_csv(
        ["Centro", "Proveedor", "Categoría", "Teléfono", "Email", "Notas", "Alta"],
        [
            [c_de(p), texto(p.get("nombre")), texto(p.get("categoria")), telefono(p.get("telefono"), r_de(p)),
             texto(p.get("email")), texto(p.get("notas")), fecha(p.get("created_at"), z_de(p))]
            for p in d.proveedores
        ],
    )

    def franja(h) -> list[str]:
        cerrado = bool(h.get("cerrado"))
        pausa = (f"{hora(h.get('pausa_inicio'))}–{hora(h.get('pausa_fin'))}"
                 if h.get("tiene_pausa") and not cerrado else "")
        return [si_no(cerrado), "" if cerrado else hora(h.get("apertura")),
                "" if cerrado else hora(h.get("cierre")), pausa]

    def fechas(h) -> str:
        # Días de calendario del centro: no se convierten.
        ini, fin = fecha(h.get("fecha_inicio")), fecha(h.get("fecha_fin"))
        return ini if not fin or fin == ini else f"Del {ini} al {fin}"

    horarios: list[list[str]] = []
    for h in d.horario_centro:
        horarios.append([c_de(h), "El centro", "Semanal", DIA.get(h.get("dia_semana"), ""), *franja(h), ""])
    for h in d.excepciones_centro:
        horarios.append([c_de(h), "El centro", "Excepción", fechas(h), *franja(h), texto(h.get("motivo"))])
    for h in d.horario_empleadas:
        salon = {"salon_id": centro_de_empleada.get(h.get("employee_id"))}
        horarios.append([c_de(salon), texto(empleada.get(h.get("employee_id"))), "Semanal",
                         DIA.get(h.get("dia_semana"), ""), *franja(h), ""])
    for h in d.excepciones_empleadas:
        salon = {"salon_id": centro_de_empleada.get(h.get("employee_id"))}
        horarios.append([c_de(salon), texto(empleada.get(h.get("employee_id"))), "Excepción", fechas(h),
                         *franja(h), texto(h.get("motivo"))])
    ficheros["horarios.csv"] = tabla_csv(
        ["Centro", "De quién", "Tipo", "Día o fechas", "Cerrado", "Abre", "Cierra", "Pausa", "Motivo"], horarios
    )

    # La administradora entra en todos los centros de su cuenta: es la única
    # que tiene usuario (0069). Las columnas se quedan, para no cambiar el fichero.
    ficheros["usuarios.csv"] = tabla_csv(
        ["Nombre", "Email", "Rol", "Centros a los que entra", "Alta"],
        [
            [texto(u.get("nombre")), texto(u.get("email")), enum(u.get("rol"), ROL),
             "Todos",
             fecha(u.get("created_at"), z_de(u))]
            for u in d.usuarios
        ],
    )

    leeme = leeme_txt(d, generado)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("LEEME.txt", leeme.encode("utf-8-sig"))
        for nombre, contenido in ficheros.items():
            z.writestr(nombre, contenido)
    return buf.getvalue()


def leeme_txt(d: Datos, generado: datetime) -> str:
    centros = ", ".join(c.get("nombre_comercial") or "(sin nombre)" for c in d.centros)
    # La hora de la descarga, en la zona de los centros si todos comparten una;
    # si la cuenta tiene centros en las dos, en UTC y dicho.
    zonas_de_la_cuenta = {c.get("zona_horaria") for c in d.centros}
    if len(zonas_de_la_cuenta) == 1:
        nombre_zona = next(iter(zonas_de_la_cuenta))
        cuando = f"{fecha_hora(generado, zonas.zona(nombre_zona))} (hora de {zonas.ZONAS[nombre_zona]})"
    else:
        cuando = zonas.utc_texto(generado)
    lineas = [
        "TUS DATOS DE ESTETIXAPP",
        "",
        f"Descargados el {cuando}.",
        f"Centros incluidos: {centros}.",
        "",
        "Cada fichero .csv es una tabla. Se abre con doble clic en Excel, en",
        "LibreOffice o en Numbers. En todos hay una columna «Centro» que dice a",
        "qué centro pertenece cada fila.",
        "",
        "QUÉ HAY EN CADA FICHERO",
        "",
        "centros.csv — tus centros, con su dirección, su teléfono, tu identificación",
        f"    fiscal y cómo tienes configuradas las reservas y los recordatorios. ({len(d.centros)})",
        "clientas.csv — cada clienta, con sus datos de contacto, alergias, notas,",
        f"    lo que lleva gastado y sus visitas. ({len(d.clientas)})",
        "citas.csv — todas las citas, pasadas y futuras, con la clienta, el servicio,",
        f"    la empleada y el estado. ({len(d.citas)})",
        f"servicios.csv — tu carta de servicios, con precio y duración. ({len(d.servicios)})",
        "empleadas.csv — tu equipo, con su puesto, sus comisiones y los servicios",
        f"    que hace cada una. ({len(d.empleadas)})",
        f"fichajes.csv — cada entrada, pausa y salida que se ha fichado. ({len(d.fichajes)})",
        "correcciones_de_fichaje.csv — cada corrección de un fichaje: quién, cuándo,",
        f"    qué había, qué hay y por qué. Lo fichado no cambia. ({len(d.correcciones_fichaje)})",
        f"ventas.csv — los ingresos apuntados en Caja, con los anulados. ({len(d.ventas)})",
        f"stock.csv — tus productos, con precio y existencias. ({len(d.productos)})",
        f"gastos.csv — los gastos apuntados, con su proveedor, si están pagados y los anulados. ({len(d.gastos)})",
        "correcciones_de_caja.csv — cada corrección o anulación de un ingreso o un gasto:",
        f"    quién, cuándo, qué había, qué hay, por qué y qué pasó con el stock. ({len(d.correcciones_caja)})",
        f"proveedores.csv — tus proveedores y sus datos de contacto. ({len(d.proveedores)})",
        "recordatorios.csv — qué clientas no quieren recordatorios por correo, y desde",
        "    cuándo: las que se dieron de baja desde un correo y las que dijeron que",
        f"    no cuando el asistente de WhatsApp se lo ofreció. ({len(dichos_de_recordatorio(d))})",
        "horarios.csv — el horario de cada centro y de cada empleada, semana a",
        f"    semana, y sus excepciones (festivos, vacaciones, cierres). ({len(d.horario_centro) + len(d.excepciones_centro) + len(d.horario_empleadas) + len(d.excepciones_empleadas)})",
        "usuarios.csv — las personas que entran en EstetixApp con tu cuenta, su rol",
        f"    y a qué centros entran. Sin contraseñas. ({len(d.usuarios)})",
        "conversaciones.csv — los mensajes de WhatsApp con tus clientas: lo que",
        "    escribió la clienta, lo que respondió el asistente y lo que respondió",
        "    el salón. Cada conversación tiene su número para poder seguirla.",
        "",
        "LAS COLUMNAS «Id»",
        "",
        "Son el identificador de cada fila en EstetixApp. Sirven para relacionar",
        "ficheros: el «Id clienta» de una cita es el «Id» de esa clienta en",
        "clientas.csv. Si solo quieres leer los datos, puedes ignorarlas.",
        "",
        "SI UNA CELDA EMPIEZA POR UN APÓSTROFO (')",
        "",
        "Es a propósito. Un texto que empieza por = + - o @ Excel lo trataría",
        "como una fórmula y lo ejecutaría. Con el apóstrofo delante se queda como",
        "texto. El dato original es lo que va detrás del apóstrofo.",
        "",
        "LO QUE NO ESTÁ AQUÍ",
        "",
        "No incluimos contraseñas, PIN ni claves de acceso, ni los datos de pago",
        "de tu suscripción (tus facturas están en tu área de suscripción), ni los",
        "registros internos de EstetixApp.",
        "",
        "Estos datos incluyen información personal de tus clientas y de tu",
        "equipo. Guárdalos en un sitio seguro.",
    ]
    return "\r\n".join(lineas) + "\r\n"
