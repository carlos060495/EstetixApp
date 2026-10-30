-- Suspensión de un salón desde el panel Super Admin.
--
-- Suspender = bloquear el acceso de los usuarios del salón + pausar el cobro en
-- Stripe. Es REVERSIBLE: reactivar quita el flag y reanuda el cobro.
--
-- El flag lo escribimos NOSOTROS desde el panel; no viene de Stripe. Stripe no
-- tiene un concepto de "cuenta suspendida" que se pueda leer: `pause_collection`
-- es solo la pausa de cobro, y hay salones sin suscripción ninguna a los que
-- también hay que poder cerrarles el acceso.

alter table public.salons
  add column suspendido boolean not null default false,
  add column suspendido_at timestamptz,
  -- Qué admin del panel lo suspendió. `on delete set null` y no `restrict`:
  -- borrar a un empleado de la plataforma no debe impedir tocar el salón, y el
  -- rastro imborrable de quién fue está en admin_logs, no aquí.
  add column suspendido_por uuid references public.admin_users(id) on delete set null;

-- Índice parcial: la pregunta que se hace el panel es "¿cuáles están
-- suspendidos?", y los suspendidos son una minoría. Un índice sobre toda la
-- columna sería casi todo `false` y no lo usaría nadie.
create index salons_suspendidos on public.salons (id) where suspendido;

-- ── Que el salón no pueda quitarse la suspensión él mismo ───────────────────
--
-- MISMO AGUJERO QUE EL PLAN EN LA 0014, y por la misma razón: la política
-- `salons_update_admin` (0003) es a nivel de FILA, no de columna. Un admin de
-- tenant con su JWT todavía válido puede llamar a PostgREST DIRECTAMENTE —la
-- clave publicable es pública, está en el navegador— y hacer
-- `PATCH /salons?id=eq.<suyo>` con `{"suspendido": false}`. Sin este trigger, la
-- suspensión duraría lo que tarde el dueño en abrir la consola del navegador.
--
-- Se bloquea para 'authenticated' (el tenant) y se sigue permitiendo a
-- 'service_role' (el backend del panel, que es quien suspende y reactiva) y a
-- postgres/superusuario (SQL editor de soporte).
create or replace function public.enforce_suspension_service_role_only()
returns trigger
language plpgsql
as $$
begin
  if current_user = 'authenticated' and (
       new.suspendido    is distinct from old.suspendido
    or new.suspendido_at is distinct from old.suspendido_at
    or new.suspendido_por is distinct from old.suspendido_por
  ) then
    raise exception 'La suspensión de un salón solo se cambia desde el panel de administración.'
      using errcode = 'insufficient_privilege';
  end if;
  return new;
end;
$$;

create trigger salons_suspension_service_role_only
  before update on public.salons
  for each row
  execute function public.enforce_suspension_service_role_only();

-- admin_logs.accion es `text` (0004), así que "suspend_account" y
-- "reactivate_account" no necesitan migración: entran tal cual.
