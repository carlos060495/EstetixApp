-- 0068 · El borrado a 90 días. NACE APAGADO: esta migración NO BORRA NADA.
--
-- Es la última pieza del plazo de los datos: el reloj y los avisos (0064), el
-- simulacro y el reloj de quien nunca pagó (0065), lo nuestro que no cuelga
-- del salón (0066) y la excepción de las correcciones (0067) ya existen. Aquí
-- está la función que borra, y nada la llama.
--
-- ═══ UNA SELECCIÓN, NO DOS ═══
--
-- Quién está en plazo y quién se ha pasado lo decidía Python
-- (`tareas/plazo_de_datos.relojes` + `core/plazo_de_datos.plazo`), y una
-- función de la base no puede preguntarle a Python. Para que el simulacro, los
-- avisos, el muro y el borrado NO PUEDAN discrepar, la selección se ha sacado a
-- la base, a un solo sitio, y todos la leen:
--
--   · `relojes_del_plazo(p_ahora, p_cuenta)` — las cuentas con el reloj en
--     marcha, con sus fechas en el calendario de la cuenta. La usan la tarea de
--     los avisos, el panel y el muro (`/auth/me`), por RPC.
--   · `cuentas_fuera_de_plazo(p_ahora)` — las que ya se han pasado. La usan el
--     simulacro del panel y el borrado.
--   · `plan_de_borrado(p_cuenta)` — QUÉ se borra de una cuenta y en qué orden,
--     caminando las claves ajenas. Antes vivía dentro del simulacro; ahora el
--     simulacro y el borrado la llaman los dos.
--
-- Las reglas son las de siempre, trasladadas tal cual (y probadas contra los
-- mismos casos): 90 días de calendario de la cuenta desde que la suscripción
-- termina de verdad, o desde que habría acabado la prueba de quien nunca la
-- activó (14 días). Lo único nuevo: una cuenta que ya está en
-- `cuentas_borradas` sale del reloj, para que no se borre dos veces ni se le
-- escriba después.
--
-- ═══ NACE APAGADO, Y SE ENCIENDE A MANO ═══
--
-- `borrado_interruptor` es una sola fila y empieza en apagado. Mientras lo
-- esté, `borrar_cuentas_fuera_de_plazo()` se NIEGA con un error (55000), no
-- devolviendo cero. El interruptor no es un parámetro: la función no tiene
-- ninguno. Encenderlo es un UPDATE a mano, como `postgres`, que exige decir
-- quién y por qué, y cada cambio queda en `borrado_interruptor_historial`, que
-- no se puede editar ni borrar. Nadie más tiene permiso sobre esas tablas, y
-- nadie más que `postgres` puede llamar a la función: no hay cron, ni ruta, ni
-- botón.
--
-- ═══ EL TOPE: 3 CUENTAS POR PASADA ═══
--
-- Si la selección trae más de 3, no se borra NINGUNA y salta una excepción con
-- el número. Medido el 04-10-2026 en producción: 4 cuentas, las 4 con la
-- suscripción activa y ninguna sin pago. Una pasada normal encontrará 0, y
-- de vez en cuando 1. Con 3 hay margen para que coincidan dos, y por encima de
-- 3 ya serían casi todos los clientes que tenemos: eso no es un borrado
-- grande, es una selección rota. Hay que revisarlo a medida que crezcamos; se
-- cambia con una migración.
--
-- ═══ EL ORDEN SALE DE LAS CLAVES AJENAS ═══
--
-- No hay lista de tablas. `plan_de_borrado` camina el esquema desde las raíces
-- de la cuenta (la cuenta, sus centros, sus usuarios, su acceso de Auth y sus
-- entradas en el registro de Auth):
--   · lo que cuelga EN CASCADA se borra con su padre: no se toca a mano;
--   · se borran a mano las raíces y lo que no cuelga de nada pero se va (las
--     correcciones de fichaje, que se descubren por su trigger de la 0067);
--   · el orden entre ellas sale de las claves que NO son en cascada: si algo
--     que se va con A apunta a algo que se va con B, A va antes. Y lo que se
--     va «después de sus centros» va después de `salons`: la excepción de la
--     0067 solo lo deja pasar cuando el centro ya no existe.
-- Si el orden tuviera un ciclo, el plan LANZA y no se borra nada.
--
-- ═══ UNA SOLA TRANSACCIÓN ═══
--
-- Es una FUNCIÓN, no un procedimiento: no puede hacer COMMIT. O se van todas
-- las cuentas de la pasada, con su fila en `cuentas_borradas`, o no se va
-- nada. Si algo falla a mitad (una clave que lo impide, el trigger de las
-- correcciones), se deshace todo.
--
-- ═══ LA CONSTANCIA: `cuentas_borradas` ═══
--
-- Una fila por cuenta borrada: el id de la cuenta, cuándo, por qué, de qué
-- reloj venía, qué día tocaba y cuántas filas se llevó de cada tabla. SIN
-- NADA PERSONAL, sin clave ajena, sin UPDATE ni DELETE (como la 0054).
--
-- ═══ LA CUENTA SE BORRA (Carlos, 04-10-2026, revisado) ═══
--
-- El borrado se lleva la fila de la cuenta y su suscripción. «La cuenta se
-- queda» era la regla de la BAJA, y ahí es correcta: cancela, queda apagada,
-- puede volver. El simulacro la heredó y nadie la revisó para el borrado, que
-- es el final del mismo camino. Si la cuenta sobreviviera con su nombre, su
-- identificación fiscal y su id de cliente de Stripe, el borrado a los 90 días
-- no borraría a la persona: borraría su salón.
--
-- SE QUEDAN, y no se tocan: el registro de dinero (0054), el de descargas
-- (0056), el de soporte (visitas y códigos) y el registro del panel, con nombre
-- y correo dentro. Son el registro contable y de abuso, y se defienden igual
-- que Stripe defiende sus facturas. La ficha operativa del cliente, no.
--
-- ═══ EL USUARIO DE AUTH, DENTRO DE LA MISMA TRANSACCIÓN ═══
--
-- Se borran `auth.users` (y en cascada sus identidades, sesiones, factores y
-- tokens) y sus entradas de `auth.audit_log_entries`, que no cuelgan de nada y
-- guardan su correo. El precio, aceptado: se escribe en tablas de Auth sin su
-- API, y si una versión futura cambia su forma el borrado se rompe ENTERO (y
-- se ve en el simulacro antes de encender nada). Por la API se perdería el
-- todo o nada: un fallo después del borrado dejaría un acceso huérfano que
-- nadie descubre. El guarda vigila la forma de esas tablas.
--
-- ═══ EL PROCEDIMIENTO: STRIPE → STORAGE → LA FUNCIÓN ═══
--
-- Stripe y Storage no pueden entrar en la transacción. Se hacen ANTES, nunca
-- después: al borrar en la base desaparecen los identificadores con los que
-- se limpia el exterior. El día que se ejecute:
--
--   0. SIMULACRO. En el panel, «Simulacro de las que están fuera de plazo»
--      (o `select * from public.simulacro_de_borrado('<cuenta>')`). Cero
--      problemas en rojo, y apuntar de cada cuenta lo que dice el paso 5:
--      los ids de cliente de Stripe y las rutas de los logos.
--   1. STRIPE. Cada id de cliente, borrado en Stripe (panel de Stripe →
--      Customers → Delete, o `DELETE /v1/customers/<id>`). Quita sus métodos
--      de pago; las facturas y cobros los conserva Stripe por obligación legal.
--   2. STORAGE. Cada ruta, borrada del bucket `salon-assets` (panel de
--      Supabase → Storage, o la API de Storage). La base no deja borrarlos con
--      SQL. LA FUNCIÓN SE NIEGA si queda alguno: este paso no se puede saltar.
--   3. LA FUNCIÓN, como `postgres`, en el editor SQL:
--        update public.borrado_interruptor set encendido = true,
--          cambiado_por = '<quién>', motivo = '<por qué>';
--        select * from public.borrar_cuentas_fuera_de_plazo();
--        update public.borrado_interruptor set encendido = false,
--          cambiado_por = '<quién>', motivo = 'terminado';
--      Si algo falla, no se ha borrado nada de ninguna cuenta.
--
-- ═══ LOS DÍAS DE PRUEBA VIVEN EN LA BASE ═══
--
-- `plataforma_config.dias_de_prueba` (14). Los lee el reloj de quien nunca
-- pagó, y el backend para la prueba de Stripe y el muro. `TRIAL_DEFAULT_DAYS`
-- deja de ser una fuente: un guarda no puede ver Railway, así que el dato
-- vive en un solo sitio en vez de vigilarse desde lejos.
--
-- ═══ LA REGLA DE LA COLUMNA OBLIGATORIA ═══
--
-- Una columna nueva, `plataforma_config.dias_de_prueba`: NOT NULL CON VALOR
-- POR DEFECTO, así que el backend anterior (que no la conoce) no se entera.
-- Las tablas nuevas solo las escribe `postgres`.
--
-- ═══ ORDEN DE DESPLIEGUE: 0068 → BACKEND ═══
--
-- El backend nuevo lee el reloj por RPC (`relojes_del_plazo`,
-- `cuentas_fuera_de_plazo`): sin esta migración, la tarea de los avisos, el
-- panel y el muro fallarían. El backend que hay sigue funcionando con ella.

