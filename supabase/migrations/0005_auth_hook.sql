-- Custom Access Token Hook: the only place that decides what salon_id/rol
-- end up signed into a tenant's JWT. The client can never set these itself.
-- Registered for local dev in supabase/config.toml; when projects move to
-- the hosted platform this must also be wired in Auth > Hooks (Dashboard).

create or replace function public.custom_access_token_hook(event jsonb)
returns jsonb
language plpgsql
stable
as $$
declare
  claims jsonb;
  v_salon_id uuid;
  v_rol public.user_rol;
begin
  select salon_id, rol into v_salon_id, v_rol
  from public.users
  where auth_user_id = (event ->> 'user_id')::uuid
  limit 1;

  claims := event -> 'claims';

  if v_salon_id is not null then
    claims := jsonb_set(claims, '{salon_id}', to_jsonb(v_salon_id::text));
    claims := jsonb_set(claims, '{rol}', to_jsonb(v_rol::text));
  end if;

  event := jsonb_set(event, '{claims}', claims);
  return event;
end;
$$;

-- Only the auth server may invoke the hook; never expose it to API roles.
grant usage on schema public to supabase_auth_admin;
grant execute on function public.custom_access_token_hook to supabase_auth_admin;
revoke execute on function public.custom_access_token_hook from authenticated, anon, public;

-- The hook runs before the JWT (and therefore current_salon_id()) exists,
-- so it needs its own narrow read path into public.users, independent of
-- the tenant RLS policies defined in 0003.
grant select on public.users to supabase_auth_admin;
create policy "auth_admin_select" on public.users
  as permissive for select
  to supabase_auth_admin
  using (true);
