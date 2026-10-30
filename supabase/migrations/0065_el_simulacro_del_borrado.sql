-- 0065 · El simulacro del borrado, y el reloj de quien nunca pagó. NO BORRA NADA.
--
-- Carlos (04-10-2026): el borrado a los 90 días se construye al revés. Primero
-- que enseñe qué haría; solo cuando se hayan visto unos cuantos simulacros, que
-- lo haga. Esta migración trae lo primero.
--
-- ═══ 1. EL SIMULACRO: `simulacro_de_borrado(cuenta)` ═══
--
-- Dice, para una cuenta, qué se llevaría por delante un borrado que empezara
-- por sus centros (`salons`) y sus usuarios (`users`), que es por donde
-- empezaría el de verdad: la cuenta (`accounts`) se queda, porque es nuestro
-- registro comercial (decisión de Carlos, 04-10-2026).
--
-- LO SACA DEL ESQUEMA, NO DE UNA LISTA. Recorre las claves ajenas que hay EN
-- ESTE MOMENTO en la base: lo que caería en cascada, lo que se quedaría con un
-- NULL, lo que lo impediría. Una lista escrita a mano se queda vieja justo
-- cuando alguien añade una tabla; esto ve la tabla nueva el mismo día. Por eso
-- también enseña lo que NO alcanza: las tablas con `account_id`, `salon_id` o
-- `target_salon_id` que tienen filas de esta cuenta y que el borrado no
-- tocaría, porque no cuelgan de lo que se borra. Y los triggers que harían
-- fallar un DELETE (los registros que «no se tocan»), el logo en Storage y el
-- usuario de acceso en Auth.
--
-- NO PUEDE BORRAR, Y NO POR PROMESA: es `stable`, y Postgres no deja ejecutar
-- un INSERT, UPDATE o DELETE dentro de una función `stable`. Si alguien colara
-- uno, la función fallaría en vez de borrar. Solo lo ejecuta `service_role`.
--
-- ═══ 2. EL RELOJ DE QUIEN NUNCA PAGÓ ═══
--
-- Una cuenta que se dio de alta y no llegó a activar la prueba (no tiene fila
-- en `subscriptions`) no tenía reloj: sus datos se guardaban para siempre.
-- Desde ahora su plazo empieza el día en que habría terminado la prueba
-- (`accounts.created_at` + los días de prueba) y se avisa igual. Los sellos de
-- esos avisos van en `accounts`, porque no hay suscripción donde ponerlos.
--
-- ═══ LA REGLA DE LA COLUMNA OBLIGATORIA ═══
--
-- `accounts` es una tabla en la que el backend inserta (el alta). Las tres
-- columnas son ANULABLES y sin valor por defecto.
--
-- ═══ ORDEN DE DESPLIEGUE: 0065 → BACKEND → FRONTEND ═══
--
-- El backend nuevo llama a la función y lee las columnas. El anterior no.

begin;

alter table public.accounts
  add column aviso_plazo_terminada_at timestamptz,
  add column aviso_plazo_30_at timestamptz,
  add column aviso_plazo_7_at timestamptz;

comment on column public.accounts.aviso_plazo_terminada_at is
  'Sello del aviso «tu prueba no llegó a empezar; tus datos se borran el …». Solo '
  'para cuentas sin suscripción (las que la tienen sellan en subscriptions, 0064).';
comment on column public.accounts.aviso_plazo_30_at is 'Sello del aviso «quedan 30 días» (cuentas sin suscripción).';
comment on column public.accounts.aviso_plazo_7_at is 'Sello del aviso «quedan 7 días» (cuentas sin suscripción).';

create or replace function public.simulacro_de_borrado(p_cuenta uuid)
returns table (paso int, tabla text, filas bigint, accion text, detalle text)
language plpgsql
stable
security definer
set search_path = public, pg_temp
as $$
declare
  -- Lo que se borraría, por tabla: la condición que selecciona sus filas
  -- (varias se juntan con OR). Empieza por los centros y los usuarios.
  borrar jsonb := jsonb_build_object(
    'public.salons', jsonb_build_array(format('account_id = %L', p_cuenta)),
    'public.users',  jsonb_build_array(format('account_id = %L', p_cuenta))
  );
  pendientes jsonb := jsonb_build_array(
    jsonb_build_object('t', 'public.salons', 'p', format('account_id = %L', p_cuenta), 'via', 'raíz', 'n', 0),
    jsonb_build_object('t', 'public.users',  'p', format('account_id = %L', p_cuenta), 'via', 'raíz', 'n', 0)
  );
  vias jsonb := jsonb_build_object('public.salons', 'raíz', 'public.users', 'raíz');
  actual jsonb;
  fk record;
  hijo_pred text;
  pred text;
  n bigint;
  t text;
  colhijo text;
  colpadre text;