begin;

-- ═══════════════ 1. la constancia de las cuentas borradas

create table public.cuentas_borradas (
  id bigint generated always as identity primary key,
  account_id uuid not null,
  borrada_at timestamptz not null default now(),
  motivo text not null check (motivo = 'fuera_de_plazo_90_dias'),
  origen text not null check (origen in ('suscripcion', 'sin_pago')),
  se_borraba_el date not null,
  filas jsonb not null
);

comment on table public.cuentas_borradas is
  'Una fila por cuenta borrada a los 90 días (0068). Demuestra que el borrado ocurrió; '
  'NO recuerda a quién: sin nombre, sin correo, sin clave ajena. No se edita ni se borra.';
comment on column public.cuentas_borradas.filas is
  'Cuántas filas se llevó de cada tabla: {"public.clients": 12, ...}. Solo nombres de tabla y números.';

create index cuentas_borradas_account_id_idx on public.cuentas_borradas (account_id);

alter table public.cuentas_borradas enable row level security;
revoke all on public.cuentas_borradas from public, anon, authenticated, service_role;
grant select on public.cuentas_borradas to service_role;

create function public.cuentas_borradas_no_se_tocan()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  raise exception 'cuentas_borradas no se puede % : es la constancia de un borrado', tg_op
    using errcode = '42501';
