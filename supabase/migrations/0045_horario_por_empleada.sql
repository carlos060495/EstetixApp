-- 0045 · Horario por empleada
--
-- Hasta aquí la disponibilidad de una empleada salía del horario del SALÓN
-- menos su estado, sus servicios y sus citas. Un salón no podía decir «Ana
-- trabaja solo por las mañanas», así que el bot y el widget ofrecían huecos con
-- gente que no estaba: una clienta que se presenta y no la atiende nadie. Pasa
-- con dos centros y con uno.
--
-- ═══ LA PROPIEDAD QUE HACE DESPLEGABLE ESTA MIGRACIÓN ═══
--
-- UNA FICHA SIN FILAS AQUÍ SE COMPORTA EXACTAMENTE COMO HOY: disponible en todo
-- el horario del salón. Estas tablas nacen vacías y nadie las rellena hasta
-- que exista la pantalla (entrega 2), así que el día del despliegue no cambia
-- nada para ningún salón. Es la misma equivalencia de la Fase A.
--
-- Y EL HORARIO DEL SALÓN ES EL TECHO. Lo que se guarda aquí se INTERSECTA con
-- él (app/core/horario_empleada.py): si el salón cierra a las 20:00, el horario
-- de nadie pasa de ahí. No es una excepción que lo sobrescriba.
--
-- ═══ LA MISMA FORMA QUE LAS DEL SALÓN, A PROPÓSITO ═══
--
-- `employee_schedule` es `salon_schedule` con la ficha en vez del salón, y
-- `employee_schedule_exceptions` es `salon_schedule_exceptions` igual. Así el
-- cálculo de tramos, los validadores y el editor de un día son los mismos, y
-- un arreglo en uno no se queda sin llegar al otro. Los nombres de columna se
-- leen distinto para una persona y significan lo mismo:
--
--   apertura / cierre         → entra / sale
--   tiene_pausa + pausa_*     → jornada partida (el hueco del medio)
--   cerrado                   → libra ese día
--
-- ═══ POR FICHA, NO POR PERSONA ═══
--
-- Una persona con ficha en dos centros tiene DOS horarios, uno por centro. De
-- ahí sale sola la regla de «un centro al día»: el salón dice dónde trabaja
-- cada día, y el sistema no adivina nada. El cruce entre las dos fichas (que
-- el martes no trabaje a la vez aquí y allí) lo rechaza el backend al guardar;
-- no es restricción de base porque aquí no hay riesgo de doble cita: eso ya lo
-- impide la de la 0044 sobre `appointments`.
--
-- LA EQUIVALENCIA A MEDIAS, que sorprende a quien no la conoce: si solo UNA de
-- sus dos fichas tiene horario, la otra sigue ofreciéndola todo el día en su
-- centro. Es la consecuencia directa de «sin filas = como hoy», y la pantalla
-- tiene que decirlo (entrega 2).
--
-- ═══ LA REGLA DE LA COLUMNA OBLIGATORIA (supabase/README.md) ═══
--
-- No se añade ninguna columna a una tabla existente: solo dos tablas nuevas en
-- las que el backend anterior no escribe. No hay ventana de despliegue que
-- romper.

-- ═══════════════════════════════════════════ 1. la semana de cada ficha

