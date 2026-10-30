-- 0077 · Las agencias: un referido que vende con varios comerciales.
--
-- Carlos, 06-10-2026. Hasta aquí un referido era una persona. Falta el otro
-- caso: una agencia con comerciales. LA REGLA DE DINERO ES UNA SOLA: LA AGENCIA
-- ES LA ÚNICA CONTRAPARTE. Su porcentaje, su cuenta de Stripe, su mínimo y sus
-- pagos. Lo que reparta con sus comerciales es cosa suya: la plataforma no lo
-- conoce, no lo calcula y no lo muestra.
--
-- ═══ LO QUE HAY ═══
--
--   · `referidos.tipo`: «individual» o «agencia», elegido al aprobarlo. Todos los
--     que ya existen son personas: nacen «individual» (columna con valor por
--     defecto). Una agencia con trabajadores no puede dejar de serlo (trigger).
--   · `trabajadores_de_agencia`: nombre, código de alta y, si la agencia lo
--     generó, el HASH de su enlace de seguimiento. NADA MÁS: ni porcentaje, ni
--     cuenta de Stripe, ni contraseña. Un trabajador no es un referido: no está
--     en `referidos`, no entra a ningún sitio y nunca es destinatario de un pago
--     (`pagos_a_referidos.referido_id` sale siempre de `referidos`). Lo vigila
--     `test_agencias_base_real.py`, recorriendo el esquema.
--   · Un código de alta es ÚNICO ENTRE LAS DOS TABLAS (trigger con candado por
--     código): un mismo código no puede casar con un referido y con un
--     trabajador a la vez.
--
-- ═══ LA ATRIBUCIÓN ═══
--
-- `atribuir_alta` (la misma firma) busca el código primero entre los referidos
-- y después entre los trabajadores. Con el de un trabajador, el salón se
-- atribuye A SU AGENCIA, con el porcentaje de la agencia congelado al llegar
-- (0075), y en la MISMA fila inmutable se guarda qué trabajador lo trajo, con
-- su nombre copiado y sin clave ajena (como los nombres de 0057). Que el
-- trabajador se dé de baja después no mueve ni un euro ni reasigna salones: la
-- fila no se toca. Un trabajador no cambia de agencia: si una persona se va a
-- otra, aquella le da de baja y esta le da otro código.
--
-- La misma fila guarda también EL PLAN CON EL QUE LLEGÓ (`plan_al_llegar`): el
-- que eligió en el formulario de alta (`accounts.pending_plan`, que es lo que
-- hay en la cuenta en el instante de atribuir), para que la página de
-- seguimiento diga, si hoy tiene otro, cuál vendió el comercial. Las
-- atribuciones que ya había quedan sin él: no se sabe y no se inventa.
--
-- Un código de un trabajador DADO DE BAJA no atribuye: el alta entra igual y el
-- código queda en `codigos_que_no_valieron` (motivo `trabajador_de_baja`).
-- Esa tabla gana la cuenta a la que llegó el código (`cuenta_id`, nombre
-- copiado): sin ella no se podía repescar la atribución a mano. Las filas que
-- ya había quedan sin cuenta: no se sabe y no se inventa.
--
-- UN CÓDIGO QUE NO VALE NUNCA PARA UN ALTA: un alta perdida cuesta más que una
-- atribución perdida. `codigo_de_alta_vale` deja comprobarlo antes de enviar,
-- con la misma regla de comparación que `atribuir_alta`
-- (`codigo_de_alta_normalizado`: sin espacios alrededor y sin mayúsculas).
--
-- ═══ LO QUE SE VE ═══
--
--   · `trabajadores_de_una_agencia` y `devengado_por_trabajador`: el desglose
--     por trabajador (salones traídos, cuántos pagan hoy, lo devengado por sus
--     salones). Sale de las MISMAS filas de `comisiones` que el total: es un
--     desglose, no otra contabilidad. Lo que trajo la agencia con su propio
--     código va en una fila sin trabajador, y todo suma el total.
--   · `seguimiento_de_un_trabajador(hash)`: la página pública del enlace de
--     seguimiento. SOLO nombre del salón, nombre del plan, desde cuándo y si
--     paga, está en prueba o se cayó. Ni un importe, ni un porcentaje, ni
--     datos de contacto. El enlace es aleatorio (lo genera el backend), se
--     guarda solo su hash y la agencia lo revoca o lo cambia por otro.
--
-- DAR DE BAJA REVOCA EL ENLACE, en el mismo UPDATE (Carlos, 06-10-2026): quien
-- se va de una agencia no se lleva la cartera —qué salones hay, con qué plan
-- y quién paga—. Y por si un hash quedara puesto por otra vía, la página
-- tampoco abre para un trabajador de baja, y a uno de baja no se le puede
-- poner un enlace nuevo. Darlo de alta otra vez es otro trabajador, con otro
-- código: el enlace viejo no revive. Lo vigila `test_agencias_base_real.py`.
--
-- ═══ ARRASTRES DE LA 0076 ═══
--
--   · EL MÍNIMO DE PAGO SE CAMBIA DESDE EL PANEL (antes, solo con una
--     migración): `cambiar_minimo_de_pago`, solo el owner, con motivo escrito
--     obligatorio, y cada cambio en `cambios_de_minimo` (quién, cuándo, de
--     cuánto a cuánto), inmutable como `cambios_de_porcentaje`. Vale también
--     para fijar el mínimo de una moneda que no lo tenía (USD).
--   · PAGAR POR DEBAJO DEL MÍNIMO: `ordenar_pago_o_saltar_el_minimo`, con
--     motivo obligatorio que queda en la orden (`minimo_saltado_motivo` y el
--     mínimo que se saltó). `ordenar_pago` (la de 0076, la que llama el backend
--     desplegado) es ahora esa misma función sin saltarse nada: un solo cuerpo.
--   · `porcentajes_de_sus_salones()`: con qué % van HOY los salones de cada
--     referido, para avisar en su ficha si no coincide con el de los próximos.
--   · `portal_del_referido` devuelve además qué trabajador trajo cada salón.
--
-- COLUMNAS OBLIGATORIAS: `referidos.tipo` nace `not null` con valor por defecto
-- «individual» (lo que son todos hoy); la escribe el panel al aprobar. Las
-- demás columnas nuevas en tablas que ya existen admiten nulo. Las tablas
-- nuevas: todas sus columnas `not null` las escriben las funciones de aquí.
--
-- ═══ ORDEN DE DESPLIEGUE: 0077 → BACKEND + FRONTEND (a la vez) ═══
--
-- Todo lo de aquí es compatible con el backend desplegado: `atribuir_alta` y
-- `ordenar_pago` conservan su firma y lo que devuelven; `portal_del_referido`
-- devuelve una columna más, que el backend viejo ignora. Ningún paso en Stripe.
--
-- Probado en local, contra la base de verdad: `test_agencias_base_real.py`.

