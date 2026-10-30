"""UN CASO DE CADA CAMINO, CON NÚMEROS (0078), contra la base de verdad y en una
transacción que se deshace.

  · EL SALÓN ASIGNA A MITAD DE MES: «zz Lucía Estética» se dio de alta hace 20
    días y pagó su primer recibo (49 €) ese día. Hoy asigna el código de su
    vendedora (al 10 %). Ese recibo no genera nada; el siguiente, 4,90 €.
  · LA REPESCA DEVENGA DESDE EL ALTA: «zz Salón Nerea» se dio de alta hace 75
    días tecleando «ZZV00O» (una O por un cero). Pagó tres recibos de 49 €; el
    tercero se le devolvió entero. Hoy la vendedora va al 12 % y el owner
    repesca: tres devengos de 5,88 € y la corrección del devuelto, −5,88 €.

La repesca va de punta a punta por el código de verdad: `devengar_lo_cobrado`
(el barrido del backend) contra un Stripe de mentira, grabando lo que le pide a
la base; y eso, ejecutado en la base de verdad. Con `-s` imprime la tabla.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
import stripe

from app.core import comisiones
from app.core.config import Settings
from tests.test_comisiones_base_real import _lineas
from tests.test_un_salon_no_puede_cambiarse_el_plan import sql

pytestmark = [pytest.mark.base_real]

AHORA = datetime.now(timezone.utc).replace(microsecond=0)
ALTA_NEREA = AHORA - timedelta(days=75)
AJUSTES = Settings(stripe_secret_key="sk_test_x", supabase_url="http://x", supabase_secret_key="k")


def _ts(d: datetime) -> int:
    return int(d.timestamp())


# Los tres recibos de Nerea: al alta, a los 30 y a los 60 días. El tercero, devuelto.
RECIBOS = [(f"in_zzn{n}", f"inpay_zzn{n}", f"ch_zzn{n}", ALTA_NEREA + timedelta(days=30 * (n - 1)), devuelto)
           for n, devuelto in ((1, 0), (2, 0), (3, 4900))]


@pytest.fixture(autouse=True)
def _la_hora_de_este_test() -> None:
    """LA HORA SE TOMA AL EMPEZAR CADA TEST, no al importar el fichero. Tomada al
    importar, envejecía con la tirada: pasados 25 minutos la sesión de soporte del
    doble ya había caducado y la suite se colgaba (15-10-2026)."""
    global AHORA, ALTA_NEREA, RECIBOS
    AHORA = datetime.now(timezone.utc).replace(microsecond=0)
    ALTA_NEREA = AHORA - timedelta(days=75)
    RECIBOS = [(f"in_zzn{n}", f"inpay_zzn{n}", f"ch_zzn{n}", ALTA_NEREA + timedelta(days=30 * (n - 1)), devuelto)
               for n, devuelto in ((1, 0), (2, 0), (3, 4900))]


class _Stripe:
    """Lo justo de Stripe para el barrido: facturas, sus pagos, el cobro y las disputas."""

    class Invoice:
        @staticmethod
        async def list_async(**kw):
            assert kw["customer"] == "cus_zznerea"
            return {"data": [{"id": r[0]} for r in reversed(RECIBOS)], "has_more": False}

        @staticmethod
        async def retrieve_async(fid):
            return {"id": fid, "status": "paid", "customer": "cus_zznerea", "currency": "eur",
                    "total": 4900, "total_excluding_tax": 4900}

    class InvoicePayment:
        @staticmethod
        async def list_async(invoice, **kw):
            r = next(x for x in RECIBOS if x[0] == invoice)
            return {"data": [{"id": r[1], "amount_paid": 4900, "currency": "eur",
                              "payment": {"type": "charge", "charge": r[2]},
                              "status_transitions": {"paid_at": _ts(r[3])}}]}

    class Charge:
        @staticmethod
        async def retrieve_async(cid):
            return {"id": cid, "amount_refunded": next(x[4] for x in RECIBOS if x[2] == cid)}

    class Dispute:
        @staticmethod
        async def list_async(**kw):
            return {"data": []}


class _Grabadora:
    """Una base que apunta lo que el backend le pide (y contesta como la de verdad)."""

    def __init__(self):
        self.pedido: list[tuple[str, dict]] = []

    async def select(self, tabla, params):
        assert tabla == "accounts"
        return [{"stripe_customer_id": "cus_zznerea"}]

    async def rpc(self, funcion, cuerpo):
        self.pedido.append((funcion, cuerpo))
        return [{"resultado": "devengada" if funcion == "devengar_comision" else "corregida"}]


def _lit(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, int):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


def _en_sql(funcion: str, cuerpo: dict) -> str:
    args = ", ".join(_lit(v) for v in cuerpo.values())
    return f"select 'RPC|{funcion}|' || resultado from public.{funcion}({args});"


def test_un_caso_de_cada_camino_con_numeros(monkeypatch):
    for nombre in ("Invoice", "InvoicePayment", "Charge", "Dispute"):
        monkeypatch.setattr(stripe, nombre, getattr(_Stripe, nombre))
    grabadora = _Grabadora()
    asyncio.run(comisiones.devengar_lo_cobrado(grabadora, AJUSTES, "x", "repesca:demo"))
    lo_que_pide_el_backend = "\n".join(_en_sql(f, c) for f, c in grabadora.pedido)

    salida = sql(f"""begin;
