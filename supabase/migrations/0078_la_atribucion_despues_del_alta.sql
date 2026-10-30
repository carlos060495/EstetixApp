-- 0078 · La atribución que no se hizo en el alta: dos caminos para arreglarla.
--
-- Carlos, 07-10-2026. Un vendedor visita un salón y le deja su código; el salón
-- se da de alta y no lo escribe (o lo escribe mal). Hasta aquí ese vendedor no
-- cobraba nunca, y la única forma de arreglarlo era escribir en la base a mano,
-- que es justo lo que no hacemos. Son dos caminos distintos, con dos pruebas
-- distintas, y por eso con dos reglas distintas de cuándo empieza a devengar.
--
-- ═══ 1. EL SALÓN ASIGNA EL CÓDIGO DESPUÉS DEL ALTA (`asignar_su_vendedor`) ═══
--
--   · Solo si la cuenta NO tiene ya una atribución, y solo durante los 30 días
--     siguientes a su alta (`alta_de_la_cuenta`). Sin plazo, cualquier salón de
--     hace años podría regalarle una comisión perpetua a un conocido.
--   · Vale el código de un referido aprobado o el de un trabajador de agencia de
--     alta (con su agencia aprobada): la misma comparación y las mismas
--     condiciones que el alta (`vendedor_de_un_codigo`). Con el de un trabajador
--     la atribución es DE SU AGENCIA y queda apuntado quién lo trajo (0077).
--   · Antes de confirmar se le enseña el NOMBRE del vendedor, y la confirmación
--     lleva el vendedor que vio (`p_referido`, `p_trabajador`): si el código ya
--     no casa con esa persona, no se asigna (`ha_cambiado`).
--   · UNA SOLA VEZ: confirmada, la cuenta ya tiene atribución y el campo no
--     vuelve a salir. El porcentaje se congela en ese momento (0075).
--   · DEVENGA SOBRE LOS COBROS QUE ENTREN A PARTIR DE LA ASIGNACIÓN:
--     `devenga_desde` = el instante de la asignación, y `devengar_comision` no
--     devenga un cobro anterior (`antes_de_la_atribucion`). Lo cobrado antes no
--     genera nada: «Se le empieza a pagar desde el siguiente recibo; el que ya
--     se cobró no cuenta.»
--   · Queda quién del salón lo asignó (`asignada_por`, su correo copiado) y
--     cuándo (`atribuida_el`).
--
-- ═══ 2. EL OWNER REPESCA UNA ATRIBUCIÓN (`repescar_atribucion`) ═══
--
--   · Solo sobre una cuenta cuyo alta dejó apuntado un código que no valía
--     (`codigos_que_no_valieron` con su cuenta, 0077): es la prueba de que el
--     salón quiso poner un código. Sin ese rastro no hay repesca
--     (`sin_rastro`), y la fila queda enlazada a él (`codigo_que_no_valio_id`,
--     con clave ajena: los dos son registros que no se borran nunca).
--   · Solo si la cuenta no tiene ya una atribución, y dentro de los 90 días
--     siguientes a su alta. Motivo escrito obligatorio; queda quién (el admin y
--     su correo), cuándo y desde qué código inválido.
--   · DEVENGA DESDE EL ALTA DE LA CUENTA: `devenga_desde` = el alta. Aquí la
--     intención estaba escrita en el propio formulario: el error de tecleo no
--     es culpa del vendedor. Los cobros ya ocurridos los devenga el backend con
--     LA MISMA función de siempre (`devengar_comision` y `ajustar_comision`,
--     vía `core/comisiones.devengar_lo_cobrado`): el cálculo no cambia y las
--     devoluciones que ya hubiera se corrigen igual que hoy.
--   · El porcentaje es el que el referido tenga el día de la repesca, congelado
--     desde ese momento: se escribe en la atribución y `porcentaje_del_salon`
--     lo devuelve también para los cobros pasados (no había otro).
--   · «Solo el owner» lo impone el backend (la ruta); aquí la función es solo
--     de `service_role`, como todas las de dinero.
--
-- ═══ EL ALTA DE UNA CUENTA ═══
--
-- `alta_de_la_cuenta` = la fecha más antigua entre la cuenta y sus centros. Las
-- cuentas de antes de la 0039 nacieron con la fecha de ESA migración, no con la
-- del alta: con `accounts.created_at` a pelo, un salón de hace años parecería
-- recién llegado durante un mes. Sus centros sí conservan la suya.
--
-- ═══ LO QUE NO SE TOCA ═══
--
-- El cálculo de la comisión, el pago a Stripe, la reversión de transferencias y
-- el borrado de los 90 días. `devengar_comision` se rehace con la MISMA firma y
-- el MISMO cuerpo que en la 0075 más una sola guarda antes de calcular (lo
-- vigila `test_la_atribucion_despues_del_alta_base_real.py`, comparando los
-- dos cuerpos). Las altas de siempre siguen igual: `origen` = «alta» y
-- `devenga_desde` nulo (desde el alta, que es cuando se atribuyeron).
--
-- ═══ CAMBIARLA DESPUÉS ═══
--
-- Una atribución, venga del camino que venga, es inmutable (0070): ni el salón
-- ni el owner la cambian por aquí. Un salón con atribución no recibe otra por
-- ninguno de los dos caminos (`una_atribucion_por_cuenta` + candado por cuenta).
--
-- COLUMNAS OBLIGATORIAS: `altas_atribuidas.origen` nace `not null` con valor por
-- defecto «alta», que es lo que son todas las que hay y lo que escribe
-- `atribuir_alta` (que no cambia). Las demás columnas nuevas admiten nulo y
-- solo las escriben las funciones de aquí. `portal_del_referido` devuelve dos
-- columnas más, que el backend desplegado ignora.
--
-- ═══ ORDEN DE DESPLIEGUE: 0077 → 0078 → BACKEND + FRONTEND (a la vez) ═══
--
-- Esta migración va ENCIMA de la 0077 (usa `codigos_que_no_valieron.cuenta_id`,
-- `trabajadores_de_agencia` y `codigo_de_alta_normalizado`). Es compatible con
-- el backend desplegado: nada de lo que ya llama cambia de firma ni de
-- respuesta, salvo `devengar_comision`, que puede contestar además
-- `antes_de_la_atribucion` (el backend lo apunta en el registro y sigue). Ningún
-- paso en Stripe.
--
-- Probado en local, contra la base de verdad:
-- `test_la_atribucion_despues_del_alta_base_real.py`.

