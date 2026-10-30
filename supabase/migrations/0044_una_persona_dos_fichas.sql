-- 0044 · La misma persona, con ficha en dos centros
--
-- El modelo no cambia: UNA FICHA POR CENTRO. Eva tiene dos, y así todo sigue
-- filtrando por centro sin excepciones. Lo que falta es que el sistema sepa que
-- son la misma persona, y para exactamente dos cosas: que no se la agende en
-- dos sitios a la vez, y que sus horas se puedan sumar.
--
-- ═══ POR QUÉ LA GARANTÍA ES DURA Y NO UN AVISO ═══
--
-- Porque cabía. La protección contra solapes NO vive en una tabla aparte ni es
-- por centro: es una restricción de exclusión sobre `appointments`, medida:
--
--   EXCLUDE USING gist (employee_id WITH =, tstzrange(inicio, fin) WITH &&)
--   WHERE (employee_id IS NOT NULL AND estado <> ALL (…))
--
-- Es «por employee_id» y employee_id resulta ser por centro solo porque las
-- fichas lo son. O sea que basta con que la clave deje de ser la FICHA y pase a
-- ser la PERSONA. No hace falta tabla nueva, ni tocar las 50 políticas, ni
-- cambiar cómo se lee nada: dos columnas, dos triggers y cambiar la clave de la
-- restricción.
--
-- Un aviso en la aplicación se pulsa sin leer, y aquí el precio de pulsarlo son
-- dos clientas esperando a la misma persona en dos sitios. Con lo que cuesta la
-- dura, no hay discusión.
--
-- ═══ CÓMO SE IDENTIFICA A LA PERSONA ═══
--
-- Sin tabla `personas`. `persona_id` es EL ID DE LA PRIMERA FICHA: al enlazar
-- la segunda, las dos apuntan a la primera. Una tabla nueva solo para decir
-- «estas dos filas son la misma señora» habría que crearla, poblarla, darle
-- permisos y RLS, y no diría nada más que esto.
--
-- ═══ LA REGLA (supabase/README.md) ═══
--
-- `appointments` y `employees` son tablas en las que el backend inserta, así
-- que las dos columnas van ANULABLES y sin valor por defecto. El backend
-- ANTERIOR sigue insertando citas sin saber que existen y el trigger las
-- rellena: la garantía protege también lo que escriba él.

begin;

-- ═══════════════ 1. el enlace entre fichas

alter table public.employees add column persona_id uuid;

comment on column public.employees.persona_id is
  'La persona detrás de la ficha, cuando trabaja en más de un centro. Es el id '
  'de su PRIMERA ficha, y todas las suyas lo comparten. NULL = trabaja en un '
  'solo sitio, que es la mayoría de la plantilla.';

create index employees_persona_id_idx on public.employees (persona_id)
  where persona_id is not null;

-- La misma persona no puede tener DOS fichas en el mismo centro: eso no es
-- trabajar en dos sitios, es un duplicado, y rompería el recuento del tope de
-- empleados y la suma de horas.
create unique index employees_una_ficha_por_centro
  on public.employees (salon_id, persona_id)
  where persona_id is not null;

-- ═══════════════ 2. la cita sabe de quién es, no solo de qué ficha

alter table public.appointments add column persona_id uuid;

comment on column public.appointments.persona_id is
  'Copia de `employees.persona_id` (o del propio `employee_id` si no hay '
  'enlace). Está aquí porque una restricción de exclusión solo puede mirar '
  'columnas de su propia fila: sin esta copia no hay forma de impedir en la '
  'base que se agende a la misma persona en dos centros a la vez.';

create or replace function public.cita_apunta_a_la_persona()
returns trigger language plpgsql security definer
set search_path = public, pg_temp as $$
begin
  if new.employee_id is null then
    new.persona_id := null;
  else
    select coalesce(e.persona_id, e.id) into new.persona_id
    from public.employees e where e.id = new.employee_id;
  end if;
  return new;
end;
$$;

create trigger cita_apunta_a_la_persona
  before insert or update of employee_id on public.appointments
  for each row execute function public.cita_apunta_a_la_persona();

-- Y cuando se enlaza (o se desenlaza) una ficha, sus citas YA EXISTENTES
-- tienen que repuntar. Sin esto, enlazar a Eva hoy no protegería nada de lo que
-- ya está en la agenda — que es justo lo que hay cuando alguien se da cuenta
-- del problema.
create or replace function public.repuntar_citas_de_la_persona()
returns trigger language plpgsql security definer
set search_path = public, pg_temp as $$
begin
  update public.appointments
  set persona_id = coalesce(new.persona_id, new.id)
  where employee_id = new.id
    and persona_id is distinct from coalesce(new.persona_id, new.id);
  return new;
