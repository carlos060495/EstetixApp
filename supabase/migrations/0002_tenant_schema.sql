-- Tenant schema. Every table here carries salon_id and gets RLS in 0003.

create type plan_tipo as enum ('basico', 'pro', 'elite');
create type user_rol as enum ('admin', 'estilista', 'recepcionista');
create type categoria_tipo as enum ('servicio', 'producto');
create type comision_tipo as enum ('porcentaje', 'fijo');
create type cita_estado as enum ('pendiente', 'confirmada', 'completada', 'cancelada', 'no_show');
create type cita_canal as enum ('manual', 'whatsapp', 'instagram', 'google', 'online');
create type gasto_tipo as enum ('fijo', 'variable');
create type gasto_estado as enum ('pendiente', 'pagado');
create type fichaje_tipo as enum ('entrada', 'pausa', 'salida');
create type consent_tipo as enum ('rgpd', 'marketing', 'imagen', 'salud');

create table public.salons (
  id uuid primary key default gen_random_uuid(),
  nombre_comercial text not null,
  cif text,
  direccion text,
  telefono text,
  email text,
  logo_url text,
  plan plan_tipo not null default 'basico',
  stripe_customer_id text,
  created_at timestamptz not null default now()
);

create table public.users (
  id uuid primary key default gen_random_uuid(),
  salon_id uuid not null references public.salons(id) on delete cascade,
  nombre text not null,
  email text not null,
  rol user_rol not null default 'estilista',
  pin_fichaje_hash text,
  auth_user_id uuid references auth.users(id) on delete set null,
  created_at timestamptz not null default now(),
  unique (salon_id, email)
);
create index on public.users (salon_id);
create index on public.users (auth_user_id);

create table public.clients (
  id uuid primary key default gen_random_uuid(),
  salon_id uuid not null references public.salons(id) on delete cascade,
  nombre text not null,
  telefono text,
  email text,
  cumpleanos date,
  alergias text,
  notas text,
  vip boolean not null default false,
  total_gastado numeric(10,2) not null default 0,
  visitas integer not null default 0,
  created_at timestamptz not null default now()
);
create index on public.clients (salon_id);

create table public.service_categories (
  id uuid primary key default gen_random_uuid(),
  salon_id uuid not null references public.salons(id) on delete cascade,
  nombre text not null,
  tipo categoria_tipo not null default 'servicio',
  color text not null default '#C9A84C'
);
create index on public.service_categories (salon_id);

create table public.services (
  id uuid primary key default gen_random_uuid(),
  salon_id uuid not null references public.salons(id) on delete cascade,
  category_id uuid references public.service_categories(id) on delete set null,
  nombre text not null,
  precio numeric(10,2) not null check (precio >= 0),
  duracion_min integer not null check (duracion_min > 0 and duracion_min % 15 = 0),
  visible_online boolean not null default true
);
create index on public.services (salon_id);

create table public.employees (
  id uuid primary key default gen_random_uuid(),
  salon_id uuid not null references public.salons(id) on delete cascade,
  user_id uuid references public.users(id) on delete set null,
  nombre text not null,
  rol text not null,
  comision_tipo comision_tipo not null default 'porcentaje',
  comision_servicios numeric(5,2) not null default 0,
  comision_productos numeric(5,2) not null default 0
);
create index on public.employees (salon_id);

create table public.employee_services (
  employee_id uuid not null references public.employees(id) on delete cascade,
  service_id uuid not null references public.services(id) on delete cascade,
  primary key (employee_id, service_id)
);

create table public.appointments (
  id uuid primary key default gen_random_uuid(),
  salon_id uuid not null references public.salons(id) on delete cascade,
  client_id uuid references public.clients(id) on delete set null,
  employee_id uuid references public.employees(id) on delete set null,
  service_id uuid references public.services(id) on delete set null,
  inicio timestamptz not null,
  fin timestamptz not null check (fin > inicio),
  estado cita_estado not null default 'pendiente',
  notas text,
  canal cita_canal not null default 'manual',
  created_at timestamptz not null default now()
);
create index on public.appointments (salon_id);
create index on public.appointments (salon_id, employee_id, inicio);

create table public.products (
  id uuid primary key default gen_random_uuid(),
  salon_id uuid not null references public.salons(id) on delete cascade,
  category_id uuid references public.service_categories(id) on delete set null,
  nombre text not null,
  marca text,
  codigo_barras text,
  precio numeric(10,2) not null default 0 check (precio >= 0),
  stock integer not null default 0 check (stock >= 0),
  stock_min integer not null default 0 check (stock_min >= 0),
  venta_publico boolean not null default false,
  consumo_interno boolean not null default false
);
create index on public.products (salon_id);

create table public.providers (
  id uuid primary key default gen_random_uuid(),
  salon_id uuid not null references public.salons(id) on delete cascade,
  nombre text not null,
  categoria text,
  telefono text,
  email text,
  notas text,
  created_at timestamptz not null default now()
);
create index on public.providers (salon_id);

create table public.expenses (
  id uuid primary key default gen_random_uuid(),
  salon_id uuid not null references public.salons(id) on delete cascade,
  fecha date not null default current_date,
  categoria text not null,
  provider_id uuid references public.providers(id) on delete set null,
  tipo gasto_tipo not null default 'variable',
  estado gasto_estado not null default 'pendiente',
  importe numeric(10,2) not null check (importe >= 0),
  factura_url text
);
create index on public.expenses (salon_id);

create table public.time_entries (
  id uuid primary key default gen_random_uuid(),
  salon_id uuid not null references public.salons(id) on delete cascade,
  employee_id uuid not null references public.employees(id) on delete cascade,
  tipo fichaje_tipo not null,
  timestamp timestamptz not null default now()
);
create index on public.time_entries (salon_id);
create index on public.time_entries (salon_id, employee_id, timestamp);

create table public.automations (
  id uuid primary key default gen_random_uuid(),
  salon_id uuid not null references public.salons(id) on delete cascade,
  tipo text not null,
  activo boolean not null default false,
  mensaje_plantilla text,
  disparador_config jsonb not null default '{}'::jsonb
);
create index on public.automations (salon_id);

create table public.subscriptions (
  id uuid primary key default gen_random_uuid(),
  salon_id uuid not null references public.salons(id) on delete cascade,
  stripe_subscription_id text,
  plan plan_tipo not null default 'basico',
  estado text not null default 'activa',
  periodo_fin timestamptz
);
create index on public.subscriptions (salon_id);

create table public.consents (
  id uuid primary key default gen_random_uuid(),
  salon_id uuid not null references public.salons(id) on delete cascade,
  client_id uuid not null references public.clients(id) on delete cascade,
  tipo consent_tipo not null,
  firmado_at timestamptz not null default now(),
  documento_url text
);
create index on public.consents (salon_id);
