-- Fase 1 · Paso 11 (chunk 3, revertido) — El producto pasa a modelo ADMIN-ONLY:
-- solo el admin accede al SaaS; el resto del personal usa únicamente el quiosco
-- de fichaje con PIN. Se elimina la gestión de equipo introducida en 0011.
-- (El enum de rol se deja intacto.)

DROP TABLE IF EXISTS public.team_audit_logs;

ALTER TABLE public.users
  DROP COLUMN IF EXISTS activo;
