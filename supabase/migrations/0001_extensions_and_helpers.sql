-- Extensions
create extension if not exists pgcrypto;

-- Helper functions used by every tenant RLS policy.
-- salon_id and rol arrive as custom claims injected by the auth hook (0005_auth_hook.sql),
-- never derived from anything the client can set directly.
create or replace function public.current_salon_id()
returns uuid
language sql
stable
as $$
  select (auth.jwt() ->> 'salon_id')::uuid;
$$;

create or replace function public.current_rol()
returns text
language sql
stable
as $$
  select auth.jwt() ->> 'rol';
$$;

create or replace function public.is_admin_rol()
returns boolean
language sql
stable
as $$
  select public.current_rol() = 'admin';
$$;
