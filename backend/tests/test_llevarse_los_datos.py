"""LLEVARSE LOS DATOS: el ZIP de la cuenta (`routers/datos.py`, `core/volcado.py`).

  1. EL FORMATO QUE ABRE EXCEL: UTF-16 con BOM y tabulador, acentos intactos,
     coma decimal, fechas en hora de Madrid, teléfonos que siguen siendo texto
     y ninguna celda que Excel pueda ejecutar como fórmula.
  2. LO QUE NO SALE: aunque la base devolviera columnas secretas (hashes,
     tokens, Stripe, la caja negra del bot), el ZIP no las lleva. Se prueba
     con una base que las devuelve TODAS, no fiándose de la lista.
  3. LA CONTRASEÑA, SIEMPRE, contra el servicio de autenticación y con el
     contador del PIN; nunca en un mensaje ni en un log; una mal puesta no
     gasta cupo. Y el rastro (0056): entregadas y rechazadas.
  4. EL LÍMITE: tres por hora y cuenta, una a la vez.
  5. TODO LO DEL SALÓN: recordatorios (quién no los quiere), gastos, proveedores, horarios,
     usuarios, y los servicios de cada empleada.
"""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.core import volcado
from app.core.config import get_settings
from app.core.contrasena import Resultado, comprobar_contrasena
from app.core.security import (
    CurrentUser,
    get_current_user_aunque_suspendida,
    get_postgrest_client_aunque_suspendida,
    get_service_postgrest_client,
)
from app.core.volcado import Datos, construir_zip
from app.routers import datos

C1 = "11111111-1111-1111-1111-111111111111"
C2 = "22222222-2222-2222-2222-222222222222"
CUENTA = "acc-1"

SECRETOS = {
    "pin_fichaje_hash": "$2b$12$HASHDEPINQUENOPUEDESALIR",
    "recordatorios_token": "token-baja-recordatorios-secreto",
    "telefono_digitos": "34600111222",
    "stripe_customer_id": "cus_SECRETO",
    "payload": {"thought_signature": "FIRMA-INTERNA-DE-GEMINI"},
    "wa_message_id": "wamid.SECRETO",
    "context_summary": "RESUMEN-INTERNO-DEL-BOT",
    "pin_hash": "$2b$12$PINDEGESTION",
}


