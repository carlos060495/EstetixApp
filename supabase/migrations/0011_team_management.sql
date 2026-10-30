-- Fase 1 · Paso 11 (chunk 3) — Equipo y permisos (gestión de cuentas `users`).
--
-- · users.activo: desactivar el acceso sin borrar la fila (se conservan las
--   referencias históricas). El corte real de acceso lo aplica el backend
--   baneando además al usuario en Supabase Auth.
-- · team_audit_logs: auditoría de invitaciones, cambios de rol y
--   des/reactivaciones (quién, a quién, qué) sin PII innecesaria.

ALTER TABLE public.users
  ADD COLUMN IF NOT EXISTS activo boolean NOT NULL DEFAULT true;

CREATE TABLE IF NOT EXISTS public.team_audit_logs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  salon_id uuid NOT NULL REFERENCES public.salons(id) ON DELETE CASCADE,
  actor_auth_sub uuid,
  action text NOT NULL,
  target_user_id uuid,
  detail jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS team_audit_logs_salon_created_idx
  ON public.team_audit_logs (salon_id, created_at);

ALTER TABLE public.team_audit_logs ENABLE ROW LEVEL SECURITY;

-- Superficie admin-only, igual que la tabla users (0003): default deny para
-- el resto de roles del tenant.
DROP POLICY IF EXISTS "team_audit_admin_all" ON public.team_audit_logs;
CREATE POLICY "team_audit_admin_all" ON public.team_audit_logs
  FOR ALL
  USING (salon_id = public.current_salon_id() AND public.is_admin_rol())
  WITH CHECK (salon_id = public.current_salon_id() AND public.is_admin_rol());
