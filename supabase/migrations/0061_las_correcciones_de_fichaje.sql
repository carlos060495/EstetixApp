-- 0061 · Las correcciones de fichaje
--
-- Desde la entrega del «en curso» la app marca días como «Incompleto · falta
-- una salida» y no había forma de arreglarlos. Arreglarlos no es editar un
-- campo: es el registro de jornada, y lo que hay que hacer es DEJAR CONSTANCIA.
--
-- ═══ LO QUE MARCÓ EL RELOJ NO SE TOCA NUNCA ═══
--
-- `time_entries` sigue siendo lo que se fichó en el quiosco, tal cual. Una
-- corrección NO lo reescribe: se AÑADE encima, en una tabla aparte, y el
-- historial calcula el día con las dos cosas. Así siempre se puede enseñar qué
-- se fichó, qué se corrigió, quién, cuándo y por qué. Y para que no dependa de
-- la buena voluntad del código, esta migración hace que la base RECHACE
-- cualquier UPDATE de `time_entries` (ver 3). El DELETE sigue: borrar una
-- empleada o un centro arrastra sus fichajes, y eso no es corregir.
--
-- ═══ UNA CORRECCIÓN ES UNA FILA, Y LA FILA ES EL REGISTRO ═══
--
-- Cada fila cambia UNA marca:
--
--   · 'nueva'  — una marca que no se fichó: la salida que falta, o la entrada
--                y la salida de una jornada que nadie fichó. `ts_antes` vacío.
--   · 'cambia' — otra hora para una marca: una entrada o una salida mal
--                fichadas. `ts_antes` → `ts_despues`.
--   · 'anula'  — una marca que no debía existir (se fichó en el centro
--                equivocado, se tocó el botón sin querer). NO se borra: se
--                queda en el historial, tachada y con su motivo, y no cuenta.
--                `ts_despues` vacío.
--
-- `marca_id` es la marca afectada: el `id` de `time_entries`, o el de una
-- marca 'nueva' (que nace aquí y se puede cambiar o anular después igual).
-- Las filas de una misma corrección —la entrada y la salida de una jornada
-- entera— comparten `lote` y motivo.
--
-- Esta tabla es de la misma familia que los registros de dinero (0054) y de
-- descargas (0056): con quién, cuándo, qué había antes y qué hay ahora, y sin
-- poder editarse ni borrarse. Por eso no hay una segunda tabla de «auditoría»
-- que copie lo mismo: dos copias pueden acabar diciendo cosas distintas, y una
-- sola no. El guarda `tests/test_los_registros_sobreviven.py` la descubre por
-- su trigger y le exige lo mismo que a las otras dos.
--
-- EL MOTIVO es obligatorio y lo escribe quien corrige: nada de una lista
-- cerrada. La base exige al menos 10 caracteres que no sean espacios.
--
-- ═══ NO SE PUEDE EDITAR NI BORRAR, Y LO IMPIDE LA BASE (como 0054/0056) ═══
--
--   1. PERMISOS: `service_role` solo INSERT y SELECT; la sesión, nada.
--   2. RLS activada y sin políticas: se lee desde el backend, que comprueba
--      que quien mira es la administradora del centro.
--   3. UN TRIGGER contra UPDATE, DELETE y TRUNCATE.
--   SIN CLAVES AJENAS y CON INSTANTÁNEA de nombres (0057): la fila sigue
--   diciendo quién corrigió y a quién aunque ya no existan.
--
-- ═══ LA REGLA DE LA COLUMNA OBLIGATORIA (supabase/README.md) ═══
--
-- Tabla nueva: solo la escribe el backend nuevo. El trigger nuevo de
-- `time_entries` no rompe nada desplegado: ningún backend actualiza fichajes,
-- solo los inserta (comprobado en `routers/fichaje.py`).
--
-- ═══ ORDEN DE DESPLIEGUE: 0061 → BACKEND → FRONTEND ═══
--
-- El backend nuevo lee esta tabla para cada historial de fichajes: sin ella,
-- el historial contestaría 502.

begin;

-- ── La tabla ──────────────────────────────────────────────────────────────
create table public.correcciones_de_fichaje (
  id bigint generated always as identity primary key,
  lote uuid not null,
  account_id uuid not null,
  salon_id uuid not null,
  employee_id uuid not null,
  ocurrido_at timestamptz not null default now(),
  accion text not null check (accion in ('nueva', 'cambia', 'anula')),
  marca_id uuid not null,
  tipo public.fichaje_tipo not null,
  ts_antes timestamptz,
  ts_despues timestamptz,
  motivo text not null check (char_length(btrim(motivo)) >= 10),
  usuario_id uuid not null,
  -- Quien corrige sus PROPIAS horas (la administradora con ficha). Se permite
  -- —en un salón de una persona no hay nadie más— y queda dicho.
  sobre_si_misma boolean not null default false,
  -- La instantánea (0057).
  cuenta_nombre text,
  usuario_nombre text,
  usuario_email text,
  empleada_nombre text,
  centro_nombre text,
  constraint correcciones_de_fichaje_forma check (
    (accion = 'nueva'  and ts_antes is null     and ts_despues is not null) or
    (accion = 'cambia' and ts_antes is not null and ts_despues is not null) or
    (accion = 'anula'  and ts_antes is not null and ts_despues is null)
  )
);

comment on table public.correcciones_de_fichaje is
  'Cada corrección de un fichaje: se añade encima de time_entries, nunca lo '
  'reescribe. No se puede editar ni borrar: lo impiden los permisos y un trigger (0061).';

create index correcciones_de_fichaje_por_cuenta
  on public.correcciones_de_fichaje (account_id, ocurrido_at);

create index correcciones_de_fichaje_por_ficha
  on public.correcciones_de_fichaje (salon_id, employee_id, ocurrido_at);

-- Una marca se anula una vez. Dos administradoras a la vez no dejan dos
-- anulaciones de la misma marca: la segunda choca aquí.
create unique index correcciones_de_fichaje_una_anulacion
  on public.correcciones_de_fichaje (marca_id)
  where accion = 'anula';

-- ── 1. Permisos ───────────────────────────────────────────────────────────
revoke all on public.correcciones_de_fichaje from anon, authenticated, service_role;
grant select, insert on public.correcciones_de_fichaje to service_role;

-- ── 2. RLS sin políticas ──────────────────────────────────────────────────
alter table public.correcciones_de_fichaje enable row level security;

-- ── 3. El trigger del registro ────────────────────────────────────────────
create function public.correcciones_de_fichaje_no_se_tocan()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  raise exception 'correcciones_de_fichaje no se puede % : es un registro', tg_op
    using errcode = '42501';
end;
$$;

create trigger correcciones_de_fichaje_sin_update_ni_delete
  before update or delete on public.correcciones_de_fichaje
  for each row execute function public.correcciones_de_fichaje_no_se_tocan();

create trigger correcciones_de_fichaje_sin_truncate
  before truncate on public.correcciones_de_fichaje
  for each statement execute function public.correcciones_de_fichaje_no_se_tocan();

-- ── 4. Lo que marcó el reloj, intocable ───────────────────────────────────
-- Una hora fichada no se reescribe: se corrige encima. El DELETE no se toca
-- (las cascadas al borrar una empleada o un centro).
create function public.lo_que_marco_el_reloj_no_se_reescribe()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  raise exception 'time_entries no se puede actualizar: se corrige en correcciones_de_fichaje'
    using errcode = '42501';
end;
$$;

create trigger time_entries_sin_update
  before update on public.time_entries
  for each row execute function public.lo_que_marco_el_reloj_no_se_reescribe();

commit;
