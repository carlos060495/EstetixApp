-- 0076 · Pagar a los referidos.
--
-- Carlos, 06-10-2026. El dinero sale: desde el panel, el OWNER elige un
-- referido y le paga lo que se le debe, con una confirmación que dice cuánto,
-- a quién, en qué moneda y qué comisiones cubre. Ni automático, ni programado,
-- ni al llegar a una cifra: enviar dinero a alguien se decide, no ocurre.
--
-- ═══ PAGAR DOS VECES ES EL FALLO QUE NO NOS PODEMOS PERMITIR ═══
--
-- Entre «Stripe ya mandó el dinero» y «nosotros lo apuntamos» hay un hueco
-- donde se puede caer el navegador, el backend o la red. Por eso:
--
--   1. LA ORDEN SE ESCRIBE ANTES DE MOVER EL DINERO (`ordenar_pago`), con su
--      propio identificador (`pagos_a_referidos.id`). Desde ese instante el
--      importe cuenta como pagado: no se puede ordenar otro pago de esa deuda.
--   2. La transferencia de Stripe lleva ESE identificador como clave de
--      idempotencia y como `transfer_group`. Si algo se cae a mitad y se
--      reintenta, Stripe reconoce la clave y no manda el dinero dos veces; y
--      pasadas las 24 h de la clave, se busca la transferencia por su grupo
--      antes de volver a intentarlo (`core/pagos_referido.py`).
--   3. Un referido no puede tener dos órdenes SIN RESOLVER en la misma moneda,
--      y la orden tiene que coincidir con lo que se le debe en ese momento
--      (si cambió desde que se vio la pantalla, no se ordena). Con un candado
--      por referido y moneda: dos clics a la vez se esperan el uno al otro.
--
-- ═══ LAS COMISIONES NO SE TOCAN ═══
--
-- Pagar no cambia ni una fila de `comisiones`: «pagado» es la suma de los pagos,
-- y lo que se debe es una resta — devengado, menos anulado, menos pagado —. Un
-- pago vale su importe salvo que Stripe lo rechace (entonces 0) o lo revierta
-- (entonces menos lo revertido). Una orden todavía sin resolver CUENTA COMO
-- PAGADA: es dinero que puede haber salido, y tratarla como deuda sería
-- invitar a pagarla otra vez.
--
-- ═══ INMUTABLES, SIN CLAVES AJENAS ═══
--
-- `pagos_a_referidos` (la orden: quién, cuándo, cuánto, en qué moneda, a qué
-- referido, a qué cuenta de Stripe y qué comisiones y periodo cubre) y
-- `pagos_a_referidos_resultados` (lo que pasó después: enviado con su
-- transferencia `tr_…`, rechazado con el porqué, revertido con cuánto). Una
-- orden no cambia de estado: se le añaden resultados. Como `comisiones`: sin
-- INSERT, UPDATE, DELETE ni TRUNCATE para nadie (se escribe solo con las
-- funciones de aquí), RLS sin políticas, el trigger `*_no_se_tocan`, y sin
-- ninguna clave ajena (0057). Las dos van en `SE_QUEDA`.
--
-- ═══ LAS MONEDAS NO SE MEZCLAN ═══
--
-- Cada orden es de UNA moneda, y la deuda se calcula solo con las comisiones y
-- los pagos de esa moneda. Comisiones en euros y en dólares son dos pagos.
--
-- ═══ EL MÍNIMO ═══
--
-- `minimos_de_pago`: por debajo de esto no se ordena un pago, porque lo que
-- cuesta enviarlo se come una parte que no tiene sentido. Lo que cuesta, según
-- la página de precios de Connect en España (06-10-2026), con las comisiones a
-- cargo de la plataforma: 2 € al mes por cada cuenta que recibe un pago a su
-- banco ese mes, más 0,25 % + 0,10 € por cada pago a su banco. La
-- transferencia de nuestra cuenta a la suya, en sí, no tiene coste. Con 25 €,
-- lo que cuesta es ~2,16 € (≈ 8,6 %). Una fila por moneda, en un solo sitio:
-- una moneda sin fila no se puede pagar (y la pantalla lo dice). Se cambia
-- con una migración, como los días de prueba; la app solo lo lee
-- (`minimos_de_pago_vigentes`).
--
-- COLUMNAS OBLIGATORIAS: tres tablas nuevas; todas sus columnas `not null` las
-- escriben las funciones de aquí, o la semilla del mínimo en euros.
--
-- ═══ ORDEN DE DESPLIEGUE: 0076 → BACKEND → FRONTEND ═══
--
-- El backend nuevo llama a estas funciones; el viejo no las conoce.
--
-- Probado en local, contra la base de verdad: `test_pagos_referido_base_real.py`.