def _filas() -> dict[str, list[dict]]:
    """Lo que devolvería la base, CON columnas que nunca deben salir."""
    s = SECRETOS
    return {
        "salons": [
            {"zona_horaria": "Europe/Madrid", "id": C1, "nombre_comercial": "Peluquería Ñandú", "direccion": "Calle Mayor 1", "telefono": "+34600111222",
             "email": "hola@nandu.es", "created_at": "2025-01-10T09:00:00+00:00", "account_id": CUENTA, **s},
            {"zona_horaria": "Europe/Madrid", "id": C2, "nombre_comercial": "Estética Ñandú Norte", "direccion": "Avenida 4", "telefono": "+34600333444",
             "email": None, "created_at": "2026-02-01T09:00:00+00:00", "account_id": CUENTA, **s},
        ],
        # Las clientas se leen de la vista (0058): visitas y gastado ya calculados.
        "clientas_con_actividad": [
            {"id": "cl-1", "salon_id": C1, "nombre": "María José Güell", "telefono": "+34600555666",
             "email": "mj@correo.es", "cumpleanos": "1990-03-04", "alergias": "Látex", "notas": "Prefiere la tarde",
             "vip": True, "total_gastado": 1234.5, "visitas": 12, "created_at": "2025-02-01T10:00:00+00:00",
             "recordatorios_baja_at": None, **s},
            {"id": "cl-2", "salon_id": C2, "nombre": "=HIPERVINCULO(\"http://malo\")", "telefono": None,
             "email": None, "cumpleanos": None, "alergias": None, "notas": "línea 1\nlínea 2", "vip": False,
             "total_gastado": 0, "visitas": 0, "created_at": "2026-03-01T10:00:00+00:00",
             "recordatorios_baja_at": "2026-04-01T10:00:00+00:00", **s},
        ],
        "appointments": [
            {"id": "ap-1", "salon_id": C1, "client_id": "cl-1", "employee_id": "em-1", "service_id": "sv-1",
             "inicio": "2026-07-15T08:30:00+00:00", "fin": "2026-07-15T09:30:00+00:00", "estado": "no_show",
             "notas": None, "canal": "whatsapp", "created_at": "2026-07-01T10:00:00+00:00", "nombre_reserva": None,
             "recordatorio_error": "SMTP-INTERNO", **s},
        ],
        "services": [
            {"id": "sv-1", "salon_id": C1, "category_id": "ca-1", "nombre": "Corte y peinado", "precio": 25,
             "duracion_min": 45, "visible_online": True, **s},
        ],
        "service_categories": [{"id": "ca-1", "salon_id": C1, "nombre": "Peluquería", **s}],
        "employees": [
            {"id": "em-1", "salon_id": C1, "nombre": "Begoña", "puesto_id": "pu-1", "telefono": "+34600777888",
             "email": None, "estado": "activo", "comision_tipo": "porcentaje", "comision_servicios": 12.5,
             "comision_productos": 5, "user_id": "user-interno", **s},
        ],
        "puestos": [{"id": "pu-1", "salon_id": C1, "nombre": "Estilista", **s}],
        "time_entries": [{"id": "te-1", "salon_id": C1, "employee_id": "em-1", "tipo": "entrada",
                          "ts": "2026-01-15T08:00:00+00:00", **s}],
        "correcciones_de_fichaje": [{"id": 1, "salon_id": C1, "employee_id": "em-1",
                                     "ocurrido_at": "2026-01-16T09:00:00+00:00", "accion": "nueva",
                                     "marca_id": "mc-1", "tipo": "salida", "ts_antes": None,
                                     "ts_despues": "2026-01-15T16:00:00+00:00",
                                     "motivo": "Se fue a las 17:00 y no fichó la salida",
                                     "usuario_nombre": "Íñigo Ñúñez", "usuario_email": "i@n.es",
                                     "empleada_nombre": "x", "sobre_si_misma": False, **s}],
        "incomes": [{"id": "in-1", "salon_id": C1, "fecha": "2026-07-15", "concepto": "-descuento aplicado",
                     "tipo": "producto", "importe": -3.5, "product_id": "pr-1", "cantidad": 1, **s}],
        "products": [{"id": "pr-1", "salon_id": C1, "category_id": "ca-1", "nombre": "Champú", "marca": "Olé",
                      "codigo_barras": "8410000000001", "precio": 12.9, "precio_coste": 6, "stock": 4,
                      "stock_min": 2, "venta_publico": True, "consumo_interno": False, **s}],
        "bot_sessions": [{"id": "bs-1", "salon_id": C1, "customer_phone": "34600555666", **s}],
        "bot_messages": [
            {"id": "m1", "salon_id": C1, "session_id": "bs-1", "role": "user", "content": "¿Tenéis hueco mañana?",
             "created_at": "2026-07-14T18:00:00+00:00", **s},
            {"id": "m2", "salon_id": C1, "session_id": "bs-1", "role": "tool", "content": "{\"huecos\": []}",
             "created_at": "2026-07-14T18:00:01+00:00", **s},
            {"id": "m3", "salon_id": C1, "session_id": "bs-1", "role": "model", "content": "Sí, a las 10:30.",
             "created_at": "2026-07-14T18:00:02+00:00", **s},
        ],
        # Quién dijo «no» al asistente (0037). La baja del correo va con la clienta (cl-2).
        "clients": [{"id": "cl-1", "salon_id": C1, "recordatorios_email_rechazado_at": "2026-05-02T09:15:00+00:00",
                     **s}],
        "expenses": [{"id": "ex-1", "salon_id": C2, "fecha": "2026-06-30", "categoria": "Alquiler",
                      "provider_id": "pv-1", "tipo": "fijo", "estado": "pagado", "importe": 950,
                      **s}],
        "providers": [{"id": "pv-1", "salon_id": C2, "nombre": "Inmobiliaria Íñiguez", "categoria": "Local",
                       "telefono": "+34944123456", "email": None, "notas": None,
                       "created_at": "2026-02-01T09:00:00+00:00", **s}],
        "salon_schedule": [{"id": "ss-1", "salon_id": C1, "dia_semana": 6, "cerrado": False, "apertura": "09:30:00",
                            "cierre": "14:00:00", "tiene_pausa": False, "pausa_inicio": None, "pausa_fin": None, **s}],
        "salon_schedule_exceptions": [{"id": "se-1", "salon_id": C1, "fecha_inicio": "2026-08-10",
                                       "fecha_fin": "2026-08-24", "cerrado": True, "apertura": None, "cierre": None,
                                       "tiene_pausa": False, "pausa_inicio": None, "pausa_fin": None,
                                       "motivo": "Vacaciones de agosto", **s}],
        "employee_schedule": [{"id": "es-1", "employee_id": "em-1", "dia_semana": 1, "cerrado": False,
                               "apertura": "10:00:00", "cierre": "19:00:00", "tiene_pausa": True,
                               "pausa_inicio": "14:00:00", "pausa_fin": "15:00:00", **s}],
        "employee_schedule_exceptions": [],
        "employee_services": [{"employee_id": "em-1", "service_id": "sv-1", **s}],
        "users": [
            {"id": "us-1", "salon_id": C1, "nombre": "Dueña", "email": "duena@nandu.es", "rol": "admin",
             "created_at": "2025-01-10T09:00:00+00:00", **s},
        ],
        "salon_whatsapp_config": [{"id": "wa-1", "salon_id": C1, "bot_tono": "Cercano, de tú",
                                   "bot_politicas": "No damos cita los domingos", "phone_number_id": "PNID-SECRETO",
                                   **s}],
        "accounts": [{"nombre": "Ñandú SL", "cif": "B12345674", **s}],
    }