begin;

-- ═══════════════ 1. la atribución sabe de dónde vino y desde cuándo devenga

alter table public.altas_atribuidas
  add column origen text not null default 'alta' check (origen in ('alta', 'salon', 'repesca')),
  -- Nulo: desde el alta (las de siempre). Lo mira `devengar_comision`.
  add column devenga_desde timestamptz,
  -- Quién la hizo: el usuario del salón o el admin del panel, con su correo copiado.
  -- SIN clave ajena, como todo nombre de un registro (0057).
  add column asignada_por uuid,
  add column asignada_por_email text,
  -- Solo la repesca: por qué, y desde qué código que no valió.
  add column motivo text,
  add column codigo_que_no_valio_id bigint references public.codigos_que_no_valieron (id) on delete restrict,
  add constraint cada_origen_con_lo_suyo check (
    (origen = 'alta' and devenga_desde is null and asignada_por is null and asignada_por_email is null
       and motivo is null and codigo_que_no_valio_id is null)
    or (origen = 'salon' and devenga_desde is not null and asignada_por is not null
       and coalesce(btrim(asignada_por_email), '') <> '' and motivo is null and codigo_que_no_valio_id is null)
    or (origen = 'repesca' and devenga_desde is not null and asignada_por is not null
       and coalesce(btrim(asignada_por_email), '') <> '' and char_length(btrim(motivo)) >= 10
       and codigo_que_no_valio_id is not null));

-- Un código que no valió repesca, como mucho, una atribución.
create unique index altas_atribuidas_una_repesca_por_codigo
  on public.altas_atribuidas (codigo_que_no_valio_id) where codigo_que_no_valio_id is not null;

comment on column public.altas_atribuidas.origen is
  'alta | salon | repesca (0078). salon: la puso el propio salón en sus 30 primeros días; '
  'repesca: el owner, desde un código que no valió en el alta.';
