-- 0093 · Fuera `expenses.factura_url`. NO BORRA DATOS: si alguna fila tiene
-- valor, se para y no cambia nada.
--
-- VA DESPUÉS DE DESPLEGAR EL BACKEND. El backend viejo la pide en el listado
-- de gastos y en la descarga de datos: con ella quitada y el backend viejo
-- vivo, Finanzas daría error. El backend nuevo ya no la nombra.
--
-- Carlos (29-10-2026): la columna existía para un OCR de facturas que nunca se
-- construyó. Nadie la escribe (el alta de gastos no la acepta desde el
-- cliente) y se leía en dos sitios. Una columna que se lee y que nadie escribe
-- es justo lo que no queremos. Con el botón del OCR fuera, se va también ella.

begin;

do $$
declare
  n bigint;
begin
  select count(*) into n from public.expenses where factura_url is not null;
  if n > 0 then
    raise exception 'expenses.factura_url tiene % filas con valor: no se quita la columna. Mirarlas antes.', n;
  end if;
end;
$$;

alter table public.expenses drop column factura_url;

commit;