begin;

-- ═══════════════ 1. la comparación de un código, en un solo sitio

-- Sin espacios alrededor (también el duro, chr(160), que pega un móvil) y sin
-- mayúsculas. NADA MÁS: quitar caracteres de en medio podría convertir un
-- código mal copiado en el de otra persona (ver 0070).
create function public.codigo_de_alta_normalizado(p_codigo text)
returns text
language sql
immutable
set search_path = public, pg_temp
as $$
  select upper(regexp_replace(coalesce(p_codigo, ''), '^[\s' || chr(160) || ']+|[\s' || chr(160) || ']+$', '', 'g'));
$$;

-- ═══════════════ 2. el tipo de referido

alter table public.referidos
  add column tipo text not null default 'individual' check (tipo in ('individual', 'agencia'));

comment on column public.referidos.tipo is
  'individual | agencia (0077). Se elige al aprobar. Solo una agencia tiene trabajadores.';

-- ═══════════════ 3. los trabajadores

create table public.trabajadores_de_agencia (
  id uuid primary key default gen_random_uuid(),
  -- A la agencia sí, y con `restrict`: los referidos no se borran nunca.
  agencia_id uuid not null references public.referidos (id) on delete restrict,
  nombre text not null check (char_length(btrim(nombre)) between 1 and 80),
  -- El mismo formato que el de un referido (`core/codigo_referido.py`).
  codigo text not null check (codigo ~ '^[0-9A-Z]{6}$'),
  creado_at timestamptz not null default now(),
  -- Dado de baja: conserva sus salones, su código deja de atribuir.
  de_baja_at timestamptz,
  -- El enlace de seguimiento: SOLO su hash (sha256). Nulo = sin enlace.
  seguimiento_hash text check (seguimiento_hash ~ '^[0-9a-f]{64}$'),
  seguimiento_creado_at timestamptz,
  constraint seguimiento_con_fecha check ((seguimiento_hash is null) = (seguimiento_creado_at is null))
);

comment on table public.trabajadores_de_agencia is
  'Los comerciales de una agencia (0077). NO son referidos: sin porcentaje, sin cuenta de Stripe, sin '
  'contraseña, nunca destinatarios de un pago. Solo nombre, código de alta y enlace de seguimiento.';

