-- Lo que se paga cuelga de la cuenta (migración 0040).
--
-- `account_isolation.sql` prueba QUIÉN VE QUÉ. Esto prueba lo otro: que el
-- dinero, la suspensión y la marca viven en la cuenta, que las dos copias no
-- se separan mientras dure la ventana de despliegue, y que un salón nuevo
-- nace con cuenta —que es lo que la 0039 rompió en producción sin que ninguna
-- suite se enterara.
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

-- ═══════════════ 1. el alta: un salón sin cuenta ES una cuenta nueva

-- LA REGRESIÓN QUE LLEGÓ A PRODUCCIÓN. La 0039 puso `salons.account_id` en not
-- null y no tocó el backend, que da de alta insertando el salón sin cuenta. El
-- registro devolvía 502 para todo el mundo. Este insert es, columna por
-- columna, el de `auth.py`.
do $$
declare v_salon uuid := gen_random_uuid(); v_cuenta uuid;
begin
  insert into public.salons (id, nombre_comercial, email, pending_plan)
  values (v_salon, '  Peluquería Alta  ', 'alta@test.local', 'elite');

  select account_id into v_cuenta from public.salons where id = v_salon;
  if v_cuenta is null then
    raise exception 'EL ALTA ESTÁ ROTA: el salón nació sin cuenta';
  end if;

  if (select nombre from public.accounts where id = v_cuenta) <> 'Peluquería Alta' then
    raise exception 'la cuenta no se llamó como su salón (ni se recortó el nombre)';
  end if;
  if (select pending_plan::text from public.accounts where id = v_cuenta) <> 'elite' then
    raise exception 'el plan pendiente del alta no subió a la cuenta';
  end if;

  -- Y el usuario del alta, que tampoco sabe de cuentas. Sin esto tiene
  -- `account_id` nulo y la rama de admin de `salones_visibles()` —que empareja
  -- por cuenta— no le devuelve ni su propio centro.
  insert into public.users (salon_id, nombre, email, rol)
  values (v_salon, 'Admin Alta', 'admin.alta@test.local', 'admin');
  if (select account_id from public.users where email = 'admin.alta@test.local')
     is distinct from v_cuenta then
    raise exception 'el usuario del alta no entró en la cuenta de su salón';
  end if;

  raise notice '1. el alta funciona: salón, cuenta y usuario nacen atados';
end $$;

-- ═══════════════ 1b. y la suscripción, para el backend que ya no corre

-- EL SEGUNDO INCIDENTE, y no lo puede cazar ningún test del backend actual: el
-- backend de hoy pasa `account_id` explícitamente, así que nunca ejercita este
-- camino. Quien lo ejercitaba era el backend ANTERIOR durante la ventana de
-- despliegue —y volvería a hacerlo si hubiera que revertir—, insertando la
-- suscripción con `salon_id` y nada más. Con la 0040 aplicada y sin trigger,
-- eso reventaba con un not-null, el webhook lo tomaba por una carrera y
-- contestaba 200: el cliente pagaba en Stripe y se quedaba fuera.
--
-- Por eso esto se prueba AQUÍ, contra la base, con la forma exacta del insert
-- viejo. Es el único sitio donde se puede.
do $$
declare v_cuenta uuid;
begin
  insert into public.subscriptions (salon_id, plan, estado)
  values ('00000000-0000-0000-0000-000000000001', 'pro', 'trialing');

  select account_id into v_cuenta from public.subscriptions
  where salon_id = '00000000-0000-0000-0000-000000000001';

  if v_cuenta is null then
    raise exception 'EL COBRO ESTÁ ROTO: la suscripción nació sin cuenta';
  end if;
  if v_cuenta <> (select account_id from public.salons
                  where id = '00000000-0000-0000-0000-000000000001') then
    raise exception 'la suscripción entró en la cuenta EQUIVOCADA';
  end if;

  raise notice '1b. el insert del backend ANTERIOR sigue entrando en su cuenta';
end $$;

-- ═══════════════ 2. el backfill no dejó a nadie discrepando