class BaseQueLoDevuelveTodo:
    """Devuelve todas las columnas aunque se pidan pocas: el volcado tiene que
    quedarse solo con las suyas. Y apunta qué se le pidió."""

    def __init__(self, filas=None):
        self.filas = filas or _filas()
        self.pedidos: list[tuple[str, dict]] = []
        self.registro: list[dict] = []  # descargas_de_datos (0056)
        self.fallar_registro = False

    async def select(self, tabla, params=None):
        params = dict(params or {})
        self.pedidos.append((tabla, params))
        if tabla == "salons" and params.get("id"):
            # El centro: su cuenta y su zona horaria (0060), como en la base.
            return [{"account_id": CUENTA, "zona_horaria": "Europe/Madrid"}]
        if tabla == "users" and params.get("auth_user_id"):
            return [{"id": "us-1"}]
        offset = int(params.get("offset", 0))
        return [dict(f) for f in self.filas.get(tabla, [])][offset:]

    async def insert(self, tabla, body, select=None):
        if tabla == "descargas_de_datos":
            if self.fallar_registro:
                from app.db.postgrest_client import PostgrestError

                raise PostgrestError(503, "sin base")
            self.registro.append(dict(body))
        return [body]


def _zip_de(base=None) -> zipfile.ZipFile:
    import asyncio

    base = base or BaseQueLoDevuelveTodo()
    d = asyncio.run(datos.leer_datos(base, CUENTA, base))
    return zipfile.ZipFile(io.BytesIO(construir_zip(d, datetime(2026, 9, 30, 10, 0, tzinfo=timezone.utc))))


def _tabla(z: zipfile.ZipFile, nombre: str) -> list[list[str]]:
    crudo = z.read(nombre)
    assert crudo[:2] == b"\xff\xfe", "sin BOM de UTF-16: Excel no acertaría los acentos"
    return list(csv.reader(io.StringIO(crudo.decode("utf-16")), delimiter="\t"))


# ═══ 1. El formato ════════════════════════════════════════════════════════


def test_estan_todos_los_ficheros_y_el_leeme():
    assert sorted(_zip_de().namelist()) == sorted([
        "LEEME.txt", "centros.csv", "clientas.csv", "citas.csv", "servicios.csv", "empleadas.csv",
        "fichajes.csv", "correcciones_de_fichaje.csv", "ventas.csv", "stock.csv", "conversaciones.csv",
        "recordatorios.csv", "gastos.csv", "correcciones_de_caja.csv", "proveedores.csv", "horarios.csv",
        "usuarios.csv",
    ])


def test_las_correcciones_de_fichaje_salen_aparte_y_en_palabras():
    """Lo fichado sale tal cual en fichajes.csv; lo corregido, aparte, con quién y por qué (0061)."""
    filas = _tabla(_zip_de(), "correcciones_de_fichaje.csv")
    fila = dict(zip(filas[0], filas[1]))
    assert (fila["Quién corrigió"], fila["Qué"], fila["Tipo"], fila["Antes"], fila["Después"]) == (
        "Íñigo Ñúñez", "Añadida", "Salida", "", "15/01/2026 17:00")
    assert fila["Motivo"] == "Se fue a las 17:00 y no fichó la salida"


