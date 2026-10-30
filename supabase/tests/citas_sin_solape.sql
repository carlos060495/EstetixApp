-- El candado de solape de citas (migración 0030), contra Postgres de verdad.
--
--   psql "$DATABASE_URL" -f supabase/tests/citas_sin_solape.sql
--
-- Cada comprobación lanza una excepción si falla, así que una ejecución limpia
-- sin más salida que el NOTICE final significa que el candado aguanta. Todo va
-- dentro de una transacción que se deshace al terminar, así que no toca los
-- datos del seed aunque inserte a propósito.
--
-- POR QUÉ ESTE FICHERO EXISTE: la comprobación de solape en Python
-- (`core/citas.asegurar_sin_solape`) consulta y LUEGO inserta, y entre las dos
-- cosas cabe otra petición entera. Los tests de Python usan un fake que imita
-- el constraint, y un fake nunca demuestra que el constraint EXISTA: eso solo
-- lo demuestra Postgres rechazando la fila. Con el bot de WhatsApp agendando
-- solo, dos clientes pidiendo el mismo hueco a la vez es el caso normal.
--
-- IDs fijos del seed (supabase/seed.sql):
--   salón A       = 00000000-0000-0000-0000-000000000001
--   Luis Ramírez  = 30000000-0000-0000-0000-000000000001
--   Juan Pérez    = 30000000-0000-0000-0000-000000000002
--   Lucía         = 50000000-0000-0000-0000-000000000001

begin;

-- Se trabaja con el rol de servicio: aquí se prueba el CONSTRAINT, no la RLS
-- (eso ya está en tenant_isolation.sql). Un fallo de permisos por el camino
-- confundiría un test con el otro.
set local role service_role;

-- Una franja futura y fija, para no depender de la hora a la que se ejecute.
create or replace function pg_temp.hueco(p_desde int, p_hasta int)
returns tstzrange language sql immutable as $$
  select tstzrange(
    date_trunc('day', now()) + interval '30 days' + (p_desde || ' hours')::interval,
    date_trunc('day', now()) + interval '30 days' + (p_hasta || ' hours')::interval
  );
$$;

create or replace function pg_temp.cita(p_empleado uuid, p_desde int, p_hasta int, p_estado cita_estado default 'confirmada')
returns uuid language plpgsql as $$
declare
  v_id uuid;
begin
  insert into public.appointments (salon_id, client_id, employee_id, service_id, inicio, fin, estado, canal)
  values (
    '00000000-0000-0000-0000-000000000001',
    '50000000-0000-0000-0000-000000000001',
    p_empleado,
    (select id from public.services where salon_id = '00000000-0000-0000-0000-000000000001' limit 1),
    lower(pg_temp.hueco(p_desde, p_hasta)),
    upper(pg_temp.hueco(p_desde, p_hasta)),
    p_estado,
    'whatsapp'
  )
  returning id into v_id;
  return v_id;
end;
$$;

-- ---------------------------------------------------------------------------
-- 0. El constraint existe. Si no, el resto de este fichero pasaría sin probar
--    nada: sin candado, todos los inserts entran tan contentos.
-- ---------------------------------------------------------------------------
do $$
begin
  if not exists (
    select 1 from pg_constraint
     where conname = 'appointments_sin_solape'
       and conrelid = 'public.appointments'::regclass
  ) then
    raise exception 'FALTA EL CANDADO: no existe el constraint appointments_sin_solape (¿se aplicó la migración 0030?)';
  end if;
end $$;

-- ---------------------------------------------------------------------------
-- 1. Dos citas del MISMO empleado que se solapan: la segunda tiene que fallar.
--    Es el caso de los dos clientes pidiendo el mismo hueco a la vez.
-- ---------------------------------------------------------------------------
do $$
declare
  v_paso boolean := false;
begin
  perform pg_temp.cita('30000000-0000-0000-0000-000000000001', 10, 11);
  begin
    perform pg_temp.cita('30000000-0000-0000-0000-000000000001', 10, 11);
    v_paso := true;
  exception when exclusion_violation then
    null;  -- lo esperado
  end;
  if v_paso then
    raise exception 'FALLO: se han agendado dos citas a la vez con el mismo empleado';
  end if;
end $$;

