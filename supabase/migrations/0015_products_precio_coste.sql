-- Refinamiento del modelo financiero — coste de adquisición por producto.
--
-- precio_coste es OPCIONAL (NULL = coste desconocido): sirve para el margen
-- unitario (venta − coste) como INDICADOR de viabilidad, no como gasto. El
-- gasto real de comprar stock se registra aparte en expenses vía la
-- reposición, así el coste nunca se resta dos veces del P&L global.

ALTER TABLE public.products
  ADD COLUMN IF NOT EXISTS precio_coste numeric(10, 2);

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'products_precio_coste_no_negativo'
      AND conrelid = 'public.products'::regclass
  ) THEN
    ALTER TABLE public.products
      ADD CONSTRAINT products_precio_coste_no_negativo
      -- NULL pasa el CHECK (unknown): coste opcional sin backfill.
      CHECK (precio_coste >= 0);
  END IF;
END $$;