def test_lo_nuevo_se_lee_en_palabras():
    z = _zip_de()
    recordatorios = _tabla(z, "recordatorios.csv")
    assert recordatorios == [
        ["Centro", "Clienta", "Qué dijo", "Cuándo", "Id clienta"],
        ["Estética Ñandú Norte", "'=HIPERVINCULO(\"http://malo\")", "Se dio de baja de los recordatorios por correo",
         "01/04/2026 12:00", "cl-2"],
        ["Peluquería Ñandú", "María José Güell",
         "Dijo que no cuando el asistente de WhatsApp le ofreció recordatorios por correo", "02/05/2026 11:15", "cl-1"],
    ]
    gasto = dict(zip(*_tabla(z, "gastos.csv")[:2]))
    assert (gasto["Centro"], gasto["Proveedor"], gasto["Tipo"], gasto["Estado"], gasto["Importe (€)"]) == (
        "Estética Ñandú Norte", "Inmobiliaria Íñiguez", "Fijo", "Pagado", "950,00")
    proveedor = dict(zip(*_tabla(z, "proveedores.csv")[:2]))
    assert proveedor["Teléfono"] == "+34 944 12 34 56"
    horarios = _tabla(z, "horarios.csv")
    assert horarios[0] == ["Centro", "De quién", "Tipo", "Día o fechas", "Cerrado", "Abre", "Cierra", "Pausa", "Motivo"]
    assert horarios[1:] == [
        ["Peluquería Ñandú", "El centro", "Semanal", "Sábado", "No", "09:30", "14:00", "", ""],
        ["Peluquería Ñandú", "El centro", "Excepción", "Del 10/08/2026 al 24/08/2026", "Sí", "", "", "",
         "Vacaciones de agosto"],
        ["Peluquería Ñandú", "Begoña", "Semanal", "Lunes", "No", "10:00", "19:00", "14:00–15:00", ""],
    ]
    usuarios = _tabla(z, "usuarios.csv")
    # Solo la administradora tiene usuario (0069), y entra en todos los centros.
    assert [(u[0], u[2], u[3]) for u in usuarios[1:]] == [("Dueña", "Administradora", "Todos")]
    empleada = dict(zip(*_tabla(z, "empleadas.csv")[:2]))
    assert empleada["Servicios que hace"] == "Corte y peinado"
    centro = dict(zip(*_tabla(z, "centros.csv")[:2]))
    assert (centro["Titular"], centro["Identificación fiscal"], centro["Tono del asistente de WhatsApp"]) == (
        "Ñandú SL", "B12345674", "Cercano, de tú")


def test_acentos_y_centro_en_cada_fila():
    filas = _tabla(_zip_de(), "clientas.csv")
    assert filas[0][:3] == ["Centro", "Nombre", "Teléfono"]
    assert [f[0] for f in filas[1:]] == ["Peluquería Ñandú", "Estética Ñandú Norte"]
    assert filas[1][1] == "María José Güell"


def test_numeros_con_coma_fechas_en_hora_de_madrid_y_telefonos_legibles():
    z = _zip_de()
    clienta = dict(zip(*_tabla(z, "clientas.csv")[:2]))
    assert clienta["Total gastado (€)"] == "1234,50"
    assert clienta["Cumpleaños"] == "04/03/1990"
    assert clienta["Teléfono"] == "+34 600 55 56 66"
    cita = dict(zip(*_tabla(z, "citas.csv")[:2]))
    assert cita["Inicio"] == "15/07/2026 10:30"  # 08:30 UTC en verano = 10:30 en Madrid
    assert (cita["Estado"], cita["Canal"], cita["Clienta"], cita["Servicio"], cita["Empleada"]) == (
        "No se presentó", "WhatsApp", "María José Güell", "Corte y peinado", "Begoña")
    fichaje = dict(zip(*_tabla(z, "fichajes.csv")[:2]))
    assert fichaje["Fecha y hora"] == "15/01/2026 09:00"  # invierno: +1


def test_ninguna_celda_empieza_por_algo_que_Excel_ejecute():
    z = _zip_de()
    for nombre in z.namelist():
        if not nombre.endswith(".csv"):
            continue
        for fila in _tabla(z, nombre)[1:]:
            for celda in fila:
                es_numero = celda.replace(",", "").replace("-", "", 1).isdigit()
                es_telefono = celda.startswith("+") and celda.replace(" ", "")[1:].isdigit()
                assert es_numero or es_telefono or not celda.startswith(("=", "+", "-", "@")), (nombre, celda)
    clienta = _tabla(z, "clientas.csv")[2]
    assert clienta[1] == "'=HIPERVINCULO(\"http://malo\")"
    venta = dict(zip(*_tabla(z, "ventas.csv")[:2]))
    assert (venta["Concepto"], venta["Importe (€)"]) == ("'-descuento aplicado", "-3,50")


def test_un_salto_de_linea_en_una_nota_no_rompe_la_fila():
    clienta = _tabla(_zip_de(), "clientas.csv")[2]
    assert clienta[6] == "línea 1\nlínea 2"


def test_la_conversacion_sin_la_caja_negra_del_bot():
    filas = _tabla(_zip_de(), "conversaciones.csv")
    assert [(f[4], f[5]) for f in filas[1:]] == [("Clienta", "¿Tenéis hueco mañana?"), ("Asistente", "Sí, a las 10:30.")]
    assert filas[1][2] == "+34 600 55 56 66"