comment on column public.altas_atribuidas.devenga_desde is
  'Desde qué instante devenga (0078). Nulo: desde el alta. salon: la asignación; repesca: el alta de la cuenta.';

-- ═══════════════ 2. el alta de una cuenta, y los plazos

create function public.alta_de_la_cuenta(p_cuenta uuid)
returns timestamptz
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  -- La más antigua entre la cuenta y sus centros: ver la cabecera.
  select least(a.created_at, (select min(s.created_at) from public.salons s where s.account_id = a.id))
  from public.accounts a where a.id = p_cuenta;
$$;

revoke all on function public.alta_de_la_cuenta(uuid) from public, anon, authenticated;
grant execute on function public.alta_de_la_cuenta(uuid) to service_role;

-- Los dos plazos, en un solo sitio.
create function public.plazo_para_atribuir(p_origen text)
returns interval
language sql
immutable
set search_path = public, pg_temp
as $$
  select case p_origen when 'salon' then interval '30 days' when 'repesca' then interval '90 days' end;
$$;

revoke all on function public.plazo_para_atribuir(text) from public, anon, authenticated;
grant execute on function public.plazo_para_atribuir(text) to service_role;

-- ═══════════════ 3. de quién es un código (para enseñar su nombre)

-- La misma comparación y las mismas condiciones que `atribuir_alta` (0077):
-- un referido aprobado, o un trabajador de alta de una agencia aprobada. Nada
-- si no vale. `referido_id` es SIEMPRE quien cobra (la agencia, para un
-- trabajador).
create function public.vendedor_de_un_codigo(p_codigo text)
returns table (referido_id uuid, referido_nombre text, tipo text, trabajador_id uuid, trabajador_nombre text)
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  with b as (select public.codigo_de_alta_normalizado(p_codigo) as c)
  select r.id, r.nombre, r.tipo, null::uuid, null::text
  from public.referidos r, b
  where b.c <> '' and upper(btrim(r.codigo)) = b.c and r.estado = 'aprobado'
  union all
  select r.id, r.nombre, r.tipo, t.id, t.nombre
  from public.trabajadores_de_agencia t
  join public.referidos r on r.id = t.agencia_id, b
  where b.c <> '' and upper(btrim(t.codigo)) = b.c and t.de_baja_at is null and r.estado = 'aprobado';
$$;

revoke all on function public.vendedor_de_un_codigo(text) from public, anon, authenticated;
grant execute on function public.vendedor_de_un_codigo(text) to service_role;

-- ═══════════════ 4. ¿puede el salón asignar su vendedor?

create function public.puede_asignar_su_vendedor(p_cuenta uuid)
returns table (resultado text, hasta timestamptz)
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select case
           when public.alta_de_la_cuenta(p_cuenta) is null then 'sin_cuenta'
           when exists (select 1 from public.altas_atribuidas a where a.cuenta_id = p_cuenta) then 'ya_atribuida'
           when now() > public.alta_de_la_cuenta(p_cuenta) + public.plazo_para_atribuir('salon') then 'fuera_de_plazo'
           else 'puede'
         end,
         public.alta_de_la_cuenta(p_cuenta) + public.plazo_para_atribuir('salon');
$$;

revoke all on function public.puede_asignar_su_vendedor(uuid) from public, anon, authenticated;
grant execute on function public.puede_asignar_su_vendedor(uuid) to service_role;

-- ═══════════════ 5. el salón asigna su vendedor

create function public.asignar_su_vendedor(
  p_cuenta uuid,
  p_codigo text,
  p_referido uuid,      -- el que se le enseñó (quien cobra)
  p_trabajador uuid,    -- el trabajador que se le enseñó, o nulo
  p_usuario uuid,
  p_usuario_email text
)
returns table (resultado text, referido_nombre text, trabajador_nombre text, devenga_desde timestamptz)
language plpgsql
security definer
set search_path = public, pg_temp
as $$
#variable_conflict use_column
declare
  v_alta timestamptz;
  v_vendedor record;
  v_referido public.referidos;
  v_nombre text;
  v_global numeric(5, 2);
  v_ahora timestamptz;
  v_escritas int;
