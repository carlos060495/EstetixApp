-- La frontera nueva, probada donde de verdad cambia.
--
-- `tenant_isolation.sql` ya prueba que un salón no ve a otro, y sigue pasando
-- sin tocarse. Lo que NO podía probar es lo que hoy no existía: una cuenta con
-- DOS centros. Ahí es donde «está entre los salones de mi cuenta» deja de dar
-- el mismo resultado que «es EL salón de mi token», y donde una frontera mal
-- puesta se lleva por delante la privacidad entre centros del mismo dueño.
--
-- LO QUE SE FIJA, por orden de gravedad:
--
--   1. Un empleado del centro 1 NO lee nada del centro 2 de SU PROPIA cuenta.
--      Es el que protege la decisión que se acaba de tomar, y el que no existía
--      porque hasta ahora no hacía falta.
--   2. Un usuario de la cuenta A no lee un salón de la cuenta B, ni pidiéndolo
--      por su id.
--   3. Un admin sí ve los dos centros de su cuenta.
--   4. Y la equivalencia: con un salón por cuenta, todo se comporta igual.
--
-- TODO OCURRE DENTRO DE UNA TRANSACCIÓN QUE SE DESHACE. El decorado —el segundo
-- centro, el usuario nuevo— no sobrevive al fichero, así que las otras dos
-- suites siguen contando los mismos datos de siempre.

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

create or replace function pg_temp.como_postgres() returns void language plpgsql as $$
begin
  reset role;
end;
$$;

-- ───────────────────────────────────────────────── el decorado

-- Un SEGUNDO centro en la cuenta del salón A, que es lo que todavía no existe
-- en producción y por eso hay que fabricarlo aquí.
insert into public.salons (id, nombre_comercial, plan, account_id)
select '0a0a0a0a-0000-0000-0000-00000000000b', 'Centro Norte', 'elite', s.account_id
from public.salons s where s.id = '00000000-0000-0000-0000-000000000001';

-- Una clienta que SOLO existe en el centro nuevo. Es la fila que nadie del
-- centro 1 debería poder leer.
insert into public.clients (id, salon_id, nombre)
values ('0c0c0c0c-0000-0000-0000-00000000000b',
        '0a0a0a0a-0000-0000-0000-00000000000b', 'Clienta del Norte');

-- La identidad en GoTrue. `users.auth_user_id` tiene clave foránea contra
-- `auth.users`, así que no se puede inventar un `sub`: se clona una fila que ya
-- existe cambiándole el id y el correo. Todo dentro de la transacción, así que
-- ni esta identidad ni nada de lo demás sobrevive al fichero.
insert into auth.users (id, instance_id, aud, role, email)
select '1e1e1e1e-0000-0000-0000-000000000001', u.instance_id, u.aud, u.role,
       'de-fuera.centro1@test.local'
from auth.users u limit 1;

-- ALGUIEN DE FUERA (reescrito en la 0069): usuario de Auth y NINGUNA fila en
-- `users`. Hasta la 0069 era una estilista del centro 1; ese rol ya no existe,
-- y lo que sí puede existir es un usuario de Auth que no es la administradora de
-- ninguna cuenta. Se presenta con el token más peligroso: centro 1 y rol admin.
do $$
begin
  if exists (select 1 from public.users where auth_user_id = '1e1e1e1e-0000-0000-0000-000000000001') then
    raise exception 'EL DECORADO ES FALSO: la identidad de prueba tiene fila en users';
  end if;
end $$;

-- ═══════════════════ 1. alguien de fuera no entra ni en el centro 1 ni en el 2

select pg_temp.login_as('00000000-0000-0000-0000-000000000001', 'admin',
                        '1e1e1e1e-0000-0000-0000-000000000001');

do $$
declare v_n int;
begin
  -- Pidiéndolo por su id, que es lo que haría un selector de centro manipulado.
  select count(*) into v_n from public.clients
  where salon_id = '0a0a0a0a-0000-0000-0000-00000000000b';
  if v_n <> 0 then
    raise exception 'FUGA ENTRE CENTROS: alguien de fuera leyó % clientes del centro 2', v_n;
  end if;

  -- Y sin filtrar por nada: tampoco se le cuela.
  select count(*) into v_n from public.clients
  where id = '0c0c0c0c-0000-0000-0000-00000000000b';
  if v_n <> 0 then
    raise exception 'FUGA ENTRE CENTROS: la clienta del centro 2 se leyó sin filtro de salón';
  end if;

  -- El propio salón del otro centro tampoco se ve.
  select count(*) into v_n from public.salons
  where id = '0a0a0a0a-0000-0000-0000-00000000000b';
  if v_n <> 0 then
    raise exception 'FUGA ENTRE CENTROS: se leyó la ficha del centro 2';
  end if;

  -- SOLO ENTRA LA ADMINISTRADORA (0063, 0069): tampoco ve el centro que dice su
  -- token. Las fugas de arriba las vigila, con una administradora, el resto de
  -- este fichero.
  select count(*) into v_n from public.clients
  where salon_id = '00000000-0000-0000-0000-000000000001';
  if v_n <> 0 then
    raise exception 'SOLO ENTRA LA ADMINISTRADORA: alguien de fuera leyó % clientes del centro de su token', v_n;
  end if;
end $$;

-- ═══════════════════ 2. y no cruza a otra cuenta, que es lo de siempre

do $$
declare v_n int;
begin
  select count(*) into v_n from public.clients
  where salon_id = '00000000-0000-0000-0000-000000000002';
  if v_n <> 0 then
    raise exception 'FUGA ENTRE CUENTAS: se leyeron % clientes de la cuenta B', v_n;
  end if;