create unique index trabajadores_codigo_normalizado on public.trabajadores_de_agencia (upper(btrim(codigo)));
create unique index trabajadores_seguimiento on public.trabajadores_de_agencia (seguimiento_hash)
  where seguimiento_hash is not null;
create index trabajadores_por_agencia on public.trabajadores_de_agencia (agencia_id, creado_at);

-- Solo se escribe con las funciones de aquí; el backend lee.
revoke all on public.trabajadores_de_agencia from public, anon, authenticated, service_role;
grant select on public.trabajadores_de_agencia to service_role;
alter table public.trabajadores_de_agencia enable row level security;

-- Un código, una sola dueña, entre las dos tablas.
create function public.el_codigo_de_alta_es_unico()
returns trigger
language plpgsql
set search_path = public, pg_temp
as $$
declare
  v_codigo text := upper(btrim(new.codigo));
begin
  if new.codigo is null then
    return new;
  end if;
  perform pg_advisory_xact_lock(hashtextextended('codigo_de_alta:' || v_codigo, 0));
  if tg_table_name = 'referidos' then
    if exists (select 1 from public.trabajadores_de_agencia t where upper(btrim(t.codigo)) = v_codigo) then
      raise exception 'el código % ya es de un trabajador de agencia', new.codigo using errcode = '23505';
    end if;
  elsif exists (select 1 from public.referidos r where upper(btrim(r.codigo)) = v_codigo) then
    raise exception 'el código % ya es de un referido', new.codigo using errcode = '23505';
  end if;
  return new;
end;
$$;

create trigger referidos_codigo_unico_entre_tablas
  before insert or update of codigo on public.referidos
  for each row execute function public.el_codigo_de_alta_es_unico();
create trigger trabajadores_codigo_unico_entre_tablas
  before insert or update of codigo on public.trabajadores_de_agencia
  for each row execute function public.el_codigo_de_alta_es_unico();

-- Una agencia con trabajadores no deja de ser agencia.
create function public.una_agencia_con_trabajadores_sigue_siendolo()
returns trigger
language plpgsql
set search_path = public, pg_temp
as $$
begin
  if old.tipo = 'agencia' and new.tipo <> 'agencia'
     and exists (select 1 from public.trabajadores_de_agencia t where t.agencia_id = new.id) then
    raise exception 'una agencia con trabajadores no puede pasar a ser individual' using errcode = '23514';
  end if;
  return new;
end;
$$;

create trigger referidos_agencia_con_trabajadores
  before update of tipo on public.referidos
  for each row execute function public.una_agencia_con_trabajadores_sigue_siendolo();

-- Dar de alta, de baja y el enlace: la agencia, solo los suyos.
create function public.alta_de_trabajador(p_agencia uuid, p_nombre text, p_codigo text)
returns table (resultado text, trabajador_id uuid)
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_id uuid;
begin
  if not exists (select 1 from public.referidos r
                 where r.id = p_agencia and r.tipo = 'agencia' and r.estado = 'aprobado') then
    resultado := 'no_es_una_agencia'; return next; return;
  end if;
  if char_length(btrim(coalesce(p_nombre, ''))) not between 1 and 80 then
    raise exception 'alta_de_trabajador: el nombre va de 1 a 80 caracteres' using errcode = '22023';
  end if;
  begin
    insert into public.trabajadores_de_agencia (agencia_id, nombre, codigo)
    values (p_agencia, btrim(p_nombre), p_codigo)
    returning id into v_id;
  exception when unique_violation then
    -- El código ya es de alguien (referido o trabajador): el backend genera otro.
    resultado := 'codigo_ocupado'; return next; return;
  end;
  resultado := 'creado'; trabajador_id := v_id; return next;
end;
$$;

create function public.baja_de_trabajador(p_agencia uuid, p_trabajador uuid)
returns text
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  -- Y su enlace de seguimiento, revocado en el mismo acto (ver la cabecera).
  update public.trabajadores_de_agencia
  set de_baja_at = now(), seguimiento_hash = null, seguimiento_creado_at = null
  where id = p_trabajador and agencia_id = p_agencia and de_baja_at is null;
  if found then
    return 'de_baja';
  end if;
  return case when exists (select 1 from public.trabajadores_de_agencia
                           where id = p_trabajador and agencia_id = p_agencia)
              then 'ya_estaba' else 'no_es_suyo' end;
end;
$$;

