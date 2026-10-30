-- Horario por empleada (0045): la frontera y la forma, probadas contra Postgres.
--
-- LAS DOS MITADES, porque con una sola un fallo pasa desapercibido (0029):
--   · lo prohibido NO se puede: leer o escribir el horario de otra cuenta o de
--     otro centro que no ves, escribir sin ser admin, leer sin sesión;
--   · lo legítimo SÍ se puede: el admin escribe el de su centro, cualquiera del
--     centro lo lee, service_role lo lee (el widget público lo necesita).
--
-- Y LA FORMA: los CHECK de la semana y las excepciones, el EXCLUDE que impide
-- dos ausencias en las mismas fechas y el ON DELETE CASCADE de la ficha.
--
-- TODO DENTRO DE UNA TRANSACCIÓN QUE SE DESHACE.

begin;

set client_min_messages to notice;

create or replace function pg_temp.login_as(p_salon_id uuid, p_rol text, p_user_id uuid)
returns void language plpgsql as $$
begin
  set local role authenticated;
  perform set_config('request.jwt.claims',
    json_build_object('sub', p_user_id, 'salon_id', p_salon_id, 'rol', p_rol)::text,
    true);
end;
$$;

-- ───────────────────────────────────────────────── el decorado

-- Centro 1 (cuenta A): Luis, del seed. Cuenta B: una ficha nueva en su salón.
-- Y un SEGUNDO centro de la cuenta A, con su ficha, para la frontera entre
-- centros del mismo dueño.
insert into public.employees (id, salon_id, nombre) values
  ('ee450000-0000-0000-0000-00000000000b', '00000000-0000-0000-0000-000000000002', 'Ficha de la cuenta B');

insert into public.salons (id, nombre_comercial, plan, account_id)
select '0a450000-0000-0000-0000-00000000000b', 'Centro Norte', 'elite', s.account_id
from public.salons s where s.id = '00000000-0000-0000-0000-000000000001';

insert into public.employees (id, salon_id, nombre) values
  ('ee450000-0000-0000-0000-0000000000a2', '0a450000-0000-0000-0000-00000000000b', 'Ficha del Centro Norte');

insert into public.employee_schedule (employee_id, dia_semana, cerrado, apertura, cierre)
select f.id, d, d = 7, '10:00', '14:00'
from (values ('30000000-0000-0000-0000-000000000001'::uuid),
             ('ee450000-0000-0000-0000-00000000000b'::uuid),
             ('ee450000-0000-0000-0000-0000000000a2'::uuid)) f(id),
     generate_series(1, 7) d;

insert into public.employee_schedule_exceptions (employee_id, fecha_inicio, fecha_fin, cerrado, motivo)
values ('30000000-0000-0000-0000-000000000001', '2026-10-05', '2026-10-09', true, 'Vacaciones'),
       ('ee450000-0000-0000-0000-00000000000b', '2026-10-05', '2026-10-09', true, 'Vacaciones');

-- Alguien de fuera, con identidad propia y SIN fila en `users` (patrón de
-- account_isolation, reescrito en la 0069: hasta entonces era una estilista del
-- centro 1, un rol que ya no existe). Se presenta con centro 1 y rol admin.
insert into auth.users (id, instance_id, aud, role, email)
select '1e450000-0000-0000-0000-000000000001', u.instance_id, u.aud, u.role, 'de-fuera.horario@test.local'
from auth.users u limit 1;

-- ═══════════════════ 1. el admin de la cuenta A

select pg_temp.login_as('00000000-0000-0000-0000-000000000001', 'admin', '10000000-0000-0000-0000-000000000001');

