-- Cantidad por venta de producto (unidades vendidas en una operación).
--
-- Un ingreso de venta puede representar N unidades: importe = precio × cantidad
-- y el stock baja en `cantidad`. Guardar la cantidad deja resuelto el cálculo
-- de "unidades vendidas / margen acumulado por producto". Los ingresos que no
-- son de producto conservan el default 1.

ALTER TABLE public.incomes
  ADD COLUMN IF NOT EXISTS cantidad integer NOT NULL DEFAULT 1;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'incomes_cantidad_positiva'
      AND conrelid = 'public.incomes'::regclass
  ) THEN
    ALTER TABLE public.incomes
      ADD CONSTRAINT incomes_cantidad_positiva CHECK (cantidad > 0);
  END IF;
END $$;
