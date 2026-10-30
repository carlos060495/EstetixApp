-- Puestos del salón — el «Rol» decorativo del empleado pasa a ser una lista
-- que el salón edita.
--
-- AVISO IMPORTANTE: esto NO reparte permisos. Los permisos los da
-- `public.users.rol` (enum admin/estilista/recepcionista), que es de donde sale
-- el claim del JWT y lo que miran TODAS las políticas de RLS. `employees.rol`
-- era otra cosa: un texto libre que solo se pintaba en pantalla, y que además
-- ofrecía «Admin» como opción —lo cual daba a entender que concedía accesos
-- que nunca concedió—. Esta migración lo convierte en lo que de verdad es: un
-- PUESTO de trabajo. Ni una política de seguridad se toca aquí.
--
-- Se modela como tabla y no como enum ni texto libre por dos razones: un enum
-- obligaría a una migración cada vez que un salón invente un puesto suyo, y el
-- texto libre suelto no permite RENOMBRAR («Estilista» → «Estilista sénior»)
-- sin repasar empleado por empleado. Con la FK, renombrar el puesto se ve al
-- instante en todos sus empleados.

create table public.puestos (
  id uuid primary key default gen_random_uuid(),
  salon_id uuid not null references public.salons(id) on delete cascade,
  nombre text not null check (length(btrim(nombre)) between 1 and 60),
  created_at timestamptz not null default now()
);

-- Único por salón e IGNORANDO mayúsculas: «Barbero» y «barbero» son el mismo
-- puesto escrito de dos formas, y tenerlos los dos en el desplegable solo
-- genera dudas sobre cuál elegir. Va como índice de expresión porque un
-- `unique (salon_id, nombre)` normal no distingue el caso.
create unique index puestos_salon_nombre_unico on public.puestos (salon_id, lower(btrim(nombre)));

create index on public.puestos (salon_id);

-- ---------------------------------------------------------------------------
-- Siembra: los puestos que cada salón YA está usando
-- ---------------------------------------------------------------------------
-- Sin pérdida de datos: lo que hoy hay escrito a mano en `employees.rol` se
-- convierte en la lista inicial de ese salón. `distinct on` elige una sola
-- grafía por puesto (la primera alfabéticamente) para no chocar con el índice
-- de arriba cuando el mismo puesto está escrito de dos maneras.
--
-- «Admin» queda FUERA a propósito: es el valor que hacía creer que este campo
-- daba permisos. Los empleados que lo tuvieran no se pierden — se les pasa a
-- «Encargado», que es lo que en la práctica significaba.
insert into public.puestos (salon_id, nombre)
select distinct on (e.salon_id, lower(btrim(e.rol))) e.salon_id, btrim(e.rol)
  from public.employees e
 where btrim(e.rol) <> ''
   and lower(btrim(e.rol)) <> 'admin'
 order by e.salon_id, lower(btrim(e.rol)), btrim(e.rol);

-- «Encargado» solo para los salones que tuvieran algún «Admin» que reubicar.
insert into public.puestos (salon_id, nombre)
select distinct e.salon_id, 'Encargado'
  from public.employees e
 where lower(btrim(e.rol)) = 'admin'
   and not exists (
     select 1 from public.puestos p
      where p.salon_id = e.salon_id and lower(p.nombre) = 'encargado'
   );

-- ---------------------------------------------------------------------------
-- El empleado apunta a su puesto
-- ---------------------------------------------------------------------------
-- NULLABLE a propósito: un salón recién creado no tiene ningún puesto todavía
-- (la siembra sale de los empleados que ya existen, y él no tiene ninguno).
-- Exigirlo dejaría el alta de empleados bloqueada hasta crear un puesto, que
-- es un requisito artificial para un campo que no hace nada más que mostrarse.
--
-- `on delete restrict` y no `set null`: borrar un puesto en uso vaciaría el de
-- varios empleados de golpe y en silencio. La base de datos lo impide, y el
-- backend traduce ese error a un mensaje que dice cuántos lo tienen.
alter table public.employees
  add column puesto_id uuid references public.puestos(id) on delete restrict;

update public.employees e
   set puesto_id = p.id
  from public.puestos p
 where p.salon_id = e.salon_id
   and lower(btrim(p.nombre)) = case
         when lower(btrim(e.rol)) = 'admin' then 'encargado'
         else lower(btrim(e.rol))
       end;

create index on public.employees (puesto_id);

-- Ya no hace falta: el puesto vive en `puestos` y se lee por la FK. Se tira
-- ahora y no «más adelante» para que no queden dos fuentes de verdad, que es
-- como se acaba con un empleado cuyo texto dice una cosa y su FK otra.
alter table public.employees drop column rol;

-- ---------------------------------------------------------------------------
-- RLS — mismo patrón que salon_schedule (0022)
-- ---------------------------------------------------------------------------
-- Aislamiento por el claim salon_id, NUNCA por auth.uid(). Lo lee todo el
-- personal (el desplegable del formulario de empleado lo necesita) y lo escribe
-- solo admin, como el resto de la configuración del salón.
alter table public.puestos enable row level security;

create policy "puestos_select" on public.puestos
  for select using (salon_id = public.current_salon_id());
create policy "puestos_write_admin" on public.puestos
  for all using (salon_id = public.current_salon_id() and public.is_admin_rol())
  with check (salon_id = public.current_salon_id() and public.is_admin_rol());

-- Permisos de tabla para los roles de PostgREST. Explícitos y no heredados del
-- entorno: en el Supabase local una tabla nueva de `public` nace SIN
-- select/insert/update/delete para `authenticated`, y PostgREST responde «403
-- permission denied for table puestos» aunque la RLS esté perfecta (está
-- documentado en supabase/README.md). `anon` queda fuera: aquí solo llega
-- personal autenticado.
grant select, insert, update, delete on public.puestos to authenticated, service_role;