do $$
declare v_n int;
begin
  -- LO LEGÍTIMO: ve y escribe el horario de su ficha.
  select count(*) into v_n from public.employee_schedule where employee_id = '30000000-0000-0000-0000-000000000001';
  if v_n <> 7 then raise exception 'FRONTERA DEMASIADO CERRADA: el admin ve % días de su ficha, esperaba 7', v_n; end if;

  update public.employee_schedule set cierre = '15:00'
  where employee_id = '30000000-0000-0000-0000-000000000001' and dia_semana = 1;
  get diagnostics v_n = row_count;
  if v_n <> 1 then raise exception 'EL ADMIN NO PUEDE EDITAR EL HORARIO DE SU FICHA'; end if;

  insert into public.employee_schedule_exceptions (employee_id, fecha_inicio, fecha_fin, cerrado)
  values ('30000000-0000-0000-0000-000000000001', '2026-11-02', '2026-11-02', true);

  -- Y, como admin de la cuenta, también el de su otro centro.
  select count(*) into v_n from public.employee_schedule where employee_id = 'ee450000-0000-0000-0000-0000000000a2';
  if v_n <> 7 then raise exception 'EL ADMIN NO VE EL HORARIO DE SU OTRO CENTRO: % filas', v_n; end if;

  -- LO PROHIBIDO: la cuenta B no existe para él.
  select count(*) into v_n from public.employee_schedule where employee_id = 'ee450000-0000-0000-0000-00000000000b';
  if v_n <> 0 then raise exception 'FUGA ENTRE CUENTAS: el admin de A leyó % días del horario de B', v_n; end if;
  select count(*) into v_n from public.employee_schedule_exceptions where employee_id = 'ee450000-0000-0000-0000-00000000000b';
  if v_n <> 0 then raise exception 'FUGA ENTRE CUENTAS: el admin de A leyó las ausencias de B'; end if;

  update public.employee_schedule set cierre = '20:00' where employee_id = 'ee450000-0000-0000-0000-00000000000b';
  get diagnostics v_n = row_count;
  if v_n <> 0 then raise exception 'FUGA AL ESCRIBIR: el admin de A cambió % días del horario de B', v_n; end if;

  begin
    insert into public.employee_schedule_exceptions (employee_id, fecha_inicio, fecha_fin, cerrado)
    values ('ee450000-0000-0000-0000-00000000000b', '2026-12-01', '2026-12-01', true);
    raise exception 'FUGA AL ESCRIBIR: el admin de A creó una ausencia en la ficha de B';
  exception when insufficient_privilege then null;
  end;
end $$;

reset role;

-- ═══════════════════ 2. alguien de fuera, con un token que dice centro 1 y admin

select pg_temp.login_as('00000000-0000-0000-0000-000000000001', 'admin', '1e450000-0000-0000-0000-000000000001');

do $$
declare v_n int;
begin
  -- SOLO ENTRA LA ADMINISTRADORA (0063, 0069): no ve ninguna fila, ni del centro
  -- que dice su token.
  select count(*) into v_n from public.employee_schedule where employee_id = '30000000-0000-0000-0000-000000000001';
  if v_n <> 0 then raise exception 'SOLO ENTRA LA ADMINISTRADORA (0063): alguien de fuera leyó % días de horario', v_n; end if;
  select count(*) into v_n from public.employee_schedule_exceptions where employee_id = '30000000-0000-0000-0000-000000000001';
  if v_n <> 0 then raise exception 'SOLO ENTRA LA ADMINISTRADORA (0063): alguien de fuera leyó % ausencias', v_n; end if;

  -- LO PROHIBIDO: escribir.
  update public.employee_schedule set cierre = '20:00' where employee_id = '30000000-0000-0000-0000-000000000001';
  get diagnostics v_n = row_count;
  if v_n <> 0 then raise exception 'ALGUIEN DE FUERA CAMBIÓ % DÍAS DE UN HORARIO', v_n; end if;
  begin
    insert into public.employee_schedule_exceptions (employee_id, fecha_inicio, fecha_fin, cerrado)
    values ('30000000-0000-0000-0000-000000000001', '2026-12-24', '2026-12-24', true);
    raise exception 'ALGUIEN DE FUERA PUSO UNA AUSENCIA';
  exception when insufficient_privilege then null;
  end;

  -- LO PROHIBIDO: el otro centro de esa cuenta.
  select count(*) into v_n from public.employee_schedule where employee_id = 'ee450000-0000-0000-0000-0000000000a2';
  if v_n <> 0 then raise exception 'FUGA ENTRE CENTROS: alguien de fuera leyó % días del Centro Norte', v_n; end if;