-- `p_hash` nulo = revocar. Uno nuevo sustituye al anterior: el viejo deja de abrir.
create function public.poner_seguimiento(p_agencia uuid, p_trabajador uuid, p_hash text)
returns text
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  -- Revocar se puede siempre; un enlace NUEVO, solo a quien sigue de alta.
  update public.trabajadores_de_agencia
  set seguimiento_hash = p_hash, seguimiento_creado_at = case when p_hash is null then null else now() end
  where id = p_trabajador and agencia_id = p_agencia and (p_hash is null or de_baja_at is null);
  if found then
    return 'hecho';
  end if;
  return case when exists (select 1 from public.trabajadores_de_agencia
                           where id = p_trabajador and agencia_id = p_agencia)
              then 'de_baja' else 'no_es_suyo' end;
end;
$$;

revoke all on function public.alta_de_trabajador(uuid, text, text), public.baja_de_trabajador(uuid, uuid),
  public.poner_seguimiento(uuid, uuid, text) from public, anon, authenticated;
grant execute on function public.alta_de_trabajador(uuid, text, text), public.baja_de_trabajador(uuid, uuid),
  public.poner_seguimiento(uuid, uuid, text) to service_role;

-- ═══════════════ 4. la atribución: el trabajador, dentro de la fila de su agencia

alter table public.altas_atribuidas
  -- SIN clave ajena y con el nombre copiado, como los nombres de 0057.
  add column trabajador_id uuid,
  add column trabajador_nombre text,
  -- El plan elegido en el alta (ver la cabecera). Nulo en las que ya había.
  add column plan_al_llegar public.plan_tipo,
  add constraint trabajador_con_su_nombre check ((trabajador_id is null) = (trabajador_nombre is null));

comment on column public.altas_atribuidas.trabajador_id is
  'Qué trabajador de la agencia trajo el salón (0077). La atribución es de la AGENCIA (referido_id).';

alter table public.codigos_que_no_valieron
  add column cuenta_id uuid,
  add column cuenta_nombre text,
  drop constraint codigos_que_no_valieron_motivo_check,
  add constraint codigos_que_no_valieron_motivo_check
    check (motivo in ('no_existe', 'pendiente', 'rechazado', 'trabajador_de_baja'));

-- La misma firma: el backend desplegado la sigue llamando igual.
create or replace function public.atribuir_alta(p_cuenta uuid, p_codigo text)
returns text
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  -- Tal como llegó, sin tocar (solo acotado: viene de un formulario público).
  v_llegado text := left(p_codigo, 64);
  v_buscado text := public.codigo_de_alta_normalizado(p_codigo);
  v_referido public.referidos;
  v_trabajador public.trabajadores_de_agencia;
  v_nombre text;
  v_global numeric(5, 2);
  v_escritas int;
begin
  if v_buscado = '' then
    return 'sin_codigo';
  end if;

  -- Para dejar constancia de a qué cuenta llegó un código que no vale (0077).
  select a.nombre into v_nombre from public.accounts a where a.id = p_cuenta;

  -- `for share`: un rechazo, o un cambio de su %, espera a que esta atribución termine.
  select * into v_referido from public.referidos r
  where upper(btrim(r.codigo)) = v_buscado
  for share;

  if not found then
    -- ¿El de un trabajador de una agencia? (0077). Una baja espera a que esto termine.
    select * into v_trabajador from public.trabajadores_de_agencia t
    where upper(btrim(t.codigo)) = v_buscado
    for share;
    if not found then
      insert into public.codigos_que_no_valieron (codigo_llegado, motivo, cuenta_id, cuenta_nombre)
      values (v_llegado, 'no_existe', p_cuenta, v_nombre);
      return 'no_existe';
    end if;
    if v_trabajador.de_baja_at is not null then
      insert into public.codigos_que_no_valieron (codigo_llegado, motivo, cuenta_id, cuenta_nombre)
      values (v_llegado, 'trabajador_de_baja', p_cuenta, v_nombre);
      return 'trabajador_de_baja';
    end if;
    -- LA ATRIBUCIÓN ES DE SU AGENCIA: la única contraparte.
    select * into v_referido from public.referidos r where r.id = v_trabajador.agencia_id for share;
  end if;

  if v_referido.estado::text <> 'aprobado' then
    insert into public.codigos_que_no_valieron (codigo_llegado, motivo, cuenta_id, cuenta_nombre)
    values (v_llegado, v_referido.estado::text, p_cuenta, v_nombre);
    return v_referido.estado::text;
  end if;

  if not exists (select 1 from public.accounts a where a.id = p_cuenta) then
    raise exception 'atribuir_alta: la cuenta % no existe', p_cuenta;
  end if;

  -- EL PORCENTAJE QUE SE LE PROMETIÓ HOY a quien cobra (la agencia), en la misma fila (0075).
  select c.comision_referido_pct into v_global from public.plataforma_config c where c.id;

  -- Una por cuenta: si ya la tiene, gana la primera y no se escribe nada.
  insert into public.altas_atribuidas (referido_id, cuenta_id, cuenta_nombre, codigo_llegado, pct, pct_de_donde,
                                       trabajador_id, trabajador_nombre, plan_al_llegar)
  values (v_referido.id, p_cuenta, v_nombre, v_llegado,
          coalesce(v_referido.comision_pct, v_global),
          case when v_referido.comision_pct is null then 'global' else 'propio' end,
          v_trabajador.id, v_trabajador.nombre,
          (select coalesce(a.pending_plan, a.plan) from public.accounts a where a.id = p_cuenta))
  on conflict on constraint una_atribucion_por_cuenta do nothing;
  get diagnostics v_escritas = row_count;
  return case when v_escritas = 1 then 'atribuida' else 'ya_atribuida' end;