def test_el_leeme_explica_cada_fichero():
    texto = _zip_de().read("LEEME.txt").decode("utf-8-sig")
    for f in ("centros.csv", "clientas.csv", "citas.csv", "servicios.csv", "empleadas.csv", "fichajes.csv",
              "ventas.csv", "stock.csv", "conversaciones.csv", "Peluquería Ñandú", "apóstrofo"):
        assert f in texto


# ═══ 2. Lo que no sale ════════════════════════════════════════════════════


def test_ningun_secreto_sale_aunque_la_base_lo_devuelva():
    z = _zip_de()
    todo = "".join(
        z.read(n).decode("utf-16" if n.endswith(".csv") else "utf-8-sig") for n in z.namelist()
    )
    for campo, valor in SECRETOS.items():
        buscado = valor["thought_signature"] if isinstance(valor, dict) else valor
        if campo == "telefono_digitos":
            continue  # el mismo número sale, legible, en la columna Teléfono
        assert buscado not in todo, campo
    assert "SMTP-INTERNO" not in todo and "user-interno" not in todo and "huecos" not in todo
    assert "PNID-SECRETO" not in todo


def test_se_pide_con_lista_de_columnas_y_nunca_con_asterisco():
    base = BaseQueLoDevuelveTodo()
    _zip_de(base)
    for tabla, params in base.pedidos:
        assert params.get("select") and "*" not in params["select"], tabla
        for prohibida in ("pin_fichaje_hash", "recordatorios_token", "payload", "stripe", "pin_hash"):
            assert prohibida not in params["select"], (tabla, prohibida)


def test_solo_se_leen_los_centros_de_la_cuenta():
    base = BaseQueLoDevuelveTodo()
    _zip_de(base)
    (salones,) = [p for t, p in base.pedidos if t == "salons" and "account_id" in p]
    assert salones["account_id"] == f"eq.{CUENTA}"
    por_cuenta = {"salons", "users", "accounts"}
    por_empleada = {"employee_schedule", "employee_schedule_exceptions", "employee_services"}
    for tabla, params in base.pedidos:
        if tabla in por_empleada:
            assert params["employee_id"] == "in.(em-1)", tabla
        elif tabla == "users":
            assert params["account_id"] == f"eq.{CUENTA}"
        elif tabla == "accounts":
            assert params["id"] == f"eq.{CUENTA}"
        elif tabla not in por_cuenta:
            assert params["salon_id"] == f"in.({C1},{C2})", tabla


def test_se_pagina_hasta_vaciar():
    muchas = _filas()
    muchas["time_entries"] = [
        {"id": f"te-{i:05d}", "salon_id": C1, "employee_id": "em-1", "tipo": "entrada",
         "ts": "2026-01-15T08:00:00+00:00"} for i in range(2500)
    ]

    class Paginada(BaseQueLoDevuelveTodo):
        async def select(self, tabla, params=None):
            filas = await super().select(tabla, params)
            return filas[: int((params or {}).get("limit", 10**9))]

    assert len(_tabla(_zip_de(Paginada(muchas)), "fichajes.csv")) == 2501


# ═══ 3 y 4. El endpoint: contraseña, rastro y límite ══════════════════════

CONTRASENA = "Una-Contraseña-De-Verdad-123"


@pytest.fixture()
def cliente(monkeypatch):
    estado = {"resultado": Resultado("ok"), "llamadas": []}

    async def comprobar(settings, servicio, account_id, auth_user_id, contrasena):
        estado["llamadas"].append((account_id, auth_user_id, contrasena))
        return estado["resultado"]

    monkeypatch.setattr(datos, "comprobar_contrasena", comprobar)
    monkeypatch.setattr(datos, "_cupo", datos.AdminLoginRateLimiter(max_fails=datos.POR_HORA, window_minutes=60))
    datos._en_curso.clear()

    base = BaseQueLoDevuelveTodo()
    app = FastAPI()
    app.include_router(datos.router)
    usuario = CurrentUser(sub="auth-1", salon_id=C1, rol="admin", cuenta_suspendida=True)
    app.dependency_overrides[get_current_user_aunque_suspendida] = lambda: usuario
    app.dependency_overrides[get_postgrest_client_aunque_suspendida] = lambda: base
    app.dependency_overrides[get_service_postgrest_client] = lambda: base
    app.dependency_overrides[get_settings] = lambda: None
    c = TestClient(app)
    c.estado, c.base, c.usuario = estado, base, usuario
    return c


def _bajar(c, cuerpo=None):
    return c.post("/cuenta/datos/descarga", json={"contrasena": CONTRASENA} if cuerpo is None else cuerpo)