end;
$$;

create trigger cuentas_borradas_sin_update_ni_delete
  before update or delete on public.cuentas_borradas
  for each row execute function public.cuentas_borradas_no_se_tocan();
create trigger cuentas_borradas_sin_truncate
  before truncate on public.cuentas_borradas
  for each statement execute function public.cuentas_borradas_no_se_tocan();

-- ═══════════════ 2. el interruptor: una fila, apagada

create table public.borrado_interruptor (
  unico boolean primary key default true check (unico),
  encendido boolean not null default false,
  cambiado_at timestamptz not null default now(),
  cambiado_por text,
  motivo text,
  constraint encender_dice_quien_y_por_que check (
    not encendido or (nullif(btrim(cambiado_por), '') is not null and nullif(btrim(motivo), '') is not null)
  )
);

comment on table public.borrado_interruptor is
  'El interruptor del borrado a 90 días (0068). Nace APAGADO. Se enciende a mano, como postgres: '
  'update public.borrado_interruptor set encendido = true, cambiado_por = ''…'', motivo = ''…''; '
  'Cada cambio queda en borrado_interruptor_historial.';

insert into public.borrado_interruptor (encendido, motivo) values (false, 'nace apagado (0068)');

create table public.borrado_interruptor_historial (
  id bigint generated always as identity primary key,
  encendido boolean not null,
  cambiado_at timestamptz not null,
  cambiado_por text,
  motivo text,
  quien_en_la_base text not null default current_user
);

alter table public.borrado_interruptor enable row level security;
alter table public.borrado_interruptor_historial enable row level security;
revoke all on public.borrado_interruptor, public.borrado_interruptor_historial
  from public, anon, authenticated, service_role;

create function public.borrado_interruptor_historial_no_se_tocan()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  raise exception 'borrado_interruptor_historial no se puede % : es un registro', tg_op
    using errcode = '42501';
end;
$$;

create trigger borrado_interruptor_historial_sin_update_ni_delete
  before update or delete on public.borrado_interruptor_historial
  for each row execute function public.borrado_interruptor_historial_no_se_tocan();
create trigger borrado_interruptor_historial_sin_truncate
  before truncate on public.borrado_interruptor_historial
  for each statement execute function public.borrado_interruptor_historial_no_se_tocan();

-- El interruptor no se crea ni se quita: solo se cambia, y cada cambio se apunta.
create function public.borrado_interruptor_se_apunta()
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
  insert into public.borrado_interruptor_historial (encendido, cambiado_at, cambiado_por, motivo)
  values (new.encendido, new.cambiado_at, new.cambiado_por, new.motivo);
  return new;
end;
$$;

create trigger borrado_interruptor_se_apunta
  before update or delete on public.borrado_interruptor
  for each row execute function public.borrado_interruptor_se_apunta();
