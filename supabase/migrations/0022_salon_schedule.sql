-- Fase 1 · Paso 16A — Horario de apertura del salón.
--
-- Una fila por día de la semana y salón (7 por salón). Se modela relacional y
-- no como un JSON en `salons` por dos razones: la agenda tendrá que preguntar
-- «¿qué horario tiene este salón el martes?» sin desenrollar un documento, y
-- así las invariantes se pueden exigir en la propia base de datos con CHECK,
-- no solo en el backend.
--
-- dia_semana sigue ISO-8601: 1 = lunes … 7 = domingo. Se elige el ISO y no el
-- getDay() de JavaScript (0 = domingo) para que el orden natural del número
-- coincida con el orden en que se lee un calendario aquí.
--
-- Sin pausa el día es un tramo continuo (10:00–20:00); con pausa queda partido
-- (10:00–14:00 y 16:00–20:00).

create table public.salon_schedule (
  id uuid primary key default gen_random_uuid(),
  salon_id uuid not null references public.salons(id) on delete cascade,
  dia_semana smallint not null check (dia_semana between 1 and 7),
  cerrado boolean not null default false,
  apertura time not null default '10:00',
  cierre time not null default '20:00',
  tiene_pausa boolean not null default false,
  pausa_inicio time,
  pausa_fin time,
  created_at timestamptz not null default now(),

  -- Un solo horario por día y salón. Además es la clave que usa el upsert del
  -- backend para guardar la semana entera en una sola sentencia.
  unique (salon_id, dia_semana),

  -- El día abre antes de cerrar. Se exige también en los días cerrados: las
  -- horas siguen ahí para cuando se vuelva a abrir, y no tiene sentido
  -- guardarlas al revés.
  constraint salon_schedule_tramo_valido check (cierre > apertura),

  -- La pausa existe si y solo si tiene_pausa, y cae DENTRO del tramo del día.
  -- Con esto la base de datos no puede acabar con «pausa de 16:00 a 15:00» ni
  -- con una pausa que empieza después de cerrar, pase lo que pase por encima.
  constraint salon_schedule_pausa_coherente check (
    (not tiene_pausa and pausa_inicio is null and pausa_fin is null)
    or (
      tiene_pausa
      and pausa_inicio is not null
      and pausa_fin is not null
      and pausa_fin > pausa_inicio
      and pausa_inicio >= apertura
      and pausa_fin <= cierre
    )
  )
);

create index on public.salon_schedule (salon_id, dia_semana);

alter table public.salon_schedule enable row level security;

-- Mismo patrón que el resto de tablas del inquilino (0003): aislamiento por el
-- claim salon_id del token, nunca por auth.uid(). Lectura para todo el
-- personal —la agenda la necesitará para pintar la jornada—; escritura solo
-- admin, como el resto de la configuración del salón.
create policy "salon_schedule_select" on public.salon_schedule
  for select using (salon_id = public.current_salon_id());
create policy "salon_schedule_write_admin" on public.salon_schedule
  for all using (salon_id = public.current_salon_id() and public.is_admin_rol())
  with check (salon_id = public.current_salon_id() and public.is_admin_rol());

-- Permisos de tabla para los roles de PostgREST. Van explícitos y no se dejan a
-- los privilegios por defecto del entorno: en el Supabase local una tabla nueva
-- de `public` nace SIN select/insert/update/delete para `authenticated`, y
-- PostgREST responde «403 permission denied for table salon_schedule» aunque la
-- RLS esté perfecta (está documentado en supabase/README.md). Con el grant aquí,
-- la migración se basta a sí misma en cualquier entorno.
--
-- `anon` queda fuera a propósito: al horario solo llega personal autenticado.
-- El grant es el permiso de entrar en la tabla; quién ve qué fila lo sigue
-- decidiendo la RLS de arriba.
grant select, insert, update, delete on public.salon_schedule to authenticated, service_role;