begin;

-- ═══════════════ 1. el mínimo

create table public.minimos_de_pago (
  moneda text primary key check (moneda ~ '^[a-z]{3}$'),
  centimos bigint not null check (centimos > 0),
  por_que text not null,
  actualizado_at timestamptz not null default now()
);

comment on table public.minimos_de_pago is
  'Lo mínimo que se envía a un referido en cada moneda (0076). Una moneda sin fila no se paga.';

insert into public.minimos_de_pago (moneda, centimos, por_que) values
  ('eur', 2500, 'Enviar cuesta 2 €/mes por cuenta + 0,25 % + 0,10 € por pago a su banco: con 25 €, ~8,6 %.');

revoke all on public.minimos_de_pago from public, anon, authenticated, service_role;
grant select on public.minimos_de_pago to service_role;
alter table public.minimos_de_pago enable row level security;

-- Lo lee la app con esta función, como los días de prueba: se cambia con una
-- migración, no desde el panel (Carlos: «en la base, en un solo sitio»).
create function public.minimos_de_pago_vigentes()
returns table (moneda text, centimos bigint)
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select m.moneda, m.centimos from public.minimos_de_pago m order by m.moneda;
$$;

revoke all on function public.minimos_de_pago_vigentes() from public, anon, authenticated;
grant execute on function public.minimos_de_pago_vigentes() to service_role;

-- ═══════════════ 2. la orden de pago

create table public.pagos_a_referidos (
  -- Es también la clave de idempotencia y el `transfer_group` en Stripe.
  id uuid primary key,
  -- SIN claves ajenas (0057): ver la cabecera.
  referido_id uuid not null,
  referido_nombre text not null,
  -- La cuenta de Stripe a la que se manda. Nunca sale por ninguna ruta.
  destino_stripe text not null check (destino_stripe ~ '^acct_[A-Za-z0-9]+$'),
  moneda text not null check (moneda ~ '^[a-z]{3}$'),
  importe_centimos bigint not null check (importe_centimos > 0),
  -- Qué cubre: las comisiones de esa moneda con id en (desde_excluido, hasta],
  -- y su periodo.
  desde_comision_excluido bigint not null,
  hasta_comision_id bigint not null,
  periodo_desde timestamptz not null,
  periodo_hasta timestamptz not null,
  -- Quién lo ordenó: una persona del panel.
  ordenado_por uuid not null,
  ordenado_por_email text not null,
  ordenado_at timestamptz not null default now(),
  constraint cubre_algo check (hasta_comision_id > desde_comision_excluido and periodo_hasta >= periodo_desde)
);

comment on table public.pagos_a_referidos is
  'Cada pago a un referido, escrito ANTES de mover el dinero (0076). Su id es la clave de idempotencia '
  'de la transferencia. No se puede editar ni borrar; no cuelga de nada.';

create index pagos_a_referidos_por_referido on public.pagos_a_referidos (referido_id, moneda, ordenado_at);

-- ═══════════════ 3. lo que pasó con cada orden

create table public.pagos_a_referidos_resultados (
  id bigint generated always as identity primary key,
  pago_id uuid not null,
  resultado text not null check (resultado in ('enviado', 'rechazado', 'revertido')),
  stripe_transferencia text check (stripe_transferencia ~ '^tr_[A-Za-z0-9]+$'),
  -- Solo `revertido`: lo revertido EN TOTAL según Stripe, hasta ese aviso.
  revertido_total_centimos bigint check (revertido_total_centimos > 0),
  -- Por qué: el mensaje de Stripe en un rechazo, o de dónde vino el resultado.
  detalle text,
  stripe_evento text,
  ocurrido_at timestamptz not null default now(),
  constraint enviado_con_transferencia check (resultado <> 'enviado' or stripe_transferencia is not null),
  constraint revertido_con_cuanto check (resultado <> 'revertido'
    or (stripe_transferencia is not null and revertido_total_centimos is not null))
);