do $$
declare v_n int;
begin
  select count(*) into v_n
  from public.salons s join public.accounts a on a.id = s.account_id
  where (s.plan, s.pending_plan, s.stripe_customer_id, s.suspendido, s.suspendido_at,
         s.suspendido_por, s.cif, s.logo_url, s.accent_color)
     is distinct from
        (a.plan, a.pending_plan, a.stripe_customer_id, a.suspendido, a.suspendido_at,
         a.suspendido_por, a.cif, a.logo_url, a.accent_color);
  if v_n <> 0 then
    raise exception 'EL BACKFILL DEJÓ % centros discrepando de su cuenta', v_n;
  end if;
  raise notice '2. las dos copias dicen lo mismo en los % salones',
    (select count(*) from public.salons);
end $$;

-- ═══════════════ 3. las dos copias, en los dos sentidos

-- Durante la ventana de despliegue hay dos escritores posibles y no se sabe
-- cuál va a escribir: el backend viejo pone el plan en `salons`, el nuevo lo
-- pone en `accounts`, y revertir el despliegue vuelve al primero. Los dos
-- caminos tienen que acabar igual.
do $$
declare v_s uuid; v_a uuid;
begin
  select id, account_id into v_s, v_a from public.salons order by id limit 1;

  -- El backend VIEJO escribe en el centro.
  update public.salons set plan = 'elite', suspendido = true, suspendido_at = now()
  where id = v_s;
  if (select plan::text from public.accounts where id = v_a) <> 'elite'
     or not (select suspendido from public.accounts where id = v_a) then
    raise exception 'SE ESCRIBIÓ EN salons Y LA CUENTA NO SE ENTERÓ';
  end if;

  -- El backend NUEVO escribe en la cuenta.
  update public.accounts
  set plan = 'pro', suspendido = false, suspendido_at = null, accent_color = '#123456'
  where id = v_a;
  if (select plan::text from public.salons where id = v_s) <> 'pro'
     or (select suspendido from public.salons where id = v_s)
     or (select accent_color from public.salons where id = v_s) <> '#123456' then
    raise exception 'SE ESCRIBIÓ EN accounts Y EL CENTRO NO SE ENTERÓ';
  end if;

  raise notice '3. escriba quien escriba, las dos copias acaban diciendo lo mismo';
end $$;

-- ═══════════════ 4. una cuenta suspendida apaga TODOS sus centros

do $$
declare v_a uuid; v_b uuid := gen_random_uuid(); v_n int; v_total int;
begin
  select account_id into v_a from public.salons order by id limit 1;

  insert into public.salons (id, nombre_comercial, account_id)
  values (v_b, 'Centro Norte', v_a);

  -- El centro nuevo hereda el plan de su cuenta en vez de nacer en 'basico':
  -- si no, las dos copias empezarían discrepando el día que se abra el segundo
  -- local.
  if (select plan::text from public.salons where id = v_b) <> 'pro' then
    raise exception 'EL CENTRO NUEVO NO HEREDÓ EL PLAN DE SU CUENTA';
  end if;
  -- Y abrirlo no le baja el plan a la cuenta, que es el accidente contrario.
  if (select plan::text from public.accounts where id = v_a) <> 'pro' then
    raise exception 'ABRIR UN CENTRO PISÓ EL PLAN DE LA CUENTA';
  end if;

  -- SE CUENTAN LOS QUE HAY, no «dos». Fijar el número ataba el test a cuántos
  -- centros existieran en la base local, y bastó abrir uno de verdad para que
  -- fallara sin que nada estuviera roto. Lo que se afirma es «TODOS».
  select count(*) into v_total from public.salons where account_id = v_a;
  update public.accounts set suspendido = true where id = v_a;
  select count(*) into v_n from public.salons where account_id = v_a and suspendido;
  if v_n <> v_total then
    raise exception 'LA SUSPENSIÓN APAGÓ % centros de %', v_n, v_total;
  end if;

  -- Control: reactivar los enciende otra vez. Sin esto, un trigger que pusiera
  -- `suspendido` a true sin mirar el valor pasaría el test de arriba.
  update public.accounts set suspendido = false where id = v_a;
  select count(*) into v_n from public.salons where account_id = v_a and suspendido;
  if v_n <> 0 then
    raise exception 'AL REACTIVAR QUEDARON % centros apagados', v_n;
  end if;

  raise notice '4. una cuenta suspendida apaga sus % centros, y reactivarla los enciende', v_total;
