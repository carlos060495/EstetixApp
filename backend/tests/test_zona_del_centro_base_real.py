"""La zona horaria del centro contra la base de verdad (0060).

  · Un centro nuevo nace en la península, sin que nadie la mande.
  · La base rechaza una zona que no esté en la lista, aunque la API no estuviera.
  · `zona_del_centro` la lee con el token del salón (la RLS deja ver la suya).
"""

from __future__ import annotations

import pytest

from app.core import zona as zonas
from app.db.postgrest_client import PostgrestClient, PostgrestError
from tests.test_alta_completa_e2e import SECRET_KEY, SUPABASE_URL, _token_de_inquilino
from tests.test_camino_humano_dos_centros import (  # noqa: F401  (fixtures)
    anyio_backend,
    dos_centros,
)

pytestmark = [pytest.mark.base_real, pytest.mark.anyio]


async def test_un_centro_nuevo_nace_en_la_peninsula_y_se_lee_con_su_token(dos_centros):  # noqa: F811
    token = _token_de_inquilino(dos_centros["auth_user_id"], dos_centros["salon_id"])
    db = PostgrestClient(SUPABASE_URL, token, timeout=30.0)
    assert (await zonas.zona_del_centro(db, dos_centros["salon_id"])).key == "Europe/Madrid"
    assert (await zonas.zona_del_centro(db, dos_centros["centro_2"])).key == "Europe/Madrid"


async def test_canarias_se_guarda_y_una_inventada_no(dos_centros):  # noqa: F811
    servicio = PostgrestClient(SUPABASE_URL, SECRET_KEY, timeout=30.0)
    salon = dos_centros["centro_2"]
    await servicio.update("salons", {"id": f"eq.{salon}"}, {"zona_horaria": "Atlantic/Canary"})
    assert (await zonas.zona_del_centro(servicio, salon)).key == "Atlantic/Canary"
    with pytest.raises(PostgrestError):
        await servicio.update("salons", {"id": f"eq.{salon}"}, {"zona_horaria": "Europe/London"})
    assert (await zonas.zona_del_centro(servicio, salon)).key == "Atlantic/Canary"
