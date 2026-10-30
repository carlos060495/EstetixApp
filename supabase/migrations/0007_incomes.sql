-- Fase 1 · Paso 8 — Ingresos manuales (ventas de producto y otros ingresos).
--
-- Modelo financiero confirmado: los ingresos por SERVICIOS no se registran a
-- mano (se derivan de las citas 'completada' y el precio de su servicio);
-- esta tabla recoge solo ventas de producto y otros ingresos manuales.
-- Finanzas es solo-admin: mismas políticas que expenses (0003 §4.4).

create type ingreso_tipo as enum ('producto', 'otro');

create table public.incomes (
  id uuid primary key default gen_random_uuid(),
  salon_id uuid not null references public.salons(id) on delete cascade,
  fecha date not null default current_date,
  concepto text not null,
  tipo ingreso_tipo not null default 'producto',
  importe numeric(10,2) not null check (importe >= 0),
  product_id uuid references public.products(id) on delete set null,
  created_at timestamptz not null default now()
);
create index on public.incomes (salon_id, fecha);

alter table public.incomes enable row level security;

create policy "incomes_all_admin" on public.incomes
  for all using (salon_id = public.current_salon_id() and public.is_admin_rol())
  with check (salon_id = public.current_salon_id() and public.is_admin_rol());
