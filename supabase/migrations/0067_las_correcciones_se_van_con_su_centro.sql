-- 0067 · Las correcciones de fichaje se van con su centro. NO BORRA NADA.
--
-- Carlos decidió (04-10-2026) que el registro de jornada es del salón y se va
-- con él a los 90 días. Los fichajes ya se iban: cuelgan del centro en
-- cascada. Las correcciones (0061) no podían: no tienen clave ajena (para no
-- perder de quién eran) y su trigger rechaza CUALQUIER DELETE. El simulacro
-- (0065) lo enseñaba en rojo: el borrado fallaría en cuanto la cuenta tuviera
-- una sola corrección.
--
-- ═══ LA EXCEPCIÓN, Y POR QUÉ NO ES UN AGUJERO ═══
--
-- Desde aquí el DELETE de una corrección pasa SOLO SI SU CENTRO YA NO EXISTE.
-- Nada más la abre: ni quién borra, ni un rol, ni una bandera de sesión, ni un
-- permiso. Carlos eligió esta opción (A) frente a un rol propio (B), una marca
-- de sesión (C) o apagar el trigger (D) precisamente porque NO SE PUEDE PEDIR:
--
--   · B depende de QUIÉN borra, y un rol se concede.
--   · C y D dependen de que alguien HAGA algo: poner la marca, apagar el trigger.
--   · A depende de un HECHO que ya ocurrió y no se deshace: que el centro ya
--     no existe. No hay forma de convencer al trigger; solo hay forma de haber
--     borrado ya el centro, que es justo el acto protegido. Y borrar el centro
--     ya se lleva en cascada sus fichajes, que son la otra mitad del registro.
--
-- Mientras el centro exista, una corrección no se puede borrar, igual que
-- antes. El UPDATE y el TRUNCATE siguen rechazados SIEMPRE: la excepción es
-- solo para el DELETE, fila a fila, y solo por la desaparición de su centro.
--
-- ═══ LA GRIETA DE A: ¿QUIÉN PUEDE BORRAR UN CENTRO? (recorrido el 04-10-2026) ═══
--
-- Si borrar un centro fuera fácil, A dejaría huérfanas unas correcciones que
-- cualquiera podría borrar. Recorridos el código y el esquema, un centro solo
-- desaparece por:
--
--   1. El ALTA que falla: el backend (service_role) borra el centro que ESA
--      MISMA petición acaba de crear, con un id que genera él. Ese centro no ha
--      tenido tiempo de tener fichajes ni correcciones.
--   2. Nosotros, a mano, como `postgres` (panel de Supabase): es nuestro.
--   3. Nada más. No hay endpoint que cierre un centro (ni la baja ni la bajada
--      de plan lo borran: lo PAUSAN), el panel de plataforma no borra centros,
--      ninguna función de la base los borra, ninguna cascada llega a `salons`
--      (su clave hacia `accounts` es NO ACTION), y la sesión (`authenticated`)
--      tiene el DELETE pero NINGUNA política que lo deje pasar.
--
-- Esta migración cierra además lo que solo estaba cerrado por RLS o porque
-- PostgREST no lo expone: quita DELETE y TRUNCATE de `salons` a `anon` y
-- `authenticated`, y el TRUNCATE a `service_role`. Y una corrección huérfana
-- solo la puede borrar quien tiene DELETE sobre la tabla, que es `postgres` y
-- nadie más (0061: `service_role` solo INSERT y SELECT; la sesión, nada).
--
-- ═══ POR QUÉ LA FUNCIÓN ES SECURITY DEFINER ═══
--
-- Para que «el centro ya no existe» signifique eso y no «yo no lo veo»: si la
-- comprobación corriera con los permisos de quien borra, la RLS podría
-- esconderle el centro y la excepción se abriría sin que el centro se hubiera
-- borrado. Como `postgres` (dueño, salta la RLS), ve todos los centros.
--
-- ═══ UNA SOLA TRANSACCIÓN ═══
--
-- Entre borrar el centro y borrar sus correcciones hay un momento en que están
-- huérfanas. El borrado de los 90 días (que NO existe todavía) tiene que ser
-- una sola función, y una función es una sola transacción: ese momento no
-- existe fuera de ella. Fuera, las demás sesiones siguen viendo el centro
-- hasta el COMMIT, y después del COMMIT las correcciones ya no están. Si el
-- borrado intentara las correcciones ANTES que el centro, el trigger lo
-- rechazaría y se desharía entero.
--
-- ═══ EL GUARDA ═══
--
-- `backend/tests/test_la_excepcion_del_centro_base_real.py` se pone ROJO si
-- la excepción deja de depender SOLO de que el centro no exista (un rol, una
-- bandera, un permiso, otra tabla, SECURITY INVOKER), si el DELETE de las
-- correcciones pasa por otra función, o si vuelve a haber un camino fácil para
-- borrar un centro. Cada cosa, con su control.
--
-- ═══ LA REGLA DE LA COLUMNA OBLIGATORIA ═══
--
-- No añade columnas. Ningún backend borra correcciones ni borra centros con la
-- sesión (comprobado en `backend/app`).
--
-- ═══ ORDEN DE DESPLIEGUE: 0066 → 0067 → BACKEND ═══
--
-- Funciona con el backend que hay. El backend nuevo solo trae el guarda.