end $$;

-- ═══════════════ 5. el plan y la suspensión no los toca quien paga

select pg_temp.login_as('00000000-0000-0000-0000-000000000001', 'admin',
                        '10000000-0000-0000-0000-000000000001');

do $$
declare v_ok boolean;
begin
  -- Ve su cuenta, y SOLO la suya.
  if (select count(*) from public.accounts) <> 1 then
    raise exception 'un administrador ve % cuentas; debería ver 1',
      (select count(*) from public.accounts);
  end if;

  -- Pero no se sube de plan. Hoy lo para el `grant` por columna; si alguien lo
  -- ampliara, lo pararía el trigger. Aquí solo importa que NO PASE.
  v_ok := false;
  begin
    update public.accounts set plan = 'elite' where id = public.cuenta_actual();
    v_ok := not found;
  exception when others then v_ok := true;
  end;
  if not v_ok then
    raise exception 'UN ADMINISTRADOR SE CAMBIÓ EL PLAN ÉL SOLO';
  end if;
  raise notice '5. el administrador no puede cambiarse el plan';
end $$;

-- La suspensión, con la cuenta SUSPENDIDA DE VERDAD: un `suspendido = false`
-- sobre una cuenta que ya lo está no compara nada, y el guarda no tendría por
-- qué saltar. Se suspende primero como `postgres`, que es quien puede.
reset role;
update public.accounts set suspendido = true, suspendido_at = now()
where id = (select account_id from public.salons
            where id = '00000000-0000-0000-0000-000000000001');

select pg_temp.login_as('00000000-0000-0000-0000-000000000001', 'admin',
                        '10000000-0000-0000-0000-000000000001');

do $$
declare v_ok boolean := false;
begin
  if not (select suspendido from public.accounts where id = public.cuenta_actual()) then
    raise exception 'EL DECORADO NO ESTÁ SUSPENDIDO: la prueba no probaría nada';
  end if;

  begin
    update public.accounts set suspendido = false, suspendido_at = null
    where id = public.cuenta_actual();
    v_ok := not found;
  exception when others then v_ok := true;
  end;
  if not v_ok then
    raise exception 'UN ADMINISTRADOR SE LEVANTÓ LA SUSPENSIÓN ÉL SOLO';
  end if;

  -- Y el control: la marca sí es suya. Sin esto, los dos casos de arriba
  -- pasarían igual si `accounts` estuviera cerrada del todo.
  update public.accounts set accent_color = '#ABCDEF' where id = public.cuenta_actual();
  if not found then
    raise exception 'FRONTERA DEMASIADO CERRADA: no pudo ni cambiar su color';
  end if;

  raise notice '5. tampoco levantarse la suspensión; la marca sí la cambia';
end $$;

-- ═══════════════ 6. y alguien de fuera no toca la marca

-- Reescrito en la 0069: era una estilista, un rol que ya no existe. Ahora es un
-- token que dice centro 1 y rol admin, de alguien sin fila en `users`.

select pg_temp.login_as('00000000-0000-0000-0000-000000000001', 'admin',
                        'de0f0e0a-0000-0000-0000-000000000006');

do $$
declare v_ok boolean := false;
begin
  begin
    update public.accounts set accent_color = '#000FFF' where id = public.cuenta_actual();
    v_ok := not found;
  exception when others then v_ok := true;
  end;
  if not v_ok then
    raise exception 'ALGUIEN DE FUERA CAMBIÓ LA MARCA DE LA CUENTA';
  end if;
  raise notice '6. alguien de fuera no cambia la marca de la cuenta';
end $$;

do $$
begin
  raise notice 'CUENTA PAGA TESTS: ALL PASSED';
end $$;

rollback;
