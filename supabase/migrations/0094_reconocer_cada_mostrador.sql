-- 0094 · RECONOCER CADA MOSTRADOR (Carlos, 29-10-2026).
--
-- Ajustes enseña los mostradores en pie de la cuenta, con «Retirar» en cada
-- uno: una tablet que se rompe o se borra dejaba su fila en pie para siempre, y
-- con ella el PIN de gestión imposible de quitar. Para saber cuál es cuál:
--
--   · descripcion: navegador y sistema al activarlo («Chrome en Android»),
--     sacado del User-Agent. Lo escribe `POST /mostrador/activar`.
--   · usado_el: la última vez que el dispositivo pidió algo, apuntado como
--     mucho cada cinco minutos. Lo escribe el backend en cada uso, y es también
--     cuando se renueva la cookie del dispositivo.
--
-- Las dos pueden ser NULL: los mostradores activados antes de esta migración
-- no tienen descripción, y uno que no se ha usado desde entonces no tiene uso.
-- La tabla ya concede select/insert/update a service_role (0090): las columnas
-- nuevas lo heredan.
--
-- ORDEN: 0094 → backend → frontend. El backend nuevo lee `usado_el` en cada
-- petición del mostrador: contra una base sin la columna, el mostrador entero
-- daría error. El backend viejo con la 0094 no las nombra y sigue igual.

begin;

alter table public.dispositivos_mostrador
  add column descripcion text check (char_length(descripcion) <= 80),
  add column usado_el timestamptz;

commit;
