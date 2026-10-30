-- Fase 1 · Paso 11 (chunk 2a) — Color de acento por salón (white-label ligero).
--
-- La base oscura de la app es fija; solo el acento es configurable.
-- El default dorado hace backfill de las filas existentes.

ALTER TABLE public.salons
  ADD COLUMN IF NOT EXISTS accent_color text NOT NULL DEFAULT '#C9A84C';

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'salons_accent_color_hex'
      AND conrelid = 'public.salons'::regclass
  ) THEN
    ALTER TABLE public.salons
      ADD CONSTRAINT salons_accent_color_hex
      CHECK (accent_color ~ '^#[0-9A-Fa-f]{6}$');
  END IF;
END $$;
