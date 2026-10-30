-- Fase 1 · Paso 16D — Festivos y cierres del salón.
--
-- Excepciones POR FECHA al horario semanal (0022): festivos oficiales,
-- vacaciones y cierres puntuales. Una excepción siempre gana al horario semanal
-- del día que cubre.
--
-- Se guarda como RANGO (fecha_inicio…fecha_fin, ambos incluidos) y no como una
-- fila por día: una semana de vacaciones es una sola decisión del salón y debe
-- poder borrarse de una sola vez. Un día suelto es simplemente inicio = fin.
--
-- Dos formas:
--   · cerrado = true  → el salón no abre esos días.
--   · cerrado = false → horario ESPECIAL, con la misma forma que un día del
--     horario semanal (apertura/cierre y pausa opcional).

-- Necesaria para el EXCLUDE de más abajo: permite mezclar en un mismo índice
-- GiST una comparación por igualdad (salon_id) con una de solape (el rango).
create extension if not exists btree_gist;

create table public.salon_schedule_exceptions (
  id uuid primary key default gen_random_uuid(),
  salon_id uuid not null references public.salons(id) on delete cascade,
  fecha_inicio date not null,
  fecha_fin date not null,
  cerrado boolean not null default true,
  -- Solo se usan si cerrado = false. Se dejan NULL en los cierres para que no
  -- haya horas huérfanas que alguien pueda leer por error.
  apertura time,
  cierre time,
  tiene_pausa boolean not null default false,
  pausa_inicio time,
  pausa_fin time,
  -- Para que el salón sepa por qué cerró: «Navidad», «Vacaciones de agosto».
  motivo text,
  created_at timestamptz not null default now(),

  constraint salon_schedule_exceptions_rango_valido check (fecha_fin >= fecha_inicio),

  -- Un cierre no lleva horas; un horario especial las lleva TODAS y bien
  -- ordenadas. Con esto no puede existir una excepción «abierta» sin saber a
  -- qué hora abre.
  constraint salon_schedule_exceptions_horas_coherentes check (
    (cerrado and apertura is null and cierre is null
      and not tiene_pausa and pausa_inicio is null and pausa_fin is null)
    or (
      not cerrado
      and apertura is not null and cierre is not null and cierre > apertura
      and (
        (not tiene_pausa and pausa_inicio is null and pausa_fin is null)
        or (
          tiene_pausa
          and pausa_inicio is not null and pausa_fin is not null
          and pausa_fin > pausa_inicio
          and pausa_inicio >= apertura
          and pausa_fin <= cierre
        )
      )
    )
  ),

  -- Dos excepciones del MISMO salón no pueden cubrir la misma fecha. Si se
  -- permitiera, «¿qué horario tiene el 15 de agosto?» tendría dos respuestas y
  -- el cálculo de horas activas se volvería ambiguo. Se resuelve en la base de
  -- datos y no a base de comprobar-antes-de-insertar, que siempre tiene una
  -- carrera entre la comprobación y el insert.
  -- El rango es '[]' (ambos extremos incluidos), igual que se lee una fecha:
  -- del 1 al 7 de agosto son siete días, no seis.
  constraint salon_schedule_exceptions_sin_solape exclude using gist (
    salon_id with =,
    daterange(fecha_inicio, fecha_fin, '[]') with &&
  )
);

-- El cálculo de horas activas pregunta siempre «¿qué excepción cubre ESTA
-- fecha de ESTE salón?».
create index on public.salon_schedule_exceptions (salon_id, fecha_inicio, fecha_fin);

alter table public.salon_schedule_exceptions enable row level security;

-- Mismo patrón que salon_schedule (0022) y que el resto del inquilino (0003):
-- aislamiento por el claim salon_id del token, nunca por auth.uid(). Lectura
-- para todo el personal —la agenda la necesita para pintar el día—; escritura
-- solo admin, como el resto de la configuración del salón.
create policy "salon_schedule_exceptions_select" on public.salon_schedule_exceptions
  for select using (salon_id = public.current_salon_id());
create policy "salon_schedule_exceptions_write_admin" on public.salon_schedule_exceptions
  for all using (salon_id = public.current_salon_id() and public.is_admin_rol())
  with check (salon_id = public.current_salon_id() and public.is_admin_rol());

-- Permisos de tabla para los roles de PostgREST, explícitos por el mismo motivo
-- que en 0022: en el Supabase local una tabla nueva de `public` nace SIN
-- select/insert/update/delete para `authenticated` y PostgREST responde 403
-- aunque la RLS esté perfecta. `anon` queda fuera: aquí solo llega personal
-- autenticado.
grant select, insert, update, delete
  on public.salon_schedule_exceptions
  to authenticated, service_role;
