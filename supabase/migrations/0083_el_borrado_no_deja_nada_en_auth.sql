-- 0083 · El borrado no deja nada en Auth.
--
-- Carlos, 12-10-2026.
--
-- ═══ LO QUE PASABA ═══
--
-- `auth.scim_users` cuelga del usuario con SET NULL: al borrar una cuenta, su
-- fila se quedaba, sin dueño y con el correo dentro de `resource`. El
-- simulacro lo enseñaba («queda_sin_referencia») y el borrado seguía igual. Hoy
-- en producción la tabla está vacía (no hay SSO), pero el mismo plan lo usan el
-- barrido de las demos y el borrado a 90 días, y lo que sobrevive a un borrado
-- son datos de alguien que pidió desaparecer.
--
-- ═══ LO QUE SALIÓ DE RECORRER AUTH (v2.197.0, la de producción) ═══
--
-- Las claves ajenas de TODO el esquema `auth`, no una lista de memoria:
--
--   · `scim_users.user_id → users` es SET NULL: la única clave que no se va en
--     cascada con el usuario. Raíz nueva, y ANTES que `auth.users` (después su
--     `user_id` ya es NULL y no hay por dónde encontrarla).
--   · Sin clave ajena, pero nombrando al usuario: `flow_state.user_id`,
--     `refresh_tokens.user_id` (texto; los que tienen sesión ya se iban con
--     ella, los que no, se quedaban) y `saml_relay_states.for_email`. Raíces
--     nuevas.
--   · `audit_log_entries` se borraba mirando cuatro campos; ahora, cualquier
--     entrada que nombre su id o su correo, en el campo que sea.
--
-- ═══ Y PARA QUE NO VUELVA A PASAR SIN QUE NADIE LO VEA ═══
--
--   · `marcas_de_auth(cuenta)`: sus accesos, correos y teléfonos, entre
--     comillas, tal como salen en el JSON de una fila. Se fijan ANTES de borrar.
--   · `lo_que_nombra_en_auth(marcas)`: recorre TODAS las tablas de `auth` (las
--     que haya en esa versión, sin lista) y dice cuáles tienen filas que las
--     nombran.
--   · El simulacro gana un paso 6, «queda_en_auth»: lo que quedaría. Tiene que
--     ser nada.
--   · El borrado a 90 días y el de las demos lo comprueban al final, dentro de
--     su transacción: si queda algo, SE NIEGAN y no se borra nada.
--
-- ═══ LO QUE NO CAMBIA ═══
--
-- Los interruptores (`borrado_interruptor`, `barrido_demos_interruptor`) siguen
-- APAGADOS: esta migración no los toca. El tope, la selección, lo que se queda
-- en `public`, el orden salones-antes-que-correcciones, los permisos de cada
-- función (CREATE OR REPLACE los conserva). Las cuatro funciones son las de la
-- 0068 y la 0080 con los cambios de arriba, nada más.
--
-- ═══ ORDEN DE DESPLIEGUE: 0083 → BACKEND + FRONTEND (a la vez) ═══
--
-- El backend desplegado sigue funcionando con ella: el simulacro devuelve las
-- mismas columnas, con un paso más. El nuevo pinta ese paso en rojo. Nada en
-- Stripe.

begin;

-- ═══════════════ 1. las marcas de una cuenta en Auth

