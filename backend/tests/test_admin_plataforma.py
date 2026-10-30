"""Métricas, facturación y auditoría del panel de plataforma.

Lo que se fija aquí, por orden de importancia:

  1. Que el MRR NO cuenta lo que no se está cobrando. Meter las pruebas o los
     impagados en el MRR es el error clásico que hace parecer que entra más
     dinero del que entra, y desde un panel de plataforma se toman decisiones
     con ese número.
  2. Que una suscripción cuyo importe no se puede saber se cuenta APARTE, no
     como cero euros. Un total corto y silencioso es peor que un total corto
     que avisa.
  3. Que el log de auditoría pagina de verdad y dice el total sin paginar.
  4. Que la lista del equipo no puede filtrar el hash de la contraseña ni el
     secreto TOTP: no se piden a la base de datos.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest
from starlette.testclient import TestClient

from app.core.admin_deps import get_admin_db
from app.main import app
from app.routers import admin_plataforma
from tests.conftest import ADMIN_ID, FakePostgrest, admin_cookie

SALON_A = "aaaaaaaa-0000-0000-0000-000000000001"
SALON_B = "bbbbbbbb-0000-0000-0000-000000000002"
SALON_C = "cccccccc-0000-0000-0000-000000000003"
SALON_D = "dddddddd-0000-0000-0000-000000000004"

# Reloj fijo: «altas este mes» y «la prueba termina en N días» dependen de la
# fecha, y un test que cambia de resultado según el día no prueba nada.
AHORA = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)


def _iso(dias: float) -> str:
    return (AHORA + timedelta(days=dias)).isoformat()


def _salon(id_, nombre, plan="pro", creado=None):
    return {
        "zona_horaria": "Europe/Madrid",
        "id": id_,
        "nombre_comercial": nombre,
        "plan": plan,
        "created_at": creado or "2025-01-10T09:00:00+00:00",
    }


def _sus(salon_id, plan, intervalo, estado, **extra):
    base = {
        "salon_id": salon_id,
        "plan": plan,
        "intervalo": intervalo,
        "estado": estado,
        "trial_ends_at": None,
        "current_period_end": None,
        "cancel_at_period_end": False,
    }
    base.update(extra)
    return base


def _datos() -> dict[str, list[dict]]:
    return {
        "salons": [
            # Alta de ESTE mes (junio de 2026).
            _salon(SALON_A, "Salón Demo", "pro", creado="2026-06-02T09:00:00+00:00"),
            _salon(SALON_B, "Barbería Norte", "pro"),
            _salon(SALON_C, "Glow Spa", "elite"),
            # Sin fila en subscriptions: nunca llegó a pagar.
            _salon(SALON_D, "Corte Real", "basico"),
        ],
        "subscriptions": [
            _sus(SALON_A, "pro", "mensual", "active", current_period_end=_iso(20)),
            _sus(SALON_B, "pro", "mensual", "past_due", current_period_end=_iso(-3)),
            _sus(SALON_C, "elite", "anual", "active", current_period_end=_iso(200)),
        ],
        "admin_users": [
            {
                "id": ADMIN_ID,
                "email": "owner@estetixapp.test",
                "nombre": "Owner Admin",
                "rol": "owner",
                "totp_enabled": True,
                "ultimo_acceso": "2026-06-15T11:00:00+00:00",
                # Estos DOS no deben salir nunca por la API.
                "password_hash": "$argon2id$secreto",
                "totp_secret": "JBSWY3DPEHPK3PXP",
            }
        ],
        # El panel lee la vista (0091); aquí cada fila es un apunte suelto.
        "admin_logs_vista": [
            {
                "id": f"log-{i}",
                "admin_id": ADMIN_ID,
                "accion": "impersonation_start" if i % 2 else "extend_trial",
                "target_salon_id": SALON_A if i % 2 else None,
                "detalle": {"dias": 7} if not i % 2 else {},
                "ip": "88.12.4.190",
                "timestamp": f"2026-06-{i + 1:02d}T10:00:00+00:00",
                "ultima": f"2026-06-{i + 1:02d}T10:00:00+00:00",
                "veces": 1,
                "resultado": None,
            }
            for i in range(12)
        ],
    }


@pytest.fixture()
def db() -> FakePostgrest:
    return FakePostgrest(_datos())


@pytest.fixture()
def client(db: FakePostgrest, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(admin_plataforma, "_ahora", lambda: AHORA)
    app.dependency_overrides[get_admin_db] = lambda: db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_admin_db, None)


def _get(client: TestClient, ruta: str, rol: str = "owner", **params):
    return client.get(ruta, cookies={"__Host-sa-access-token": admin_cookie(rol=rol)}, params=params)


# ══════════════════════════════════════════════════════════ métricas / MRR


def test_el_mrr_suma_solo_lo_que_se_esta_cobrando(client):
    m = _get(client, "/admin/metricas").json()
    # Pro mensual 39 + Elite anual 55/mes = 94. El past_due (pro, 39) queda
    # fuera, y el salón sin suscripción no aporta nada. El anual sigue contando
    # aunque ya no se venda: hay suscripciones vivas.
    assert m["mrr"] == 94.0
    assert m["arr"] == 1128.0


def test_una_prueba_no_es_ingreso(db, client):
    db.tables["subscriptions"].append(
        _sus(SALON_D, "elite", "mensual", "trialing", trial_ends_at=_iso(20))
    )
    m = _get(client, "/admin/metricas").json()

    assert m["mrr"] == 94.0          # los 79 del trial NO entran
    assert m["trials_activos"] == 1


def test_el_impago_va_aparte_y_no_al_mrr(client):
    m = _get(client, "/admin/metricas").json()
    assert m["mrr_en_riesgo"] == 39.0   # el past_due de Barbería Norte
    assert m["mrr"] == 94.0


def test_una_suscripcion_sin_intervalo_no_se_cuenta_como_cero(db, client):
    """El price de Stripe no está mapeado en las env, así que el webhook no
    pudo rellenar el intervalo. Suponerle uno inflaría o hundiría el MRR."""
    db.tables["subscriptions"].append(_sus(SALON_D, "pro", None, "active"))
    m = _get(client, "/admin/metricas").json()

    assert m["suscripciones_sin_tarifa"] == 1
    assert m["mrr"] == 94.0  # sigue sin sumar nada por ella
    # Pero el salón SÍ se cuenta: existe, solo que no sabemos cuánto aporta.
    pro = next(p for p in m["por_plan"] if p["plan"] == "pro")
    assert pro["salones"] == 2


def test_el_reparto_por_plan_suma_el_total(client):
    m = _get(client, "/admin/metricas").json()
    por_plan = {p["plan"]: p for p in m["por_plan"]}

    assert por_plan["pro"]["mrr"] == 39.0 and por_plan["pro"]["salones"] == 1
    assert por_plan["elite"]["mrr"] == 55.0 and por_plan["elite"]["salones"] == 1
    assert por_plan["basico"]["mrr"] == 0.0
    assert round(sum(p["mrr"] for p in m["por_plan"]), 2) == m["mrr"]
    assert round(sum(p["porcentaje"] for p in m["por_plan"])) == 100


def test_los_tres_planes_salen_siempre_y_en_orden(client):
    # Para que el donut no reordene sus colores cuando cambian los datos.
    m = _get(client, "/admin/metricas").json()
    assert [p["plan"] for p in m["por_plan"]] == ["basico", "pro", "elite"]


def test_sin_suscripciones_no_se_divide_entre_cero(db, client):
    db.tables["subscriptions"] = []
    m = _get(client, "/admin/metricas").json()

    assert m["mrr"] == 0.0
    assert all(p["porcentaje"] == 0.0 for p in m["por_plan"])


def test_altas_de_este_mes(client):
    m = _get(client, "/admin/metricas").json()
    # Solo Salón Demo (2 jun 2026); los demás son de 2025.
    assert m["altas_este_mes"] == 1
    assert m["salones_total"] == 4
    assert m["salones_de_pago"] == 2


# ══════════════════════════════════════════════════════════════════ alertas


def test_un_pago_fallido_genera_alerta(client):
    alertas = _get(client, "/admin/metricas").json()["alertas"]
    fallidos = [a for a in alertas if a["tipo"] == "pago_fallido"]

    assert len(fallidos) == 1
    assert fallidos[0]["salon"] == "Barbería Norte"
    assert fallidos[0]["salon_id"] == SALON_B  # para poder abrir su ficha


def test_una_prueba_que_termina_pronto_avisa(db, client):
    db.tables["subscriptions"].append(
        _sus(SALON_D, "pro", "mensual", "trialing", trial_ends_at=_iso(2))
    )
    alertas = _get(client, "/admin/metricas").json()["alertas"]
    trial = [a for a in alertas if a["tipo"] == "trial_termina"]

    assert len(trial) == 1
    assert "2 días" in trial[0]["detalle"]


def test_una_prueba_lejana_no_molesta(db, client):
    db.tables["subscriptions"].append(
        _sus(SALON_D, "pro", "mensual", "trialing", trial_ends_at=_iso(30))
    )
    alertas = _get(client, "/admin/metricas").json()["alertas"]
    assert [a for a in alertas if a["tipo"] == "trial_termina"] == []


def test_una_prueba_que_termina_hoy_lo_dice_asi(db, client):
    db.tables["subscriptions"].append(
        _sus(SALON_D, "pro", "mensual", "trialing", trial_ends_at=_iso(0.2))
    )
    trial = [a for a in _get(client, "/admin/metricas").json()["alertas"] if a["tipo"] == "trial_termina"]
    assert "termina hoy" in trial[0]["detalle"]


# ═════════════════════════════════════════════════════════════ facturación


def test_la_tabla_lista_todos_los_salones(client):
    f = _get(client, "/admin/facturacion").json()
    assert len(f["filas"]) == 4
    por_nombre = {x["salon"]: x for x in f["filas"]}

    assert por_nombre["Salón Demo"]["estado"] == "active"
    assert por_nombre["Salón Demo"]["euros_al_mes"] == 39.0
    assert por_nombre["Glow Spa"]["intervalo"] == "anual"
    assert por_nombre["Glow Spa"]["euros_al_mes"] == 55.0


def test_un_salon_sin_suscripcion_no_se_esconde(client):
    """«Este salón nunca ha llegado a pagar» es justo la fila que se quiere ver
    desde el panel de plataforma."""
    fila = next(x for x in _get(client, "/admin/facturacion").json()["filas"] if x["salon"] == "Corte Real")

    assert fila["estado"] is None
    assert fila["euros_al_mes"] is None
    assert fila["proximo_cobro"] is None


def test_la_facturacion_y_el_dashboard_dan_el_mismo_mrr(client):
    # Dos endpoints, un solo cálculo: si divergieran, el panel se contradiría
    # a sí mismo entre dos pantallas.
    assert _get(client, "/admin/facturacion").json()["mrr"] == _get(client, "/admin/metricas").json()["mrr"]


def test_el_proximo_cobro_sale_de_current_period_end(client):
    fila = next(x for x in _get(client, "/admin/facturacion").json()["filas"] if x["salon"] == "Salón Demo")
    assert fila["proximo_cobro"].startswith("2026-07-05")


# ════════════════════════════════════════════════════════════════════ logs


def test_los_logs_llegan_del_mas_reciente_al_mas_antiguo(client):
    filas = _get(client, "/admin/logs").json()["filas"]
    fechas = [f["timestamp"] for f in filas]
    assert fechas == sorted(fechas, reverse=True)


def test_los_logs_paginan_y_dicen_el_total_sin_paginar(client):
    primera = _get(client, "/admin/logs", limit=5).json()
    assert len(primera["filas"]) == 5
    assert primera["total"] == 12  # el total NO se recorta con el limit

    segunda = _get(client, "/admin/logs", limit=5, offset=5).json()
    assert len(segunda["filas"]) == 5
    # Sin solaparse con la primera página.
    assert {f["id"] for f in primera["filas"]} & {f["id"] for f in segunda["filas"]} == set()


def test_los_logs_se_pueden_filtrar_por_accion(client):
    r = _get(client, "/admin/logs", accion="extend_trial").json()
    assert r["total"] == 6
    assert {f["accion"] for f in r["filas"]} == {"extend_trial"}


def test_los_logs_se_pueden_filtrar_por_salon(client):
    r = _get(client, "/admin/logs", salon_id=SALON_A).json()
    assert r["total"] == 6
    assert {f["salon"] for f in r["filas"]} == {"Salón Demo"}


def test_el_log_dice_quien_y_sobre_quien(client):
    fila = next(f for f in _get(client, "/admin/logs").json()["filas"] if f["accion"] == "impersonation_start")
    # Ids resueltos a nombres: un panel de auditoría con UUIDs no se lee.
    assert fila["admin_email"] == "owner@estetixapp.test"
    assert fila["salon"] == "Salón Demo"
    assert fila["ip"] == "88.12.4.190"


def test_el_detalle_del_log_llega_entero(client):
    fila = next(f for f in _get(client, "/admin/logs").json()["filas"] if f["accion"] == "extend_trial")
    assert fila["detalle"] == {"dias": 7}


@pytest.mark.parametrize("limit", [0, 500])
def test_un_limit_fuera_de_rango_se_rechaza(client, limit):
    assert _get(client, "/admin/logs", limit=limit).status_code == 422


# ═══════════════════════════════════════════════════════════ configuración


def test_el_equipo_no_expone_ni_el_hash_ni_el_totp(client):
    r = _get(client, "/admin/configuracion")
    crudo = r.text

    assert r.status_code == 200
    # Lo que no se pide a la base de datos no se puede filtrar por descuido.
    assert "$argon2id" not in crudo
    assert "JBSWY3DPEHPK3PXP" not in crudo
    assert "password_hash" not in crudo
    assert "totp_secret" not in crudo


def test_el_equipo_trae_lo_necesario_para_pintarlo(client):
    equipo = _get(client, "/admin/configuracion").json()["equipo"]
    assert equipo[0]["email"] == "owner@estetixapp.test"
    assert equipo[0]["rol"] == "owner"
    assert equipo[0]["totp_enabled"] is True
    assert equipo[0]["ultimo_acceso"].startswith("2026-06-15")


def test_las_tarifas_salen_de_la_config_del_backend(client):
    tarifas = {t["plan"]: t for t in _get(client, "/admin/configuracion").json()["tarifas"]}

    assert [t for t in tarifas] == ["basico", "pro", "elite"]
    # Solo lo que se VENDE: mensual en las dos monedas. El anual sigue en la
    # tarifa interna para poder contar las suscripciones vivas, pero enseñarlo
    # en la pantalla de tarifas invitaría a ofrecerlo.
    assert tarifas["pro"]["eur"] == 39.0
    assert tarifas["pro"]["usd"] == 42.0
    assert tarifas["pro"]["max_empleados"] == 8
    assert "anual_total" not in tarifas["pro"]


# ═══════════════════════════════════════════════════════════════════ acceso


@pytest.mark.parametrize("ruta", ["/admin/metricas", "/admin/facturacion", "/admin/logs", "/admin/configuracion"])
def test_sin_sesion_de_panel_no_se_entra(client, ruta):
    assert client.get(ruta).status_code == 401


@pytest.mark.parametrize("ruta", ["/admin/metricas", "/admin/facturacion", "/admin/logs", "/admin/configuracion"])
def test_el_viewer_si_puede_leer(client, ruta):
    # Son cuatro lecturas: `viewer` es de solo lectura, no de «no ve nada».
    assert _get(client, ruta, rol="viewer").status_code == 200


@pytest.mark.parametrize("ruta", ["/admin/metricas", "/admin/facturacion", "/admin/logs", "/admin/configuracion"])
def test_no_hay_forma_de_escribir_por_aqui(client, ruta):
    """Este módulo es de solo lectura: ningún verbo mutable está registrado.

    Sale 403 y no 405 porque la defensa CSRF del panel (app/main.py) rechaza
    los métodos mutables sin `Origin` válido ANTES de que el enrutado llegue a
    decidir que la ruta no existe. Se aceptan los dos: lo que se afirma es que
    no se puede escribir, no cuál de las dos capas lo impide primero.
    """
    for metodo in (client.post, client.patch, client.delete):
        respuesta = metodo(ruta, cookies={"__Host-sa-access-token": admin_cookie()})
        assert respuesta.status_code in (403, 405), respuesta.status_code


# ══════════════════════════════════════════ el vigía de los recordatorios
#
# `atrasadas` es una ALARMA DE SALUD del scheduler, no un contador de calidad de
# los datos. En funcionamiento normal es CERO; si no lo es, algo está roto — el
# cron caído, una pasada que revienta, una consulta con un fallo que no lanza.
# Lo que se fija aquí es justo eso: que el cero se mantenga cuando todo va bien.

SALON_REC = "eeeeeeee-0000-0000-0000-000000000005"


def _con_recordatorios(**cambios):
    """Un salón con el interruptor puesto y una cita que le toca."""
    hace = lambda h: (AHORA - timedelta(hours=h)).isoformat()  # noqa: E731
    dentro = lambda h: (AHORA + timedelta(hours=h)).isoformat()  # noqa: E731

    datos = _datos()
    datos["salons"] = list(datos["salons"]) + [
        {
            "zona_horaria": "Europe/Madrid",
            "id": SALON_REC,
            "nombre_comercial": "Salón con recordatorios",
            "plan": "pro",
            "created_at": "2025-01-10T09:00:00+00:00",
            "recordatorios_email_activo": True,
            "recordatorio_horas": 24,
            "suspendido": False,
        }
    ]
    for otro in datos["salons"][:-1]:
        otro.setdefault("recordatorios_email_activo", False)
        otro.setdefault("suspendido", False)
        otro.setdefault("recordatorio_horas", 24)

    cita = {
        "id": "cita-atrasada",
        "salon_id": SALON_REC,
        # Entró en el techo hace 3 h y sigue sin sello: eso son 12 pasadas.
        "inicio": dentro(21),
        "created_at": hace(48),
        "estado": "confirmada",
        "recordatorio_enviado_at": None,
        "clients": {"email": "quien@correo.es", "recordatorios_baja_at": None},
    }
    cita.update(cambios)
    datos["appointments"] = [cita]
    return datos


def _atrasadas(monkeypatch, datos) -> int:
    from tests.conftest import FakePostgrest

    db = FakePostgrest(datos)
    monkeypatch.setattr(admin_plataforma, "_ahora", lambda: AHORA)
    app.dependency_overrides[get_admin_db] = lambda: db
    try:
        with TestClient(app) as c:
            m = _get(c, "/admin/metricas").json()
    finally:
        app.dependency_overrides.pop(get_admin_db, None)
    return m["tareas"]["atrasadas"]


def test_un_recordatorio_que_lleva_tres_horas_sin_salir_es_una_alarma(monkeypatch):
    assert _atrasadas(monkeypatch, _con_recordatorios()) == 1


@pytest.mark.parametrize(
    ("caso", "cambios"),
    [
        # EL RUIDO QUE SE QUITA: en Salón Demo, 9 de cada 11 citas son de gente
        # sin correo. Contarlas dejaba el vigía distinto de cero para siempre.
        ("el cliente no tiene correo",
         {"clients": {"email": None, "recordatorios_baja_at": None}}),
        ("el cliente se dio de baja",
         {"clients": {"email": "x@y.es", "recordatorios_baja_at": "2026-01-01T00:00:00+00:00"}}),
        # Todavía no le ha tocado: el techo del salón son 24 h.
        ("la cita está fuera del techo",
         {"inicio": (AHORA + timedelta(hours=40)).isoformat()}),
        # Se reservó con menos de 3 h de margen: no lleva recordatorio.
        ("reservada con prisa",
         {"inicio": (AHORA + timedelta(hours=2)).isoformat(),
          "created_at": (AHORA + timedelta(hours=1)).isoformat()}),
        # Ya salió.
        ("ya tiene sello",
         {"recordatorio_enviado_at": (AHORA - timedelta(hours=1)).isoformat()}),
        # La cita ya empezó: no hay nada que recordar.
        ("la cita ya pasó", {"inicio": (AHORA - timedelta(hours=1)).isoformat()}),
        ("cancelada", {"estado": "cancelada"}),
    ],
)
def test_lo_que_nunca_iba_a_salir_NO_es_una_alarma(monkeypatch, caso, cambios):
    assert _atrasadas(monkeypatch, _con_recordatorios(**cambios)) == 0, caso


def test_un_salon_con_el_interruptor_APAGADO_no_aporta_alarmas(monkeypatch):
    datos = _con_recordatorios()
    datos["salons"][-1]["recordatorios_email_activo"] = False
    assert _atrasadas(monkeypatch, datos) == 0


def test_un_salon_SUSPENDIDO_tampoco(monkeypatch):
    """No manda correos en su nombre, así que no puede llegar tarde."""
    datos = _con_recordatorios()
    datos["salons"][-1]["suspendido"] = True
    assert _atrasadas(monkeypatch, datos) == 0


def test_dentro_de_la_tolerancia_todavia_no_hay_alarma(monkeypatch):
    """Una pasada perdida no es una avería: un despliegue, un salto del
    planificador. Tres seguidas sí."""
    from app.tareas.recordatorios import TOLERANCIA_ATRASO

    a_tiempo = _con_recordatorios(
        inicio=(AHORA + timedelta(hours=24) - TOLERANCIA_ATRASO + timedelta(minutes=1)).isoformat()
    )
    assert _atrasadas(monkeypatch, a_tiempo) == 0

    tarde = _con_recordatorios(
        inicio=(AHORA + timedelta(hours=24) - TOLERANCIA_ATRASO - timedelta(minutes=1)).isoformat()
    )
    assert _atrasadas(monkeypatch, tarde) == 1


def test_el_techo_que_manda_es_el_DEL_SALON(monkeypatch):
    """Un salón a 48 h tiene 48 h de responsabilidad, no 24.

    Sin este caso, dar por hecho 24 para todos pasaba la suite entera: todos los
    salones de las demás fixtures están a 24, así que la diferencia no se veía
    por ningún lado. La cita entra a 30 h de su hora — dentro del techo de este
    salón y fuera del de 24 — y lleva 18 h sin salir.
    """
    datos = _con_recordatorios(inicio=(AHORA + timedelta(hours=30)).isoformat())
    datos["salons"][-1]["recordatorio_horas"] = 48

    assert _atrasadas(monkeypatch, datos) == 1


def test_sin_ningun_salon_con_recordatorios_el_vigia_es_cero(monkeypatch):
    """Y sin ir a buscar citas: si nadie los tiene encendidos, no hay nada que
    vigilar."""
    datos = _datos()
    for salon in datos["salons"]:
        salon["recordatorios_email_activo"] = False
    datos["appointments"] = []
    assert _atrasadas(monkeypatch, datos) == 0


# ══════════════════════════════════ las tres señales que se pintan en Alertas
#
# El vigía existía y NO SE VEÍA: el frontend no leía `tareas`, así que un cron
# caído no producía ni un pixel. Estas alertas son lo que se enseña, y el texto
# se escribe aquí porque aquí viven el umbral (sale de `CADENCIA_CRON`) y el
# «desde cuándo».


def _salud(**kw):
    from app.schemas.admin_plataforma import SaludTareas

    base = dict(ultima_at="2026-06-15T11:55:00+00:00", hace_minutos=5,
                procesados=2, fallidos=0, fallidos_definitivos=0,
                atrasadas=0, atraso_max_minutos=None)
    base.update(kw)
    return SaludTareas(**base)


def _alertas(**kw):
    return admin_plataforma._alertas_de_las_tareas(_salud(**kw))


def test_con_todo_sano_NO_se_enseña_nada():
    """EL REQUISITO. Un panel que siempre enseña algo enseña ruido, y el día que
    haya una avería nadie la va a distinguir del ruido de siempre."""
    assert _alertas() == []


def test_un_cron_que_NUNCA_ha_corrido_se_dice_asi():
    """Es lo que se ve antes de crear el servicio en Railway, y lo que se vería
    si alguien lo borrara."""
    alertas = _alertas(ultima_at=None, hace_minutos=None)

    assert [a.tipo for a in alertas] == ["sin_latido"]
    assert alertas[0].gravedad == "critica"
    assert "Railway" in alertas[0].detalle


def test_el_umbral_del_latido_sale_de_la_CADENCIA_del_cron():
    """Tres cadencias. Una es la espera normal; dos absorben una pasada perdida.
    A las tres, tres pasadas seguidas han fallado."""
    from app.tareas.recordatorios import CADENCIA_CRON, SIN_LATIDO

    assert SIN_LATIDO == 3 * CADENCIA_CRON
    minutos = int(SIN_LATIDO.total_seconds() // 60)

    assert _alertas(hace_minutos=minutos) == []
    assert [a.tipo for a in _alertas(hace_minutos=minutos + 1)] == ["sin_latido"]


def test_el_latido_dice_DESDE_CUANDO_y_no_solo_que_pasa():
    """«Un número sin desde-cuándo» no distingue lo que acaba de empezar de lo
    que lleva días."""
    alerta = _alertas(hace_minutos=200)[0]

    assert "3 h 20 min" in alerta.detalle
    assert "15 minutos" in alerta.detalle  # la cadencia que debería haber


def test_los_dias_se_leen_como_dias():
    assert "2 días" in _alertas(hace_minutos=2 * 24 * 60)[0].detalle


@pytest.mark.parametrize(
    ("perdidos", "trozo"),
    [(1, "1 recordatorio que ya no va a salir"), (3, "3 recordatorios que ya no van a salir")],
)
def test_los_envios_PERDIDOS_son_criticos(perdidos, trozo):
    """Ya no van a salir: o el fallo no permitía reintentar sin arriesgar un
    duplicado, o se agotaron los intentos."""
    alerta = next(
        a for a in _alertas(fallidos_definitivos=perdidos) if a.tipo == "envios_fallidos"
    )

    assert alerta.gravedad == "critica"
    assert trozo in alerta.titulo
    assert "por perdido" in alerta.detalle


def test_un_fallo_que_TODAVIA_se_reintenta_no_es_una_alarma_de_perdido():
    """El contador de la última pasada no manda: un envío que falló de forma
    segura ha soltado el sello y volverá a intentarse. Ese está llegando tarde,
    no perdido, y para eso está `atrasadas`."""
    assert [a.tipo for a in _alertas(fallidos=3, fallidos_definitivos=0)] == []


def test_el_atraso_dice_cuantas_y_desde_cuando():
    alerta = next(
        a for a in _alertas(atrasadas=4, atraso_max_minutos=150)
        if a.tipo == "recordatorios_atrasados"
    )

    assert "4 recordatorios" in alerta.titulo
    assert "2 h 30 min" in alerta.detalle
    # Un aviso, no una crítica: llegan tarde pero todavía pueden llegar.
    assert alerta.gravedad == "aviso"


def test_el_SILENCIO_va_primero():
    """Es la que de verdad duele: si el cron no corre, no sale nada y ninguna de
    las otras dos se entera a tiempo — el atraso necesita que exista una cita
    elegible para crecer, y los fallos solo se cuentan de pasadas que ocurren."""
    alertas = _alertas(
        hace_minutos=300, fallidos_definitivos=2, atrasadas=5, atraso_max_minutos=90
    )

    assert [a.tipo for a in alertas] == [
        "sin_latido",
        "envios_fallidos",
        "recordatorios_atrasados",
    ]


def test_las_alertas_de_tareas_viajan_en_la_respuesta(monkeypatch):
    """De punta a punta: sin latido en la tabla, el panel lo recibe."""
    datos = _datos()
    datos["tareas_latido"] = []
    from tests.conftest import FakePostgrest

    db = FakePostgrest(datos)
    monkeypatch.setattr(admin_plataforma, "_ahora", lambda: AHORA)
    app.dependency_overrides[get_admin_db] = lambda: db
    try:
        with TestClient(app) as c:
            m = _get(c, "/admin/metricas").json()
    finally:
        app.dependency_overrides.pop(get_admin_db, None)

    assert [a["tipo"] for a in m["alertas_tareas"]] == ["sin_latido"]
    assert m["tareas"]["ultima_at"] is None


# ══════════════════════════════ los envíos que ya no van a salir


def _perdidos(monkeypatch, **cambios) -> int:
    """Una cita sellada y con motivo anotado: se dio por perdida."""
    base = {
        "recordatorio_enviado_at": (AHORA - timedelta(minutes=20)).isoformat(),
        "recordatorio_error": "[definitivo, 5 intentos] ConnectError: nunca",
    }
    # Los cambios pisan la base; pasar los dos como kwargs daba «multiple values».
    datos = _con_recordatorios(**{**base, **cambios})
    from tests.conftest import FakePostgrest

    db = FakePostgrest(datos)
    monkeypatch.setattr(admin_plataforma, "_ahora", lambda: AHORA)
    app.dependency_overrides[get_admin_db] = lambda: db
    try:
        with TestClient(app) as c:
            m = _get(c, "/admin/metricas").json()
    finally:
        app.dependency_overrides.pop(get_admin_db, None)
    return m["tareas"]["fallidos_definitivos"]


def test_un_correo_dado_por_perdido_SIGUE_saliendo_pasadas_despues(monkeypatch):
    """LA RAZÓN DE QUE ESTO NO SALGA DEL LATIDO. Ese contador se sobrescribe
    cada 15 minutos: un correo perdido a las 10:16 desaparecía de la alarma a
    las 10:31. Contado desde las citas, sigue ahí."""
    assert _perdidos(monkeypatch) == 1


def test_un_fallo_que_AUN_se_reintenta_no_cuenta_como_perdido(monkeypatch):
    """Tiene el sello suelto: la pasada siguiente lo va a coger. Está llegando
    tarde, no perdido."""
    assert _perdidos(monkeypatch, recordatorio_enviado_at=None) == 0


def test_una_cita_que_YA_PASO_deja_de_contar(monkeypatch):
    """Esto es una alarma sobre alguien que va a quedarse sin su aviso, no un
    libro de cuentas. Lo de ayer ya no se puede arreglar."""
    assert _perdidos(monkeypatch, inicio=(AHORA - timedelta(hours=1)).isoformat()) == 0


def test_un_envio_que_salio_BIEN_no_cuenta(monkeypatch):
    """Sellada sin error: salió. El error se limpia al reclamar justo para que
    «sellada + con error» signifique una sola cosa."""
    assert _perdidos(monkeypatch, recordatorio_error=None) == 0

