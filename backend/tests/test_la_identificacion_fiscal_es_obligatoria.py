"""LA IDENTIFICACIÓN FISCAL ES OBLIGATORIA, EN EL ALTA Y EN EL PERFIL.

Fue opcional un tiempo, sin saber que iba a hacer falta para facturar: la
factura sin IVA por inversión del sujeto pasivo exige el número fiscal del
salón. Si alguien vuelve a dejarla entrar vacía —en un esquema, en un validador
o en la base—, este guarda se pone rojo. Qué cuenta como «vacía» y por qué el
perfil puede no mandarla: `tests/cif_opcional.py`. La mitad de la pantalla es
`lib/cif-obligatorio.test.ts`.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.identificacion_fiscal import exigir_identificacion, limpiar_identificacion
from tests.cif_opcional import huecos_de, huecos_en_sql, todas_las_puertas, todos


def test_ninguna_puerta_deja_entrar_la_identificacion_fiscal_vacia():
    huecos = todos()

    assert huecos == [], "La identificación fiscal entra vacía:\n  " + "\n  ".join(str(h) for h in huecos)


def test_mira_de_verdad_el_alta_y_el_perfil():
    """Antivacío: el descubridor tiene que encontrar las dos puertas por las
    que entra el dato. Si dejara de verlas, el guarda pasaría sin mirar nada."""
    vistas = {(p.metodo, p.esquema.__name__) for p in todas_las_puertas()}

    assert {("post", "SignupIn"), ("patch", "SalonProfileUpdate")} <= vistas, vistas


# ── LOS CONTROLES ─────────────────────────────────────────────────────────

class _AltaOpcional(BaseModel):
    """Así estaba el alta cuando era opcional."""

    email: str
    cif: str | None = Field(default=None, max_length=40)

    @field_validator("cif")
    @classmethod
    def _v(cls, v):
        return limpiar_identificacion(v)


class _AltaObligatoria(BaseModel):
    """Y así está ahora."""

    email: str
    cif: str = Field(max_length=40)

    @field_validator("cif")
    @classmethod
    def _v(cls, v):
        return exigir_identificacion(v)


class _PerfilObligatorio(BaseModel):
    cif: str | None = Field(default=None, max_length=40)

    @field_validator("cif")
    @classmethod
    def _v(cls, v):
        return exigir_identificacion(v)


class _SoloLaAnotacion(BaseModel):
    """`str` sin validador: exige que venga, pero «» y los espacios entran."""

    cif: str = Field(max_length=40)


def test_EL_CONTROL_el_alta_opcional_de_la_entrega_pasada_se_detecta():
    huecos = huecos_de(_AltaOpcional, "post")

    # Sin el campo, null, «», espacios y un salto: las cinco formas.
    assert len(huecos) == 5, huecos


def test_EL_CONTROL_el_perfil_opcional_de_la_entrega_pasada_se_detecta():
    """En un patch faltar no cuenta; mandarlo vacío, sí (4 formas)."""
    assert len(huecos_de(_AltaOpcional, "patch")) == 4


def test_EL_CONTROL_la_anotacion_sola_no_basta():
    """El caso que un guarda que solo mirase la anotación dejaría pasar: `str`
    obliga a mandarlo, pero no a escribir algo."""
    huecos = huecos_de(_SoloLaAnotacion, "post")

    assert [h.motivo for h in huecos] == ["entra con ''", "entra con '   '", "entra con '\\n'"]


def test_EL_CONTROL_obligatoria_no_se_denuncia():
    assert huecos_de(_AltaObligatoria, "post") == []
    assert huecos_de(_PerfilObligatorio, "patch") == []


def test_EL_CONTROL_la_tarjeta_del_local_puede_no_mandarlo():
    """La excepción, fijada: el mismo esquema, como `post`, sí se denuncia por
    faltar. Como `patch`, no: es la tarjeta del local."""
    assert [h.motivo for h in huecos_de(_PerfilObligatorio, "post")] == ["entra sin el campo"]


class _RechazoDelModelo(BaseModel):
    """La forma de HEAD: el campo admite «   » y lo rechaza el modelo."""

    email: str
    cif: str = Field(min_length=1, max_length=40)

    @model_validator(mode="after")
    def _v(self):
        if not self.cif.strip():
            raise ValueError("Falta la identificación fiscal.")
        return self


def test_EL_CONTROL_un_rechazo_del_modelo_no_basta_a_proposito():
    """La regla escrita en `cif_opcional.py`: el error tiene que ser del campo.
    Un validador de modelo no corre si falta otro campo, así que sin un cuerpo
    completo no se puede saber si rechaza: se cuenta como hueco."""
    huecos = huecos_de(_RechazoDelModelo, "post")

    assert [h.motivo for h in huecos] == ["entra con '   '", "entra con '\\n'"]


def test_EL_CONTROL_y_en_la_base():
    assert huecos_en_sql("alter table public.accounts alter column cif drop not null;")
    assert huecos_en_sql("alter table accounts drop constraint accounts_cif_presente;")
    assert huecos_en_sql("ALTER TABLE public.accounts\n  DROP COLUMN cif;")
    # La 0051 quitó `salons.cif`, que era la copia vieja: no es la de la cuenta.
    assert huecos_en_sql("alter table public.salons drop column cif;") == []
    # Un comentario que lo cuenta no es hacerlo.
    assert huecos_en_sql("-- alter table accounts alter column cif drop not null;\nselect 1;") == []