end;
$$;

revoke all on function public.atribuir_alta(uuid, text) from public, anon, authenticated;
grant execute on function public.atribuir_alta(uuid, text) to service_role;

-- ¿Vale este código? Para avisar en el formulario ANTES de enviar. La misma
-- comparación y las mismas condiciones que `atribuir_alta`. Solo sí o no.
create function public.codigo_de_alta_vale(p_codigo text)
returns boolean
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  with b as (select public.codigo_de_alta_normalizado(p_codigo) as c)
  select (select c from b) <> '' and (
    exists (select 1 from public.referidos r, b
            where upper(btrim(r.codigo)) = b.c and r.estado = 'aprobado')
    or exists (select 1 from public.trabajadores_de_agencia t
               join public.referidos r on r.id = t.agencia_id, b
               where upper(btrim(t.codigo)) = b.c and t.de_baja_at is null and r.estado = 'aprobado'));
$$;

revoke all on function public.codigo_de_alta_vale(text) from public, anon, authenticated;
grant execute on function public.codigo_de_alta_vale(text) to service_role;

-- ═══════════════ 5. lo que se ve: el desglose por trabajador

-- Los salones que cuentan, como en todo lo demás (0070/0075): la cuenta existe
-- o consta como borrada.
create function public.trabajadores_de_una_agencia(p_agencia uuid)
returns table (id uuid, nombre text, codigo text, creado_at timestamptz, de_baja_at timestamptz,
               con_seguimiento boolean, seguimiento_creado_at timestamptz, salones bigint, pagando bigint)
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select t.id, t.nombre, t.codigo, t.creado_at, t.de_baja_at, t.seguimiento_hash is not null,
         t.seguimiento_creado_at,
         (select count(*) from public.altas_atribuidas a
          where a.referido_id = p_agencia and a.trabajador_id = t.id
            and (exists (select 1 from public.accounts c where c.id = a.cuenta_id)
                 or exists (select 1 from public.cuentas_borradas b where b.account_id = a.cuenta_id))),
         (select count(*) from public.altas_atribuidas a
          where a.referido_id = p_agencia and a.trabajador_id = t.id
            and exists (select 1 from public.subscriptions s where s.account_id = a.cuenta_id and s.estado = 'active'))
  from public.trabajadores_de_agencia t
  where t.agencia_id = p_agencia
  order by t.de_baja_at nulls first, t.creado_at, t.id;
$$;

-- Lo devengado por los salones de cada trabajador, por moneda, de las MISMAS
-- filas que el total. `trabajador_id` nulo: lo que trajo la agencia con su código.
create function public.devengado_por_trabajador(p_agencia uuid)
returns table (trabajador_id uuid, moneda text, neto bigint)
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select a.trabajador_id, c.moneda, sum(c.comision_centimos)::bigint
  from public.comisiones c
  join public.altas_atribuidas a on a.cuenta_id = c.cuenta_id and a.referido_id = c.referido_id
  where c.referido_id = p_agencia
  group by a.trabajador_id, c.moneda
  order by a.trabajador_id nulls first, c.moneda;
$$;

