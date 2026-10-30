-- Fase 1 · Paso 10 — Time entries para quiosco de fichaje.
--
-- Ajuste compatible con el esquema existente:
-- - amplía enum fichaje_tipo con 'reanudacion'
-- - normaliza columna temporal a `ts` (legacy: `timestamp`)
-- - añade created_at
-- - refuerza índice operativo
-- - aplica política RLS tenant_isolation (default deny)

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_type t
    JOIN pg_enum e ON e.enumtypid = t.oid
    WHERE t.typname = 'fichaje_tipo'
      AND e.enumlabel = 'reanudacion'
  ) THEN
    ALTER TYPE public.fichaje_tipo ADD VALUE 'reanudacion';
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS public.time_entries (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  salon_id uuid NOT NULL REFERENCES public.salons(id) ON DELETE CASCADE,
  employee_id uuid NOT NULL REFERENCES public.employees(id) ON DELETE CASCADE,
  tipo public.fichaje_tipo NOT NULL,
  ts timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now()
);

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'time_entries'
      AND column_name = 'timestamp'
  ) AND NOT EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'time_entries'
      AND column_name = 'ts'
  ) THEN
    ALTER TABLE public.time_entries RENAME COLUMN "timestamp" TO ts;
  END IF;
END $$;

ALTER TABLE public.time_entries
  ADD COLUMN IF NOT EXISTS ts timestamptz,
  ADD COLUMN IF NOT EXISTS created_at timestamptz;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'time_entries'
      AND column_name = 'timestamp'
  ) THEN
    EXECUTE 'UPDATE public.time_entries SET ts = COALESCE(ts, "timestamp")';
    EXECUTE 'ALTER TABLE public.time_entries DROP COLUMN "timestamp"';
  END IF;
END $$;

UPDATE public.time_entries
SET ts = now()
WHERE ts IS NULL;

UPDATE public.time_entries
SET created_at = COALESCE(created_at, ts, now())
WHERE created_at IS NULL;

ALTER TABLE public.time_entries
  ALTER COLUMN ts SET DEFAULT now(),
  ALTER COLUMN ts SET NOT NULL,
  ALTER COLUMN created_at SET DEFAULT now(),
  ALTER COLUMN created_at SET NOT NULL;

CREATE INDEX IF NOT EXISTS time_entries_salon_employee_ts_idx
  ON public.time_entries (salon_id, employee_id, ts);

ALTER TABLE public.time_entries ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "time_entries_select_admin" ON public.time_entries;
DROP POLICY IF EXISTS "tenant_isolation" ON public.time_entries;

CREATE POLICY "tenant_isolation"
  ON public.time_entries
  FOR ALL
  USING (salon_id::text = auth.jwt() ->> 'salon_id')
  WITH CHECK (salon_id::text = auth.jwt() ->> 'salon_id');
