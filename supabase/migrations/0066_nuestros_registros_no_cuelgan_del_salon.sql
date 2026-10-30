-- 0066 · Nuestros registros no cuelgan del salón. NO BORRA NADA.
--
-- El simulacro del borrado (0065) enseñó que, al borrar los centros de una
-- cuenta, la base se llevaría EN CASCADA tres cosas que Carlos decidió que se
-- quedan (04-10-2026), porque son NUESTRAS y no del salón:
--
--   · `subscriptions`: qué pagó y cuándo se fue. Nuestro registro comercial.
--   · `impersonation_sessions` y `support_access_codes`: quién de los nuestros
--     entró en un salón, cuándo y con qué consentimiento. Nuestra auditoría.
--
-- Es el mismo fallo que se arregló con los fichajes (0062) y con los registros
-- de dinero y de descargas (0057), y se arregla igual: SIN CLAVE AJENA HACIA
-- LO QUE SE BORRA, y CON EL NOMBRE GUARDADO, para que la fila siga diciendo de
-- quién era cuando el salón ya no exista.
--
-- ═══ POR QUÉ LA SUSCRIPCIÓN COLGABA DEL SALÓN ═══
--
-- Un descuido antiguo, no una decisión. Nació en la 0002 (02-07-2026), cuando un
-- salón era un cliente. La 0040 (la cuenta paga) le añadió `account_id` y dejó
-- `salon_id` «por la ventana de despliegue», con un comentario que decía que lo
-- quitaría «la migración que contrae». Esa migración no se escribió nunca, y
-- con ella se quedaron la cascada y la política de lectura por centro. Aquí se
-- contrae: la suscripción cuelga SOLO de la cuenta. `salon_id` se queda como
-- dato (el backend lo sigue escribiendo y su índice único sigue valiendo),
-- pero ya no es una clave ajena, y la lectura pasa a ser por cuenta: si el
-- centro al que apuntaba desapareciera, la administradora seguiría viendo su
-- suscripción.
--
-- ═══ EL CREADOR DE UN CÓDIGO DE SOPORTE ═══
--
-- Quedó apuntado que `support_access_codes.created_by_user_id` impedía borrar a
-- quien creó un código. Medido hoy (04-10-2026), como `postgres` y como
-- `service_role`: ya no lo impide (es SET NULL). Pero lo que hacía era peor que
-- impedirlo: al borrar a esa persona, el código se quedaba sin saber QUIÉN lo
-- autorizó, que es justo lo que tiene que decir. Mismo arreglo: sin clave
-- ajena, con el nombre y el correo de quien lo creó guardados.
--
-- ═══ LOS NOMBRES ═══
--
-- Los rellena un trigger al insertar y en cada actualización MIENTRAS el salón,
-- la cuenta o la persona existan: la fila guarda el último nombre conocido, y
-- cuando lo que nombra desaparece, se queda con él. Se rellenan también las
-- filas que ya existen.
--
-- ═══ LA REGLA DE LA COLUMNA OBLIGATORIA ═══
--
-- Las columnas nuevas son ANULABLES y sin valor por defecto, y además las
-- rellena un trigger: el backend anterior no tiene que saber que existen.
--
-- ═══ ORDEN DE DESPLIEGUE: 0066 → BACKEND ═══
--
-- Funciona con el backend que hay: no le quita nada que lea ni le pide nada que
-- no mande. El backend nuevo solo añade el guarda y el simulacro al día.

begin;

-- ═══════════════ 1. la suscripción: de la cuenta, no del centro

alter table public.subscriptions drop constraint subscriptions_salon_id_fkey;

alter table public.subscriptions
  add column cuenta_nombre text,
  add column centro_nombre text;

comment on column public.subscriptions.salon_id is
  'El centro desde el que se dio de alta. Ya NO es clave ajena (0066): la '
  'suscripción es de la cuenta y no se borra con el centro. Ver centro_nombre.';
comment on column public.subscriptions.cuenta_nombre is
  'El nombre de la cuenta, guardado: sigue diciendo de quién era cuando la cuenta ya no lo diga.';
comment on column public.subscriptions.centro_nombre is
  'El nombre del centro de salon_id, guardado: el centro puede no existir ya.';

-- Leer la suscripción: la administradora de LA CUENTA, no la de un centro.
drop policy subscriptions_select_admin on public.subscriptions;
create policy subscriptions_select_admin on public.subscriptions
  for select using (account_id = public.cuenta_actual() and public.is_admin_rol());

-- ═══════════════ 2. la auditoría de soporte: sobrevive al salón

alter table public.impersonation_sessions drop constraint impersonation_sessions_salon_id_fkey;
alter table public.impersonation_sessions
  add column centro_nombre text,
  add column cuenta_nombre text;
comment on column public.impersonation_sessions.centro_nombre is
  'El salón en el que entró soporte, guardado: es nuestra auditoría y sobrevive al salón (0066).';
comment on column public.impersonation_sessions.cuenta_nombre is
  'La cuenta de ese salón, guardada.';

alter table public.support_access_codes drop constraint support_access_codes_salon_id_fkey;
alter table public.support_access_codes drop constraint support_access_codes_created_by_user_id_fkey;
alter table public.support_access_codes
  add column centro_nombre text,
  add column cuenta_nombre text,
  add column creado_por_nombre text,
  add column creado_por_email text;