-- LA PÁGINA DEL ENLACE DE SEGUIMIENTO. Solo esto: el nombre del salón, el de su
-- plan, desde cuándo y si paga, está en prueba o se cayó. NI UN IMPORTE NI UN
-- PORCENTAJE: lo vigila `test_seguimiento_sin_dinero.py`, mirando también las
-- columnas que devuelve esta función. Sin salones, una fila con el salón nulo
-- (para decir de quién es la página). Con la agencia ya no aprobada, o el
-- trabajador de baja, nada. `plan_al_llegar`, solo si no es el de hoy.
create function public.seguimiento_de_un_trabajador(p_hash text)
returns table (trabajador text, agencia text, salon text, plan text, plan_al_llegar text, desde timestamptz,
               estado text)
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select t.nombre, r.nombre, coalesce(c.nombre, a.cuenta_nombre), c.plan::text,
         case when c.plan is not null and a.plan_al_llegar is distinct from c.plan then a.plan_al_llegar::text end,
         a.atribuida_el,
         case
           when a.cuenta_id is null then null
           when exists (select 1 from public.subscriptions s where s.account_id = a.cuenta_id and s.estado = 'active') then 'paga'
           when exists (select 1 from public.subscriptions s where s.account_id = a.cuenta_id and s.estado = 'trialing') then 'en_prueba'
           else 'caido'
         end
  from public.trabajadores_de_agencia t
  join public.referidos r on r.id = t.agencia_id and r.estado = 'aprobado'
  left join public.altas_atribuidas a on a.trabajador_id = t.id and a.referido_id = t.agencia_id
    and (exists (select 1 from public.accounts x where x.id = a.cuenta_id)
         or exists (select 1 from public.cuentas_borradas b where b.account_id = a.cuenta_id))
  left join public.accounts c on c.id = a.cuenta_id
  where p_hash ~ '^[0-9a-f]{64}$' and t.seguimiento_hash = p_hash and t.de_baja_at is null
  order by a.atribuida_el desc nulls last;
$$;

revoke all on function public.trabajadores_de_una_agencia(uuid), public.devengado_por_trabajador(uuid),
  public.seguimiento_de_un_trabajador(text) from public, anon, authenticated;
grant execute on function public.trabajadores_de_una_agencia(uuid), public.devengado_por_trabajador(uuid),
  public.seguimiento_de_un_trabajador(text) to service_role;

-- El portal: además, qué trabajador trajo cada salón. Cambia lo que devuelve,
-- así que se rehace; el backend desplegado ignora la columna nueva.
drop function public.portal_del_referido(uuid);
create function public.portal_del_referido(p_referido uuid)
returns table (cuenta_nombre text, atribuida_el timestamptz, situacion text, trabajador_nombre text)
language sql
stable
set search_path = public, pg_temp
as $$
  select a.cuenta_nombre, a.atribuida_el,
    case
      when not exists (select 1 from public.accounts c where c.id = a.cuenta_id) then 'cuenta_borrada'
      when exists (select 1 from public.subscriptions s where s.account_id = a.cuenta_id and s.estado = 'active') then 'paga'
      when exists (select 1 from public.subscriptions s where s.account_id = a.cuenta_id and s.estado = 'trialing') then 'en_prueba'
      when exists (select 1 from public.subscriptions s where s.account_id = a.cuenta_id
                   and s.estado in ('past_due', 'unpaid', 'incomplete')) then 'pago_pendiente'
      else 'no_paga'
    end,
    a.trabajador_nombre
  from public.altas_atribuidas a
  where a.referido_id = p_referido
    and (exists (select 1 from public.accounts c where c.id = a.cuenta_id)
         or exists (select 1 from public.cuentas_borradas b where b.account_id = a.cuenta_id))
  order by a.atribuida_el desc;
$$;

revoke all on function public.portal_del_referido(uuid) from public, anon, authenticated;
grant execute on function public.portal_del_referido(uuid) to service_role;

-- Con qué % van HOY los salones de cada referido (para su ficha).
create function public.porcentajes_de_sus_salones()
returns table (referido_id uuid, pct numeric, salones bigint)
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select a.referido_id, public.porcentaje_del_salon(a.cuenta_id, clock_timestamp()) as pct, count(*)
  from public.altas_atribuidas a
  where exists (select 1 from public.accounts c where c.id = a.cuenta_id)
     or exists (select 1 from public.cuentas_borradas b where b.account_id = a.cuenta_id)
  group by 1, 2
  order by 1, 2;
$$;

revoke all on function public.porcentajes_de_sus_salones() from public, anon, authenticated;
grant execute on function public.porcentajes_de_sus_salones() to service_role;

-- ═══════════════ 6. el mínimo, desde el panel

create table public.cambios_de_minimo (
  id bigint generated always as identity primary key,
  moneda text not null check (moneda ~ '^[a-z]{3}$'),
  -- Nulo: esa moneda no tenía mínimo (se fija por primera vez).
  centimos_antes bigint check (centimos_antes > 0),
  centimos_despues bigint not null check (centimos_despues > 0),
  motivo text not null check (char_length(btrim(motivo)) >= 10),
  hecho_por uuid not null,
  hecho_por_email text not null,
  hecho_at timestamptz not null default now(),
  constraint un_cambio_de_minimo_cambia_algo check (centimos_antes is distinct from centimos_despues)
);