create table public.employee_schedule (
  id uuid primary key default gen_random_uuid(),
  employee_id uuid not null references public.employees(id) on delete cascade,
  dia_semana smallint not null check (dia_semana between 1 and 7),
  cerrado boolean not null default false,
  apertura time not null default '10:00',
  cierre time not null default '20:00',
  tiene_pausa boolean not null default false,
  pausa_inicio time,
  pausa_fin time,
  created_at timestamptz not null default now(),
  unique (employee_id, dia_semana),
  constraint employee_schedule_tramo_valido check (cierre > apertura),
  constraint employee_schedule_pausa_coherente check (
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

comment on table public.employee_schedule is
  'Qué días y en qué franjas trabaja una ficha en su centro. Sin filas = sin '
  'horario propio: disponible en todo el horario del salón, como antes de la '
  '0045. Con filas, se intersecta con el horario del salón (el techo).';

create index on public.employee_schedule (employee_id, dia_semana);

-- ═══════════════════════════════════ 2. vacaciones, días libres y cambios

-- `btree_gist` ya lo creó la 0023; se repite por si esta migración se aplica
-- sobre una base que no la tuviera.
create extension if not exists btree_gist;

create table public.employee_schedule_exceptions (
  id uuid primary key default gen_random_uuid(),
  employee_id uuid not null references public.employees(id) on delete cascade,
  fecha_inicio date not null,
  fecha_fin date not null,
  cerrado boolean not null default true,
  apertura time,
  cierre time,
  tiene_pausa boolean not null default false,
  pausa_inicio time,
  pausa_fin time,
  motivo text,
  created_at timestamptz not null default now(),
  constraint employee_schedule_exceptions_rango_valido check (fecha_fin >= fecha_inicio),
  constraint employee_schedule_exceptions_horas_coherentes check (
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
  -- Como en el salón: dos excepciones de la misma ficha no pueden cubrir la
  -- misma fecha, y eso lo decide la base, no un «comprobar antes de insertar»
  -- que tendría carrera. El backend traduce el 23P01 a un 409 legible.
  constraint employee_schedule_exceptions_sin_solape exclude using gist (
    employee_id with =,
    daterange(fecha_inicio, fecha_fin, '[]') with &&
  )
);

comment on table public.employee_schedule_exceptions is
  'Excepciones por fecha al horario de una ficha: vacaciones, un día libre o '
  'un cambio puntual. Ganan a su semana. Valen también para una ficha sin '
  'horario propio. Nunca pasan del horario del salón.';

create index on public.employee_schedule_exceptions (employee_id, fecha_inicio, fecha_fin);

-- ═══════════════════════════════════════════════════════ 3. la frontera

-- EL PATRÓN DE `employee_services` (0039): la tabla no lleva `salon_id`, así
-- que la frontera va por la ficha. Se ve la fila si su ficha es de un centro
-- visible para quien pregunta.
--
-- LEER, CUALQUIERA DEL CENTRO. Decisión de Carlos: la empleada va a ver la
-- agenda completa, y si Eva tiene citas el martes, Eva trabaja el martes.
-- Esconder el horario mientras se enseña la agenda no protegería nada.
--
-- ESCRIBIR, SOLO ADMIN, igual que el horario del salón.

alter table public.employee_schedule enable row level security;
alter table public.employee_schedule_exceptions enable row level security;

create policy "employee_schedule_select" on public.employee_schedule
  as permissive for select
  to public
  using (exists (
    select 1 from public.employees e
    where e.id = employee_schedule.employee_id
      and e.salon_id = any (public.salones_visibles())
  ));

create policy "employee_schedule_write_admin" on public.employee_schedule
  as permissive for all
  to public
  using (public.is_admin_rol() and exists (
    select 1 from public.employees e
    where e.id = employee_schedule.employee_id
      and e.salon_id = any (public.salones_visibles())
  ))
  with check (public.is_admin_rol() and exists (
    select 1 from public.employees e
    where e.id = employee_schedule.employee_id
      and e.salon_id = any (public.salones_visibles())
  ));

create policy "employee_schedule_exceptions_select" on public.employee_schedule_exceptions
  as permissive for select
  to public
  using (exists (
    select 1 from public.employees e
    where e.id = employee_schedule_exceptions.employee_id
      and e.salon_id = any (public.salones_visibles())
  ));

create policy "employee_schedule_exceptions_write_admin" on public.employee_schedule_exceptions
  as permissive for all
  to public
  using (public.is_admin_rol() and exists (
    select 1 from public.employees e
    where e.id = employee_schedule_exceptions.employee_id
      and e.salon_id = any (public.salones_visibles())
  ))
  with check (public.is_admin_rol() and exists (
    select 1 from public.employees e
    where e.id = employee_schedule_exceptions.employee_id
      and e.salon_id = any (public.salones_visibles())
  ));

-- UNA TABLA NUEVA NACE SIN DML PARA NADIE, service_role incluido (ver la 0029).
-- Sin estas líneas el backend respondería «permission denied» con la RLS
-- perfecta. `service_role` hace falta además por el widget público, que lee
-- la disponibilidad sin sesión.
grant select, insert, update, delete on public.employee_schedule to authenticated, service_role;
grant select, insert, update, delete on public.employee_schedule_exceptions to authenticated, service_role;