begin
  -- ── 1. El cierre de las cascadas ──────────────────────────────────────────
  while jsonb_array_length(pendientes) > 0 loop
    actual := pendientes -> 0;
    pendientes := pendientes - 0;
    if (actual ->> 'n')::int >= 8 then
      continue;  -- tope de profundidad: no hay cadenas tan largas en el esquema
    end if;
    for fk in
      select k.conrelid::regclass::text as hijo, k.confdeltype::text as tipo,
             (select string_agg(quote_ident(a.attname), ',' order by x.i)
                from unnest(k.conkey) with ordinality x(c, i)
                join pg_attribute a on a.attrelid = k.conrelid and a.attnum = x.c) as cols_hijo,
             (select string_agg(quote_ident(a.attname), ',' order by x.i)
                from unnest(k.confkey) with ordinality x(c, i)
                join pg_attribute a on a.attrelid = k.confrelid and a.attnum = x.c) as cols_padre
      from pg_constraint k
      where k.contype = 'f' and k.confrelid = (actual ->> 't')::regclass
        and k.conrelid <> k.confrelid
    loop
      t := case when fk.hijo like '%.%' then fk.hijo else 'public.' || fk.hijo end;
      if fk.tipo <> 'c' then
        continue;  -- lo que no es cascada se mira en el paso 2
      end if;
      hijo_pred := format('(%s) in (select %s from %s where %s)',
                          fk.cols_hijo, fk.cols_padre, actual ->> 't', actual ->> 'p');
      if not coalesce(borrar -> t, '[]'::jsonb) ? hijo_pred then
        borrar := jsonb_set(borrar, array[t], coalesce(borrar -> t, '[]'::jsonb) || to_jsonb(hijo_pred));
        if not vias ? t then
          vias := jsonb_set(vias, array[t], to_jsonb(actual ->> 't'));
        end if;
        pendientes := pendientes || jsonb_build_array(jsonb_build_object(
          't', t, 'p', hijo_pred, 'n', (actual ->> 'n')::int + 1));
      end if;
    end loop;
  end loop;

  -- Lo que se borraría, contado. Una fila alcanzada por dos caminos cuenta una vez.
  for t in select jsonb_object_keys(borrar) loop
    select string_agg('(' || x || ')', ' or ') into pred from jsonb_array_elements_text(borrar -> t) x;
    execute format('select count(*) from %s where %s', t, pred) into n;
    paso := 1; tabla := t; filas := n; accion := 'borra';
    detalle := case when vias ->> t = 'raíz' then 'por donde empieza el borrado'
                    else 'en cascada desde ' || (vias ->> t) end;
    return next;
  end loop;

  -- ── 2. Lo que apunta a lo que se borra y NO cae en cascada ──────────────
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
    select string_agg('(' || x || ')', ' or ') into pred from jsonb_array_elements_text(borrar -> t) x;
    hijo_pred := format('(%s) in (select %s from %s where %s)', fk.cols_hijo, fk.cols_padre, t, pred);
    -- Las filas del hijo que también se borran no cuentan: no se quedan colgando.
    if borrar ? colhijo then
      select string_agg('(' || x || ')', ' or ') into pred from jsonb_array_elements_text(borrar -> colhijo) x;
      hijo_pred := hijo_pred || format(' and not (%s)', pred);
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

  -- ── 3. Lo que es de la cuenta y el borrado NO alcanza ─────────────────────
  for fk in
    select c.oid::regclass::text as tabla, a.attname::text as col
    from pg_class c
    join pg_namespace s on s.oid = c.relnamespace
    join pg_attribute a on a.attrelid = c.oid and a.attnum > 0 and not a.attisdropped
    where s.nspname = 'public' and c.relkind = 'r'
      and a.attname in ('account_id', 'salon_id', 'target_salon_id')
      -- Con `account_id` basta esa: contar también por centro sería la misma fila dos veces.
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
      select string_agg('(' || x || ')', ' or ') into pred from jsonb_array_elements_text(borrar -> t) x;
      hijo_pred := hijo_pred || format(' and not (%s)', pred);
    end if;
    execute format('select count(*) from %s where %s', t, hijo_pred) into n;
    if n > 0 then
      paso := 3; tabla := t; filas := n; accion := 'queda';
      detalle := format('tiene filas de la cuenta (por %s) y no cuelga de lo que se borra', fk.col);
      return next;
    end if;
  end loop;
  -- La propia cuenta, que se queda a propósito.
  paso := 3; tabla := 'public.accounts'; filas := 1; accion := 'queda';
  detalle := 'la cuenta: el borrado empieza por sus centros y sus usuarios';
  return next;

  -- ── 4. Lo que haría fallar un DELETE: los registros que «no se tocan» ────
  for fk in
    select distinct c.oid::regclass::text as tabla, p.proname::text as funcion
    from pg_trigger tr
    join pg_class c on c.oid = tr.tgrelid
    join pg_proc p on p.oid = tr.tgfoid
    where not tr.tgisinternal and (tr.tgtype & 8) <> 0  -- se dispara en DELETE
      and p.proname like '%no\_se\_tocan%'
  loop
    t := case when fk.tabla like '%.%' then fk.tabla else 'public.' || fk.tabla end;
    paso := 4; tabla := t; filas := null; accion := 'trigger_impide_borrar';
    detalle := format('el trigger %s rechaza cualquier DELETE', fk.funcion);
    return next;
  end loop;

  -- ── 5. Fuera de las tablas: el logo y el usuario de acceso ──────────────
  select count(*) into n from storage.objects o
  where o.bucket_id = 'salon-assets'
    and split_part(o.name, '/', 1) in (select id::text from public.salons where account_id = p_cuenta);
  paso := 5; tabla := 'storage.objects (salon-assets)'; filas := n; accion := 'borra';
  detalle := 'el logo de cada centro: no lo borra ninguna cascada, hay que borrarlo aparte';
  return next;

  select count(*) into n from auth.users au
  where au.id in (select auth_user_id from public.users where account_id = p_cuenta and auth_user_id is not null);
  paso := 5; tabla := 'auth.users'; filas := n; accion := 'borra';
  detalle := 'el usuario de acceso: no lo borra ninguna cascada, hay que borrarlo aparte';
  return next;
end;
$$;

comment on function public.simulacro_de_borrado(uuid) is
  'Qué borraría el borrado a 90 días de esta cuenta, sacado de las claves ajenas '
  'del esquema. STABLE: no puede modificar nada. Solo service_role.';

revoke all on function public.simulacro_de_borrado(uuid) from public, anon, authenticated;
grant execute on function public.simulacro_de_borrado(uuid) to service_role;

commit;