comment on table public.pagos_a_referidos_resultados is
  'Lo que pasó con cada pago a un referido (0076): enviado, rechazado o revertido. No se puede editar ni borrar.';

-- Una orden se resuelve UNA vez (enviada o rechazada); revertir puede pasar varias.
create unique index pagos_a_referidos_un_final on public.pagos_a_referidos_resultados (pago_id)
  where resultado in ('enviado', 'rechazado');
create index pagos_a_referidos_resultados_por_transferencia on public.pagos_a_referidos_resultados (stripe_transferencia);

-- ═══════════════ 4. inmutables

revoke all on public.pagos_a_referidos, public.pagos_a_referidos_resultados
  from public, anon, authenticated, service_role;
grant select on public.pagos_a_referidos, public.pagos_a_referidos_resultados to service_role;
alter table public.pagos_a_referidos enable row level security;
alter table public.pagos_a_referidos_resultados enable row level security;

create trigger pagos_a_referidos_sin_update_ni_delete
  before update or delete on public.pagos_a_referidos
  for each row execute function public.las_comisiones_no_se_tocan();
create trigger pagos_a_referidos_sin_truncate
  before truncate on public.pagos_a_referidos
  for each statement execute function public.las_comisiones_no_se_tocan();
create trigger pagos_a_referidos_resultados_sin_update_ni_delete
  before update or delete on public.pagos_a_referidos_resultados
  for each row execute function public.las_comisiones_no_se_tocan();
create trigger pagos_a_referidos_resultados_sin_truncate
  before truncate on public.pagos_a_referidos_resultados
  for each statement execute function public.las_comisiones_no_se_tocan();

-- ═══════════════ 5. lo que vale cada pago, y la deuda

-- Lo que cuenta como pagado de cada orden: su importe, salvo rechazada (0) o
-- revertida (menos lo revertido). SIN RESOLVER CUENTA ENTERA.
create function public.pagos_que_cuentan(p_referido uuid)
returns table (pago_id uuid, moneda text, importe_centimos bigint, cuenta_centimos bigint, estado text,
               stripe_transferencia text, revertido_centimos bigint, ordenado_at timestamptz,
               hasta_comision_id bigint, desde_comision_excluido bigint, periodo_desde timestamptz,
               periodo_hasta timestamptz, resuelto_at timestamptz)
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select p.id, p.moneda, p.importe_centimos,
         case when f.resultado = 'rechazado' then 0
              else p.importe_centimos - coalesce(r.total, 0) end,
         case when f.resultado is null then 'pendiente'
              when f.resultado = 'rechazado' then 'rechazado'
              when coalesce(r.total, 0) > 0 then 'revertido'
              else 'enviado' end,
         f.stripe_transferencia, coalesce(r.total, 0), p.ordenado_at,
         p.hasta_comision_id, p.desde_comision_excluido, p.periodo_desde, p.periodo_hasta, f.ocurrido_at
  from public.pagos_a_referidos p
  left join public.pagos_a_referidos_resultados f
    on f.pago_id = p.id and f.resultado in ('enviado', 'rechazado')
  left join lateral (
    select max(x.revertido_total_centimos) as total from public.pagos_a_referidos_resultados x
    where x.pago_id = p.id and x.resultado = 'revertido') r on true
  where p.referido_id = p_referido
  order by p.ordenado_at;
$$;

-- Lo que se le debe en UNA moneda contando las comisiones hasta `p_hasta`.
create function public.deuda_con_referido(p_referido uuid, p_moneda text, p_hasta bigint default null)
returns table (neto bigint, pagado bigint, debe bigint, hasta_comision_id bigint)
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  with c as (
    select coalesce(sum(comision_centimos), 0)::bigint as neto, max(id) as hasta
    from public.comisiones
    where referido_id = p_referido and moneda = p_moneda and (p_hasta is null or id <= p_hasta)
  ), p as (
    select coalesce(sum(cuenta_centimos), 0)::bigint as pagado
    from public.pagos_que_cuentan(p_referido) where moneda = p_moneda
  )
  select c.neto, p.pagado, c.neto - p.pagado, c.hasta from c, p;
$$;

