-- 0084 · El simulacro no miente sobre lo que se queda.
--
-- Carlos, 12-10-2026.
--
-- ═══ LO QUE PASABA ═══
--
-- «Se quedaría» decía «Nada» para un salón que había generado una comisión de
-- 2,90 € y su anulación: filas que se conservan a propósito y que llevan su
-- nombre dentro. El paso 3 del simulacro buscaba las filas de la cuenta solo en
-- columnas llamadas `account_id`, `salon_id` o `target_salon_id`, y las cuatro
-- tablas de los referidos la nombran con otro nombre de columna. Una lista
-- escrita a mano: el mismo fallo que la 0083 quitó del borrado.
--
-- ═══ EL CRITERIO: POR LO QUE LLEVA DENTRO, NO POR CÓMO SE LLAMA ═══
--
-- `marcas_de_la_cuenta(cuenta)` reúne los identificadores EXACTOS de la cuenta:
-- su id, los de sus centros, los de sus usuarios y su cliente de Stripe. Son
-- UUID o ids de Stripe: no los comparte nadie más. NO entran el nombre, ni los
-- correos, ni los teléfonos: un nombre lo puede llevar otro salón, y un correo
-- otra cuenta de la misma persona. Mejor no encontrar que atribuir de más.
--
-- El paso 3 recorre TODAS las tablas de `public` (las que haya, sin lista) y
-- cuenta las filas que llevan una de esas marcas, entre comillas, en cualquier
-- columna, y que el plan no se lleva. Además dice POR QUÉ COLUMNAS: así se ve
-- cómo identifica cada tabla a la cuenta (en las de referidos, `cuenta_id`).
--
-- Si una tabla no se puede mirar (falla la lectura), sale como
-- «no_comprobada», con el motivo, y el panel la pinta en rojo. Nunca «Nada».
--
-- De paso: lo que el plan cubre se descuenta con `not coalesce(...)`. Con un
-- `not (...)` a secas, una fila con la columna del predicado a NULL desaparecía
-- de la cuenta (NULL no es «no»). Y la fila entera se toma con `row(x.*)`: con
-- `row_to_json(x)`, una tabla con una columna llamada `x` no se podía mirar.
-- Las dos cosas, también en `lo_que_nombra_en_auth`.
--
-- ═══ LO QUE NO CAMBIA ═══
--
-- El borrado: `plan_de_borrado`, `borrar_cuentas_fuera_de_plazo` y
-- `borrar_una_demo` no se tocan. Los interruptores, tampoco. El simulacro
-- devuelve las mismas columnas; los pasos 1, 2, 4, 5 y 6 son los de la 0083.
--
-- ═══ ORDEN DE DESPLIEGUE: 0083 → 0084 → BACKEND + FRONTEND (a la vez) ═══
--
-- Va DESPUÉS de la 0083 (usa su `marcas_de_auth` y su paso 6). El backend
-- desplegado sigue funcionando con ella; el nuevo pinta «no_comprobada» en
-- rojo. Nada en Stripe.

begin;

-- ═══════════════ 1. los identificadores exactos de una cuenta