begin
  if p_usuario is null or coalesce(btrim(p_usuario_email), '') = '' then
    raise exception 'asignar_su_vendedor: falta quién lo hace' using errcode = '22023';
  end if;

  -- Una atribución a la vez por cuenta, venga del camino que venga.
  perform pg_advisory_xact_lock(hashtextextended('atribucion:' || p_cuenta::text, 0));

  v_alta := public.alta_de_la_cuenta(p_cuenta);
  if v_alta is null then
    raise exception 'asignar_su_vendedor: la cuenta % no existe', p_cuenta using errcode = '22023';
  end if;
  if exists (select 1 from public.altas_atribuidas a where a.cuenta_id = p_cuenta) then
    resultado := 'ya_atribuida'; return next; return;
  end if;
  -- PASADOS 30 DÍAS DESDE EL ALTA, NO.
  if now() > v_alta + public.plazo_para_atribuir('salon') then
    resultado := 'fuera_de_plazo'; return next; return;
  end if;

  select * into v_vendedor from public.vendedor_de_un_codigo(p_codigo) limit 1;
  if not found then
    resultado := 'no_vale'; return next; return;
  end if;
  -- LA PERSONA QUE SE LE ENSEÑÓ: si el código ya no casa con ella, no se asigna.
  if v_vendedor.referido_id is distinct from p_referido or v_vendedor.trabajador_id is distinct from p_trabajador then
    resultado := 'ha_cambiado'; return next; return;
  end if;

  -- Como en el alta: un rechazo, o un cambio de su %, espera a que esto termine.
  select * into v_referido from public.referidos r where r.id = v_vendedor.referido_id for share;
  if v_vendedor.trabajador_id is not null then
    perform 1 from public.trabajadores_de_agencia t where t.id = v_vendedor.trabajador_id for share;
  end if;

  select a.nombre into v_nombre from public.accounts a where a.id = p_cuenta;
  select c.comision_referido_pct into v_global from public.plataforma_config c where c.id;
  -- La hora DE VERDAD de la escritura: un cobro de antes de este instante no devenga.
  v_ahora := clock_timestamp();

  insert into public.altas_atribuidas (referido_id, cuenta_id, cuenta_nombre, codigo_llegado, pct, pct_de_donde,
                                       trabajador_id, trabajador_nombre, plan_al_llegar, atribuida_el,
                                       origen, devenga_desde, asignada_por, asignada_por_email)
  values (v_referido.id, p_cuenta, v_nombre, left(btrim(p_codigo), 64),
          coalesce(v_referido.comision_pct, v_global),
          case when v_referido.comision_pct is null then 'global' else 'propio' end,
          v_vendedor.trabajador_id, v_vendedor.trabajador_nombre,
          (select coalesce(a.pending_plan, a.plan) from public.accounts a where a.id = p_cuenta),
          v_ahora, 'salon', v_ahora, p_usuario, btrim(p_usuario_email))
  on conflict on constraint una_atribucion_por_cuenta do nothing;
  get diagnostics v_escritas = row_count;
  if v_escritas = 0 then
    resultado := 'ya_atribuida'; return next; return;
  end if;

  resultado := 'asignada'; referido_nombre := v_referido.nombre;
  trabajador_nombre := v_vendedor.trabajador_nombre; devenga_desde := v_ahora;
  return next;
end;
$$;

comment on function public.asignar_su_vendedor(uuid, text, uuid, uuid, uuid, text) is
  'El salón asigna el código de su vendedor en sus 30 primeros días (0078). Devenga desde la asignación. '
  'Devuelve asignada | ya_atribuida | fuera_de_plazo | no_vale | ha_cambiado.';

revoke all on function public.asignar_su_vendedor(uuid, text, uuid, uuid, uuid, text) from public, anon, authenticated;
grant execute on function public.asignar_su_vendedor(uuid, text, uuid, uuid, uuid, text) to service_role;

-- ═══════════════ 6. lo que el owner puede repescar

