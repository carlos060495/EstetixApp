-- Fase 1 · Paso 10 — Antifuerza bruta PIN quiosco.
--
-- 10.000 combinaciones posibles (0000-9999): se añade un bloqueo temporal
-- por empleado tras varios fallos para reducir ataques de enumeración.

create table if not exists public.pin_attempt_locks (
  salon_id uuid not null references public.salons(id) on delete cascade,
  employee_id uuid not null references public.employees(id) on delete cascade,
  failed_count integer not null default 0 check (failed_count >= 0),
  last_failed_at timestamptz,
  locked_until timestamptz,
  primary key (salon_id, employee_id)
);

create index if not exists pin_attempt_locks_locked_idx
  on public.pin_attempt_locks (locked_until);

alter table public.pin_attempt_locks enable row level security;

-- Lectura opcional para administración interna del tenant (si se expone en futuro).
create policy "pin_attempt_locks_select_admin"
  on public.pin_attempt_locks
  for select
  using (salon_id = public.current_salon_id() and public.is_admin_rol());