end;
$$;

create trigger repuntar_citas_de_la_persona
  after update of persona_id on public.employees
  for each row execute function public.repuntar_citas_de_la_persona();

-- BACKFILL: toda cita con empleado apunta ya a su persona. Hoy nadie está
-- enlazado, así que esto es `employee_id` copiado — y por eso la restricción
-- nueva acepta exactamente lo mismo que la vieja mientras no haya enlaces.
update public.appointments a
set persona_id = coalesce(e.persona_id, e.id)
from public.employees e
where e.id = a.employee_id and a.persona_id is null;

-- ═══════════════ 3. la clave del solape pasa a ser la persona

-- El `coalesce` cubre lo que pudiera quedar sin sellar: una fila cuyo trigger
-- no haya corrido nunca sigue protegida por su ficha, que es lo que hacía
-- antes. Nunca se protege MENOS que ahora.
alter table public.appointments drop constraint appointments_sin_solape;

alter table public.appointments add constraint appointments_sin_solape
  exclude using gist (
    (coalesce(persona_id, employee_id)) with =,
    tstzrange(inicio, fin) with &&
  )
  where (employee_id is not null
         and estado <> all (array['cancelada'::cita_estado, 'no_show'::cita_estado]));

-- ═══════════════ 4. el PIN del fichaje es de la persona, no de la ficha

-- LA PREGUNTA: Eva tiene dos fichas, ¿dos PINes? Hoy sí, porque
-- `pin_fichaje_hash` está en `employees`. Y dos PINes de cuatro cifras para el
-- mismo trabajo acaban los dos escritos en el mismo papel pegado al mostrador,
-- que es peor que uno.
--
-- Así que el PIN viaja con la persona: se pone una vez y abre en los dos
-- quioscos. Lo que NO se comparte es el fichaje: `time_entries` sigue guardando
-- el `employee_id`, o sea el centro donde estuvo. Se comparte la credencial,
-- no las horas.
--
-- Va en la base y no en el backend por la misma razón que el resto de la 0044:
-- el backend ANTERIOR sigue escribiendo PINes sin saber que esto existe, y sin
-- el trigger dejaría a las dos fichas descolgadas.
--
-- DOS CAMINOS, LOS DOS DETERMINISTAS —el orden de las filas no puede decidir
-- qué PIN gana—:

-- (a) Al ENLAZARSE (o al nacer enlazada), la ficha ADOPTA el PIN de la
--     persona. Si la persona aún no tiene, ofrece el suyo.
create or replace function public.la_ficha_adopta_el_pin_de_la_persona()
returns trigger language plpgsql security definer
set search_path = public, pg_temp as $$
declare v_pin text;
begin
  if new.persona_id is null then
    return new;
  end if;
  select e.pin_fichaje_hash into v_pin
  from public.employees e
  where e.persona_id = new.persona_id and e.id <> new.id
    and e.pin_fichaje_hash is not null
  limit 1;
  if v_pin is not null then
    new.pin_fichaje_hash := v_pin;
  end if;
  return new;
end;
$$;

create trigger la_ficha_adopta_el_pin_de_la_persona
  before insert or update of persona_id on public.employees
  for each row execute function public.la_ficha_adopta_el_pin_de_la_persona();

-- (b) Al CAMBIARLO en una ficha, cambia en todas las suyas. Incluye borrarlo:
--     quitarle el PIN a Eva se lo quita en los dos sitios, que es lo que quiere
--     decir quitárselo.
create or replace function public.el_pin_es_de_la_persona()
returns trigger language plpgsql security definer
set search_path = public, pg_temp as $$
begin
  if new.persona_id is null then
    return new;
  end if;
  -- El `is distinct from` corta la recursión: la segunda vuelta no encuentra
  -- ninguna fila que cambiar y el trigger no se vuelve a disparar.
  update public.employees
  set pin_fichaje_hash = new.pin_fichaje_hash
  where persona_id = new.persona_id and id <> new.id
    and pin_fichaje_hash is distinct from new.pin_fichaje_hash;
  return new;
end;
$$;

create trigger el_pin_es_de_la_persona
  after update of pin_fichaje_hash on public.employees
  for each row execute function public.el_pin_es_de_la_persona();

commit;