def test_con_la_contrasena_buena_se_descarga_y_queda_rastro(cliente):
    assert cliente.get("/cuenta/datos").json() == {"centros": 2}
    r = _bajar(cliente)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert "horarios.csv" in zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    # Se comprueba la de ESTA sesión, en ESTA cuenta.
    assert cliente.estado["llamadas"] == [(CUENTA, "auth-1", CONTRASENA)]
    (fila,) = cliente.base.registro
    assert (fila["resultado"], fila["origen"], fila["usuario_id"], fila["centros"]) == (
        "entregada", "muro_suspension", "us-1", 2)
    assert fila["bytes"] == len(r.content) and fila["filas"] > 0
    assert CONTRASENA not in str(fila)


def test_sin_contrasena_no_hay_descarga(cliente):
    for cuerpo in ({}, {"contrasena": ""}, {"otra": "cosa"}):
        r = _bajar(cliente, cuerpo)
        assert r.status_code == 422 and r.json()["detail"]["codigo"] == "falta_contrasena"
    assert cliente.estado["llamadas"] == [] and cliente.base.registro == []


def test_la_contrasena_nunca_vuelve_en_un_error(cliente):
    """Un 422 de FastAPI devuelve lo recibido; aquí no pasa por un modelo."""
    raras = [12345, ["x"], {"a": "SECRETO-EN-UN-DICT"}, "S" * 500, "OtraContraseñaMuyLarga" * 20]
    for valor in raras:
        r = _bajar(cliente, {"contrasena": valor})
        assert r.status_code == 422
        texto = r.text
        assert "12345" not in texto and "SECRETO-EN-UN-DICT" not in texto and "SSSS" not in texto
        assert "OtraContraseña" not in texto
    r = cliente.post("/cuenta/datos/descarga", content=b'{"contrasena": "rota', headers={"content-type": "application/json"})
    assert r.status_code == 422 and "rota" not in r.text


def test_mal_puesta_se_rechaza_queda_rastro_y_no_gasta_cupo(cliente):
    cliente.estado["resultado"] = Resultado("incorrecta", intentos_restantes=3)
    r = _bajar(cliente)
    assert r.status_code == 422
    assert r.json()["detail"] == {"codigo": "contrasena_incorrecta", "mensaje": "La contraseña no es correcta.",
                                  "intentos_restantes": 3}
    assert CONTRASENA not in r.text
    assert [f["resultado"] for f in cliente.base.registro] == ["contrasena_incorrecta"]

    for _ in range(10):
        _bajar(cliente)
    cliente.estado["resultado"] = Resultado("ok")
    for _ in range(datos.POR_HORA):
        assert _bajar(cliente).status_code == 200


def test_en_espera_dice_cuanto_y_queda_rastro(cliente):
    hasta = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    cliente.estado["resultado"] = Resultado("en_espera", bloqueado_hasta=hasta)
    r = _bajar(cliente)
    assert r.status_code == 429
    assert r.json()["detail"]["codigo"] == "contrasena_en_espera"
    assert "Vuelve a probar en 5 minutos" in r.json()["detail"]["mensaje"]
    assert int(r.headers["retry-after"]) > 0
    assert [f["resultado"] for f in cliente.base.registro] == ["en_espera"]


def test_si_no_se_puede_comprobar_no_se_entrega_y_no_es_una_contrasena_mala(cliente):
    cliente.estado["resultado"] = Resultado("fallo")
    r = _bajar(cliente)
    assert r.status_code == 503 and "contraseña" in r.json()["detail"]
    assert cliente.base.registro == []


def test_sin_fila_en_el_registro_no_sale_el_zip(cliente):
    cliente.base.fallar_registro = True
    r = _bajar(cliente)
    assert r.status_code == 502
    assert r.headers["content-type"] != "application/zip"
    assert CUENTA not in datos._en_curso


def test_desde_la_app_el_origen_es_la_app(cliente):
    cliente.usuario.cuenta_suspendida = False
    assert _bajar(cliente).status_code == 200
    assert cliente.base.registro[0]["origen"] == "app"


def test_tres_por_hora_y_la_cuarta_dice_cuando(cliente):
    for _ in range(datos.POR_HORA):
        assert _bajar(cliente).status_code == 200
    r = _bajar(cliente)
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) > 0
    assert "3 veces en la última hora" in r.json()["detail"]


def test_una_a_la_vez_por_cuenta(cliente):
    datos._en_curso.add(CUENTA)
    try:
        r = _bajar(cliente)
        assert r.status_code == 429 and "Espera a que termine" in r.json()["detail"]
    finally:
        datos._en_curso.discard(CUENTA)