-- Los códigos que no valieron con su cuenta (0077), cuya cuenta sigue existiendo,
-- no tiene atribución y está dentro de los 90 días.
create function public.salones_para_repescar()
returns table (codigo_que_no_valio_id bigint, codigo_llegado text, motivo text, llegado_el timestamptz,
               cuenta_id uuid, cuenta_nombre text, alta timestamptz, hasta timestamptz)
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select q.id, q.codigo_llegado, q.motivo, q.llegado_el, q.cuenta_id, coalesce(a.nombre, q.cuenta_nombre),
         public.alta_de_la_cuenta(q.cuenta_id),
         public.alta_de_la_cuenta(q.cuenta_id) + public.plazo_para_atribuir('repesca')
  from public.codigos_que_no_valieron q
  join public.accounts a on a.id = q.cuenta_id
  where q.cuenta_id is not null
    and not exists (select 1 from public.altas_atribuidas x where x.cuenta_id = q.cuenta_id)
    and now() <= public.alta_de_la_cuenta(q.cuenta_id) + public.plazo_para_atribuir('repesca')
  order by q.llegado_el desc, q.id desc;
$$;

revoke all on function public.salones_para_repescar() from public, anon, authenticated;
grant execute on function public.salones_para_repescar() to service_role;

-- ═══════════════ 7. el owner repesca

create function public.repescar_atribucion(
  p_codigo_que_no_valio bigint,
  p_codigo text,          -- el código bueno, que escribe el owner
  p_referido uuid,        -- el vendedor que se le enseñó (quien cobra)
  p_trabajador uuid,
  p_motivo text,
  p_admin uuid,
  p_admin_email text
)
returns table (resultado text, cuenta_id uuid, referido_nombre text, trabajador_nombre text,
               devenga_desde timestamptz)
language plpgsql
security definer
set search_path = public, pg_temp
as $$
#variable_conflict use_column
declare
  v_rastro public.codigos_que_no_valieron;
  v_alta timestamptz;
  v_vendedor record;
  v_referido public.referidos;
  v_nombre text;
  v_global numeric(5, 2);
  v_escritas int;
begin
  if p_motivo is null or char_length(btrim(p_motivo)) < 10 then
    raise exception 'repescar_atribucion: hace falta un motivo escrito' using errcode = '22023';
  end if;
  if p_admin is null or coalesce(btrim(p_admin_email), '') = '' then
    raise exception 'repescar_atribucion: falta quién lo hace' using errcode = '22023';
  end if;

  -- SIN EL RASTRO DE UN CÓDIGO QUE NO VALIÓ, CON SU CUENTA, NO HAY REPESCA.
  select * into v_rastro from public.codigos_que_no_valieron q where q.id = p_codigo_que_no_valio;
  if not found or v_rastro.cuenta_id is null then
    resultado := 'sin_rastro'; return next; return;
  end if;
  cuenta_id := v_rastro.cuenta_id;

  perform pg_advisory_xact_lock(hashtextextended('atribucion:' || v_rastro.cuenta_id::text, 0));

  v_alta := public.alta_de_la_cuenta(v_rastro.cuenta_id);
  if v_alta is null then
    resultado := 'sin_cuenta'; return next; return;
  end if;
  if exists (select 1 from public.altas_atribuidas a where a.cuenta_id = v_rastro.cuenta_id) then
    resultado := 'ya_atribuida'; return next; return;
  end if;
  -- PASADOS 90 DÍAS DESDE EL ALTA, NO.
  if now() > v_alta + public.plazo_para_atribuir('repesca') then
    resultado := 'fuera_de_plazo'; return next; return;
  end if;

  select * into v_vendedor from public.vendedor_de_un_codigo(p_codigo) limit 1;
  if not found then
    resultado := 'no_vale'; return next; return;
  end if;
  if v_vendedor.referido_id is distinct from p_referido or v_vendedor.trabajador_id is distinct from p_trabajador then
    resultado := 'ha_cambiado'; return next; return;
  end if;

  select * into v_referido from public.referidos r where r.id = v_vendedor.referido_id for share;
  if v_vendedor.trabajador_id is not null then
    perform 1 from public.trabajadores_de_agencia t where t.id = v_vendedor.trabajador_id for share;
  end if;

  select a.nombre into v_nombre from public.accounts a where a.id = v_rastro.cuenta_id;
  -- EL % DE HOY, congelado desde ahora (vale también para los cobros pasados).
  select c.comision_referido_pct into v_global from public.plataforma_config c where c.id;

  insert into public.altas_atribuidas (referido_id, cuenta_id, cuenta_nombre, codigo_llegado, pct, pct_de_donde,
                                       trabajador_id, trabajador_nombre, plan_al_llegar, atribuida_el,
                                       origen, devenga_desde, asignada_por, asignada_por_email, motivo,
                                       codigo_que_no_valio_id)
  values (v_referido.id, v_rastro.cuenta_id, v_nombre, left(btrim(p_codigo), 64),
          coalesce(v_referido.comision_pct, v_global),
          case when v_referido.comision_pct is null then 'global' else 'propio' end,
          v_vendedor.trabajador_id, v_vendedor.trabajador_nombre,
          (select coalesce(a.pending_plan, a.plan) from public.accounts a where a.id = v_rastro.cuenta_id),
          clock_timestamp(), 'repesca', v_alta, p_admin, btrim(p_admin_email), btrim(p_motivo), v_rastro.id)
  on conflict on constraint una_atribucion_por_cuenta do nothing;
  get diagnostics v_escritas = row_count;
  if v_escritas = 0 then
    resultado := 'ya_atribuida'; return next; return;
  end if;

  resultado := 'repescada'; referido_nombre := v_referido.nombre;
  trabajador_nombre := v_vendedor.trabajador_nombre; devenga_desde := v_alta;
  return next;
