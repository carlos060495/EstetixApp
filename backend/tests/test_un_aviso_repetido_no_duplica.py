"""UN ENVÍO REPETIDO NO MANDA DOS AVISOS A LA MISMA PERSONA (14-10-2026).

Resend recuerda la cabecera `Idempotency-Key` 24 horas: con la misma clave, un
segundo envío no manda un segundo correo. Ver `core/emails.py`.

Lo que se fija aquí:

  1. EL GUARDA DE CÓDIGO, que descubre: todo envío de correo que hace una
     TAREA (`app/tareas/`, lo que corre solo y reintenta) lleva `clave=`. Falla
     el día que alguien escriba uno nuevo sin ella.
  2. CON NÚMEROS, contra un Resend de laboratorio que se comporta como el de
     verdad (recibe el correo, lo «manda» y, con la misma clave, no lo vuelve a
     mandar): la primera pasada manda el recordatorio y se le corta la
     respuesta —el caso que antes se perdía o, sin sello, se duplicaba—, la
     segunda lo reintenta. Al final, UN correo en el buzón de la clienta.
     CONTROL: con una clave que cambia en cada intento, son dos. Si el control
     dejara de ver el duplicado, la medida no valdría.
  3. Un 409 de idempotencia de Resend es «ya aceptado»: no es un fallo.
"""

from __future__ import annotations

import ast
import json
import pathlib

import httpx
import pytest

from app.core import emails
from app.tareas import recordatorios
from tests.test_recordatorios_cron import (  # noqa: F401  (fixtures)
    FakeDB,
    _cita,
    _reloj_fijo_por_defecto,
    _settings,
    tenant,
)

APP = pathlib.Path(__file__).resolve().parents[1] / "app"


# ═══ 1 · el guarda de código ════════════════════════════════════════════════

def envios_sin_clave(fuente: str) -> list[int]:
    """Llamadas a `enviar_email_branded` sin `clave=`."""
    return [
        n.lineno for n in ast.walk(ast.parse(fuente))
        if isinstance(n, ast.Call)
        and (getattr(n.func, "id", None) or getattr(n.func, "attr", None)) == "enviar_email_branded"
        and not any(k.arg in ("clave", None) for k in n.keywords)
    ]


def test_CONTROL_ve_un_envio_sin_clave():
    assert envios_sin_clave("await enviar_email_branded(s, a, b, c)\nawait emails.enviar_email_branded(s, a, b, c)") == [1, 2]
    assert envios_sin_clave("await enviar_email_branded(s, a, b, c, clave=k)") == []


def test_todo_correo_que_manda_una_tarea_lleva_clave():
    tareas = sorted((APP / "tareas").glob("*.py"))
    vistos = [f for f in tareas if "enviar_email_branded(" in f.read_text(encoding="utf-8")]
    assert len(vistos) >= 2, "el guarda no ve las tareas que mandan correos"  # recordatorios y plazo
    culpables = [f"{f.name}:{l}" for f in vistos for l in envios_sin_clave(f.read_text(encoding="utf-8"))]
    assert not culpables, (
        f"Correos de una tarea sin clave de idempotencia: {culpables}. Una tarea reintenta; sin clave, "
        "un reintento puede mandar el mismo aviso dos veces. Pásale `clave=clave_idempotencia(...)` con lo "
        "que identifica el AVISO (no el intento)."
    )


# ═══ 2 · con números, contra un Resend de laboratorio ═══════════════════════

class ResendDeLaboratorio:
    """Como Resend: acepta, «manda» y recuerda la clave. `cortar_la_respuesta`
    hace que la primera petición SÍ se mande pero la respuesta no llegue
    (ReadTimeout): el caso que de verdad duplica."""

    def __init__(self, cortar_la_respuesta: int = 0):
        self.buzon: list[dict] = []
        self.claves: dict[str, str] = {}
        self.cortes = cortar_la_respuesta
        self.peticiones = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.peticiones += 1
        cuerpo = json.loads(request.content)
        clave = request.headers.get("Idempotency-Key")
        if clave and clave in self.claves:
            return httpx.Response(200, json={"id": self.claves[clave]})  # no se manda otra vez
        email_id = f"email-{len(self.buzon) + 1}"
        self.buzon.append({"to": cuerpo["to"], "subject": cuerpo["subject"], "clave": clave})
        if clave:
            self.claves[clave] = email_id
        if self.cortes > 0:
            self.cortes -= 1
            raise httpx.ReadTimeout("la respuesta no llegó", request=request)
        return httpx.Response(200, json={"id": email_id})


@pytest.fixture()
def resend(monkeypatch):
    def montar(lab: ResendDeLaboratorio) -> ResendDeLaboratorio:
        cliente = httpx.AsyncClient(transport=httpx.MockTransport(lab))
        monkeypatch.setattr(emails, "cliente_de_servicios", lambda: cliente)
        return lab
    return montar


async def _dos_pasadas(tenant) -> FakeDB:
    db = FakeDB(citas=[_cita()])
    tenant["db"] = db
    await recordatorios.ejecutar(_settings())  # se manda; la respuesta se pierde
    await recordatorios.ejecutar(_settings())  # se reintenta
    return db


@pytest.mark.anyio
async def test_un_reintento_no_le_manda_dos_recordatorios_a_la_clienta(resend, tenant):
    lab = resend(ResendDeLaboratorio(cortar_la_respuesta=1))
    db = await _dos_pasadas(tenant)
    print(f"\npeticiones a Resend: {lab.peticiones} · correos en el buzón de la clienta: {len(lab.buzon)}")
    assert lab.peticiones == 2, "el reintento tiene que ocurrir: si no, el recordatorio se pierde"
    assert len(lab.buzon) == 1, lab.buzon
    assert "cita-1" in db.selladas, "tras el reintento bueno, la cita queda sellada"


@pytest.mark.anyio
async def test_CONTROL_con_una_clave_que_cambia_en_cada_intento_salen_dos(resend, tenant, monkeypatch):
    intento = {"n": 0}

    def clave_por_intento(*partes):
        intento["n"] += 1
        return f"{'-'.join(map(str, partes))}-intento-{intento['n']}"

    monkeypatch.setattr(recordatorios, "clave_idempotencia", clave_por_intento)
    lab = resend(ResendDeLaboratorio(cortar_la_respuesta=1))
    await _dos_pasadas(tenant)
    assert len(lab.buzon) == 2, "el laboratorio ya no distingue un duplicado: la medida de arriba no vale"


@pytest.mark.anyio
async def test_un_409_de_idempotencia_es_ya_aceptado(monkeypatch):
    def lab(request):
        return httpx.Response(409, json={"name": "invalid_idempotent_request", "message": "otro cuerpo"})

    cliente = httpx.AsyncClient(transport=httpx.MockTransport(lab))
    monkeypatch.setattr(emails, "cliente_de_servicios", lambda: cliente)
    await emails.enviar_email_branded(_settings(), "a@b.test", "x", "<p>x</p>", clave="k")  # no lanza
    with pytest.raises(emails.EnvioFallido):  # sin clave, un 409 sigue siendo un rechazo
        await emails.enviar_email_branded(_settings(), "a@b.test", "x", "<p>x</p>")
