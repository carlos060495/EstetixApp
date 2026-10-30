"""CAJA: CORREGIR Y ANULAR (0092), contra la base de verdad.

Lo que sujeta esto son dos funciones de la base, un trigger y unos permisos: un
doble no tiene ninguna de las tres cosas. Cada caso mira las tres patas a la
vez: la fila, el stock y la línea del registro, y que las cuentas de
Estadísticas sigan cuadrando.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.db.postgrest_client import PostgrestClient, PostgrestError
from tests.test_alta_completa_e2e import SECRET_KEY, SUPABASE_URL
from tests.test_camino_humano_dos_centros import (  # noqa: F401  (fixtures)
    _cabeceras,
    _token_de_inquilino,
    anyio_backend,
    dos_centros,
)

pytestmark = [pytest.mark.base_real, pytest.mark.anyio]

H = _cabeceras()
MOTIVO = "Se apuntó con un cero de más; el ticket dice otra cosa."
# El día del centro: la reposición apunta su gasto en ese día, no en uno fijo.
HOY = datetime.now(ZoneInfo("Europe/Madrid")).date().isoformat()


def _db() -> PostgrestClient:
    return PostgrestClient(SUPABASE_URL, SECRET_KEY, timeout=30.0)


def _stats(c):
    r = c.get("/stats", params={"desde": f"{HOY}T00:00:00Z", "hasta": f"{HOY}T23:59:59Z",
                                "fecha_desde": HOY, "fecha_hasta": HOY}, headers=H)
    assert r.status_code == 200, r.text
    return r.json()


async def _producto(c, stock=5, precio=10.0, coste=4.0) -> str:
    r = c.post("/products", json={"nombre": f"Champú {uuid.uuid4().hex[:4]}", "precio": precio,
                                  "precio_coste": coste, "stock": stock, "stock_min": 1,
                                  "venta_publico": True}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _stock(producto: str) -> int:
    return (await _db().select("products", {"select": "stock", "id": f"eq.{producto}"}))[0]["stock"]


async def _registro(movimiento_id: str) -> list[dict]:
    return await _db().select("correcciones_de_caja", {"select": "*", "movimiento_id": f"eq.{movimiento_id}",
                                                       "order": "id.asc"})


# ═══ 1. Un ingreso «otro»: corregir y anular, y las cuentas cuadran ═══════


async def test_corregir_y_anular_un_ingreso_cuadra_las_cuentas(dos_centros):  # noqa: F811
    c = dos_centros["cliente"]
    r = c.post("/incomes", json={"fecha": HOY, "concepto": "Vale regalo", "tipo": "otro", "importe": 1200},
               headers=H)
    assert r.status_code == 201, r.text
    ingreso = r.json()["id"]
    assert _stats(c)["ingresos_otros"] == 1200

    # Sin motivo, no.
    r = c.post(f"/incomes/{ingreso}/corregir", json={"importe": 120, "motivo": "corto"}, headers=H)
    assert r.status_code == 422

    r = c.post(f"/incomes/{ingreso}/corregir", json={"importe": 120, "motivo": MOTIVO}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["importe"] == 120
    assert _stats(c)["ingresos_otros"] == 120
    (linea,) = await _registro(ingreso)
    assert (linea["accion"], linea["antes"], linea["despues"], linea["motivo"]) == (
        "corrige", {"importe": 1200}, {"importe": 120}, MOTIVO)
    assert linea["usuario_email"] and linea["usuario_id"]

    # Corregir sin cambiar nada se dice, y no deja línea.
    r = c.post(f"/incomes/{ingreso}/corregir", json={"importe": 120, "motivo": MOTIVO}, headers=H)
    assert r.status_code == 400 and "No has cambiado nada" in r.json()["detail"]

    r = c.post(f"/incomes/{ingreso}/anular", json={"motivo": "Era de otro centro, no de este."}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json() == {"stock_movido": None, "stock_sin_ajustar": None, "stock_sin_ajustar_detalle": None}
    assert _stats(c)["ingresos_otros"] == 0
    # LA FILA SE QUEDA, tachada.
    (fila,) = [i for i in c.get("/incomes", headers=H).json() if i["id"] == ingreso]
    assert fila["anulado_at"] and fila["anulado_motivo"] == "Era de otro centro, no de este."

    # Anulado ya no se cambia, ni se vuelve a anular.
    r = c.post(f"/incomes/{ingreso}/corregir", json={"importe": 5, "motivo": MOTIVO}, headers=H)
    assert r.status_code == 409 and "ya está anulado" in r.json()["detail"]
    r = c.post(f"/incomes/{ingreso}/anular", json={"motivo": MOTIVO}, headers=H)
    assert r.status_code == 409

    assert [x["accion"] for x in await _registro(ingreso)] == ["corrige", "anula"]
    r = c.get(f"/incomes/{ingreso}/registro", headers=H)
    assert r.status_code == 200 and [x["accion"] for x in r.json()] == ["corrige", "anula"]


# ═══ 2. Venta de producto: el stock se ajusta ═════════════════════════════


async def test_anular_una_venta_devuelve_las_unidades(dos_centros):  # noqa: F811
    c = dos_centros["cliente"]
    prod = await _producto(c, stock=5)
    r = c.post("/incomes", json={"fecha": HOY, "concepto": "Venta", "product_id": prod, "cantidad": 2}, headers=H)
    assert r.status_code == 201, r.text
    venta = r.json()["id"]
    assert await _stock(prod) == 3

    # Corregir la cantidad ajusta la diferencia y el importe sigue al precio.
    r = c.post(f"/incomes/{venta}/corregir", json={"cantidad": 3, "motivo": MOTIVO}, headers=H)
    assert r.status_code == 200, r.text
    assert (r.json()["cantidad"], r.json()["importe"]) == (3, 30)
    assert await _stock(prod) == 2
    # El importe de una venta no se toca a mano: lo fija el precio.
    r = c.post(f"/incomes/{venta}/corregir", json={"importe": 1, "motivo": MOTIVO}, headers=H)
    assert r.status_code == 400 and "no se puede corregir el importe" in r.json()["detail"]

    r = c.post(f"/incomes/{venta}/anular", json={"motivo": MOTIVO}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["stock_movido"]["unidades"] == 3
    assert await _stock(prod) == 5
    *_, anula = await _registro(venta)
    assert anula["stock_movido"]["unidades"] == 3 and anula["stock_sin_ajustar"] is None


async def test_venta_cuyo_producto_se_borro_lo_deja_escrito(dos_centros):  # noqa: F811
    c = dos_centros["cliente"]
    prod = await _producto(c, stock=5)
    venta = c.post("/incomes", json={"fecha": HOY, "concepto": "Venta", "product_id": prod, "cantidad": 1},
                   headers=H).json()["id"]
    await _db().delete("products", {"id": f"eq.{prod}"})

    r = c.post(f"/incomes/{venta}/anular", json={"motivo": MOTIVO}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["stock_sin_ajustar"] == "producto_borrado"
    (anula,) = await _registro(venta)
    assert anula["stock_sin_ajustar"] == "producto_borrado"
    assert "se borró después" in anula["stock_sin_ajustar_detalle"]


# ═══ 3. Compras de stock ══════════════════════════════════════════════════


async def test_anular_una_compra_resta_y_se_para_si_no_hay_bastantes(dos_centros):  # noqa: F811
    c = dos_centros["cliente"]
    prod = await _producto(c, stock=0)
    r = c.post(f"/products/{prod}/reposicion", json={"cantidad": 4, "coste_unitario": 2.5}, headers=H)
    assert r.status_code == 200, r.text
    (compra,) = [g for g in c.get("/expenses", headers=H).json() if g.get("product_id") == prod]
    # LA COMPRA DICE DE QUÉ Y CUÁNTAS (0092).
    assert (compra["cantidad"], compra["importe"], compra["products"]["nombre"].startswith("Champú")) == (4, 10, True)

    # Se vendieron 3: solo queda 1 y no se pueden restar 4.
    c.post("/incomes", json={"fecha": HOY, "concepto": "Venta", "product_id": prod, "cantidad": 3}, headers=H)
    assert await _stock(prod) == 1
    r = c.post(f"/expenses/{compra['id']}/anular", json={"motivo": MOTIVO}, headers=H)
    assert r.status_code == 409, r.text
    assert "solo quedan 1" in r.json()["detail"]["mensaje"]
    # NADA CAMBIÓ: ni la fila, ni el stock, ni el registro.
    assert await _stock(prod) == 1
    assert await _registro(compra["id"]) == []
    (fila,) = [g for g in c.get("/expenses", headers=H).json() if g["id"] == compra["id"]]
    assert fila["anulado_at"] is None

    # Corregir la cantidad a 2 resta 2 (quedan 1 → no se puede); a 5 suma 1.
    r = c.post(f"/expenses/{compra['id']}/corregir", json={"cantidad": 5, "motivo": MOTIVO}, headers=H)
    assert r.status_code == 200, r.text
    assert (r.json()["cantidad"], r.json()["importe"]) == (5, 12.5)
    assert await _stock(prod) == 2
    r = c.post(f"/expenses/{compra['id']}/corregir", json={"cantidad": 1, "motivo": MOTIVO}, headers=H)
    assert r.status_code == 409

    gastos_antes = _stats(c)["gastos_variables"]
    r = c.post(f"/products/{prod}/reposicion", json={"cantidad": 10, "coste_unitario": 1}, headers=H)
    assert r.status_code == 200
    r = c.post(f"/expenses/{compra['id']}/anular", json={"motivo": MOTIVO}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["stock_movido"]["unidades"] == -5
    assert await _stock(prod) == 7
    assert _stats(c)["gastos_variables"] == gastos_antes + 10 - 12.5


async def test_compra_anterior_al_registro_anula_el_dinero_y_dice_por_que_no_el_stock(dos_centros):  # noqa: F811
    c = dos_centros["cliente"]
    prod = await _producto(c, stock=3)
    # Como las de antes de la 0092: solo categoría e importe.
    vieja = await _db().insert("expenses", {"salon_id": dos_centros["salon_id"], "fecha": HOY,
                                            "categoria": "Compra de stock", "tipo": "variable",
                                            "estado": "pagado", "importe": 80})
    r = c.post(f"/expenses/{vieja['id']}/anular", json={"motivo": MOTIVO}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["stock_sin_ajustar"] == "compra_sin_producto"
    assert await _stock(prod) == 3
    (anula,) = await _registro(vieja["id"])
    assert anula["stock_movido"] is None
    assert "no dice de qué producto" in anula["stock_sin_ajustar_detalle"]
    # Su cantidad no se puede corregir: no se sabe de qué es.
    otra = await _db().insert("expenses", {"salon_id": dos_centros["salon_id"], "fecha": HOY,
                                           "categoria": "Compra de stock", "importe": 5})
    r = c.post(f"/expenses/{otra['id']}/corregir", json={"cantidad": 2, "motivo": MOTIVO}, headers=H)
    assert r.status_code == 400 and "la cantidad" in r.json()["detail"]


async def test_un_gasto_normal_no_dice_nada_del_stock(dos_centros):  # noqa: F811
    c = dos_centros["cliente"]
    g = c.post("/expenses", json={"fecha": HOY, "categoria": "Luz", "tipo": "fijo", "importe": 900}, headers=H).json()
    r = c.post(f"/expenses/{g['id']}/corregir", json={"importe": 90, "estado": "pagado", "motivo": MOTIVO}, headers=H)
    assert r.status_code == 200, r.text
    (linea,) = await _registro(g["id"])
    assert linea["antes"] == {"importe": 900, "estado": "pendiente"}
    assert linea["despues"] == {"importe": 90, "estado": "pagado"}
    r = c.post(f"/expenses/{g['id']}/anular", json={"motivo": MOTIVO}, headers=H)
    assert r.json() == {"stock_movido": None, "stock_sin_ajustar": None, "stock_sin_ajustar_detalle": None}


# ═══ 4. Nadie se salta el registro ════════════════════════════════════════


async def test_la_sesion_no_cambia_ni_borra_caja_y_el_registro_no_se_toca(dos_centros):  # noqa: F811
    c = dos_centros["cliente"]
    g = c.post("/expenses", json={"fecha": HOY, "categoria": "Luz", "importe": 50}, headers=H).json()
    sesion = PostgrestClient(SUPABASE_URL, _token_de_inquilino(dos_centros["auth_user_id"], dos_centros["salon_id"]),
                             timeout=30.0)
    with pytest.raises(PostgrestError):
        await sesion.update("expenses", {"id": f"eq.{g['id']}"}, {"importe": 5})
    with pytest.raises(PostgrestError):
        await sesion.delete("expenses", {"id": f"eq.{g['id']}"})
    with pytest.raises(PostgrestError):
        await sesion.rpc("anular_movimiento_de_caja", {"p_salon": dos_centros["salon_id"], "p_movimiento": "gasto",
                                                       "p_id": g["id"], "p_motivo": MOTIVO,
                                                       "p_usuario": dos_centros["id"], "p_usuario_nombre": None,
                                                       "p_usuario_email": None})
    # Las rutas viejas ya no existen.
    assert c.patch(f"/expenses/{g['id']}", json={"importe": 1}, headers=H).status_code in (404, 405)
    assert c.delete(f"/expenses/{g['id']}", headers=H).status_code in (404, 405)

    c.post(f"/expenses/{g['id']}/anular", json={"motivo": MOTIVO}, headers=H)
    # Ni con la clave de servicio: ni cambiar la anulada, ni tocar el registro.
    with pytest.raises(PostgrestError):
        await _db().update("expenses", {"id": f"eq.{g['id']}"}, {"importe": 5})
    with pytest.raises(PostgrestError):
        await _db().update("correcciones_de_caja", {"movimiento_id": f"eq.{g['id']}"}, {"motivo": "x" * 20})
    with pytest.raises(PostgrestError):
        await _db().delete("correcciones_de_caja", {"movimiento_id": f"eq.{g['id']}"})
    assert len(await _registro(g["id"])) == 1


async def test_otro_centro_no_puede_corregir_lo_mio(dos_centros):  # noqa: F811
    c = dos_centros["cliente"]
    g = c.post("/expenses", json={"fecha": HOY, "categoria": "Luz", "importe": 50}, headers=H).json()
    otro = _cabeceras(dos_centros["centro_2"])
    r = c.post(f"/expenses/{g['id']}/anular", json={"motivo": MOTIVO}, headers=otro)
    assert r.status_code == 404
    assert await _registro(g["id"]) == []