comment on table public.cambios_de_minimo is
  'Cada cambio del mínimo de pago a los referidos (0077): quién, cuándo, de cuánto a cuánto y por qué. '
  'No se puede editar ni borrar.';

revoke all on public.cambios_de_minimo from public, anon, authenticated, service_role;
grant select on public.cambios_de_minimo to service_role;
alter table public.cambios_de_minimo enable row level security;

create trigger cambios_de_minimo_sin_update_ni_delete
  before update or delete on public.cambios_de_minimo
  for each row execute function public.las_comisiones_no_se_tocan();
create trigger cambios_de_minimo_sin_truncate
  before truncate on public.cambios_de_minimo
  for each statement execute function public.las_comisiones_no_se_tocan();

create function public.cambiar_minimo_de_pago(p_moneda text, p_centimos bigint, p_motivo text, p_admin uuid,
                                              p_admin_email text)
returns table (moneda text, centimos_antes bigint, centimos_despues bigint)
language plpgsql
security definer
set search_path = public, pg_temp
as $$
#variable_conflict use_column
declare
  v_antes bigint;
begin
  if p_motivo is null or char_length(btrim(p_motivo)) < 10 then
    raise exception 'cambiar_minimo_de_pago: hace falta un motivo escrito' using errcode = '22023';
  end if;
  if p_moneda is null or p_moneda !~ '^[a-z]{3}$' or p_centimos is null or p_centimos <= 0 then
    raise exception 'cambiar_minimo_de_pago: moneda o importe inválidos' using errcode = '22023';
  end if;
  if p_admin is null or coalesce(btrim(p_admin_email), '') = '' then
    raise exception 'cambiar_minimo_de_pago: falta quién lo hace' using errcode = '22023';
  end if;
  -- Un cambio de mínimo a la vez por moneda.
  perform pg_advisory_xact_lock(hashtextextended('minimo:' || p_moneda, 0));
  select m.centimos into v_antes from public.minimos_de_pago m where m.moneda = p_moneda;
  if v_antes = p_centimos then
    raise exception 'cambiar_minimo_de_pago: ya es ese' using errcode = '22023';
  end if;
  insert into public.minimos_de_pago (moneda, centimos, por_que, actualizado_at)
  values (p_moneda, p_centimos, btrim(p_motivo), now())
  on conflict (moneda) do update
    set centimos = excluded.centimos, por_que = excluded.por_que, actualizado_at = excluded.actualizado_at;
  insert into public.cambios_de_minimo (moneda, centimos_antes, centimos_despues, motivo, hecho_por, hecho_por_email)
  values (p_moneda, v_antes, p_centimos, btrim(p_motivo), p_admin, p_admin_email);
  moneda := p_moneda; centimos_antes := v_antes; centimos_despues := p_centimos;
  return next;
end;
$$;

revoke all on function public.cambiar_minimo_de_pago(text, bigint, text, uuid, text) from public, anon, authenticated;
grant execute on function public.cambiar_minimo_de_pago(text, bigint, text, uuid, text) to service_role;

-- ═══════════════ 7. pagar por debajo del mínimo

alter table public.pagos_a_referidos
  add column minimo_saltado_centimos bigint,
  add column minimo_saltado_motivo text,
  add constraint saltar_el_minimo_con_motivo check (
    (minimo_saltado_centimos is null and minimo_saltado_motivo is null)
    or (minimo_saltado_centimos > 0 and char_length(btrim(minimo_saltado_motivo)) >= 10));

comment on column public.pagos_a_referidos.minimo_saltado_motivo is
  'Si se pagó por debajo del mínimo (0077): por qué, escrito por el owner. El mínimo saltado, al lado.';

-- El cuerpo de `ordenar_pago` (0076), con UNA diferencia: por debajo del mínimo
-- se ordena si llega un motivo escrito, y queda en la orden.
create function public.ordenar_pago_o_saltar_el_minimo(
  p_referido uuid,
  p_moneda text,
  p_importe bigint,       -- lo que vio y confirmó quien lo ordena
  p_hasta bigint,         -- la última comisión que vio en la confirmación
  p_destino text,
  p_admin uuid,
  p_admin_email text,
  p_saltar_minimo text    -- nulo: el mínimo manda (lo de 0076)
)
returns table (resultado text, pago_id uuid)
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_deuda record;
  v_minimo bigint;
  v_saltado bigint;
  v_desde bigint;
  v_periodo record;
  v_nombre text;
  v_id uuid;