end;
$$;

comment on function public.repescar_atribucion(bigint, text, uuid, uuid, text, uuid, text) is
  'El owner repesca la atribución de una cuenta cuyo alta dejó un código que no valió (0078). Devenga '
  'desde el alta. Devuelve repescada | sin_rastro | sin_cuenta | ya_atribuida | fuera_de_plazo | no_vale | ha_cambiado.';

revoke all on function public.repescar_atribucion(bigint, text, uuid, uuid, text, uuid, text)
  from public, anon, authenticated;
grant execute on function public.repescar_atribucion(bigint, text, uuid, uuid, text, uuid, text) to service_role;

-- De dónde vino la atribución de UNA cuenta viva (para volver a buscar los
-- cobros de una repesca a la que se le cayó Stripe a mitad).
create function public.atribucion_de_la_cuenta(p_cuenta uuid)
returns table (origen text, devenga_desde timestamptz)
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select a.origen, a.devenga_desde from public.altas_atribuidas a
  where a.cuenta_id = p_cuenta and exists (select 1 from public.accounts c where c.id = a.cuenta_id);
$$;

revoke all on function public.atribucion_de_la_cuenta(uuid) from public, anon, authenticated;
grant execute on function public.atribucion_de_la_cuenta(uuid) to service_role;

-- ═══════════════ 8. devengar: la de 0075, con una sola guarda más

-- EL CUERPO ES EL DE LA 0075, letra por letra, salvo el bloque marcado.
create or replace function public.devengar_comision(
  p_cliente_stripe text,
  p_factura text,
  p_pago text,
  p_cobro text,
  p_payment_intent text,
  p_moneda text,
  p_importe_cobrado bigint,
  p_base bigint,
  p_pagado_at timestamptz,
  p_evento text
)
returns table (resultado text, comision_id bigint)
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_cuenta uuid;
  v_nombre text;
  v_alta public.altas_atribuidas;
  v_pct numeric(5, 2);
  v_comision bigint;
  v_id bigint;