create function public.marcas_de_la_cuenta(p_cuenta uuid)
returns text[]
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  -- Entre comillas, como sale un valor en el JSON de una fila, y escapando los
  -- comodines de LIKE (como `marcas_de_auth`, 0083).
  select coalesce(array_agg(distinct '%"' || replace(replace(replace(lower(m), '\', '\\'), '%', '\%'), '_', '\_') || '"%'),
                  '{}'::text[])
  from (
    select a.id::text as m from public.accounts a where a.id = p_cuenta
    union select s.id::text from public.salons s where s.account_id = p_cuenta
    union select u.id::text from public.users u where u.account_id = p_cuenta
    union select a.stripe_customer_id from public.accounts a where a.id = p_cuenta
    union select s.stripe_customer_id from public.subscriptions s where s.account_id = p_cuenta
  ) x
  where coalesce(m, '') <> '';
$$;

comment on function public.marcas_de_la_cuenta(uuid) is
  'Los identificadores EXACTOS de una cuenta (su id, sus centros, sus usuarios, su cliente de Stripe), como '
  'patrones LIKE entre comillas (0084). Ni nombres ni correos: esos los puede compartir otra cuenta.';

revoke all on function public.marcas_de_la_cuenta(uuid) from public, anon, authenticated, service_role;

-- ═══════════════ 2. lo de Auth: lo cubierto se descuenta bien con NULL

create or replace function public.lo_que_nombra_en_auth(p_marcas text[], p_cubiertas jsonb default '{}'::jsonb)
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
    execute format('select count(*) from %s x where lower(row_to_json(row(x.*))::text) like any (%L::text[])%s',
                   t.nombre, p_marcas,
                   case when p_cubiertas ? t.nombre then format(' and not coalesce((%s), false)', p_cubiertas ->> t.nombre)
                        else '' end)
      into n;
    if n > 0 then
      tabla := t.nombre;
      filas := n;
      return next;
    end if;
  end loop;
end;
$$;

-- ═══════════════ 3. el simulacro: el paso 3, descubriendo

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
  marcas text[];
  cols text;
  hay boolean;
  columna record;
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

  -- 3. (0084) LO QUE SE QUEDA, DESCUBIERTO. No se busca por el NOMBRE de una
  -- columna (antes: account_id, salon_id y target_salon_id, y por eso
  -- `cuenta_id` no salía): se busca en TODAS las tablas de `public` cualquier
  -- fila que lleve dentro, en la columna que sea, uno de los identificadores
  -- de la cuenta —su id, el de sus centros, el de sus usuarios, su cliente de
  -- Stripe— y que el plan no se lleva. Un valor exacto, entre comillas: una
  -- columna que se llame parecido pero guarde otra cosa no casa. Y se dice
  -- POR QUÉ COLUMNAS la nombra, para que se vea cómo se la encontró.
  -- Si una tabla no se puede mirar, se DICE («no_comprobada»): callarse sería
  -- decir «nada».
  marcas := public.marcas_de_la_cuenta(p_cuenta);
  for fk in
    select c.oid, c.oid::regclass::text as tabla
    from pg_class c join pg_namespace s on s.oid = c.relnamespace
    where s.nspname = 'public' and c.relkind in ('r', 'p')
    order by 2
  loop
    t := case when fk.tabla like '%.%' then fk.tabla else 'public.' || fk.tabla end;
    -- `row(x.*)`: la fila entera aunque la tabla tenga una columna llamada `x`.
    pred := format('lower(row_to_json(row(x.*))::text) like any (%L::text[])', marcas);
    if borrar ? t then
      pred := pred || format(' and not coalesce((%s), false)', borrar ->> t);
    end if;
    begin
      execute format('select count(*) from %s x where %s', t, pred) into n;
      if n > 0 then
        cols := null;
        for columna in
          select at.attname from pg_attribute at
          where at.attrelid = fk.oid and at.attnum > 0 and not at.attisdropped order by at.attnum
        loop
          execute format('select exists (select 1 from %s x where lower(to_jsonb(x.%I)::text) like any (%L::text[])%s)',
                         t, columna.attname, marcas,
                         case when borrar ? t then format(' and not coalesce((%s), false)', borrar ->> t) else '' end)
            into hay;
          if hay then
            cols := coalesce(cols || ', ', '') || columna.attname;
          end if;
        end loop;
        paso := 3; tabla := t; filas := n; accion := 'queda';
        detalle := format('tiene filas que nombran a la cuenta (por %s) y no se van con el borrado', coalesce(cols, 'la fila entera'));
        return next;
      end if;
    exception when others then
      paso := 3; tabla := t; filas := null; accion := 'no_comprobada';
      detalle := format('no se ha podido mirar si guarda algo de la cuenta: %s', sqlerrm);
      return next;
    end;
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

commit;
