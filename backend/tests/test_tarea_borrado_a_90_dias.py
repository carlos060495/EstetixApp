"""La tarea del borrado a 90 días (0089): lo de fuera ANTES, y solo las preparadas.

  · Apagado: no toca Stripe ni Storage, ni llama al borrado.
  · Stripe y los logos se borran antes de llamar a la función; «ya no existe» vale.
  · Si Stripe falla con una cuenta, esa no se da por preparada: la base no la toca.
  · Lo que la base devuelve con error cuenta como no borrado, y se dice.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import stripe

from app.db.postgrest_client import PostgrestError
from app.tareas import borrado_a_90_dias as tarea

AJUSTES = SimpleNamespace(stripe_secret_key="sk_test_falsa", supabase_url="http://x", supabase_secret_key="k")


class Base:
    def __init__(self, pasada=None, apagado=False, errores=()):
        self.pasada, self.apagado, self.errores, self.llamadas = pasada or [], apagado, set(errores), []

    async def rpc(self, nombre, params):
        self.llamadas.append((nombre, params))
        if self.apagado:
            raise PostgrestError(400, '{"code":"55000","message":"El borrado a 90 días está APAGADO"}')
        if nombre == "proxima_pasada_de_borrado":
            return self.pasada
        return [{"account_id": c, "filas": None if c in self.errores else {}, "error": "23503: sujeta" if c in self.errores else None}
                for c in params["p_preparadas"]]


class Almacen:
    def __init__(self):
        self.borrados = []

    async def delete(self, bucket, ruta):
        self.borrados.append((bucket, ruta))


class Stripe:
    def __init__(self, falla=(), no_existe=()):
        self.falla, self.no_existe, self.borrados = set(falla), set(no_existe), []
    async def borrar(self, clave, cliente):
        if cliente in self.falla:
            raise stripe.APIConnectionError("Stripe no contesta")
        if cliente in self.no_existe:
            raise stripe.InvalidRequestError("No such customer", "id", code="resource_missing")
        self.borrados.append(cliente)


def _cuenta(i, stripe_ids=(), logos=()):
    return {"account_id": f"a{i}", "origen": "suscripcion", "se_borran_el": "2026-01-01",
            "clientes_stripe": list(stripe_ids), "logos": list(logos), "fuera_de_plazo": 5}


def _correr(base, s, monkeypatch):
    monkeypatch.setattr(tarea, "_borrar_cliente", s.borrar)
    almacen = Almacen()
    return asyncio.run(tarea.ejecutar(AJUSTES, base, almacen)), almacen


def test_apagado_no_toca_nada_de_fuera(monkeypatch):
    base, s = Base(apagado=True), Stripe()
    resumen, almacen = _correr(base, s, monkeypatch)
    assert resumen == {"estado": "apagado"}
    assert [n for n, _ in base.llamadas] == ["proxima_pasada_de_borrado"]
    assert s.borrados == [] and almacen.borrados == []


def test_lo_de_fuera_va_antes_y_solo_se_borran_las_preparadas(monkeypatch):
    base = Base([_cuenta(1, ["cus_1"], ["c1/logo.png"]), _cuenta(2, ["cus_2"]), _cuenta(3, ["cus_3"])],
                errores={"a3"})
    s = Stripe(falla={"cus_2"}, no_existe={"cus_3"})
    resumen, almacen = _correr(base, s, monkeypatch)
    assert s.borrados == ["cus_1"]
    assert almacen.borrados == [("salon-assets", "c1/logo.png")]
    # La 2 no se prepara (Stripe falló); la 3 sí (ya no existía en Stripe), pero la base la devuelve con error.
    assert base.llamadas[-1] == ("borrar_cuentas_fuera_de_plazo", {"p_preparadas": ["a1", "a3"]})
    assert resumen == {"estado": "hecho", "borradas": 1, "con_error": 2, "quedan": 4}


def test_sin_nada_fuera_de_plazo_no_llama_al_borrado(monkeypatch):
    base = Base([])
    resumen, _ = _correr(base, Stripe(), monkeypatch)
    assert resumen["borradas"] == 0 and [n for n, _ in base.llamadas] == ["proxima_pasada_de_borrado"]


def test_la_cadena_de_tareas_la_llama_la_ultima_y_aislada():
    from pathlib import Path

    fuente = Path(tarea.__file__).with_name("recordatorios.py").read_text(encoding="utf-8")
    main = fuente[fuente.index("def main()"):]
    assert main.index("plazo_de_datos.ejecutar()") < main.index("borrado_a_90_dias.ejecutar()")
    bloque = main[main.rindex("try:", 0, main.index("borrado_a_90_dias.ejecutar()")):]
    assert "except Exception" in bloque
