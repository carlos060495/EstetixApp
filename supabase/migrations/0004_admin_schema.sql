-- Super Admin platform tables. No salon_id, RLS deliberately OFF: these are
-- never queried with a tenant's anon/authenticated JWT, only from the
-- Railway backend using the service role after it has verified a separate
-- admin_users JWT + TOTP. See DIRECTRICES_PROYECTO.md §4.9.

create type admin_rol as enum ('owner', 'support', 'viewer');

create table public.admin_users (
  id uuid primary key default gen_random_uuid(),
  email text not null unique,
  nombre text not null,
  rol admin_rol not null default 'viewer',
  password_hash text not null,
  totp_secret text,
  totp_enabled boolean not null default false,
  created_at timestamptz not null default now(),
  ultimo_acceso timestamptz
);

create table public.admin_logs (
  id uuid primary key default gen_random_uuid(),
  admin_id uuid not null references public.admin_users(id),
  accion text not null,
  target_salon_id uuid references public.salons(id) on delete set null,
  detalle jsonb not null default '{}'::jsonb,
  ip text,
  timestamp timestamptz not null default now()
);
create index on public.admin_logs (admin_id);
create index on public.admin_logs (target_salon_id);

-- admin_logs is append-only at the database level, not just in application
-- code: no role (including owner) can UPDATE or DELETE a log row.
revoke update, delete on public.admin_logs from public, anon, authenticated;

create or replace function public.admin_logs_block_mutation()
returns trigger
language plpgsql
as $$
begin
  raise exception 'admin_logs is append-only: % is not allowed', tg_op;
end;
$$;

create trigger admin_logs_no_update
  before update on public.admin_logs
  for each row execute function public.admin_logs_block_mutation();

create trigger admin_logs_no_delete
  before delete on public.admin_logs
  for each row execute function public.admin_logs_block_mutation();

alter table public.admin_users enable row level security;
alter table public.admin_logs enable row level security;
-- No policies are created on purpose: default-deny for anon/authenticated.
-- Only the service role (which bypasses RLS) reads/writes these tables,
-- and only from the Railway backend after admin auth + TOTP have been verified.