create trigger borrado_interruptor_sin_truncate
  before truncate on public.borrado_interruptor
  for each statement execute function public.borrado_interruptor_se_apunta();

-- El estado con el que nace, apuntado.
insert into public.borrado_interruptor_historial (encendido, cambiado_at, cambiado_por, motivo)
select encendido, cambiado_at, cambiado_por, motivo from public.borrado_interruptor;

-- ═══════════════ 3. los días de prueba, en un solo sitio

alter table public.plataforma_config
  add column dias_de_prueba int not null default 14 check (dias_de_prueba between 1 and 90);
comment on column public.plataforma_config.dias_de_prueba is
  'Los días de prueba (0068). La ÚNICA fuente: la prueba de Stripe, el muro y el reloj de quien '
  'nunca pagó los leen de aquí. TRIAL_DEFAULT_DAYS ya no cuenta.';

-- ═══════════════ 4. la selección: quién está en plazo y quién se ha pasado

create function public.relojes_del_plazo(p_ahora timestamptz default now(), p_cuenta uuid default null)
returns table (
  account_id uuid, origen text, terminada_at timestamptz, fila_id uuid,
  aviso_terminada boolean, aviso_30 boolean, aviso_7 boolean,
  zona_horaria text, terminada_el date, se_borran_el date, dias_restantes int, fuera_de_plazo boolean
)
language sql
stable
security definer
set search_path = ''
as $$
  -- EL PLAZO (el mismo que `core/plazo_de_datos.PLAZO_DIAS`: el guarda los
  -- compara) y LOS DÍAS DE PRUEBA, de su único sitio.
  with constantes as (
    select 90 as plazo_dias, p.dias_de_prueba from public.plataforma_config p
  ),
  relojes as (
    -- Por suscripción: terminada DE VERDAD (fecha puesta Y estado terminado).
    -- `unpaid` no: una suscripción sin pagar sigue existiendo y puede volver.
    select s.account_id, 'suscripcion'::text as origen, s.terminada_at, s.id as fila_id,
           s.aviso_terminada_at is not null as aviso_terminada,
           s.aviso_30_at is not null as aviso_30,
           s.aviso_7_at is not null as aviso_7
    from public.subscriptions s
    where s.terminada_at is not null
      and s.estado in ('canceled', 'incomplete_expired')
    union all
    -- Sin pago: ninguna suscripción, y la prueba ya habría acabado. Días de 24
    -- horas exactas, como el `timedelta` de antes: no días de calendario.
    select a.id, 'sin_pago', a.created_at + c.dias_de_prueba * interval '24 hours', a.id,
           a.aviso_plazo_terminada_at is not null,
           a.aviso_plazo_30_at is not null,
           a.aviso_plazo_7_at is not null
    from public.accounts a cross join constantes c
    where not exists (select 1 from public.subscriptions s where s.account_id = a.id)
      and a.created_at + c.dias_de_prueba * interval '24 hours' <= p_ahora
  ),
  con_zona as (
    -- La zona de la cuenta: la del centro que se queda al bajar de plan (el
    -- marcado; si no, el más antiguo), como `core/zona_de_la_cuenta`. Sin
    -- centros, la península.
    select r.*, coalesce((
      select z.zona_horaria from public.salons z
      where z.account_id = r.account_id
      order by z.sigue_al_bajar desc, z.created_at, z.id::text collate "C"
      limit 1
    ), 'Europe/Madrid') as zona_horaria
    from relojes r
    where (p_cuenta is null or r.account_id = p_cuenta)
      -- Una cuenta ya borrada no vuelve a entrar: ni se borra dos veces ni se le escribe.
      and not exists (select 1 from public.cuentas_borradas b where b.account_id = r.account_id)
  )
  select z.account_id, z.origen, z.terminada_at, z.fila_id, z.aviso_terminada, z.aviso_30, z.aviso_7,
         z.zona_horaria,
         (z.terminada_at at time zone z.zona_horaria)::date,
         (z.terminada_at at time zone z.zona_horaria)::date + c.plazo_dias,
         ((z.terminada_at at time zone z.zona_horaria)::date + c.plazo_dias)
           - (p_ahora at time zone z.zona_horaria)::date,
         -- FUERA DE PLAZO = pasado el día del borrado. El día 90 todavía es
         -- suyo: es el que dicen los correos («se borrarán el …»), y ese día
         -- aún sale el aviso de 7. LA ÚNICA VEZ QUE SE ESCRIBE ESTA REGLA.
         (p_ahora at time zone z.zona_horaria)::date
           > (z.terminada_at at time zone z.zona_horaria)::date + c.plazo_dias
  from con_zona z cross join constantes c
  order by z.terminada_at, z.account_id;
