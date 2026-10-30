-- 0089 · EL BORRADO A 90 DÍAS SE ENCHUFA, Y SIGUE APAGADO (Carlos, 19-10-2026).
--
-- Hasta aquí el borrado existía y nadie podía llamarlo: solo `postgres`, a mano,
-- con el procedimiento de la cabecera de la 0068 (Stripe → Storage → la
-- función). Carlos decide (19-10-2026):
--
--   · LO LLAMA LA CADENA DE TAREAS (`app/tareas/borrado_a_90_dias.py`, dentro de
--     `tareas/recordatorios.main`), con service_role. Hace ella los pasos 1 y 2
--     —Stripe y Storage— y después llama a la función. Nada más la llama.
--   · EL INTERRUPTOR SIGUE APAGADO. Esta migración no lo toca: lo enciende
--     Carlos. Apagado, la pasada se niega ANTES de tocar nada de fuera
--     (`proxima_pasada_de_borrado` lanza 55000), así que con él apagado no se
--     borra ni un cliente de Stripe ni un logo.
--   · EL TOPE VA EN LA FILA DEL INTERRUPTOR (`tope_por_pasada`, 10), y borra
--     HASTA el tope, LAS MÁS ANTIGUAS PRIMERO (por el día que les tocaba). Antes
--     era una constante de 3 y, si había más, no borraba ninguna: con la cadena
--     llamándolo cada día, una cuenta de más habría parado el borrado para
--     siempre. Lo que pase del tope espera a la siguiente pasada.
--   · NUNCA «TODO O NADA» ENTRE CUENTAS. Cada cuenta va en su propio bloque: si
--     una falla (le queda un logo, algo la sujeta), se deshace ENTERA esa y se
--     dice por qué; las demás siguen. Dentro de una cuenta, todo o nada como
--     antes: el mismo plan, las mismas comprobaciones, la misma constancia.
--   · SOLO LAS PREPARADAS. La función borra solo las cuentas de la pasada que
--     la tarea le dice que ya tienen Stripe y Storage limpios (`p_preparadas`).
--     Si Stripe falla con una, esa no entra: borrarla en la base perdería el id
--     con el que se limpia Stripe después. El parámetro NO es el interruptor:
--     el interruptor se sigue leyendo de su fila.
--
-- ORDEN DE DESPLIEGUE: esta migración, después el backend que trae la tarea. Al
-- revés, la tarea llamaría a funciones que no existen y lo apuntaría como fallo
-- de su pasada (aislada: no tumba los recordatorios), sin borrar nada.

-- ═══════════════ 1. el tope, en la fila del interruptor

alter table public.borrado_interruptor
  add column tope_por_pasada int not null default 10 check (tope_por_pasada between 1 and 100);
alter table public.borrado_interruptor_historial add column tope_por_pasada int;

comment on table public.borrado_interruptor is
  'El interruptor del borrado a 90 días (0068, 0089). Nace APAGADO. Se enciende a mano, como postgres: '
  'update public.borrado_interruptor set encendido = true, cambiado_por = ''…'', motivo = ''…''; '
  'tope_por_pasada = cuántas cuentas, como mucho, en cada pasada de la cadena (las más antiguas primero). '
  'Cada cambio queda en borrado_interruptor_historial.';

create or replace function public.borrado_interruptor_se_apunta()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if tg_op <> 'UPDATE' then
    raise exception 'borrado_interruptor no se puede %: es una sola fila, solo se cambia', tg_op
      using errcode = '42501';
  end if;
  new.cambiado_at := now();
  insert into public.borrado_interruptor_historial (encendido, tope_por_pasada, cambiado_at, cambiado_por, motivo)
  values (new.encendido, new.tope_por_pasada, new.cambiado_at, new.cambiado_por, new.motivo);
  return new;
end;
$$;

-- ═══════════════ 2. la próxima pasada: las más antiguas, hasta el tope

create function public.proxima_pasada_de_borrado()
returns table (account_id uuid, origen text, se_borran_el date, clientes_stripe text[], logos text[],
               fuera_de_plazo int)
language plpgsql
stable
security definer
set search_path = public, pg_temp
as $$
#variable_conflict use_column
declare
  v_inter public.borrado_interruptor;
  v_total int;