begin
  if p_admin is null or coalesce(btrim(p_admin_email), '') = '' then
    raise exception 'ordenar_pago: un pago lo ordena una persona' using errcode = '22023';
  end if;
  if p_importe is null or p_importe <= 0 or p_hasta is null then
    raise exception 'ordenar_pago: importe o comisiones inválidos' using errcode = '22023';
  end if;
  if p_saltar_minimo is not null and char_length(btrim(p_saltar_minimo)) < 10 then
    raise exception 'ordenar_pago: saltarse el mínimo necesita un motivo escrito' using errcode = '22023';
  end if;

  -- Un pago a la vez por referido y moneda: dos clics se esperan.
  perform pg_advisory_xact_lock(hashtextextended('pago:' || p_referido::text || ':' || p_moneda, 0));

  -- Una orden sin resolver bloquea otra: puede que ese dinero ya haya salido.
  if exists (select 1 from public.pagos_que_cuentan(p_referido) x
             where x.moneda = p_moneda and x.estado = 'pendiente') then
    resultado := 'hay_uno_pendiente'; return next; return;
  end if;

  select * into v_deuda from public.deuda_con_referido(p_referido, p_moneda, p_hasta);
  -- Lo que confirmó tiene que ser lo que se debe AHORA con esas comisiones.
  if v_deuda.debe is distinct from p_importe or v_deuda.hasta_comision_id is distinct from p_hasta then
    resultado := 'ha_cambiado'; return next; return;
  end if;

  select m.centimos into v_minimo from public.minimos_de_pago m where m.moneda = p_moneda;
  if v_minimo is null then
    resultado := 'sin_minimo'; return next; return;
  end if;
  if p_importe < v_minimo then
    if p_saltar_minimo is null then
      resultado := 'por_debajo_del_minimo'; return next; return;
    end if;
    v_saltado := v_minimo;
  end if;

  -- Qué cubre: desde donde acabó el último pago que cuenta, hasta lo confirmado.
  select coalesce(max(x.hasta_comision_id), 0) into v_desde
  from public.pagos_que_cuentan(p_referido) x where x.moneda = p_moneda and x.estado <> 'rechazado';
  select min(ocurrido_at) as desde, max(ocurrido_at) as hasta into v_periodo
  from public.comisiones
  where referido_id = p_referido and moneda = p_moneda and id > v_desde and id <= p_hasta;
  if v_periodo.desde is null then
    -- Lo que se debe viene de comisiones ya cubiertas (p. ej., un pago revertido).
    select min(ocurrido_at) as desde, max(ocurrido_at) as hasta into v_periodo
    from public.comisiones where referido_id = p_referido and moneda = p_moneda and id <= p_hasta;
    v_desde := 0;
  end if;

  select nombre into v_nombre from public.referidos where id = p_referido;

  v_id := gen_random_uuid();
  insert into public.pagos_a_referidos (id, referido_id, referido_nombre, destino_stripe, moneda, importe_centimos,
    desde_comision_excluido, hasta_comision_id, periodo_desde, periodo_hasta, ordenado_por, ordenado_por_email,
    minimo_saltado_centimos, minimo_saltado_motivo)
  values (v_id, p_referido, coalesce(v_nombre, '?'), p_destino, p_moneda, p_importe,
    v_desde, p_hasta, v_periodo.desde, v_periodo.hasta, p_admin, p_admin_email,
    v_saltado, case when v_saltado is not null then btrim(p_saltar_minimo) end);

  resultado := 'ordenado'; pago_id := v_id; return next;
end;
$$;

-- La de 0076, con su firma: la misma, sin saltarse nada. Un solo cuerpo.
create or replace function public.ordenar_pago(
  p_referido uuid, p_moneda text, p_importe bigint, p_hasta bigint, p_destino text, p_admin uuid, p_admin_email text
)
returns table (resultado text, pago_id uuid)
language sql
security definer
set search_path = public, pg_temp
as $$
  select * from public.ordenar_pago_o_saltar_el_minimo(
    p_referido, p_moneda, p_importe, p_hasta, p_destino, p_admin, p_admin_email, null);
$$;

revoke all on function public.ordenar_pago_o_saltar_el_minimo(uuid, text, bigint, bigint, text, uuid, text, text)
  from public, anon, authenticated;
grant execute on function public.ordenar_pago_o_saltar_el_minimo(uuid, text, bigint, bigint, text, uuid, text, text)
  to service_role;
revoke all on function public.ordenar_pago(uuid, text, bigint, bigint, text, uuid, text) from public, anon, authenticated;
grant execute on function public.ordenar_pago(uuid, text, bigint, bigint, text, uuid, text) to service_role;

commit;
