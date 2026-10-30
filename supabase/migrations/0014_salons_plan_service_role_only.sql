-- Paso 14a (endurecimiento post-revisión): el plan del salón es fuente de
-- verdad DESDE Stripe. El código FastAPI ya solo escribe salons.plan en el
-- webhook (service_role), PERO la política RLS salons_update_admin (0003) es a
-- nivel de FILA (no de columna), así que un admin de tenant podría cambiar
-- 'plan' directamente vía PostgREST con su JWT. Este trigger cierra ese hueco
-- a nivel de BD: bloquea el cambio de 'plan' cuando lo intenta el rol
-- 'authenticated' (el tenant). Lo sigue permitiendo a 'service_role' (webhook /
-- backend con la secret key) y a 'postgres'/superusuario (SQL editor de
-- soporte, ajustes manuales de plan).

create or replace function public.enforce_plan_source_of_truth()
returns trigger
language plpgsql
as $$
begin
  if new.plan is distinct from old.plan and current_user = 'authenticated' then
    raise exception 'El plan del salón solo se actualiza vía Stripe (webhook).'
      using errcode = 'insufficient_privilege';
  end if;
  return new;
end;
$$;

create trigger salons_plan_service_role_only
  before update on public.salons
  for each row
  execute function public.enforce_plan_source_of_truth();