-- ---------------------------------------------------------------------------
-- 2. Solape PARCIAL: no hace falta que coincidan exactamente. Una cita de
--    10:30 a 11:30 pisa a la de 10:00-11:00 y también debe caer.
-- ---------------------------------------------------------------------------
do $$
declare
  v_paso boolean := false;
begin
  begin
    perform pg_temp.cita('30000000-0000-0000-0000-000000000001', 10, 12);
    v_paso := true;
  exception when exclusion_violation then
    null;
  end;
  if v_paso then
    raise exception 'FALLO: un solape parcial ha entrado';
  end if;
end $$;

-- ---------------------------------------------------------------------------
-- 3. CONTRAPRUEBA — lo que SÍ tiene que entrar. Sin esto, un constraint
--    demasiado estricto (que bloqueara de más) pasaría los tests de arriba y
--    dejaría la agenda inservible.
-- ---------------------------------------------------------------------------

-- 3a. Otro empleado a la misma hora: el salón tiene varias personas atendiendo.
do $$
begin
  perform pg_temp.cita('30000000-0000-0000-0000-000000000002', 10, 11);
exception when exclusion_violation then
  raise exception 'FALLO: el candado bloquea a DOS EMPLEADOS DISTINTOS a la misma hora';
end $$;

-- 3b. El mismo empleado, pegado pero sin pisar: 11:00-12:00 después de
--     10:00-11:00. Los rangos son [inicio, fin), así que tocarse no es pisarse.
--     Si esto fallara, no se podrían encadenar citas seguidas.
do $$
begin
  perform pg_temp.cita('30000000-0000-0000-0000-000000000001', 11, 12);
exception when exclusion_violation then
  raise exception 'FALLO: el candado impide encadenar dos citas seguidas del mismo empleado';
end $$;

-- 3c. Una cita CANCELADA no ocupa hueco: si no, cancelar no liberaría la hora
--     y ese hueco quedaría muerto para siempre. Mismo criterio que
--     `core/citas.ESTADOS_QUE_NO_OCUPAN` y que el motor de disponibilidad.
do $$
begin
  perform pg_temp.cita('30000000-0000-0000-0000-000000000002', 14, 15, 'cancelada');
  perform pg_temp.cita('30000000-0000-0000-0000-000000000002', 14, 15, 'no_show');
  perform pg_temp.cita('30000000-0000-0000-0000-000000000002', 14, 15, 'confirmada');
exception when exclusion_violation then
  raise exception 'FALLO: una cita cancelada o no_show sigue bloqueando el hueco';
end $$;

-- 3d. Cancelar una cita libera su hueco para otra que ya existía en conflicto.
--     Es el camino real: se cancela y se vuelve a agendar a esa hora.
do $$
declare
  v_id uuid;
begin
  v_id := pg_temp.cita('30000000-0000-0000-0000-000000000001', 16, 17);
  update public.appointments set estado = 'cancelada' where id = v_id;
  perform pg_temp.cita('30000000-0000-0000-0000-000000000001', 16, 17);
exception when exclusion_violation then
  raise exception 'FALLO: tras cancelar una cita, su hueco sigue bloqueado';
end $$;

-- 3e. Sin empleado asignado no se ocupa a nadie, así que dos citas sin empleado
--     a la misma hora son legítimas (la agenda las permite).
do $$
begin
  perform pg_temp.cita(null, 18, 19);
  perform pg_temp.cita(null, 18, 19);
exception when exclusion_violation then
  raise exception 'FALLO: el candado bloquea citas SIN empleado, que no ocupan a nadie';
end $$;

-- ---------------------------------------------------------------------------
-- 4. El candado también vale al MOVER una cita, no solo al crearla.
-- ---------------------------------------------------------------------------
do $$
declare
  v_id uuid;
  v_paso boolean := false;
begin
  v_id := pg_temp.cita('30000000-0000-0000-0000-000000000001', 20, 21);
  begin
    -- Se mueve encima de la de las 11:00-12:00 creada en 3b.
    update public.appointments
       set inicio = lower(pg_temp.hueco(11, 12)), fin = upper(pg_temp.hueco(11, 12))
     where id = v_id;
    v_paso := true;
  exception when exclusion_violation then
    null;
  end;
  if v_paso then
    raise exception 'FALLO: se ha podido MOVER una cita encima de otra del mismo empleado';
  end if;
end $$;

do $$ begin raise notice 'OK: el candado de solape de citas aguanta (y no bloquea de más).'; end $$;

rollback;
