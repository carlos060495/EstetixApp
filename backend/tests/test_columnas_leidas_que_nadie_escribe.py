"""Columnas que la app lee y que no escribe nadie. Ver tests/columnas_sin_escritor.py.

TRINQUETE. Las que hay hoy están en `columnas_sin_escritor_pendientes.json`,
para decidir qué se hace con cada una. El test falla si aparece una
NUEVA (alguien lee una columna que nadie escribe) y también si una de la lista
deja de estarlo (se arregló: hay que quitarla de la lista, para que no vuelva a
colarse en silencio).

Los controles van primero: un censo que dejara de ver lecturas o escrituras
diría «todo bien» sobre un mapa vacío.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.columnas_sin_escritor import (
    Censo,
    censar_python,
    censar_sql,
    censo_del_repositorio,
    columnas_de_select,
    columnas_de_vistas,
)

PENDIENTES = Path(__file__).with_name("columnas_sin_escritor_pendientes.json")

# ═══════════════════════════════════════════════ controles con código de mentira

_MIGRACION = """
create table public.clients (
  id uuid primary key default gen_random_uuid(),
  salon_id uuid not null,
  nombre text not null,
  visitas integer not null default 0,
  total_gastado numeric(10,2) not null default 0,
  vip boolean not null default false,
  created_at timestamptz not null default now()
);
create table public.citas (
  id uuid primary key default gen_random_uuid(),
  client_id uuid,
  estado text not null default 'pendiente',
  nota text,
  persona_id uuid,
  cuenta_id uuid,
  sello text
);
create table public.cuentas (id uuid primary key default gen_random_uuid(), plan text default 'basico');
create function public.cerrar_cita(p uuid) returns void language sql as $$
  update public.citas set estado = 'completada' where id = p;
$$;
create function public.antes_de_cita() returns trigger language plpgsql as $$
begin
  new.persona_id := null;
  select c.id into new.cuenta_id from public.cuentas c limit 1;
  return new;
end $$;
create trigger antes_de_cita before insert on public.citas
  for each row execute function public.antes_de_cita();