begin;

-- ═══════════════ 1. la excepción

create function public.se_borra_solo_sin_su_centro()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  -- LA ÚNICA CONDICIÓN. No se añade aquí nada que dependa de quién borra (rol,
  -- usuario, permiso), de algo que se pueda poner (una bandera, un ajuste de
  -- sesión) ni de otra tabla: ver la cabecera de la 0067, y el guarda.
  if exists (select 1 from public.salons s where s.id = old.salon_id) then
    raise exception '% no se puede borrar mientras su centro exista: es un registro', tg_table_name
      using errcode = '42501';
  end if;
  return old;
end;
$$;

revoke all on function public.se_borra_solo_sin_su_centro() from public, anon, authenticated;

comment on function public.se_borra_solo_sin_su_centro() is
  'Deja borrar una fila de un registro SOLO si su centro (old.salon_id) ya no existe. '
  'No es un agujero: es una condición que no se puede pedir, solo cumplir borrando antes '
  'el centro. Ver la cabecera de la 0067 y tests/test_la_excepcion_del_centro_base_real.py.';

-- ═══════════════ 2. las correcciones: el DELETE pasa por ella; el UPDATE y el TRUNCATE, no

drop trigger correcciones_de_fichaje_sin_update_ni_delete on public.correcciones_de_fichaje;

create trigger correcciones_de_fichaje_sin_update
  before update on public.correcciones_de_fichaje
  for each row execute function public.correcciones_de_fichaje_no_se_tocan();

create trigger correcciones_de_fichaje_se_van_con_su_centro
  before delete on public.correcciones_de_fichaje
  for each row execute function public.se_borra_solo_sin_su_centro();

-- ═══════════════ 3. borrar un centro: solo nosotros

revoke delete, truncate on public.salons from anon, authenticated;
revoke truncate on public.salons from service_role;

-- ═══════════════ 4. el simulacro cuenta las correcciones entre lo que se borra

-- Se descubren por su trigger, no por una lista: toda tabla cuyo DELETE pasa
-- por `se_borra_solo_sin_su_centro` se va DESPUÉS de sus centros.
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

  -- Lo que se va DESPUÉS de sus centros (0067): sin clave ajena, su DELETE solo
  -- pasa cuando el centro ya no existe. Se descubre por ese trigger.
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
      || to_jsonb(format('salon_id in (select id from public.salons where account_id = %L)', p_cuenta)));
    vias := jsonb_set(vias, array[t], to_jsonb('después'::text));
  end loop;

  for t in select jsonb_object_keys(borrar) loop
    select string_agg('(' || x || ')', ' or ') into pred from jsonb_array_elements_text(borrar -> t) x;
    execute format('select count(*) from %s where %s', t, pred) into n;
    paso := 1; tabla := t; filas := n; accion := 'borra';
    detalle := case when vias ->> t = 'raíz' then 'por donde empieza el borrado'
                    when vias ->> t = 'después' then 'después de sus centros: su trigger solo deja borrarlo cuando el centro ya no existe'
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
