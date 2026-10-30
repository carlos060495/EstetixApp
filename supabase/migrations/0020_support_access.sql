-- Fase 2: acceso de soporte con consentimiento del salón.
--
-- Dos tablas con dueños distintos: el salón CREA códigos (con su JWT, bajo RLS)
-- y el panel los CONSUME (service role, sin RLS). impersonation_sessions es
-- además la lista de revocación: un JWT de 30 min no se puede invalidar solo,
-- así que cada petición en modo soporte comprueba aquí que la sesión sigue viva.

create table public.support_access_codes (
  id uuid primary key default gen_random_uuid(),
  salon_id uuid not null references public.salons(id) on delete cascade,
  code_hash text not null,
  created_by_user_id uuid references public.users(id) on delete set null,
  created_at timestamptz not null default now(),
  expires_at timestamptz not null,
  used_at timestamptz,
  used_by_admin_id uuid references public.admin_users(id),
  revoked_at timestamptz,
  intentos_fallidos int not null default 0
);
create index on public.support_access_codes (salon_id);

create table public.impersonation_sessions (
  id uuid primary key default gen_random_uuid(),
  admin_id uuid not null references public.admin_users(id),
  admin_email text not null,
  salon_id uuid not null references public.salons(id) on delete cascade,
  code_id uuid not null references public.support_access_codes(id),
  -- Identidad de tenant suplantada: se resuelve UNA vez al iniciar y se guarda,
  -- para que cada petición posterior no tenga que volver a deducirla.
  tenant_auth_user_id uuid not null,
  tenant_email text not null,
  started_at timestamptz not null default now(),
  expires_at timestamptz not null,
  ended_at timestamptz,
  ended_reason text
);
create index on public.impersonation_sessions (salon_id);

alter table public.support_access_codes enable row level security;
alter table public.impersonation_sessions enable row level security;

-- El salón solo ve lo suyo, y solo el rol admin: autorizar soporte es una
-- decisión del dueño, no de un estilista. code_hash nunca se devuelve al
-- cliente (lo recorta el esquema de respuesta, capa 3).
create policy "support_codes_admin_own" on public.support_access_codes
  for all using (salon_id = public.current_salon_id() and public.is_admin_rol())
  with check (salon_id = public.current_salon_id() and public.is_admin_rol());

-- Lectura para pintar "sesión de soporte activa" en /soporte. La escritura la
-- hace siempre el panel con service role.
create policy "impersonation_sessions_select_own" on public.impersonation_sessions
  for select using (salon_id = public.current_salon_id() and public.is_admin_rol());
create policy "impersonation_sessions_update_own" on public.impersonation_sessions
  for update using (salon_id = public.current_salon_id() and public.is_admin_rol())
  with check (salon_id = public.current_salon_id() and public.is_admin_rol());