create function public.marcas_de_auth(p_cuenta uuid)
returns text[]
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  -- Entre comillas, como sale un valor en el JSON de una fila: «"maria@x.es"»
  -- no casa con «"amaria@x.es"». Y escapando los comodines de LIKE: un «_» en
  -- un correo no puede casar con cualquier letra.
  select coalesce(array_agg(distinct '%"' || replace(replace(replace(m, '\', '\\'), '%', '\%'), '_', '\_') || '"%'),
                  '{}'::text[])
  from (
    select u.auth_user_id::text as m
    from public.users u where u.account_id = p_cuenta and u.auth_user_id is not null
    union
    select lower(x)
    from public.users u
    join auth.users au on au.id = u.auth_user_id
    cross join lateral (values (au.email), (au.phone)) v(x)
    where u.account_id = p_cuenta and coalesce(x, '') <> ''
  ) s;
$$;

comment on function public.marcas_de_auth(uuid) is
  'Los accesos, correos y teléfonos de una cuenta en Auth, como patrones LIKE entre comillas (0083). '
  'Se fijan ANTES de borrar: después ya no hay usuario por el que buscarlos.';

revoke all on function public.marcas_de_auth(uuid) from public, anon, authenticated, service_role;

-- ═══════════════ 2. lo que las nombra, en cualquier tabla de Auth

create function public.lo_que_nombra_en_auth(p_marcas text[], p_cubiertas jsonb default '{}'::jsonb)
returns table (tabla text, filas bigint)
language plpgsql
stable
security definer
set search_path = public, pg_temp
as $$
declare
  t record;
  n bigint;
begin
  if coalesce(cardinality(p_marcas), 0) = 0 then
    return;
  end if;
  -- TODAS las tablas de `auth`, las que tenga esta versión: una tabla nueva que
  -- el plan no conozca sale aquí el primer día. Solo se cuentan; no se leen.
  for t in
    select c.oid::regclass::text as nombre
    from pg_class c join pg_namespace s on s.oid = c.relnamespace
    where s.nspname = 'auth' and c.relkind in ('r', 'p')
    order by 1
  loop
    execute format('select count(*) from %s x where lower(row_to_json(x)::text) like any (%L::text[])%s',
                   t.nombre, p_marcas,
                   case when p_cubiertas ? t.nombre then format(' and not (%s)', p_cubiertas ->> t.nombre) else '' end)
      into n;
    if n > 0 then
      tabla := t.nombre;
      filas := n;
      return next;
    end if;
  end loop;
end;
$$;

comment on function public.lo_que_nombra_en_auth(text[], jsonb) is
  'Qué tablas de Auth tienen filas que nombran esas marcas (0083), sin contar las que cubren los predicados de '
  'p_cubiertas (tabla -> predicado). Después de un borrado tiene que devolver NADA.';

revoke all on function public.lo_que_nombra_en_auth(text[], jsonb) from public, anon, authenticated, service_role;

-- ═══════════════ 3. el plan: las raíces nuevas de Auth y el orden con SET NULL

create or replace function public.plan_de_borrado(p_cuenta uuid)
returns table (tabla text, predicado text, via text, orden int)
language plpgsql
stable
security definer
set search_path = public, pg_temp
as $$
declare
  -- LAS RAÍCES: por donde empieza el borrado, cada una con su predicado. Lo
  -- demás sale de caminar las claves ajenas desde aquí.
  raices jsonb;
  borrar jsonb := '{}'::jsonb;    -- tabla -> [predicados]
  vias jsonb := '{}'::jsonb;      -- tabla -> 'raíz' | 'después' | tabla padre
  alcance jsonb := '{}'::jsonb;   -- raíz -> [tablas que se van en cascada con ella]
  pendientes jsonb := '[]'::jsonb;
  actual jsonb;
  fk record;
  t text;
  r text;
  hijo_pred text;
  centros uuid[];
  explicitas text[];
  antes jsonb := '{}'::jsonb;     -- tabla -> [tablas que tienen que ir antes]
  hechas text[] := '{}';
  siguiente text;
  n int := 0;
  accesos uuid[];
  correos text[];
  marcas text[];
begin
  -- Los accesos de Auth de la cuenta y sus correos, FIJADOS AHORA: cuando se
  -- borre `public.users`, ya no se podrían encontrar.
  accesos := array(select u.auth_user_id from public.users u
                   where u.account_id = p_cuenta and u.auth_user_id is not null order by 1);
  correos := array(select distinct lower(au.email) from auth.users au
                   where au.id = any (accesos) and au.email is not null order by 1);
  marcas := public.marcas_de_auth(p_cuenta);

  raices := jsonb_build_object(
    -- La cuenta y su ficha (Carlos, 04-10-2026: la cuenta se borra). En
    -- cascada desde aquí: la suscripción y el PIN de gestión.
    'public.accounts', format('id = %L', p_cuenta),
    -- Sus centros y sus usuarios: apuntan a la cuenta SIN cascada, así que van
    -- a mano, y antes que ella (lo dice el orden de más abajo).
    'public.salons', format('account_id = %L', p_cuenta),
    'public.users', format('account_id = %L', p_cuenta),
    -- Su acceso (y en cascada sus identidades, sesiones, factores y tokens).
    'auth.users', format('id = any (%L::uuid[])', accesos),
    -- El registro de GoTrue: no cuelga de nada y guarda su correo. Toda
    -- entrada que nombre su id o su correo, en el campo que sea (0083: antes
    -- solo cuatro campos; lo demás lo habría dejado).
    'auth.audit_log_entries', format('lower(payload::text) like any (%L::text[])', marcas)
  );
  -- 0083 · LO DE AUTH QUE NO SE VA EN CASCADA CON EL USUARIO. Salió de recorrer
  -- las claves ajenas de Auth v2.197 (12-10-2026): `scim_users` cuelga con SET
  -- NULL (se quedaría sin dueño y con el correo dentro), y `flow_state`,
  -- `refresh_tokens` (los que no tienen sesión) y `saml_relay_states` nombran al
  -- usuario SIN clave ajena. Solo si la tabla existe en esta versión de Auth.
  -- `scim_users` va ANTES que `auth.users`: borrado el usuario, su `user_id` ya
  -- es NULL y no habría por dónde encontrarla (lo dice el orden de más abajo).
  if to_regclass('auth.scim_users') is not null then
    raices := raices || jsonb_build_object('auth.scim_users', format('user_id = any (%L::uuid[])', accesos));
  end if;
  if to_regclass('auth.flow_state') is not null then
    raices := raices || jsonb_build_object('auth.flow_state', format('user_id = any (%L::uuid[])', accesos));
  end if;
  if to_regclass('auth.refresh_tokens') is not null then
    raices := raices || jsonb_build_object('auth.refresh_tokens', format('user_id = any (%L::text[])', accesos::text[]));
  end if;
  if to_regclass('auth.saml_relay_states') is not null then
    raices := raices || jsonb_build_object('auth.saml_relay_states', format('lower(for_email) = any (%L::text[])', correos));
  end if;
  for r in select jsonb_object_keys(raices) loop
    borrar := jsonb_set(borrar, array[r], jsonb_build_array(raices ->> r));
    vias := jsonb_set(vias, array[r], '"raíz"');
    alcance := jsonb_set(alcance, array[r], jsonb_build_array(r));
    pendientes := pendientes || jsonb_build_array(jsonb_build_object(
      't', r, 'p', raices ->> r, 'n', 0, 'r', r));
  end loop;

  -- El cierre en cascada, caminando las claves ajenas.
  while jsonb_array_length(pendientes) > 0 loop
    actual := pendientes -> 0;
    pendientes := pendientes - 0;
    if (actual ->> 'n')::int >= 8 then
      continue;
    end if;
    for fk in
      select k.conrelid::regclass::text as hijo,
             (select string_agg(quote_ident(a.attname), ',' order by x.i)
                from unnest(k.conkey) with ordinality x(c, i)
                join pg_attribute a on a.attrelid = k.conrelid and a.attnum = x.c) as cols_hijo,
             (select string_agg(quote_ident(a.attname), ',' order by x.i)
                from unnest(k.confkey) with ordinality x(c, i)
                join pg_attribute a on a.attrelid = k.confrelid and a.attnum = x.c) as cols_padre
      from pg_constraint k
      where k.contype = 'f' and k.confdeltype = 'c'
        and k.confrelid = (actual ->> 't')::regclass and k.conrelid <> k.confrelid
    loop
      t := case when fk.hijo like '%.%' then fk.hijo else 'public.' || fk.hijo end;
      if not coalesce(alcance -> (actual ->> 'r'), '[]'::jsonb) ? t then
        alcance := jsonb_set(alcance, array[actual ->> 'r'], (alcance -> (actual ->> 'r')) || to_jsonb(t));
      end if;
      hijo_pred := format('(%s) in (select %s from %s where %s)',
                          fk.cols_hijo, fk.cols_padre, actual ->> 't', actual ->> 'p');
      if not coalesce(borrar -> t, '[]'::jsonb) ? hijo_pred then
        borrar := jsonb_set(borrar, array[t], coalesce(borrar -> t, '[]'::jsonb) || to_jsonb(hijo_pred));
        if not vias ? t then
          vias := jsonb_set(vias, array[t], to_jsonb(actual ->> 't'));
        end if;
        pendientes := pendientes || jsonb_build_array(jsonb_build_object(
          't', t, 'p', hijo_pred, 'n', (actual ->> 'n')::int + 1, 'r', actual ->> 'r'));
      end if;
    end loop;
  end loop;

  -- Lo que se va DESPUÉS de sus centros (0067): sin clave ajena, su DELETE solo
  -- pasa cuando el centro ya no existe. Se descubre por ese trigger. Los
  -- centros se fijan AHORA, con sus ids: cuando se borre, ya no estarán.
  centros := array(select s.id from public.salons s where s.account_id = p_cuenta order by s.id);
  for fk in
    select distinct c.oid::regclass::text as tabla
    from pg_trigger tr
    join pg_class c on c.oid = tr.tgrelid
    join pg_proc p on p.oid = tr.tgfoid
    where not tr.tgisinternal and (tr.tgtype & 8) <> 0
      and p.proname = 'se_borra_solo_sin_su_centro'
  loop
    t := case when fk.tabla like '%.%' then fk.tabla else 'public.' || fk.tabla end;
    borrar := jsonb_set(borrar, array[t], coalesce(borrar -> t, '[]'::jsonb)
      || to_jsonb(format('salon_id = any (%L::uuid[])', centros)));
    vias := jsonb_set(vias, array[t], '"después"');
    alcance := jsonb_set(alcance, array[t], jsonb_build_array(t));
    antes := jsonb_set(antes, array[t], '["public.salons"]');
  end loop;

  -- El orden entre lo que se borra a mano: si algo que se va con A apunta,
  -- SIN cascada ni SET NULL, a algo que se va con B, A va antes que B. Y con
  -- SET NULL o SET DEFAULT, si ese algo es la RAÍZ A misma (0083): su predicado
  -- puede ir por esa columna, que al borrar B cambiaría.
  explicitas := array(select jsonb_object_keys(alcance) order by 1);
  for fk in
    select k.conrelid::regclass::text as hijo, k.confrelid::regclass::text as padre, k.confdeltype::text as tipo
    from pg_constraint k
    where k.contype = 'f' and k.confdeltype <> 'c' and k.conrelid <> k.confrelid
  loop
    foreach r in array explicitas loop
      foreach t in array explicitas loop
        if r <> t
           and (fk.tipo in ('a', 'r') or r = (case when fk.hijo like '%.%' then fk.hijo else 'public.' || fk.hijo end))
           and alcance -> r ? (case when fk.hijo like '%.%' then fk.hijo else 'public.' || fk.hijo end)
           and alcance -> t ? (case when fk.padre like '%.%' then fk.padre else 'public.' || fk.padre end)
           and not alcance -> t ? (case when fk.hijo like '%.%' then fk.hijo else 'public.' || fk.hijo end)
           and not coalesce(antes -> t, '[]'::jsonb) ? r then
          antes := jsonb_set(antes, array[t], coalesce(antes -> t, '[]'::jsonb) || to_jsonb(r));
        end if;
      end loop;
    end loop;
  end loop;

  -- Ordenación topológica. Empates por nombre, para que sea siempre la misma.
  while coalesce(array_length(hechas, 1), 0) < array_length(explicitas, 1) loop
    select e into siguiente from unnest(explicitas) e
    where not e = any (hechas)
      and not exists (select 1 from jsonb_array_elements_text(coalesce(antes -> e, '[]'::jsonb)) a
                      where not a = any (hechas))
    order by e limit 1;
    if siguiente is null then
      raise exception 'plan_de_borrado: el orden tiene un ciclo entre %', array(
        select e from unnest(explicitas) e where not e = any (hechas)) using errcode = 'P0001';
    end if;
    hechas := hechas || siguiente;
  end loop;

  for t in select jsonb_object_keys(borrar) order by 1 loop
    tabla := t;
    select string_agg('(' || x || ')', ' or ') into predicado from jsonb_array_elements_text(borrar -> t) x;
    via := vias ->> t;
    orden := array_position(hechas, t);
    return next;
  end loop;
end;
$$;

-- ═══════════════ 4. el simulacro: el paso 6, lo que quedaría en Auth

create or replace function public.simulacro_de_borrado(p_cuenta uuid)
returns table (paso int, tabla text, filas bigint, accion text, detalle text)
language plpgsql
stable
security definer
set search_path = public, pg_temp
as $$
declare
  borrar jsonb := '{}'::jsonb;
  vias jsonb := '{}'::jsonb;
  pl record;
  fk record;
  hijo_pred text;
  pred text;
  n bigint;
  t text;
  colhijo text;
  queda record;
begin
  -- EL PLAN, el mismo que ejecuta el borrado.
  for pl in select * from public.plan_de_borrado(p_cuenta) loop
    borrar := jsonb_set(borrar, array[pl.tabla], to_jsonb(pl.predicado));
    vias := jsonb_set(vias, array[pl.tabla], to_jsonb(pl.via));
    execute format('select count(*) from %s where %s', pl.tabla, pl.predicado) into n;
    paso := 1; tabla := pl.tabla; filas := n; accion := 'borra';
    detalle := case when pl.via = 'raíz' then 'por donde empieza el borrado'
                    when pl.via = 'después' then 'después de sus centros: su trigger solo deja borrarlo cuando el centro ya no existe'
                    else 'en cascada desde ' || pl.via end;
    return next;
  end loop;

  for fk in
    select k.conrelid::regclass::text as hijo, k.confrelid::regclass::text as padre, k.confdeltype::text as tipo,
           k.conname,
           (select string_agg(quote_ident(a.attname), ',' order by x.i)
              from unnest(k.conkey) with ordinality x(c, i)
              join pg_attribute a on a.attrelid = k.conrelid and a.attnum = x.c) as cols_hijo,
           (select string_agg(quote_ident(a.attname), ',' order by x.i)
              from unnest(k.confkey) with ordinality x(c, i)
              join pg_attribute a on a.attrelid = k.confrelid and a.attnum = x.c) as cols_padre
    from pg_constraint k
    where k.contype = 'f' and k.confdeltype <> 'c'
  loop
    t := case when fk.padre like '%.%' then fk.padre else 'public.' || fk.padre end;
    if not borrar ? t then
      continue;
    end if;
    colhijo := case when fk.hijo like '%.%' then fk.hijo else 'public.' || fk.hijo end;
    hijo_pred := format('(%s) in (select %s from %s where %s)', fk.cols_hijo, fk.cols_padre, t, borrar ->> t);
    if borrar ? colhijo then
      hijo_pred := hijo_pred || format(' and not (%s)', borrar ->> colhijo);
    end if;
    execute format('select count(*) from %s where %s', colhijo, hijo_pred) into n;
    if n > 0 then
      paso := 2; tabla := colhijo; filas := n;
      accion := case fk.tipo when 'n' then 'queda_sin_referencia' when 'd' then 'queda_con_valor_por_defecto'
                             else 'impide_el_borrado' end;
      detalle := format('%s → %s (%s)', fk.conname, t,
                        case fk.tipo when 'n' then 'SET NULL' when 'r' then 'RESTRICT' when 'a' then 'NO ACTION'
                                     when 'd' then 'SET DEFAULT' else fk.tipo end);
      return next;
    end if;
  end loop;

  for fk in
    select c.oid::regclass::text as tabla, a.attname::text as col
    from pg_class c
    join pg_namespace s on s.oid = c.relnamespace
    join pg_attribute a on a.attrelid = c.oid and a.attnum > 0 and not a.attisdropped
    where s.nspname = 'public' and c.relkind = 'r'
      and a.attname in ('account_id', 'salon_id', 'target_salon_id')
      and (a.attname = 'account_id' or not exists (
        select 1 from pg_attribute b where b.attrelid = c.oid and b.attname = 'account_id' and not b.attisdropped))
    order by 1, 2
  loop
    t := case when fk.tabla like '%.%' then fk.tabla else 'public.' || fk.tabla end;
    if t = 'public.accounts' then
      continue;
    end if;
    hijo_pred := case fk.col
      when 'account_id' then format('account_id = %L', p_cuenta)
      else format('%I in (select id from public.salons where account_id = %L)', fk.col, p_cuenta) end;
    if borrar ? t then
      hijo_pred := hijo_pred || format(' and not (%s)', borrar ->> t);
    end if;
    execute format('select count(*) from %s where %s', t, hijo_pred) into n;
    if n > 0 then
      paso := 3; tabla := t; filas := n; accion := 'queda';
      detalle := format('tiene filas de la cuenta (por %s) y no cuelga de lo que se borra', fk.col);
      return next;
    end if;
  end loop;

  for fk in
    select distinct c.oid::regclass::text as tabla, p.proname::text as funcion
    from pg_trigger tr
    join pg_class c on c.oid = tr.tgrelid
    join pg_proc p on p.oid = tr.tgfoid
    where not tr.tgisinternal and (tr.tgtype & 8) <> 0
      and p.proname like '%no\_se\_tocan%'
  loop
    t := case when fk.tabla like '%.%' then fk.tabla else 'public.' || fk.tabla end;
    paso := 4; tabla := t; filas := null; accion := 'trigger_impide_borrar';
    detalle := format('el trigger %s rechaza cualquier DELETE', fk.funcion);
    return next;
  end loop;

  -- 5. LO DE FUERA, CON LO QUE HACE FALTA PARA BORRARLO ANTES (pasos 1 y 2
  -- del procedimiento de la cabecera de la 0068): los ids y las rutas, tal cual.
  select count(*), coalesce(string_agg(x.id, ' ' order by x.id), '') into n, detalle
  from (select a.stripe_customer_id as id from public.accounts a where a.id = p_cuenta
        union
        select s.stripe_customer_id from public.subscriptions s where s.account_id = p_cuenta) x
  where x.id is not null and x.id <> '';
  paso := 5; tabla := 'stripe (clientes)'; filas := n; accion := 'borra';
  detalle := case when n = 0 then 'ningún cliente en Stripe'
                  else 'PASO 1, antes de la función: borrar en Stripe ' || detalle end;
  return next;

  select count(*), coalesce(string_agg(o.name, ' ' order by o.name), '') into n, detalle
  from storage.objects o
  where o.bucket_id = 'salon-assets'
    and split_part(o.name, '/', 1) in (select id::text from public.salons where account_id = p_cuenta);
  paso := 5; tabla := 'storage.objects (salon-assets)'; filas := n; accion := 'borra';
  detalle := case when n = 0 then 'ningún logo en Storage'
                  else 'PASO 2, antes de la función: borrar de salon-assets ' || detalle end;
  return next;

  -- 6. (0083) LO QUE QUEDARÍA EN AUTH: filas de cualquier tabla de Auth que
  -- nombran a sus accesos o sus correos y que el plan no se lleva. Debe ser
  -- nada: si sale algo, el borrado se negaría.
  for queda in select * from public.lo_que_nombra_en_auth(public.marcas_de_auth(p_cuenta), borrar) loop
    paso := 6; tabla := queda.tabla; filas := queda.filas; accion := 'queda_en_auth';
    detalle := 'nombra a sus accesos o a sus correos y el plan no la borra: el borrado se negaría';
    return next;
  end loop;
end;
$$;

-- ═══════════════ 5. el borrado a 90 días: se niega si queda algo en Auth

create or replace function public.borrar_cuentas_fuera_de_plazo()
returns table (account_id uuid, filas jsonb)
language plpgsql
volatile
set search_path = public, pg_temp
as $$
#variable_conflict use_column
declare
  -- EL TOPE POR PASADA: ver la cabecera de la 0068.
  c_tope constant int := 3;
  v_encendido boolean;
  v_cuantas int;
  cuenta record;
  pl record;
  plan jsonb;
  paso jsonb;
  n bigint;
  v_centros uuid[];
  v_marcas text[];
  v_queda text;
begin
  -- 1. EL INTERRUPTOR, lo primero. Apagado = error, no cero.
  select i.encendido into v_encendido from public.borrado_interruptor i;
  if v_encendido is distinct from true then
    raise exception 'El borrado a 90 días está APAGADO: no se ha borrado nada.'
      using errcode = '55000',
            hint = 'Se enciende a mano, como postgres, en public.borrado_interruptor (quién y por qué).';
  end if;

  -- Dos pasadas a la vez no: la segunda espera y, al entrar, ya no ve lo borrado.
  perform pg_advisory_xact_lock(hashtext('borrar_cuentas_fuera_de_plazo'));

  -- 2. EL TOPE, antes de tocar nada.
  select count(*) into v_cuantas from public.cuentas_fuera_de_plazo();
  if v_cuantas > c_tope then
    raise exception 'TOPE: % cuentas fuera de plazo y el tope por pasada es %. No se ha borrado ninguna.',
      v_cuantas, c_tope
      using errcode = 'P0001',
            hint = 'Muchas más de las esperadas es señal de que la selección está mal. Mira el simulacro.';
  end if;

  -- 3. Cada cuenta, con SU plan: el mismo que enseña el simulacro.
  for cuenta in select * from public.cuentas_fuera_de_plazo() loop
    v_centros := array(select s.id from public.salons s where s.account_id = cuenta.account_id);

    -- STORAGE VA ANTES, NUNCA DESPUÉS: con los centros borrados, nadie sabría
    -- qué rutas eran suyas. Si queda un logo, el paso 2 no se hizo: no se borra
    -- nada de ninguna cuenta.
    select count(*) into n from storage.objects o
    where o.bucket_id = 'salon-assets' and split_part(o.name, '/', 1) = any (v_centros::text[]);
    if n > 0 then
      raise exception 'A la cuenta % le quedan % logos en Storage: primero el paso 2 (ver la 0068). No se ha borrado nada.',
        cuenta.account_id, n using errcode = '55000';
    end if;
    -- Sus marcas en Auth, FIJADAS ANTES: después ya no hay usuario (0083).
    v_marcas := public.marcas_de_auth(cuenta.account_id);
    plan := '[]'::jsonb;
    filas := '{}'::jsonb;
    for pl in select * from public.plan_de_borrado(cuenta.account_id) loop
      execute format('select count(*) from %s where %s', pl.tabla, pl.predicado) into n;
      filas := filas || jsonb_build_object(pl.tabla, n);
      if pl.orden is not null then
        plan := plan || jsonb_build_object('tabla', pl.tabla, 'predicado', pl.predicado, 'orden', pl.orden);
      end if;
    end loop;

    -- A mano, en el orden del plan, solo lo que no se va en cascada.
    for paso in select x from jsonb_array_elements(plan) x order by (x ->> 'orden')::int loop
      execute format('delete from %s where %s', paso ->> 'tabla', paso ->> 'predicado');
    end loop;

    -- Comprobación: lo borrado a mano ya no está (sus predicados están fijados)…
    for paso in select x from jsonb_array_elements(plan) x loop
      execute format('select count(*) from %s where %s', paso ->> 'tabla', paso ->> 'predicado') into n;
      if n > 0 then
        raise exception 'El borrado de % dejó % filas en %: no se borra nada.', cuenta.account_id, n, paso ->> 'tabla';
      end if;
    end loop;
    -- …y de lo que se iba en cascada, no queda nada de esta cuenta.
    for pl in
      select p.tabla, a.attname::text as col
      from public.plan_de_borrado(gen_random_uuid()) p
      join pg_attribute a on a.attrelid = p.tabla::regclass and a.attnum > 0 and not a.attisdropped
      where a.attname in ('account_id', 'salon_id')
    loop
      if pl.col = 'account_id' then
        execute format('select count(*) from %s where account_id = %L', pl.tabla, cuenta.account_id) into n;
      else
        execute format('select count(*) from %s where salon_id = any (%L::uuid[])', pl.tabla, v_centros) into n;
      end if;
      if n > 0 then
        raise exception 'El borrado de % dejó % filas en %: no se borra nada.', cuenta.account_id, n, pl.tabla;
      end if;
    end loop;

    -- …y en Auth no queda NADA que la nombre, en ninguna tabla (0083).
    select string_agg(q.tabla || ' (' || q.filas || ')', ', ') into v_queda from public.lo_que_nombra_en_auth(v_marcas) q;
    if v_queda is not null then
      raise exception 'El borrado de % dejó en Auth filas que la nombran: %. No se borra nada.', cuenta.account_id, v_queda;
    end if;

    -- 4. La constancia, en la misma transacción.
    insert into public.cuentas_borradas (account_id, motivo, origen, se_borraba_el, filas)
    values (cuenta.account_id, 'fuera_de_plazo_90_dias', cuenta.origen, cuenta.se_borran_el, filas);

    account_id := cuenta.account_id;
    return next;
  end loop;
end;
$$;

-- ═══════════════ 6. el borrado de una demo: lo mismo

create or replace function public.borrar_una_demo(p_cuenta uuid)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  pl record;
  paso jsonb;
  plan jsonb := '[]'::jsonb;
  filas jsonb := '{}'::jsonb;
  n bigint;
  v_centros uuid[];
  v_marcas text[];
  v_queda text;
begin
  -- LA COMPROBACIÓN QUE NO SE SALTA NADIE: con la cuenta bloqueada.
  perform 1 from public.accounts a where a.id = p_cuenta and a.es_demo for update;
  if not found then
    raise exception 'borrar_una_demo: la cuenta % NO es una demo. No se borra nada.', p_cuenta using errcode = '42501';
  end if;
  v_centros := array(select s.id from public.salons s where s.account_id = p_cuenta);
  select count(*) into n from storage.objects o
  where o.bucket_id = 'salon-assets' and split_part(o.name, '/', 1) = any (v_centros::text[]);
  if n > 0 then
    raise exception 'La demo % tiene % ficheros en Storage: una demo no sube ficheros. No se borra.', p_cuenta, n
      using errcode = '55000';
  end if;

  -- Sus marcas en Auth, FIJADAS ANTES: después ya no hay usuario (0083).
  v_marcas := public.marcas_de_auth(p_cuenta);
  -- EL MISMO PLAN que el borrado a 90 días (0068), sin cambiarlo.
  for pl in select * from public.plan_de_borrado(p_cuenta) loop
    execute format('select count(*) from %s where %s', pl.tabla, pl.predicado) into n;
    filas := filas || jsonb_build_object(pl.tabla, n);
    if pl.orden is not null then
      plan := plan || jsonb_build_object('tabla', pl.tabla, 'predicado', pl.predicado, 'orden', pl.orden);
    end if;
  end loop;
  for paso in select x from jsonb_array_elements(plan) x order by (x ->> 'orden')::int loop
    execute format('delete from %s where %s', paso ->> 'tabla', paso ->> 'predicado');
  end loop;
  for paso in select x from jsonb_array_elements(plan) x loop
    execute format('select count(*) from %s where %s', paso ->> 'tabla', paso ->> 'predicado') into n;
    if n > 0 then
      raise exception 'El borrado de la demo % dejó % filas en %: no se borra nada.', p_cuenta, n, paso ->> 'tabla';
    end if;
  end loop;
  select string_agg(q.tabla || ' (' || q.filas || ')', ', ') into v_queda from public.lo_que_nombra_en_auth(v_marcas) q;
  if v_queda is not null then
    raise exception 'El borrado de la demo % dejó en Auth filas que la nombran: %. No se borra nada.', p_cuenta, v_queda;
  end if;
  return filas;
end;
$$;

commit;