def test_si_falla_la_lectura_se_suelta_el_candado(cliente, monkeypatch):
    async def rota(db, cuenta, servicio=None):
        from app.db.postgrest_client import PostgrestError

        raise PostgrestError(503, "sin base")

    monkeypatch.setattr(datos, "leer_datos", rota)
    assert _bajar(cliente).status_code == 502
    assert CUENTA not in datos._en_curso


# ═══ La comprobación de la contraseña (`core/contrasena.py`) ══════════════


class ServicioContador:
    """El contador del PIN (0046), como lo contesta la base."""

    def __init__(self, permitido=True, fallos=1, bloqueado_hasta=None):
        self.respuesta = {"permitido": permitido, "fallos": fallos, "bloqueado_hasta": bloqueado_hasta}
        self.orden: list[str] = []

    async def rpc(self, funcion, cuerpo):
        self.orden.append(f"rpc:{funcion}")
        return [self.respuesta]

    async def delete(self, tabla, params):
        self.orden.append(f"delete:{tabla}")


@pytest.fixture()
def gotrue(monkeypatch):
    estado = {"email": "duena@nandu.es", "vigente": CONTRASENA, "pedidas": [], "caido": False}

    async def email_de(self, user_id):
        estado["pedidas"].append(("email", user_id))
        return estado["email"]

    async def contrasena_valida(self, email, password):
        if estado["caido"]:
            from app.db.supabase_auth_client import SupabaseAuthError

            raise SupabaseAuthError(500, "GoTrue no pudo comprobar la contraseña.")
        estado["pedidas"].append(("token", email))
        return password == estado["vigente"]

    from app.db.supabase_auth_client import SupabaseAuthAdminClient

    monkeypatch.setattr(SupabaseAuthAdminClient, "email_de", email_de)
    monkeypatch.setattr(SupabaseAuthAdminClient, "contrasena_valida", contrasena_valida)
    return estado


class _Ajustes:
    supabase_url = "http://gotrue.invalid"
    supabase_secret_key = "sb_secret_prueba"


def _comprobar(servicio, contrasena):
    import asyncio

    return asyncio.run(comprobar_contrasena(_Ajustes(), servicio, CUENTA, "auth-1", contrasena))


def test_se_pregunta_a_GoTrue_con_el_email_vigente_y_se_cuenta_antes(gotrue):
    s = ServicioContador()
    assert _comprobar(s, CONTRASENA) == Resultado("ok")
    assert gotrue["pedidas"] == [("email", "auth-1"), ("token", "duena@nandu.es")]
    assert s.orden == ["rpc:pin_gestion_intento", "delete:pin_attempt_locks"]


def test_el_dia_que_cambia_la_contrasena_la_nueva_vale_sola(gotrue):
    """No hay copia nuestra: vale la que tenga GoTrue en ese momento."""
    assert _comprobar(ServicioContador(), CONTRASENA).estado == "ok"
    gotrue["vigente"] = "La-Nueva-456"
    assert _comprobar(ServicioContador(), CONTRASENA).estado == "incorrecta"
    assert _comprobar(ServicioContador(), "La-Nueva-456").estado == "ok"


def test_mal_puesta_dice_cuantos_quedan_y_no_limpia_el_contador(gotrue):
    s = ServicioContador(fallos=3)
    assert _comprobar(s, "otra") == Resultado("incorrecta", intentos_restantes=2)
    assert "delete:pin_attempt_locks" not in s.orden


def test_en_espera_ni_se_pregunta_a_GoTrue(gotrue):
    s = ServicioContador(permitido=False, bloqueado_hasta="2026-10-01T10:05:00+00:00")
    assert _comprobar(s, CONTRASENA).estado == "en_espera"
    assert gotrue["pedidas"] == []


def test_el_quinto_fallo_abre_la_espera(gotrue):
    s = ServicioContador(fallos=5, bloqueado_hasta="2026-10-01T10:01:00+00:00")
    assert _comprobar(s, "otra").estado == "en_espera"


def test_si_GoTrue_falla_es_un_fallo_y_la_contrasena_no_va_al_log(gotrue, caplog):
    gotrue["caido"] = True
    caplog.set_level("DEBUG")
    assert _comprobar(ServicioContador(), CONTRASENA).estado == "fallo"
    assert CONTRASENA not in caplog.text


def test_la_contrasena_no_aparece_en_ningun_log(gotrue, caplog):
    caplog.set_level("DEBUG")
    _comprobar(ServicioContador(), CONTRASENA)
    _comprobar(ServicioContador(fallos=2), "Otra-Mala-999")
    assert CONTRASENA not in caplog.text and "Otra-Mala-999" not in caplog.text


# ═══ El guarda: todo camino que arma el ZIP deja fila ═════════════════════