comment on column public.support_access_codes.centro_nombre is
  'El salón que dio el consentimiento, guardado: sobrevive al salón (0066).';
comment on column public.support_access_codes.creado_por_nombre is
  'Quién autorizó el acceso, guardado: sobrevive a que se borre su usuario (0066).';
comment on column public.support_access_codes.creado_por_email is
  'El correo de quien autorizó el acceso, guardado.';

-- ═══════════════ 3. los nombres, rellenos por la base

create or replace function public.registro_guarda_los_nombres()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_centro text;
  v_cuenta text;
begin
  -- El último nombre conocido: si lo nombrado ya no existe, se deja el que había.
  select s.nombre_comercial, a.nombre into v_centro, v_cuenta
  from public.salons s left join public.accounts a on a.id = s.account_id
  where s.id = new.salon_id;
  if found then
    new.centro_nombre := v_centro;
    new.cuenta_nombre := v_cuenta;
  end if;
  if tg_table_name = 'subscriptions' then
    select a.nombre into v_cuenta from public.accounts a where a.id = new.account_id;
    if found then
      new.cuenta_nombre := v_cuenta;
    end if;
  end if;
  -- Anidado y no con `and`: PL/pgSQL resuelve `new.created_by_user_id` aunque
  -- la primera mitad sea falsa, y las otras dos tablas no tienen esa columna.
  if tg_table_name = 'support_access_codes' then
    if new.created_by_user_id is not null then
      select u.nombre, u.email into v_centro, v_cuenta from public.users u where u.id = new.created_by_user_id;
      if found then
        new.creado_por_nombre := v_centro;
        new.creado_por_email := v_cuenta;
      end if;
    end if;
  end if;
  return new;
end;
$$;

revoke all on function public.registro_guarda_los_nombres() from public, anon, authenticated;

create trigger subscriptions_guarda_los_nombres
  before insert or update on public.subscriptions
  for each row execute function public.registro_guarda_los_nombres();
create trigger impersonation_sessions_guarda_los_nombres
  before insert or update on public.impersonation_sessions
  for each row execute function public.registro_guarda_los_nombres();
create trigger support_access_codes_guarda_los_nombres
  before insert or update on public.support_access_codes
  for each row execute function public.registro_guarda_los_nombres();

-- Las que ya existen: un UPDATE sin cambios dispara el trigger y las rellena.
update public.subscriptions set salon_id = salon_id;
update public.impersonation_sessions set salon_id = salon_id;
update public.support_access_codes set salon_id = salon_id;

-- ═══════════════ 4. el simulacro empieza también por el PIN de gestión

-- El simulacro de una cuenta con el PIN de gestión puesto lo enseñó en rojo:
-- `pin_gestion` es configuración del salón, pero cuelga de la CUENTA, y la
-- cuenta se queda. Ninguna cascada desde los centros lo alcanzaría. El borrado
-- tiene que empezar también por ahí.
create or replace function public.simulacro_de_borrado(p_cuenta uuid)
returns table (paso int, tabla text, filas bigint, accion text, detalle text)
language plpgsql
stable
security definer
set search_path = public, pg_temp
as $$
declare
  borrar jsonb := jsonb_build_object(
    'public.salons', jsonb_build_array(format('account_id = %L', p_cuenta)),
    'public.users',  jsonb_build_array(format('account_id = %L', p_cuenta)),
    'public.pin_gestion', jsonb_build_array(format('account_id = %L', p_cuenta))
  );
  pendientes jsonb := jsonb_build_array(
    jsonb_build_object('t', 'public.salons', 'p', format('account_id = %L', p_cuenta), 'n', 0),
    jsonb_build_object('t', 'public.users',  'p', format('account_id = %L', p_cuenta), 'n', 0),
    jsonb_build_object('t', 'public.pin_gestion', 'p', format('account_id = %L', p_cuenta), 'n', 0)
  );
  vias jsonb := jsonb_build_object('public.salons', 'raíz', 'public.users', 'raíz', 'public.pin_gestion', 'raíz');
  actual jsonb;
  fk record;
  hijo_pred text;
  pred text;
  n bigint;
  t text;
  colhijo text;
begin
  while jsonb_array_length(pendientes) > 0 loop
    actual := pendientes -> 0;
    pendientes := pendientes - 0;
    if (actual ->> 'n')::int >= 8 then
      continue;
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
        continue;
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

  for t in select jsonb_object_keys(borrar) loop
    select string_agg('(' || x || ')', ' or ') into pred from jsonb_array_elements_text(borrar -> t) x;
    execute format('select count(*) from %s where %s', t, pred) into n;
    paso := 1; tabla := t; filas := n; accion := 'borra';
    detalle := case when vias ->> t = 'raíz' then 'por donde empieza el borrado'
                    else 'en cascada desde ' || (vias ->> t) end;
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
    select string_agg('(' || x || ')', ' or ') into pred from jsonb_array_elements_text(borrar -> t) x;
    hijo_pred := format('(%s) in (select %s from %s where %s)', fk.cols_hijo, fk.cols_padre, t, pred);
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
  paso := 3; tabla := 'public.accounts'; filas := 1; accion := 'queda';
  detalle := 'la cuenta: el borrado empieza por sus centros, sus usuarios y su PIN de gestión';
  return next;

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

revoke all on function public.simulacro_de_borrado(uuid) from public, anon, authenticated;
grant execute on function public.simulacro_de_borrado(uuid) to service_role;

commit;