update public.citas set sello = 'migrado';
create view public.clientas_vista with (security_invoker = true) as
select c.id, c.nombre, c.visitas, 0 as calculada from public.clients c;
"""

_CODIGO = '''
from pydantic import BaseModel

class ClienteNueva(BaseModel):
    nombre: str
    vip: bool = False

async def crear(db, payload: ClienteNueva, salon_id):
    body = payload.model_dump()
    body["salon_id"] = salon_id
    return await db.insert("clients", body)

async def listar(db):
    return await db.select("clients", {"select": "id,nombre,visitas,total_gastado,vip,created_at",
                                       "order": "nombre.asc,created_at.desc"})

async def citas(db):
    return await db.select("citas", {"select": "id,estado,nota,persona_id,cuenta_id,sello,clients(nombre)",
                                     "or": "(estado.eq.x,client_id.is.null)"})

async def por_la_vista(db):
    return await db.select("clientas_vista", {"select": "id,visitas,calculada"})
'''


def _censo_de_mentira() -> tuple[Censo, dict[str, set[str]]]:
    censo = Censo()
    ficheros = [("0001_mentira.sql", _MIGRACION)]
    censar_sql(ficheros, censo)
    vistas = columnas_de_vistas(ficheros)
    censar_python([("app/mentira.py", _CODIGO)], {"clients", "citas", "cuentas", "clientas_vista"}, censo)
    from tests.columnas_sin_escritor import _a_tablas

    _a_tablas(censo, vistas)
    esquema = {
        "clients": {"id", "salon_id", "nombre", "visitas", "total_gastado", "vip", "created_at"},
        "citas": {"id", "client_id", "estado", "nota", "persona_id", "cuenta_id", "sello"},
        "cuentas": {"id", "plan"},
    }
    return censo, esquema


def test_CONTROL_encuentra_el_caso_de_visitas():
    """Lo que se nos coló: `visitas` y `total_gastado` con `default 0`, leídas
    en la lista y sin nadie que las escriba. Y `nota` y `client_id` de las
    citas, que tampoco escribe nadie en este código de mentira."""
    censo, esquema = _censo_de_mentira()
    assert censo.leidas_sin_escritor(esquema) == [
        ("citas", "client_id"), ("citas", "nota"), ("clients", "total_gastado"), ("clients", "visitas"),
    ]


def test_CONTROL_cada_camino_de_escritura_cuenta():
    censo, _ = _censo_de_mentira()
    escrito = lambda t, c: bool(censo.escrituras.get((t, c)))
    assert escrito("clients", "nombre")      # por model_dump de un modelo Pydantic
    assert escrito("clients", "vip")         # campo del mismo modelo, con default constante
    assert escrito("clients", "salon_id")    # body["salon_id"] = ...
    assert escrito("clients", "created_at")  # default now()
    assert escrito("clients", "id")          # default gen_random_uuid()
    assert escrito("citas", "estado")        # update dentro de una función SQL
    assert escrito("citas", "persona_id")    # new.x := en el trigger de esa tabla
    assert escrito("citas", "cuenta_id")     # select ... into new.x
    assert not escrito("clients", "visitas")  # default 0: NO es escribir


def test_CONTROL_un_relleno_de_migracion_queda_marcado():
    censo, esquema = _censo_de_mentira()
    assert censo.solo_rellenadas(esquema) == [("citas", "sello")]


def test_CONTROL_un_bloque_do_es_un_relleno_y_no_una_funcion():
    """Un `do $$` escribe una vez, al migrar: queda marcado como relleno, y no
    se le atribuye a la función que tenga delante en el fichero."""
    sql = (
        "create function public.f() returns void language plpgsql as $$ begin\n"
        "  update public.t set de_la_funcion = 1;\nend $$;\n"
        "do $$ begin update public.t set del_do = null where x = 1; end $$;\n"
    )
    censo = Censo()
    censar_sql([("0099_zz.sql", sql)], censo)
    assert censo.escrituras[("t", "del_do")] == {"relleno:0099_zz.sql"}
    assert censo.escrituras[("t", "de_la_funcion")] == {"sql:f"}


def test_CONTROL_cada_camino_de_lectura_cuenta():
    censo, _ = _censo_de_mentira()
    leido = lambda t, c: bool(censo.lecturas.get((t, c)))
    assert leido("clients", "nombre")      # embed clients(nombre) desde citas
    assert leido("citas", "client_id")     # dentro del or=(...)
    assert leido("clients", "created_at")  # en el order, segunda columna
    assert leido("clients", "visitas")     # por la vista, que la copia tal cual
    assert not leido("clientas_vista", "calculada")  # calculada en la vista: no es de nadie


def test_CONTROL_el_select_se_parte_bien():
    assert columnas_de_select("id,cuenta:accounts!fk(plan,suspendido),services(nombre),x::text", "t") == [
        ("t", "id"), ("accounts", "plan"), ("accounts", "suspendido"), ("services", "nombre"), ("t", "x"),
    ]


# ═══════════════════════════════════════════════ controles contra el repositorio


@pytest.fixture(scope="module")
def repo() -> tuple[Censo, dict[str, set[str]]]:
    return censo_del_repositorio()


def test_CONTROL_ve_lecturas_y_escrituras_de_verdad(repo):
    censo, _ = repo
    assert len(censo.lecturas) > 200 and len(censo.escrituras) > 500
    assert censo.lecturas[("clients", "nombre")]                 # por la vista de la 0058
    assert censo.lecturas[("appointments", "estado")]
    assert any(e.startswith("backend:") for e in censo.escrituras[("appointments", "estado")])
    assert any(e.startswith("sql:") for e in censo.escrituras[("users", "account_id")])  # select into new.
    assert any(e.startswith("guion:") for e in censo.escrituras[("admin_users", "password_hash")])


def test_las_visitas_ya_no_se_leen_de_la_tabla(repo):
    """Lo arreglado: visitas y gastado salen de la vista."""
    censo, _ = repo
    assert ("clients", "visitas") not in censo.lecturas
    assert ("clients", "total_gastado") not in censo.lecturas


def test_lo_que_no_sabe_resolver_no_crece(repo):
    """Llamadas cuya tabla va en una variable: el censo no las ve. Que no crezcan
    sin que nadie lo decida."""
    censo, _ = repo
    assert len(censo.sin_resolver) <= 7, "\n".join(censo.sin_resolver)


# ═══════════════════════════════════════════════ el trinquete


def test_columnas_leidas_que_nadie_escribe(repo):
    censo, esquema = repo
    hoy = {
        "sin_escritor": [f"{t}.{c}" for t, c in censo.leidas_sin_escritor(esquema)],
        "solo_guion_o_relleno": [f"{t}.{c}" for t, c in censo.solo_rellenadas(esquema)],
    }
    apuntadas = json.loads(PENDIENTES.read_text(encoding="utf-8"))
    for clave in hoy:
        nuevas = sorted(set(hoy[clave]) - set(apuntadas[clave]))
        arregladas = sorted(set(apuntadas[clave]) - set(hoy[clave]))
        assert not nuevas, (
            f"{clave}: columnas que se leen y no escribe nadie: {nuevas}. "
            "O alguien tiene que escribirlas, o la app no debería leerlas."
        )
        assert not arregladas, f"{clave}: ya tienen escritor; quítalas de {PENDIENTES.name}: {arregladas}"