revoke all on function public.pagos_que_cuentan(uuid) from public, anon, authenticated;
grant execute on function public.pagos_que_cuentan(uuid) to service_role;
revoke all on function public.deuda_con_referido(uuid, text, bigint) from public, anon, authenticated;
grant execute on function public.deuda_con_referido(uuid, text, bigint) to service_role;

-- ═══════════════ 6. ordenar un pago (ANTES de mover el dinero)

create function public.ordenar_pago(
  p_referido uuid,
  p_moneda text,
  p_importe bigint,       -- lo que vio y confirmó quien lo ordena
  p_hasta bigint,         -- la última comisión que vio en la confirmación
  p_destino text,
  p_admin uuid,
  p_admin_email text
)
returns table (resultado text, pago_id uuid)
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_deuda record;
  v_minimo bigint;
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

  select centimos into v_minimo from public.minimos_de_pago where moneda = p_moneda;
  if v_minimo is null then
    resultado := 'sin_minimo'; return next; return;
  end if;
  if p_importe < v_minimo then
    resultado := 'por_debajo_del_minimo'; return next; return;
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
    desde_comision_excluido, hasta_comision_id, periodo_desde, periodo_hasta, ordenado_por, ordenado_por_email)
  values (v_id, p_referido, coalesce(v_nombre, '?'), p_destino, p_moneda, p_importe,
    v_desde, p_hasta, v_periodo.desde, v_periodo.hasta, p_admin, p_admin_email);

  resultado := 'ordenado'; pago_id := v_id; return next;
end;
$$;

revoke all on function public.ordenar_pago(uuid, text, bigint, bigint, text, uuid, text) from public, anon, authenticated;
grant execute on function public.ordenar_pago(uuid, text, bigint, bigint, text, uuid, text) to service_role;

-- ═══════════════ 7. apuntar lo que pasó

create function public.resolver_pago(p_pago uuid, p_resultado text, p_transferencia text, p_detalle text, p_evento text)
returns text
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  if p_resultado not in ('enviado', 'rechazado') then
    raise exception 'resolver_pago: % no es un final', p_resultado using errcode = '22023';
  end if;
  if not exists (select 1 from public.pagos_a_referidos where id = p_pago) then
    raise exception 'resolver_pago: la orden % no existe', p_pago using errcode = '22023';
  end if;
  insert into public.pagos_a_referidos_resultados (pago_id, resultado, stripe_transferencia, detalle, stripe_evento)
  values (p_pago, p_resultado, p_transferencia, p_detalle, p_evento)
  on conflict (pago_id) where resultado in ('enviado', 'rechazado') do nothing;
  -- Repetirlo no cambia nada; lo que ya estaba resuelto, se dice.
  return (select resultado from public.pagos_a_referidos_resultados
          where pago_id = p_pago and resultado in ('enviado', 'rechazado'));
end;
$$;

-- Stripe revirtió (todo o parte de) una transferencia: lo revertido EN TOTAL.
create function public.apuntar_reversion(p_transferencia text, p_total bigint, p_evento text)
returns text
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_pago uuid;
  v_ya bigint;
begin
  select pago_id into v_pago from public.pagos_a_referidos_resultados
  where stripe_transferencia = p_transferencia and resultado = 'enviado';
  if v_pago is null then
    return 'no_es_nuestra';
  end if;
  perform pg_advisory_xact_lock(hashtextextended('reversion:' || p_transferencia, 0));
  select coalesce(max(revertido_total_centimos), 0) into v_ya from public.pagos_a_referidos_resultados
  where pago_id = v_pago and resultado = 'revertido';
  if p_total is null or p_total <= v_ya then
    return 'sin_cambios';
  end if;
  insert into public.pagos_a_referidos_resultados (pago_id, resultado, stripe_transferencia, revertido_total_centimos,
                                                  detalle, stripe_evento)
  values (v_pago, 'revertido', p_transferencia, p_total, 'Stripe revirtió la transferencia', p_evento);
  return 'revertido';
end;
$$;

revoke all on function public.resolver_pago(uuid, text, text, text, text) from public, anon, authenticated;
grant execute on function public.resolver_pago(uuid, text, text, text, text) to service_role;
revoke all on function public.apuntar_reversion(text, bigint, text) from public, anon, authenticated;
grant execute on function public.apuntar_reversion(text, bigint, text) to service_role;

commit;