begin
  -- APAGADO, SE NIEGA AQUÍ: es lo que lee la tarea antes de tocar Stripe o
  -- Storage, así que apagado no sale nada hacia fuera.
  select * into v_inter from public.borrado_interruptor i where i.unico;
  if v_inter.encendido is distinct from true then
    raise exception 'El borrado a 90 días está APAGADO: no se ha borrado nada.'
      using errcode = '55000',
            hint = 'Se enciende a mano, como postgres, en public.borrado_interruptor (quién y por qué).';
  end if;
  select count(*) into v_total from public.cuentas_fuera_de_plazo();
  return query
  select f.account_id, f.origen, f.se_borran_el,
         -- Lo de fuera, lo mismo que enseña el simulacro en su paso 5.
         array(select x.id from (select a.stripe_customer_id as id from public.accounts a where a.id = f.account_id
                                 union
                                 select s.stripe_customer_id from public.subscriptions s where s.account_id = f.account_id) x
               where x.id is not null and x.id <> '' order by x.id),
         array(select o.name from storage.objects o
               where o.bucket_id = 'salon-assets'
                 and split_part(o.name, '/', 1) in (select s.id::text from public.salons s where s.account_id = f.account_id)
               order by o.name),
         v_total
  from public.cuentas_fuera_de_plazo() f
  order by f.se_borran_el, f.account_id
  limit v_inter.tope_por_pasada;
end;
$$;

comment on function public.proxima_pasada_de_borrado() is
  'Las cuentas que se llevará la próxima pasada del borrado a 90 días (0089): las más antiguas, hasta '
  'borrado_interruptor.tope_por_pasada, con los clientes de Stripe y los logos que hay que borrar antes. '
  'Apagado, se niega (55000).';

revoke all on function public.proxima_pasada_de_borrado() from public, anon, authenticated;
grant execute on function public.proxima_pasada_de_borrado() to service_role;

-- ═══════════════ 3. el borrado: solo las preparadas, cada una en su bloque

drop function public.borrar_cuentas_fuera_de_plazo();

create function public.borrar_cuentas_fuera_de_plazo(p_preparadas uuid[])
returns table (account_id uuid, filas jsonb, error text)
language plpgsql
volatile
security definer
set search_path = public, pg_temp
as $$
#variable_conflict use_column
declare
  v_encendido boolean;
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

  -- 2. EL TOPE: la misma pasada que acaba de leer la tarea (las más antiguas,
  -- hasta el tope de la fila), y de ella solo las que llegan preparadas.
  for cuenta in
    select p.* from public.proxima_pasada_de_borrado() p where p.account_id = any (coalesce(p_preparadas, '{}'))
  loop
    -- 3. CADA CUENTA EN SU BLOQUE: si algo falla, se deshace esta entera y se dice.
    begin
      v_centros := array(select s.id from public.salons s where s.account_id = cuenta.account_id);

      -- STORAGE VA ANTES, NUNCA DESPUÉS: con los centros borrados, nadie sabría
      -- qué rutas eran suyas.
      select count(*) into n from storage.objects o
      where o.bucket_id = 'salon-assets' and split_part(o.name, '/', 1) = any (v_centros::text[]);
      if n > 0 then
        raise exception 'A la cuenta % le quedan % logos en Storage: primero se borran ellos.',
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
          raise exception 'El borrado de % dejó % filas en %.', cuenta.account_id, n, paso ->> 'tabla';
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
          raise exception 'El borrado de % dejó % filas en %.', cuenta.account_id, n, pl.tabla;
        end if;
      end loop;

      -- …y en Auth no queda NADA que la nombre, en ninguna tabla (0083).
      select string_agg(q.tabla || ' (' || q.filas || ')', ', ') into v_queda from public.lo_que_nombra_en_auth(v_marcas) q;
      if v_queda is not null then
        raise exception 'El borrado de % dejó en Auth filas que la nombran: %.', cuenta.account_id, v_queda;
      end if;

      -- 4. La constancia, en el mismo bloque.
      insert into public.cuentas_borradas (account_id, motivo, origen, se_borraba_el, filas)
      values (cuenta.account_id, 'fuera_de_plazo_90_dias', cuenta.origen, cuenta.se_borran_el, filas);

      account_id := cuenta.account_id;
      error := null;
      return next;
    exception when others then
      -- Esta cuenta vuelve entera; la pasada sigue con la siguiente.
      account_id := cuenta.account_id;
      filas := null;
      error := sqlstate || ': ' || sqlerrm;
      return next;
    end;
  end loop;
end;
$$;

comment on function public.borrar_cuentas_fuera_de_plazo(uuid[]) is
  'EL BORRADO A 90 DÍAS (0068, 0083, 0089). Nace apagado (borrado_interruptor). Borra, de la próxima pasada '
  '(proxima_pasada_de_borrado: las más antiguas, hasta el tope), las cuentas que la tarea da por preparadas '
  '(Stripe y Storage ya limpios). Cada cuenta, todo o nada; entre cuentas, no.';

revoke all on function public.borrar_cuentas_fuera_de_plazo(uuid[]) from public, anon, authenticated;
grant execute on function public.borrar_cuentas_fuera_de_plazo(uuid[]) to service_role;