end $$;

-- Falsificar el salón al ESCRIBIR tampoco vale: el `with check` lo mira.
do $$
declare v_ok boolean := false;
begin
  begin
    insert into public.clients (salon_id, nombre)
    values ('0a0a0a0a-0000-0000-0000-00000000000b', 'colada por la puerta de atrás');
  exception when others then
    v_ok := true;
  end;
  if not v_ok then
    raise exception 'FUGA AL ESCRIBIR: alguien de fuera creó una clienta en otro centro';
  end if;
end $$;

-- ═══════════════════ 3. el admin de la cuenta sí ve los dos centros

select pg_temp.como_postgres();
select pg_temp.login_as('00000000-0000-0000-0000-000000000001', 'admin',
                        '10000000-0000-0000-0000-000000000001');

do $$
declare v_n int;
begin
  select count(*) into v_n from public.salons
  where id in ('00000000-0000-0000-0000-000000000001',
               '0a0a0a0a-0000-0000-0000-00000000000b');
  if v_n <> 2 then
    raise exception 'EL ADMIN NO VE SU CUENTA ENTERA: ve % centros de 2', v_n;
  end if;

  -- Y la clienta del centro nuevo, que alguien de fuera no podía leer.
  select count(*) into v_n from public.clients
  where id = '0c0c0c0c-0000-0000-0000-00000000000b';
  if v_n <> 1 then
    raise exception 'EL ADMIN NO VE EL CENTRO 2 DE SU CUENTA';
  end if;

  -- Pero sigue sin ver la cuenta B.
  select count(*) into v_n from public.clients
  where salon_id = '00000000-0000-0000-0000-000000000002';
  if v_n <> 0 then
    raise exception 'FUGA ENTRE CUENTAS: el admin de A leyó clientes de B';
  end if;
end $$;

-- ═══════════════════ 4. la equivalencia: un salón por cuenta

select pg_temp.como_postgres();

do $$
declare v_visibles int;
begin
  -- El admin de la cuenta B, que sigue teniendo un solo centro: la frontera
  -- nueva le devuelve exactamente uno, igual que le devolvía la vieja.
  perform set_config('request.jwt.claims',
    json_build_object('sub', '10000000-0000-0000-0000-000000000002',
                      'salon_id', '00000000-0000-0000-0000-000000000002',
                      'rol', 'admin')::text, true);
  select cardinality(public.salones_visibles()) into v_visibles;
  if v_visibles <> 1 then
    raise exception 'LA EQUIVALENCIA SE ROMPIÓ: una cuenta de un centro ve %', v_visibles;
  end if;
end $$;

-- ═══════════════════ 4b. el centro activo NO es la frontera

-- LO QUE PEDÍA LA FASE B: si alguien manipula el centro activo, la base tiene
-- que seguir diciendo que no.
--
-- El centro activo viaja en una cabecera HTTP que el backend comprueba, pero
-- eso es COMODIDAD. Aquí se prueba la frontera de verdad: se cambia el claim
-- `salon_id` del token —lo más parecido a manipularlo que se puede hacer— y se
-- comprueba que `salones_visibles()` devuelve EXACTAMENTE lo mismo, porque no
-- lo mira. Lo que mira es quién eres (`auth.uid()`) y a qué centros perteneces.
do $$
declare v_con_su_centro uuid[]; v_con_el_otro uuid[]; v_n int;
begin
  -- La administradora de la cuenta B, con su claim de siempre. (Hasta la 0069
  -- era una estilista del centro 1; ver arriba.)
  perform set_config('request.jwt.claims',
    json_build_object('sub','10000000-0000-0000-0000-000000000002',
                      'salon_id','00000000-0000-0000-0000-000000000002',
                      'rol','admin')::text, true);
  v_con_su_centro := public.salones_visibles();

  -- La MISMA persona, diciendo que su centro es el de la cuenta A.
  perform set_config('request.jwt.claims',
    json_build_object('sub','10000000-0000-0000-0000-000000000002',
                      'salon_id','00000000-0000-0000-0000-000000000001',
                      'rol','admin')::text, true);
  v_con_el_otro := public.salones_visibles();

  if v_con_su_centro is distinct from v_con_el_otro then
    raise exception 'EL CLAIM DEL TOKEN CAMBIA LO QUE SE VE: % vs %',
      v_con_su_centro, v_con_el_otro;
  end if;
  if cardinality(v_con_su_centro) <> 1 then
    raise exception 'EL DECORADO ES FALSO: la administradora de B ve % centros', cardinality(v_con_su_centro);
  end if;

  -- Y con el claim manipulado sigue sin leer nada de la cuenta A.
  set local role authenticated;
  select count(*) into v_n from public.clients
  where salon_id = '00000000-0000-0000-0000-000000000001';
  if v_n <> 0 then
    raise exception 'FUGA: manipular el centro activo dio acceso a % clientes', v_n;
  end if;
end $$;

-- ═══════════════════ 5. sin sesión no se ve nada

do $$
declare v_n int;
begin
  perform set_config('request.jwt.claims', json_build_object()::text, true);
  select cardinality(public.salones_visibles()) into v_n;
  if v_n <> 0 then
    raise exception 'SIN IDENTIDAD SE VEN % SALONES; debería ser 0', v_n;
  end if;
end $$;

do $$
begin
  raise notice 'ACCOUNT ISOLATION TESTS: ALL PASSED';
end $$;

rollback;
