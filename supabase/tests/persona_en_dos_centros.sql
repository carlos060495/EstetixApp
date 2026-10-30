-- Una persona con ficha en dos centros no se puede agendar dos veces a la vez.
--
-- `citas_sin_solape.sql` ya prueba el candado DENTRO de un centro y sigue
-- pasando sin tocarse. Lo que no podía probar es lo que hasta ahora no existía:
-- la misma señora con dos fichas. Ahí «un empleado» y «una persona» dejan de
-- ser lo mismo, y la protección de antes —por `employee_id`— dejaba pasar dos
-- clientas esperando a Eva en dos sitios a la misma hora.
--
-- TODO DENTRO DE UNA TRANSACCIÓN QUE SE DESHACE.

begin;

set client_min_messages to notice;

-- ───────────────────────────────── el decorado

insert into public.salons (id, nombre_comercial, account_id)
select 'bbbbbbbb-0000-0000-0000-00000000000b', 'Centro Norte', account_id
from public.salons where id = '00000000-0000-0000-0000-000000000001';

insert into public.employees (id, salon_id, nombre) values
  ('eeee0000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'Eva'),
  ('eeee0000-0000-0000-0000-000000000002', 'bbbbbbbb-0000-0000-0000-00000000000b', 'Eva'),
  -- Y una que NO es Eva, para el control: dos personas distintas SÍ pueden
  -- coincidir en la hora.
  ('eeee0000-0000-0000-0000-000000000003', 'bbbbbbbb-0000-0000-0000-00000000000b', 'Otra');

insert into public.services (id, salon_id, nombre, precio, duracion_min) values
  ('5e5e0000-0000-0000-0000-00000000000b', 'bbbbbbbb-0000-0000-0000-00000000000b', 'Corte', 30, 60);
insert into public.clients (id, salon_id, nombre) values
  ('c11c0000-0000-0000-0000-00000000000b', 'bbbbbbbb-0000-0000-0000-00000000000b', 'Clienta Norte');

-- ═══════════════ 1. sin enlace, todo sigue exactamente igual

-- LA EQUIVALENCIA. Mientras nadie esté enlazado, la restricción nueva acepta y
-- rechaza lo mismo que la vieja: es lo que hace desplegable esta migración, y
-- hoy no hay una sola persona enlazada en producción.
do $$
declare v_ok boolean := false;
begin
  insert into public.appointments
    (salon_id, client_id, employee_id, service_id, inicio, fin, estado, canal)
  select '00000000-0000-0000-0000-000000000001', c.id,
         'eeee0000-0000-0000-0000-000000000001', s.id,
         '2026-10-01 10:00+02', '2026-10-01 11:00+02', 'confirmada', 'manual'
  from public.clients c, public.services s
  where c.salon_id = '00000000-0000-0000-0000-000000000001'
    and s.salon_id = '00000000-0000-0000-0000-000000000001' limit 1;

  -- Sin enlace, la ficha del otro centro es OTRA empleada: se puede.
  insert into public.appointments
    (salon_id, client_id, employee_id, service_id, inicio, fin, estado, canal)
  values ('bbbbbbbb-0000-0000-0000-00000000000b', 'c11c0000-0000-0000-0000-00000000000b',
          'eeee0000-0000-0000-0000-000000000002', '5e5e0000-0000-0000-0000-00000000000b',
          '2026-10-01 10:30+02', '2026-10-01 11:30+02', 'confirmada', 'manual');
  v_ok := true;

  -- Y el candado de siempre, dentro del mismo centro, sigue cerrado.
  begin
    insert into public.appointments
      (salon_id, client_id, employee_id, service_id, inicio, fin, estado, canal)
    select '00000000-0000-0000-0000-000000000001', c.id,
           'eeee0000-0000-0000-0000-000000000001', s.id,
           '2026-10-01 10:30+02', '2026-10-01 11:30+02', 'confirmada', 'manual'
    from public.clients c, public.services s
    where c.salon_id = '00000000-0000-0000-0000-000000000001'
      and s.salon_id = '00000000-0000-0000-0000-000000000001' limit 1;
    raise exception 'EL CANDADO DE SIEMPRE SE HA ABIERTO: dos citas solapadas en el mismo centro';
  exception when exclusion_violation then
    null;
  end;

  if not v_ok then
    raise exception 'sin enlace, dos fichas distintas deberían poder coincidir';
  end if;
  raise notice '1. sin enlace, la restricción se comporta como la de siempre';
end $$;

-- ═══════════════ 2. al enlazar, las citas que YA existen repuntan

-- Es la mitad que se olvida: enlazar a Eva hoy tiene que proteger lo que YA
-- está en la agenda, que es justo lo que hay cuando alguien se da cuenta del
-- problema.
--
-- SE MIRA LA FICHA QUE **CAMBIA** DE PERSONA, no la que ya apuntaba bien. La
-- primera versión de este test miraba la del centro 1, cuyo `persona_id` ya era
-- su propio id —el mismo que se elige como persona—, así que el repunte era un
-- no-op y quitar el trigger no rompía nada. Lo caza la ficha del centro 2, que
-- pasa de apuntarse a sí misma a apuntar a Eva.
--
-- Se mueve antes la cita del centro 2 a una hora que no choque: si chocara, el
-- propio enlace sería imposible y no se probaría el repunte.
update public.appointments set inicio = '2026-10-01 16:00+02', fin = '2026-10-01 17:00+02'
where employee_id = 'eeee0000-0000-0000-0000-000000000002';

do $$
declare v_persona uuid; v_ok boolean := false;
begin
  update public.employees set persona_id = 'eeee0000-0000-0000-0000-000000000001'
  where id in ('eeee0000-0000-0000-0000-000000000001', 'eeee0000-0000-0000-0000-000000000002');

  select persona_id into v_persona from public.appointments
  where employee_id = 'eeee0000-0000-0000-0000-000000000002';

  if v_persona is distinct from 'eeee0000-0000-0000-0000-000000000001' then
    raise exception 'LA CITA DEL CENTRO 2 NO REPUNTÓ (%): quedaría fuera de la protección',
      v_persona;
  end if;

  -- Y se comprueba que el repunte SIRVE: esa hora ya está ocupada para Eva,
  -- así que el centro 1 no puede agendarla encima.
  begin
    insert into public.appointments
      (salon_id, client_id, employee_id, service_id, inicio, fin, estado, canal)
    select '00000000-0000-0000-0000-000000000001', c.id,
           'eeee0000-0000-0000-0000-000000000001', s.id,
           '2026-10-01 16:30+02', '2026-10-01 17:30+02', 'confirmada', 'manual'
    from public.clients c, public.services s
    where c.salon_id = '00000000-0000-0000-0000-000000000001'
      and s.salon_id = '00000000-0000-0000-0000-000000000001' limit 1;
  exception when exclusion_violation then
    v_ok := true;
  end;
  if not v_ok then
    raise exception 'LA CITA REPUNTADA NO PROTEGE: se agendó encima desde el otro centro';
  end if;

  raise notice '2. al enlazar, las citas que ya estaban apuntan a la persona Y protegen';
end $$;

-- ═══════════════ 3. y ya no se la puede agendar en dos sitios a la vez

do $$
declare v_ok boolean := false;
begin
  begin
    insert into public.appointments
      (salon_id, client_id, employee_id, service_id, inicio, fin, estado, canal)
    values ('bbbbbbbb-0000-0000-0000-00000000000b', 'c11c0000-0000-0000-0000-00000000000b',
            'eeee0000-0000-0000-0000-000000000002', '5e5e0000-0000-0000-0000-00000000000b',
            '2026-10-01 10:30+02', '2026-10-01 11:30+02', 'confirmada', 'manual');
  exception when exclusion_violation then
    v_ok := true;
  end;
  if not v_ok then
    raise exception 'SE AGENDÓ A LA MISMA PERSONA EN DOS CENTROS A LA VEZ';
  end if;
  raise notice '3. la base impide agendarla en dos centros a la misma hora';
end $$;

-- ═══════════════ 4. los controles, para que el 3 no pase por lo que no es

do $$
declare v_n int;
begin
  -- 4a. La MISMA persona en el otro centro, a otra hora: se puede. Si no,
  -- estaríamos impidiendo que trabaje en dos sitios, que es lo contrario.
  insert into public.appointments
    (salon_id, client_id, employee_id, service_id, inicio, fin, estado, canal)
  values ('bbbbbbbb-0000-0000-0000-00000000000b', 'c11c0000-0000-0000-0000-00000000000b',
          'eeee0000-0000-0000-0000-000000000002', '5e5e0000-0000-0000-0000-00000000000b',
          '2026-10-01 13:00+02', '2026-10-01 14:00+02', 'confirmada', 'manual');

  -- 4b. OTRA persona a la misma hora que Eva: se puede.
  insert into public.appointments
    (salon_id, client_id, employee_id, service_id, inicio, fin, estado, canal)
  values ('bbbbbbbb-0000-0000-0000-00000000000b', 'c11c0000-0000-0000-0000-00000000000b',
          'eeee0000-0000-0000-0000-000000000003', '5e5e0000-0000-0000-0000-00000000000b',
          '2026-10-01 10:30+02', '2026-10-01 11:30+02', 'confirmada', 'manual');

  -- SE CUENTAN LAS DOS QUE ACABAN DE ENTRAR, por su hora, no el total de la
  -- ficha. Atado al total, este control se rompía al cambiar cualquier otra
  -- parte del decorado — y se rompió: bastó dejar de borrar una cita para que
  -- fallara sin que nada estuviera mal.
  select count(*) into v_n from public.appointments
  where inicio in ('2026-10-01 13:00+02'::timestamptz, '2026-10-01 10:30+02'::timestamptz)
    and employee_id in ('eeee0000-0000-0000-0000-000000000002',
                        'eeee0000-0000-0000-0000-000000000003');
  if v_n <> 2 then
    raise exception 'LA RESTRICCIÓN ESTÁ CERRADA DE MÁS: entraron % de las 2 que debían', v_n;
  end if;
  raise notice '4. y no bloquea de más: otra hora sí, y otra persona a la misma hora también';
end $$;

-- ═══════════════ 5. una persona no puede tener dos fichas en el MISMO centro

do $$
declare v_ok boolean := false;
begin
  begin
    insert into public.employees (salon_id, nombre, persona_id)
    values ('00000000-0000-0000-0000-000000000001', 'Eva otra vez',
            'eeee0000-0000-0000-0000-000000000001');
  exception when unique_violation then
    v_ok := true;
  end;
  if not v_ok then
    raise exception 'SE DUPLICÓ A LA MISMA PERSONA EN EL MISMO CENTRO';
  end if;
  raise notice '5. dos fichas de la misma persona en un centro: imposible';
end $$;

-- ═══════════════ 6. el PIN del fichaje es de la persona

-- Un PIN por ficha significaría dos PINes para el mismo trabajo, y los dos
-- acabarían escritos en el mismo papel. Se comparte la credencial; NO las
-- horas, que siguen colgando del `employee_id` de cada centro.
do $$
declare v_pin1 text; v_pin2 text;
begin
  -- (a) cambiar el PIN en una ficha lo cambia en las dos
  update public.employees set pin_fichaje_hash = 'hash-de-eva'
  where id = 'eeee0000-0000-0000-0000-000000000001';

  select pin_fichaje_hash into v_pin2 from public.employees
  where id = 'eeee0000-0000-0000-0000-000000000002';
  if v_pin2 is distinct from 'hash-de-eva' then
    raise exception 'EL PIN NO VIAJÓ A LA OTRA FICHA (%): Eva tendría dos PINes', v_pin2;
  end if;

  -- (b) y borrarlo se lo quita en las dos: quitárselo quiere decir eso
  update public.employees set pin_fichaje_hash = null
  where id = 'eeee0000-0000-0000-0000-000000000002';
  select pin_fichaje_hash into v_pin1 from public.employees
  where id = 'eeee0000-0000-0000-0000-000000000001';
  if v_pin1 is not null then
    raise exception 'SE LE QUITÓ EL PIN Y SEGUÍA ABRIENDO EL OTRO QUIOSCO';
  end if;

  -- (c) EL CONTROL: no se lo pisa a QUIEN NO ES ELLA. Sin esto, un trigger que
  -- actualizara de más pasaría (a) y (b) igual de verde.
  update public.employees set pin_fichaje_hash = 'hash-de-otra'
  where id = 'eeee0000-0000-0000-0000-000000000003';
  update public.employees set pin_fichaje_hash = 'hash-de-eva-2'
  where id = 'eeee0000-0000-0000-0000-000000000001';

  select pin_fichaje_hash into v_pin1 from public.employees
  where id = 'eeee0000-0000-0000-0000-000000000003';
  if v_pin1 is distinct from 'hash-de-otra' then
    raise exception 'EL PIN DE EVA LE PISÓ EL SUYO A OTRA PERSONA (%)', v_pin1;
  end if;

  -- (d) una ficha que NACE enlazada ADOPTA el PIN, sin que nadie se lo diga.
  -- Es el caso del alta: se marca «ya trabaja en el otro centro» y Eva entra
  -- en el quiosco nuevo con el PIN de siempre. Hace falta un tercer centro
  -- porque en el suyo ya tiene ficha (bloque 5).
  insert into public.salons (id, nombre_comercial, account_id)
  select 'bbbbbbbb-0000-0000-0000-00000000000c', 'Centro Sur', account_id
  from public.salons where id = '00000000-0000-0000-0000-000000000001';

  insert into public.employees (id, salon_id, nombre, persona_id)
  values ('eeee0000-0000-0000-0000-000000000004', 'bbbbbbbb-0000-0000-0000-00000000000c',
          'Eva', 'eeee0000-0000-0000-0000-000000000001');

  select pin_fichaje_hash into v_pin1 from public.employees
  where id = 'eeee0000-0000-0000-0000-000000000004';
  if v_pin1 is distinct from 'hash-de-eva-2' then
    raise exception 'LA FICHA NUEVA NO ADOPTÓ EL PIN DE LA PERSONA (%)', v_pin1;
  end if;

  -- Y el control de (d): una ficha que nace SIN enlace no adopta nada de nadie.
  insert into public.employees (id, salon_id, nombre)
  values ('eeee0000-0000-0000-0000-000000000005', 'bbbbbbbb-0000-0000-0000-00000000000c',
          'Nadie conocida');
  select pin_fichaje_hash into v_pin1 from public.employees
  where id = 'eeee0000-0000-0000-0000-000000000005';
  if v_pin1 is not null then
    raise exception 'UNA FICHA SIN ENLACE HEREDÓ UN PIN AJENO (%)', v_pin1;
  end if;

  raise notice '6. el PIN es de la persona: viaja, se borra, se hereda al alta y no pisa el de otra';
end $$;

-- ═══════════════ 7. las HORAS, en cambio, NO se mezclan

-- Es la otra mitad de la decisión: se comparte la credencial y no el fichaje.
-- Si `time_entries` colgara de la persona, sumar las horas de un centro sería
-- imposible y la nómina de cada local dejaría de cuadrar.
do $$
declare v_n int;
begin
  insert into public.time_entries (salon_id, employee_id, tipo, ts) values
    ('00000000-0000-0000-0000-000000000001', 'eeee0000-0000-0000-0000-000000000001',
     'entrada', '2026-10-01 09:00+02'),
    ('bbbbbbbb-0000-0000-0000-00000000000b', 'eeee0000-0000-0000-0000-000000000002',
     'entrada', '2026-10-01 15:00+02');

  select count(*) into v_n from public.time_entries
  where employee_id = 'eeee0000-0000-0000-0000-000000000001';
  if v_n <> 1 then
    raise exception 'EL FICHAJE SE MEZCLÓ ENTRE CENTROS: % en la ficha del centro 1', v_n;
  end if;
  raise notice '7. las horas siguen siendo de cada centro: la nómina de cada local cuadra';
end $$;

do $$
begin
  raise notice 'PERSONA EN DOS CENTROS: ALL PASSED';
end $$;

rollback;