$$;

comment on function public.relojes_del_plazo(timestamptz, uuid) is
  'LA selección del plazo de los datos (0068): quién tiene el reloj en marcha y cuándo se borra, '
  'en el calendario de la cuenta. La leen los avisos, el panel, el muro, el simulacro y el borrado.';

create function public.cuentas_fuera_de_plazo(p_ahora timestamptz default now())
returns table (account_id uuid, origen text, se_borran_el date, dias_restantes int)
language sql
stable
security definer
set search_path = ''
as $$
  select r.account_id, r.origen, r.se_borran_el, r.dias_restantes
  from public.relojes_del_plazo(p_ahora) r
  where r.fuera_de_plazo
  order by r.account_id;
$$;

revoke all on function public.relojes_del_plazo(timestamptz, uuid) from public, anon, authenticated;
revoke all on function public.cuentas_fuera_de_plazo(timestamptz) from public, anon, authenticated;
grant execute on function public.relojes_del_plazo(timestamptz, uuid) to service_role;
grant execute on function public.cuentas_fuera_de_plazo(timestamptz) to service_role;

-- ═══════════════ 5. el plan: qué se borra de una cuenta, y en qué orden

create function public.plan_de_borrado(p_cuenta uuid)
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
begin
  -- Los accesos de Auth de la cuenta y sus correos, FIJADOS AHORA: cuando se
  -- borre `public.users`, ya no se podrían encontrar.
  accesos := array(select u.auth_user_id from public.users u
                   where u.account_id = p_cuenta and u.auth_user_id is not null order by 1);
  correos := array(select distinct lower(au.email) from auth.users au
                   where au.id = any (accesos) and au.email is not null order by 1);

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
    -- El registro de GoTrue: no cuelga de nada y guarda su correo. Por quién
    -- actuó y sobre quién, por id y por correo.
    'auth.audit_log_entries', format(
      '(payload ->> ''actor_id'') = any (%1$L::text[]) or (payload -> ''traits'' ->> ''user_id'') = any (%1$L::text[])'
      ' or lower(payload ->> ''actor_username'') = any (%2$L::text[])'
      ' or lower(payload -> ''traits'' ->> ''user_email'') = any (%2$L::text[])',
      accesos::text[], correos)
  );
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
  -- SIN cascada ni SET NULL, a algo que se va con B, A va antes que B.
  explicitas := array(select jsonb_object_keys(alcance) order by 1);
  for fk in
    select k.conrelid::regclass::text as hijo, k.confrelid::regclass::text as padre
    from pg_constraint k
    where k.contype = 'f' and k.confdeltype in ('a', 'r') and k.conrelid <> k.confrelid
  loop
    foreach r in array explicitas loop
      foreach t in array explicitas loop
        if r <> t
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

comment on function public.plan_de_borrado(uuid) is
  'QUÉ se borra de una cuenta y en qué orden (0068), caminando las claves ajenas. orden = null: se va '
  'en cascada con su padre. La llaman el simulacro y el borrado: no hay otra.';

revoke all on function public.plan_de_borrado(uuid) from public, anon, authenticated, service_role;

-- ═══════════════ 6. el simulacro lee el plan

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
end;
$$;

revoke all on function public.simulacro_de_borrado(uuid) from public, anon, authenticated;
grant execute on function public.simulacro_de_borrado(uuid) to service_role;

-- ═══════════════ 7. el borrado. NADIE LO LLAMA.

create function public.borrar_cuentas_fuera_de_plazo()
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

    -- 4. La constancia, en la misma transacción.
    insert into public.cuentas_borradas (account_id, motivo, origen, se_borraba_el, filas)
    values (cuenta.account_id, 'fuera_de_plazo_90_dias', cuenta.origen, cuenta.se_borran_el, filas);

    account_id := cuenta.account_id;
    return next;
  end loop;
end;
$$;

comment on function public.borrar_cuentas_fuera_de_plazo() is
  'EL BORRADO A 90 DÍAS (0068). Nace apagado (public.borrado_interruptor). Una sola transacción, '
  'tope de 3 cuentas por pasada, la selección y el plan del simulacro. Nadie lo llama: solo postgres puede.';

revoke all on function public.borrar_cuentas_fuera_de_plazo() from public, anon, authenticated, service_role;

commit;