begin
  -- Lo que no es un cobro real no devenga, y se dice (no se traga).
  if p_cobro is null or p_cobro !~ '^(ch|py)_[A-Za-z0-9]+$' then
    raise exception 'devengar_comision: % no es un cobro de Stripe', p_cobro using errcode = '22023';
  end if;
  if p_importe_cobrado is null or p_importe_cobrado <= 0 then
    resultado := 'sin_dinero'; return next; return;
  end if;
  if p_base is null or p_base < 0 or p_base > p_importe_cobrado then
    raise exception 'devengar_comision: base % fuera de lo cobrado %', p_base, p_importe_cobrado
      using errcode = '22023';
  end if;

  -- De quién es el cobro: el cliente de Stripe de la cuenta.
  select a.id, a.nombre into v_cuenta, v_nombre
  from public.accounts a where a.stripe_customer_id = p_cliente_stripe;
  if v_cuenta is null then
    resultado := 'sin_cuenta'; return next; return;
  end if;

  select * into v_alta from public.altas_atribuidas where cuenta_id = v_cuenta;
  if not found then
    resultado := 'sin_referido'; return next; return;
  end if;

  -- ── 0078 ── UN COBRO ANTERIOR A `devenga_desde` NO DEVENGA (el salón que
  -- asignó su vendedor después del alta). Nulo: desde el alta, como siempre.
  if v_alta.devenga_desde is not null and coalesce(p_pagado_at, now()) < v_alta.devenga_desde then
    resultado := 'antes_de_la_atribucion'; return next; return;
  end if;
  -- ── fin 0078 ──

  -- Un aviso a la vez por cobro: dos reintentos simultáneos no devengan dos veces.
  perform pg_advisory_xact_lock(hashtextextended('comision:' || p_cobro, 0));
  select c.id into v_id from public.comisiones c
  where c.tipo = 'devengo' and (c.stripe_pago = p_pago or c.stripe_cobro = p_cobro);
  if found then
    resultado := 'ya_estaba'; comision_id := v_id; return next; return;
  end if;

  -- EL PORCENTAJE: el del salón en el instante en que Stripe cobró (ver la
  -- cabecera). Con el candado del salón: un cambio a la vez que esto espera.
  perform pg_advisory_xact_lock(hashtextextended('porcentaje:' || v_cuenta::text, 0));
  v_pct := public.porcentaje_del_salon(v_cuenta, coalesce(p_pagado_at, now()));

  -- Al céntimo, redondeando la mitad hacia arriba.
  v_comision := round(p_base::numeric * v_pct / 100);

  insert into public.comisiones (
    referido_id, cuenta_id, cuenta_nombre, tipo, motivo,
    stripe_cobro, stripe_factura, stripe_pago, stripe_payment_intent, stripe_evento,
    moneda, importe_cobrado_centimos, base_centimos, pct, comision_centimos, ocurrido_at)
  values (
    v_alta.referido_id, v_cuenta, coalesce(v_nombre, v_alta.cuenta_nombre), 'devengo', 'cobro',
    p_cobro, p_factura, p_pago, p_payment_intent, p_evento,
    lower(p_moneda), p_importe_cobrado, p_base, v_pct, v_comision, p_pagado_at)
  returning id into v_id;

  resultado := 'devengada'; comision_id := v_id; return next;
end;
$$;

comment on function public.devengar_comision(text, text, text, text, text, text, bigint, bigint, timestamptz, text) is
  'Apunta la comisión de un pago cobrado de verdad (0075). Devuelve devengada | ya_estaba | '
  'sin_referido | sin_cuenta | sin_dinero | antes_de_la_atribucion (0078). El % es el del salón en el instante del cobro.';

revoke all on function public.devengar_comision(text, text, text, text, text, text, bigint, bigint, timestamptz, text)
  from public, anon, authenticated;
grant execute on function public.devengar_comision(text, text, text, text, text, text, bigint, bigint, timestamptz, text)
  to service_role;

-- ═══════════════ 9. el portal: de dónde vino cada salón y desde cuándo devenga

drop function public.portal_del_referido(uuid);
create function public.portal_del_referido(p_referido uuid)
returns table (cuenta_nombre text, atribuida_el timestamptz, situacion text, trabajador_nombre text,
               origen text, devenga_desde timestamptz)
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
    a.trabajador_nombre, a.origen, a.devenga_desde
  from public.altas_atribuidas a
  where a.referido_id = p_referido
    and (exists (select 1 from public.accounts c where c.id = a.cuenta_id)
         or exists (select 1 from public.cuentas_borradas b where b.account_id = a.cuenta_id))
  order by a.atribuida_el desc;
$$;

revoke all on function public.portal_del_referido(uuid) from public, anon, authenticated;
grant execute on function public.portal_del_referido(uuid) to service_role;

commit;