create temp table zz (k text primary key, v text) on commit drop;
do $$ declare r uuid; l uuid; n uuid; begin
  update public.plataforma_config set comision_referido_pct = 10 where id;
  insert into public.referidos (nombre, email, codigo, estado)
    values ('zz Marta Vendedora', 'zz.marta@numeros.test', 'ZZV001', 'aprobado') returning id into r;
  insert into public.accounts (nombre, stripe_customer_id, created_at)
    values ('zz Lucía Estética', 'cus_zzlucia', now() - interval '20 days') returning id into l;
  insert into public.salons (account_id, nombre_comercial, created_at) values (l, 'zz Lucía Estética', now() - interval '20 days');
  insert into public.accounts (nombre, stripe_customer_id, created_at)
    values ('zz Salón Nerea', 'cus_zznerea', '{ALTA_NEREA.isoformat()}') returning id into n;
  insert into public.salons (account_id, nombre_comercial, created_at) values (n, 'zz Salón Nerea', '{ALTA_NEREA.isoformat()}');
  perform public.atribuir_alta(n, 'ZZV00O');
  insert into zz values ('r', r), ('lucia', l), ('nerea', n);
end $$;

-- ═══ El salón asigna a mitad de mes
select 'L|primer recibo, el día del alta|' ||
  (select resultado from public.devengar_comision('cus_zzlucia', 'in_zzl1', 'inpay_zzl1', 'ch_zzl1', null, 'eur',
   4900, 4900, now() - interval '20 days', null));
select 'L|hoy asigna el código|' ||
  (select resultado || ' (devenga desde ' || to_char(devenga_desde at time zone 'Europe/Madrid', 'DD-MM HH24:MI') || ')'
   from public.asignar_su_vendedor((select v::uuid from zz where k = 'lucia'), 'zzv001',
        (select v::uuid from zz where k = 'r'), null, '00000000-0000-0000-0000-0000000000aa', 'lucia@salon.test'));
select 'L|Stripe reenvía el aviso del primer recibo|' ||
  (select resultado from public.devengar_comision('cus_zzlucia', 'in_zzl1', 'inpay_zzl1', 'ch_zzl1', null, 'eur',
   4900, 4900, now() - interval '20 days', null));
select 'L|siguiente recibo, dentro de 10 días|' ||
  (select resultado from public.devengar_comision('cus_zzlucia', 'in_zzl2', 'inpay_zzl2', 'ch_zzl2', null, 'eur',
   4900, 4900, now() + interval '10 days', null));
select 'LC|' || to_char(ocurrido_at at time zone 'Europe/Madrid', 'DD-MM') || '|' || importe_cobrado_centimos || '|'
       || pct || '|' || comision_centimos
  from public.comisiones where cuenta_id = (select v::uuid from zz where k = 'lucia') order by id;

-- ═══ La repesca, desde el alta
update public.referidos set comision_pct = 12 where id = (select v::uuid from zz where k = 'r');
select 'N|repesca|' || resultado || ' (devenga desde ' || to_char(devenga_desde at time zone 'Europe/Madrid', 'DD-MM') || ')'
  from public.repescar_atribucion(
    (select id from public.codigos_que_no_valieron where cuenta_id = (select v::uuid from zz where k = 'nerea')),
    'ZZV001', (select v::uuid from zz where k = 'r'), null, 'Tecleó una O por un cero al darse de alta',
    '00000000-0000-0000-0000-0000000000bb', 'owner@estetixapp.test');
{lo_que_pide_el_backend}
select 'NC|' || to_char(ocurrido_at at time zone 'Europe/Madrid', 'DD-MM') || '|' || tipo || '|' || motivo || '|'
       || coalesce(importe_cobrado_centimos::text, '-') || '|' || pct || '|' || comision_centimos
  from public.comisiones where cuenta_id = (select v::uuid from zz where k = 'nerea') order by ocurrido_at, id;
select 'NT|' || sum(comision_centimos) from public.comisiones where cuenta_id = (select v::uuid from zz where k = 'nerea');
rollback;
""")
    print("\n" + "\n".join(ln for ln in salida.splitlines() if ln[:2] in ("L|", "LC", "N|", "NC", "NT", "RP")))

    # El salón: el recibo de antes, nada; el siguiente, 10 % de 49 €.
    l = _lineas(salida, "L")
    assert l[0].endswith("|sin_referido")  # entonces no tenía vendedor
    assert l[1].startswith("hoy asigna el código|asignada")
    assert l[2].endswith("|antes_de_la_atribucion")  # ni aunque el aviso llegue otra vez
    assert l[3].endswith("|devengada")
    assert [x.split("|")[1:] for x in _lineas(salida, "LC")] == [["4900", "10.00", "490"]]
    # La repesca: los tres recibos desde el alta al 12 %, y el devuelto, corregido.
    assert _lineas(salida, "N")[0].startswith("repesca|repescada")
    assert [x.split("|")[1:] for x in _lineas(salida, "NC")] == [
        ["devengo", "cobro", "4900", "12.00", "588"],
        ["devengo", "cobro", "4900", "12.00", "588"],
        ["devengo", "cobro", "4900", "12.00", "588"],
        ["correccion", "devolucion", "-", "12.00", "-588"],
    ]
    assert _lineas(salida, "NT") == ["1176"]