def _funciones_que_arman(fuente: str) -> dict[str, set[str]]:
    import ast

    out = {}
    for n in ast.walk(ast.parse(fuente)):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # Todos los nombres que usa, no solo lo que llama: `construir_zip` va
            # como ARGUMENTO de `run_in_threadpool`, y una guarda que solo mirara
            # las llamadas no vería la descarga de verdad.
            llamadas = {x.id for x in ast.walk(n) if isinstance(x, ast.Name)} | {
                x.func.attr for x in ast.walk(n) if isinstance(x, ast.Call) and isinstance(x.func, ast.Attribute)}
            out[n.name] = llamadas
    return out


def _sin_rastro(fuentes: list[str]) -> list[str]:
    malas = []
    for fuente in fuentes:
        for nombre, llamadas in _funciones_que_arman(fuente).items():
            if "construir_zip" in llamadas and "apuntar_descarga" not in llamadas:
                malas.append(nombre)
    return malas


def test_todo_lo_que_arma_el_ZIP_en_app_deja_fila():
    from pathlib import Path

    app = Path(__file__).resolve().parents[1] / "app"
    fuentes = [f.read_text(encoding="utf-8") for f in app.rglob("*.py")]
    arman = [n for f in fuentes for n, ll in _funciones_que_arman(f).items() if "construir_zip" in ll]
    assert arman == ["descargar"], arman  # antivacío: si no lo viera, el verde no valdría nada
    assert _sin_rastro(fuentes) == []


def test_EL_CONTROL_una_descarga_nueva_sin_rastro_se_detecta():
    codigo = (
        "async def descarga_express(db):\n"
        "    return construir_zip(await leer_datos(db, 'x'), None)\n"
    )
    assert _sin_rastro([codigo]) == ["descarga_express"]


def test_celdas_sueltas():
    assert volcado.texto("@SUMA(A1)") == "'@SUMA(A1)"
    assert volcado.texto("Normal") == "Normal"
    assert volcado.importe(None) == ""
    assert volcado.importe("7") == "7,00"
    assert volcado.telefono("no es un número", "ES") == "no es un número"
    assert volcado.telefono("+no", "ES") == "'+no"
    assert volcado.fecha_hora(None) == ""
    assert Datos  # la forma pública del módulo


# ═══ El cliente de GoTrue: cómo lee cada respuesta ════════════════════════


def _gotrue_que_contesta(monkeypatch, codigo: int, cuerpo: dict):
    import httpx

    from app.db import supabase_auth_client as modulo

    pedidas: list[tuple[str, str]] = []

    def responder(req: httpx.Request) -> httpx.Response:
        pedidas.append((req.method, req.url.path))
        if req.url.path.endswith("/logout"):
            return httpx.Response(204)
        return httpx.Response(codigo, json=cuerpo)

    cliente = httpx.AsyncClient(transport=httpx.MockTransport(responder))
    monkeypatch.setattr(modulo, "cliente_compartido", lambda: cliente)
    return modulo.SupabaseAuthAdminClient("http://gotrue.invalid", "sb_secret_prueba"), pedidas


@pytest.mark.parametrize(
    ("codigo", "error_code"),
    [(200, None), (400, "invalid_credentials"), (400, "email_not_confirmed"),
     (422, "email_provider_disabled"), (429, "over_request_rate_limit"), (500, None)],
)
def test_GoTrue_solo_invalid_credentials_es_mala_y_lo_demas_un_fallo_sin_la_contrasena(monkeypatch, codigo, error_code):
    import asyncio

    from app.db.supabase_auth_client import SupabaseAuthError

    mala = codigo == 400 and error_code == "invalid_credentials"
    cuerpo = {"access_token": "tok"} if codigo == 200 else {"error_code": error_code, "msg": f"eco {CONTRASENA}"}
    auth, pedidas = _gotrue_que_contesta(monkeypatch, codigo, cuerpo)
    try:
        resultado = asyncio.run(auth.contrasena_valida("duena@nandu.es", CONTRASENA))
    except SupabaseAuthError as e:
        assert codigo != 200 and not mala
        assert CONTRASENA not in str(e) and CONTRASENA not in e.detail  # ni el eco del cuerpo
        return
    # Solo el 200 y el 400 son una respuesta sobre la contraseña; lo demás
    # (el login apagado, el límite de GoTrue, un 500) NO puede leerse como mala.
    assert codigo == 200 or mala, f"{codigo}/{error_code} se ha leído como contraseña {'buena' if resultado else 'mala'}"
    assert resultado is (codigo == 200)
    # Al acertar, la sesión que abrió GoTrue se cierra en el acto.
    assert (("POST", "/auth/v1/logout") in pedidas) is (codigo == 200)