end $$;

reset role;

-- ═══════════════════ 3. sin sesión, y service_role

-- Sin identidad DE VERDAD: los claims del `login_as` anterior siguen puestos
-- en la transacción, y con ellos `auth.uid()` seguiría siendo la de fuera.
select set_config('request.jwt.claims', '', true);
set local role anon;
do $$
declare v_n int;
begin
  select count(*) into v_n from public.employee_schedule;
  if v_n <> 0 then raise exception 'SIN SESIÓN SE LEEN % DÍAS DE HORARIO', v_n; end if;
  select count(*) into v_n from public.employee_schedule_exceptions;
  if v_n <> 0 then raise exception 'SIN SESIÓN SE LEEN % AUSENCIAS', v_n; end if;
end $$;
reset role;

set local role service_role;
do $$
declare v_n int;
begin
  -- LO LEGÍTIMO: el widget público lee con service_role. Sin el grant de la
  -- migración, esto sería «permission denied» con la RLS perfecta.
  select count(*) into v_n from public.employee_schedule
  where employee_id in ('30000000-0000-0000-0000-000000000001', 'ee450000-0000-0000-0000-00000000000b');
  if v_n <> 14 then raise exception 'SERVICE_ROLE NO LEE LOS HORARIOS: % filas, esperaba 14', v_n; end if;
end $$;
reset role;

-- ═══════════════════ 4. la forma

do $$
begin
  -- Dos ausencias de la misma ficha en las mismas fechas: el EXCLUDE.
  begin
    insert into public.employee_schedule_exceptions (employee_id, fecha_inicio, fecha_fin, cerrado)
    values ('30000000-0000-0000-0000-000000000001', '2026-10-08', '2026-10-12', true);
    raise exception 'EL EXCLUDE NO FRENA DOS AUSENCIAS SOLAPADAS';
  exception when exclusion_violation then null;
  end;

  -- Las mismas fechas en OTRA ficha sí (control del anterior).
  insert into public.employee_schedule_exceptions (employee_id, fecha_inicio, fecha_fin, cerrado)
  values ('30000000-0000-0000-0000-000000000002', '2026-10-08', '2026-10-12', true);

  -- Un día con salida antes de la entrada.
  begin
    insert into public.employee_schedule (employee_id, dia_semana, apertura, cierre)
    values ('30000000-0000-0000-0000-000000000002', 1, '14:00', '10:00');
    raise exception 'EL CHECK DEJA UNA SALIDA ANTERIOR A LA ENTRADA';
  exception when check_violation then null;
  end;

  -- Una ausencia «libra» con horas.
  begin
    insert into public.employee_schedule_exceptions (employee_id, fecha_inicio, fecha_fin, cerrado, apertura, cierre)
    values ('30000000-0000-0000-0000-000000000003', '2026-10-08', '2026-10-08', true, '10:00', '14:00');
    raise exception 'EL CHECK DEJA UNA AUSENCIA CON HORAS';
  exception when check_violation then null;
  end;

  -- Dos filas para el mismo día de la misma ficha.
  begin
    insert into public.employee_schedule (employee_id, dia_semana, apertura, cierre)
    values ('30000000-0000-0000-0000-000000000001', 1, '10:00', '12:00');
    raise exception 'SE ACEPTAN DOS FILAS PARA EL MISMO DÍA';
  exception when unique_violation then null;
  end;
end $$;

-- Borrar la ficha se lleva su horario: no quedan filas huérfanas.
delete from public.employees where id = 'ee450000-0000-0000-0000-0000000000a2';
do $$
declare v_n int;
begin
  select count(*) into v_n from public.employee_schedule where employee_id = 'ee450000-0000-0000-0000-0000000000a2';
  if v_n <> 0 then raise exception 'EL HORARIO SOBREVIVE A SU FICHA: % filas', v_n; end if;
end $$;

do $$ begin raise notice 'HORARIO POR EMPLEADA: ALL PASSED'; end $$;

rollback;
