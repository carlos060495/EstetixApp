-- El precio de VENTA es opcional según venta_publico, no según consumo interno.
--
-- Un producto solo de consumo interno (venta_publico=false) puede guardarse SIN
-- precio de venta → la columna pasa a nullable (null = "no se vende / sin
-- precio", distinto de 0 = "gratis"). Si venta_publico=true, la API exige el
-- precio (validación en app). Se elimina también el default 0 para que omitir
-- el precio guarde NULL, no 0. El CHECK (precio >= 0) inline se conserva y NULL
-- lo pasa (unknown).

ALTER TABLE public.products
  ALTER COLUMN precio DROP NOT NULL,
  ALTER COLUMN precio DROP DEFAULT;
