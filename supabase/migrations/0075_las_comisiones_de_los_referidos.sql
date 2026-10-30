-- 0075 · Las comisiones de los referidos: calcular y apuntar lo que se debe.
--
-- Carlos, 06-10-2026. Esta entrega NO PAGA NADA: calcula y registra.
--
-- ═══ CUÁNDO NACE UNA COMISIÓN ═══
--
-- Cuando un pago de una factura del salón se cobra DE VERDAD en Stripe: un
-- cobro (`ch_…`/`py_…`) con dinero (> 0). Ni el alta, ni la factura emitida,
-- ni la prueba, ni una factura de 0 €, ni lo pagado fuera de Stripe. Lo
-- impone la base: una fila de devengo sin cobro, o con importe 0, no entra
-- (restricciones de abajo), y solo se escribe a través de `devengar_comision`.
--
-- UNA POR PAGO. La clave del devengo es el pago de la factura de Stripe
-- (`inpay_…`) y, además, el cobro: Stripe reintenta y avisa dos veces del
-- mismo hecho, y la segunda vez no suma nada (índices únicos + lo comprueba la
-- función antes, con un candado por cobro).
--
-- ═══ LO QUE SE CORRIGE SE CUENTA ═══
--
-- Si el cobro se devuelve o se reclama, la comisión deja de deberse, pero el
-- devengo NO se toca: se escribe una fila CONTRARIA (`correccion`) que apunta a
-- él. La cuenta es «lo que Stripe dice HOY que ya no se debe» de ese cobro
-- —devuelto + reclamado sin ganar—, y cada aviso escribe solo la diferencia
-- con lo ya corregido (`ajustar_comision`). Por eso repetir un aviso no
-- escribe nada, y una reclamación ganada o una devolución que falla escriben
-- la fila a favor. Cada corrección guarda las cifras de Stripe en ese momento
-- y su motivo: un número que cambia tiene que poder explicarse.
--
-- ═══ EL PORCENTAJE SE GUARDA CUANDO LLEGA EL SALÓN ═══
--
-- Carlos, 06-10-2026 (corrige la primera versión, que lo congelaba en el
-- primer pago): lo que cuenta es lo que el referido sabía cuando vendió. Le
-- enseñó la app a un salón diciendo «yo me llevo un 10 %»; si el porcentaje
-- cambiara antes de que ese salón pague, habría vendido bajo un acuerdo y
-- cobraría otro. Y con la regla al revés habría un incentivo feo: bajar
-- porcentajes justo antes de que empiecen a pagar las altas de alguien.
--
-- Así que `atribuir_alta` escribe el porcentaje EN LA FILA DE LA ATRIBUCIÓN, en
-- el mismo momento: el propio del referido, o el global si no tiene
-- (`altas_atribuidas.pct` y `pct_de_donde`). Cambiar después el porcentaje de
-- un referido vale para las altas que traiga a partir de entonces.
--
-- LAS ATRIBUCIONES QUE YA EXISTÍAN se rellenan aquí con el porcentaje vigente
-- HOY (el propio del referido o el global). Nacieron antes de que esto
-- existiera y el de hoy es el único que se les prometió: no hay otro número
-- que se les dijera. En producción hay una sola (Carlos, 06-10-2026); en local,
-- ninguna.
--
-- ═══ CAMBIAR EL PORCENTAJE DE SALONES QUE YA LLEGARON ═══
--
-- Aparte, una acción deliberada del panel (`cambiar_porcentaje_de_salones`):
-- un porcentaje nuevo para todos los salones de un referido o para los que se
-- elijan, con un MOTIVO escrito obligatorio. Tres reglas:
--
--   · LO YA DEVENGADO NO SE TOCA. El cambio vale para lo que el salón genere
--     A PARTIR de ese momento: cada devengo lleva su porcentaje dentro y es
--     inmutable, y una devolución posterior se corrige con el porcentaje de
--     ESE devengo, no con el de hoy (`ajustar_comision`).
--   · QUEDA REGISTRADO en `cambios_de_porcentaje`, que nadie puede editar ni
--     borrar: quién, cuándo, de cuánto a cuánto, en qué salón, por qué, y el
--     lote (una fila por salón; las de una misma acción comparten lote).
--   · EL REFERIDO LO VE en su portal, suba o baje.
--
-- QUÉ PORCENTAJE LLEVA UN PAGO: el vigente en el INSTANTE EN QUE STRIPE LO
-- COBRÓ (`porcentaje_del_salon(cuenta, pagado_at)`): el del último cambio
-- hecho antes de ese instante, o el de la atribución si no hubo ninguno. No
-- depende de cuándo llegue el aviso: un webhook que se retrasa o se reintenta
-- da el mismo número. Un cambio y un devengo del mismo salón se esperan el uno
-- al otro (el mismo candado por salón) y el cambio se sella con la hora a la
-- que de verdad se escribe, así que nunca se ve un cambio «del pasado».
--
-- ═══ SOBRE QUÉ ═══
--
-- Lo que paga el salón por Stripe, sin impuestos, en la moneda de ese cobro.
-- Sin descontar lo que nos cobra Stripe. Las monedas NO se mezclan ni se
-- convierten: cada fila lleva la suya y se suma por moneda. La base la calcula
-- el backend con lo que dice Stripe (`core/comisiones.py`); aquí se comprueba
-- que no pase de lo cobrado.
--
-- ═══ NO CUELGA DE NADA ═══
--
-- `cuenta_id` SIN clave ajena y el nombre del salón COPIADO: una comisión ya
-- devengada no desaparece porque el salón se borre a los 90 días. Y, como todo
-- registro que no se puede tocar (0057, `test_los_registros_sobreviven.py`),
-- tampoco hacia el referido ni hacia el devengo que corrige: que existan lo
-- garantizan las funciones de aquí, que son lo único que escribe (el referido sale
-- de la atribución; el devengo, de la misma tabla, bajo el mismo candado). Los
-- referidos no se borran nunca: rechazar es la única forma de cortarle. Las dos
-- tablas van en `SE_QUEDA` (`core/simulacro.py`); lo vigilan
-- `test_nuestros_registros_base_real.py` (nada de lo que se queda apunta a lo
-- que se borra) y `test_comisiones_base_real.py` (el borrado de verdad, en una
-- transacción que se deshace, no se lleva ninguna).
--
-- ═══ INMUTABLES, COMO EL REGISTRO DE DECISIONES DE DINERO (0054) ═══
--
-- `comisiones` y `cambios_de_porcentaje`. Nadie tiene INSERT, UPDATE, DELETE
-- ni TRUNCATE: se escribe SOLO a través de las funciones de aquí (security
-- definer, solo `service_role`). `service_role` lee.
-- RLS activada y sin políticas. Y un trigger que revienta ante UPDATE, DELETE
-- o TRUNCATE para quien tuviera el permiso por otra vía.
--
-- ═══ PAGADO ═══
--
-- Pagar todavía no existe. El panel y el portal separan «devengado» de
-- «pagado» (hoy siempre 0) para que el día que exista no se pague dos veces;
-- esta migración no crea ninguna tabla de pagos sin quien la escriba.
--
-- COLUMNAS OBLIGATORIAS: `altas_atribuidas.pct` y `pct_de_donde` nacen `not
-- null` después de rellenar las filas que ya había (ver arriba); su único
-- escritor, `atribuir_alta`, se rehace aquí mismo con la MISMA firma, así que el
-- backend que ya está desplegado sigue atribuyendo sin saber nada. Las dos
-- tablas nuevas: todas sus columnas `not null` las escriben las funciones de
-- aquí.
--
-- ═══ ORDEN DE DESPLIEGUE: 0075 → BACKEND → FRONTEND ═══
--
-- El backend nuevo llama a `devengar_comision` y `ajustar_comision` en el
-- webhook de facturación: si llegara antes que esta migración, cada factura
-- pagada fallaría y Stripe la reintentaría (no se pierde, pero ensucia). El
-- backend viejo no sabe nada de esto: la migración sola no cambia nada.
--
-- Probado en local, contra la base de verdad: `test_comisiones_base_real.py`.

begin;

-- ═══════════════ 1. el porcentaje, en la atribución

alter table public.altas_atribuidas
  add column pct numeric(5, 2) check (pct >= 0 and pct <= 100),
  add column pct_de_donde text check (pct_de_donde in ('propio', 'global'));

-- Las que ya existían: el de hoy (ver la cabecera). La atribución no se puede
-- tocar, y por eso su trigger se aparta SOLO para esto y vuelve en la misma
-- transacción.
alter table public.altas_atribuidas disable trigger altas_atribuidas_sin_update_ni_delete;
update public.altas_atribuidas a
set pct = coalesce(r.comision_pct, c.comision_referido_pct),
    pct_de_donde = case when r.comision_pct is null then 'global' else 'propio' end
from public.referidos r, public.plataforma_config c
where r.id = a.referido_id and c.id and a.pct is null;
alter table public.altas_atribuidas enable trigger altas_atribuidas_sin_update_ni_delete;

alter table public.altas_atribuidas
  alter column pct set not null,
  alter column pct_de_donde set not null;

comment on column public.altas_atribuidas.pct is
  'El % de comisión prometido al referido por este salón, escrito al llegar (0075). '
  'Solo lo cambia una acción deliberada del panel, que queda en cambios_de_porcentaje.';

-- La misma firma: el backend desplegado la sigue llamando igual.
create or replace function public.atribuir_alta(p_cuenta uuid, p_codigo text)
returns text
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  -- Tal como llegó, sin tocar (solo acotado: viene de un enlace público).
  v_llegado text := left(p_codigo, 64);
  -- SIN MAYÚSCULAS Y SIN ESPACIOS ALREDEDOR, y nada más: ver la 0070.
  -- `\s` no incluye el espacio duro (chr(160)), que es justo el que pega un móvil.
  v_buscado text := upper(regexp_replace(coalesce(p_codigo, ''), '^[\s' || chr(160) || ']+|[\s' || chr(160) || ']+$', '', 'g'));
  v_referido public.referidos;
  v_nombre text;
  v_global numeric(5, 2);
  v_escritas int;
begin
  if v_buscado = '' then
    return 'sin_codigo';
  end if;

  -- `for share`: un rechazo, o un cambio de su %, espera a que esta atribución termine.
  select * into v_referido from public.referidos r
  where upper(btrim(r.codigo)) = v_buscado
  for share;

  if not found then
    insert into public.codigos_que_no_valieron (codigo_llegado, motivo) values (v_llegado, 'no_existe');
    return 'no_existe';
  end if;
  if v_referido.estado::text <> 'aprobado' then
    insert into public.codigos_que_no_valieron (codigo_llegado, motivo)
    values (v_llegado, v_referido.estado::text);
    return v_referido.estado::text;
  end if;

  select a.nombre into v_nombre from public.accounts a where a.id = p_cuenta;
  if not found then
    raise exception 'atribuir_alta: la cuenta % no existe', p_cuenta;
  end if;

  -- EL PORCENTAJE QUE SE LE PROMETIÓ HOY, escrito en la misma fila (0075).
  select c.comision_referido_pct into v_global from public.plataforma_config c where c.id;

  -- Una por cuenta: si ya la tiene, gana la primera y no se escribe nada.
  insert into public.altas_atribuidas (referido_id, cuenta_id, cuenta_nombre, codigo_llegado, pct, pct_de_donde)
  values (v_referido.id, p_cuenta, v_nombre, v_llegado,
          coalesce(v_referido.comision_pct, v_global),
          case when v_referido.comision_pct is null then 'global' else 'propio' end)
  on conflict on constraint una_atribucion_por_cuenta do nothing;
  get diagnostics v_escritas = row_count;
  return case when v_escritas = 1 then 'atribuida' else 'ya_atribuida' end;
end;
$$;

revoke all on function public.atribuir_alta(uuid, text) from public, anon, authenticated;
grant execute on function public.atribuir_alta(uuid, text) to service_role;

-- ═══════════════ 1b. el registro de los cambios deliberados

create table public.cambios_de_porcentaje (
  id bigint generated always as identity primary key,
  -- Las filas de una misma acción comparten lote.
  lote uuid not null,
  -- SIN claves ajenas, como todo registro (0057): ver la cabecera.
  referido_id uuid not null,
  cuenta_id uuid not null,
  cuenta_nombre text not null,
  pct_antes numeric(5, 2) not null check (pct_antes >= 0 and pct_antes <= 100),
  pct_despues numeric(5, 2) not null check (pct_despues >= 0 and pct_despues <= 100),
  -- POR QUÉ, escrito: obligatorio, y no vale un espacio ni una palabra suelta.
  motivo text not null check (char_length(btrim(motivo)) >= 10),
  -- Quién: el administrador del panel, con su correo copiado.
  hecho_por uuid not null,
  hecho_por_email text not null,
  hecho_at timestamptz not null,
  constraint un_cambio_cambia_algo check (pct_antes <> pct_despues)
);

comment on table public.cambios_de_porcentaje is
  'Cada cambio deliberado del % de un salón que ya llegó (0075): quién, cuándo, de cuánto a cuánto, '
  'dónde y por qué. No se puede editar ni borrar; el referido lo ve en su portal.';

create index cambios_de_porcentaje_por_salon on public.cambios_de_porcentaje (cuenta_id, hecho_at);
create index cambios_de_porcentaje_por_referido on public.cambios_de_porcentaje (referido_id, hecho_at);

-- ═══════════════ 2. el libro de comisiones

create table public.comisiones (
  id bigint generated always as identity primary key,
  referido_id uuid not null,
  -- SIN clave ajena a la cuenta, y su nombre copiado: ver la cabecera.
  cuenta_id uuid not null,
  cuenta_nombre text not null,
  tipo text not null check (tipo in ('devengo', 'correccion')),
  motivo text not null check (motivo in (
    'cobro',                   -- devengo: el salón pagó
    'devolucion',              -- se le devolvió el dinero
    'reclamacion',             -- reclamó el cobro a su banco (disputa)
    'devolucion_revertida',    -- una devolución que no llegó a hacerse
    'reclamacion_ganada'       -- la disputa se ganó: el dinero volvió
  )),
  -- De dónde viene, para cuadrarlo con Stripe.
  stripe_cobro text not null,
  stripe_factura text,
  stripe_pago text,
  stripe_payment_intent text,
  stripe_evento text,
  moneda text not null check (moneda ~ '^[a-z]{3}$'),
  -- Lo cobrado de verdad en ese pago, y la base sin impuestos (solo devengo).
  importe_cobrado_centimos bigint,
  base_centimos bigint,
  pct numeric(5, 2) not null check (pct >= 0 and pct <= 100),
  -- Con signo: positivo lo que se debe, negativo lo que se corrige.
  comision_centimos bigint not null,
  -- Solo corrección: a qué devengo corrige, y lo que decía Stripe al hacerla.
  corrige_a bigint,
  devuelto_centimos bigint,
  reclamado_centimos bigint,
  -- Cuándo pasó en Stripe (el pago, la devolución…) y cuándo lo apuntamos.
  ocurrido_at timestamptz not null,
  registrado_at timestamptz not null default now(),

  -- UN DEVENGO SOLO NACE DE UN COBRO REAL.
  constraint devengo_con_cobro_real check (tipo <> 'devengo' or (
    motivo = 'cobro'
    and stripe_cobro ~ '^(ch|py)_[A-Za-z0-9]+$'
    and stripe_pago is not null and stripe_factura is not null
    and importe_cobrado_centimos > 0
    and base_centimos >= 0 and base_centimos <= importe_cobrado_centimos
    and comision_centimos >= 0
    and corrige_a is null
  )),
  constraint correccion_corrige_un_devengo check (tipo <> 'correccion' or (
    motivo <> 'cobro'
    and corrige_a is not null
    and comision_centimos <> 0
    and devuelto_centimos >= 0 and reclamado_centimos >= 0
  ))
);

comment on table public.comisiones is
  'Lo que se debe a cada referido (0075): una fila por devengo y otra contraria por cada corrección. '
  'No se puede editar ni borrar; no cuelga de la cuenta (sobrevive a su borrado).';

-- EL MISMO COBRO NO DEVENGA DOS VECES.
create unique index comisiones_un_devengo_por_pago on public.comisiones (stripe_pago) where tipo = 'devengo';
create unique index comisiones_un_devengo_por_cobro on public.comisiones (stripe_cobro) where tipo = 'devengo';
create index comisiones_por_referido on public.comisiones (referido_id, ocurrido_at);
create index comisiones_por_devengo on public.comisiones (corrige_a) where corrige_a is not null;

-- ═══════════════ 3. inmutables: permisos y trigger

revoke all on public.comisiones, public.cambios_de_porcentaje from public, anon, authenticated, service_role;
grant select on public.comisiones, public.cambios_de_porcentaje to service_role;

alter table public.comisiones enable row level security;
alter table public.cambios_de_porcentaje enable row level security;

create function public.las_comisiones_no_se_tocan()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  raise exception '% no se puede % : es un registro de dinero', tg_table_name, tg_op
    using errcode = '42501';
end;
$$;

create trigger comisiones_sin_update_ni_delete
  before update or delete on public.comisiones
  for each row execute function public.las_comisiones_no_se_tocan();
create trigger comisiones_sin_truncate
  before truncate on public.comisiones
  for each statement execute function public.las_comisiones_no_se_tocan();
create trigger cambios_de_porcentaje_sin_update_ni_delete
  before update or delete on public.cambios_de_porcentaje
  for each row execute function public.las_comisiones_no_se_tocan();
create trigger cambios_de_porcentaje_sin_truncate
  before truncate on public.cambios_de_porcentaje
  for each statement execute function public.las_comisiones_no_se_tocan();

-- ═══════════════ 3b. qué porcentaje lleva un salón en un instante

create function public.porcentaje_del_salon(p_cuenta uuid, p_en timestamptz)
returns numeric
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  -- El del último cambio deliberado hecho antes de ese instante; si no hubo
  -- ninguno, el que se escribió en la atribución al llegar. NUNCA el vigente
  -- del referido ni el global: esos valen solo para altas nuevas.
  select coalesce(
    (select c.pct_despues from public.cambios_de_porcentaje c
     where c.cuenta_id = p_cuenta and c.hecho_at <= p_en
     order by c.hecho_at desc, c.id desc limit 1),
    (select a.pct from public.altas_atribuidas a where a.cuenta_id = p_cuenta));
$$;

revoke all on function public.porcentaje_del_salon(uuid, timestamptz) from public, anon, authenticated;
grant execute on function public.porcentaje_del_salon(uuid, timestamptz) to service_role;

-- ═══════════════ 3c. los salones de un referido (los que cuentan)

-- Los mismos que cuenta `altas_que_cuentan()` (0070): la cuenta existe, o
-- consta como borrada a los 90 días. Un alta deshecha deja su atribución (es
-- inmutable) y no es un salón de nadie: ni se enseña ni se le cambia el %.
create function public.salones_de_un_referido(p_referido uuid)
returns table (cuenta_id uuid, cuenta_nombre text, pct numeric, pct_de_donde text, atribuida_el timestamptz)
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select a.cuenta_id, a.cuenta_nombre, a.pct, a.pct_de_donde, a.atribuida_el
  from public.altas_atribuidas a
  where a.referido_id = p_referido
    and (exists (select 1 from public.accounts c where c.id = a.cuenta_id)
         or exists (select 1 from public.cuentas_borradas b where b.account_id = a.cuenta_id))
  order by a.atribuida_el, a.cuenta_id;
$$;

revoke all on function public.salones_de_un_referido(uuid) from public, anon, authenticated;
grant execute on function public.salones_de_un_referido(uuid) to service_role;

-- ═══════════════ 4. devengar: un pago cobrado de verdad

create function public.devengar_comision(
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
  'sin_referido | sin_cuenta | sin_dinero. El % es el del salón en el instante del cobro.';

revoke all on function public.devengar_comision(text, text, text, text, text, text, bigint, bigint, timestamptz, text)
  from public, anon, authenticated;
grant execute on function public.devengar_comision(text, text, text, text, text, text, bigint, bigint, timestamptz, text)
  to service_role;

-- ═══════════════ 5. ajustar: lo que Stripe dice hoy que ya no se debe

create function public.ajustar_comision(
  p_cobro text,
  p_devuelto bigint,
  p_reclamado bigint,
  p_ocurrido_at timestamptz,
  p_evento text
)
returns table (resultado text, comision_id bigint)
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_devengo public.comisiones;
  v_ultima public.comisiones;
  v_no_debido bigint;
  v_objetivo bigint;   -- comisión que, según Stripe, ya no se debe
  v_ya bigint;         -- lo ya corregido (en positivo)
  v_delta bigint;
  v_motivo text;
  v_id bigint;
begin
  if p_devuelto is null or p_devuelto < 0 or p_reclamado is null or p_reclamado < 0 then
    raise exception 'ajustar_comision: cifras de Stripe inválidas' using errcode = '22023';
  end if;

  -- El mismo candado que el devengo: un aviso a la vez por cobro.
  perform pg_advisory_xact_lock(hashtextextended('comision:' || p_cobro, 0));
  select * into v_devengo from public.comisiones where tipo = 'devengo' and stripe_cobro = p_cobro;
  if not found then
    -- Un cobro sin comisión (otro salón, sin referido, o aún no devengado:
    -- al devengarse se ajusta en el momento).
    resultado := 'sin_devengo'; return next; return;
  end if;

  v_no_debido := least(v_devengo.importe_cobrado_centimos, p_devuelto + p_reclamado);
  v_objetivo := round(v_devengo.comision_centimos::numeric * v_no_debido / v_devengo.importe_cobrado_centimos);
  select coalesce(-sum(comision_centimos), 0) into v_ya from public.comisiones where corrige_a = v_devengo.id;
  v_delta := v_objetivo - v_ya;
  if v_delta = 0 then
    resultado := 'sin_cambios'; comision_id := v_devengo.id; return next; return;
  end if;

  -- El porqué, comparando con lo que decía Stripe en la corrección anterior.
  select * into v_ultima from public.comisiones where corrige_a = v_devengo.id order by id desc limit 1;
  if v_delta > 0 then
    v_motivo := case when p_reclamado > coalesce(v_ultima.reclamado_centimos, 0)
                     then 'reclamacion' else 'devolucion' end;
  else
    v_motivo := case when p_reclamado < coalesce(v_ultima.reclamado_centimos, 0)
                     then 'reclamacion_ganada' else 'devolucion_revertida' end;
  end if;

  insert into public.comisiones (
    referido_id, cuenta_id, cuenta_nombre, tipo, motivo,
    stripe_cobro, stripe_factura, stripe_pago, stripe_payment_intent, stripe_evento,
    moneda, pct, comision_centimos, corrige_a, devuelto_centimos, reclamado_centimos, ocurrido_at)
  values (
    v_devengo.referido_id, v_devengo.cuenta_id, v_devengo.cuenta_nombre, 'correccion', v_motivo,
    v_devengo.stripe_cobro, v_devengo.stripe_factura, v_devengo.stripe_pago, v_devengo.stripe_payment_intent,
    p_evento, v_devengo.moneda, v_devengo.pct, -v_delta, v_devengo.id, p_devuelto, p_reclamado,
    coalesce(p_ocurrido_at, now()))
  returning id into v_id;

  resultado := 'corregida'; comision_id := v_id; return next;
end;
$$;

comment on function public.ajustar_comision(text, bigint, bigint, timestamptz, text) is
  'Escribe la fila contraria (o a favor) que deja la comisión de un cobro como dice Stripe hoy (0075). '
  'Devuelve corregida | sin_cambios | sin_devengo.';

revoke all on function public.ajustar_comision(text, bigint, bigint, timestamptz, text) from public, anon, authenticated;
grant execute on function public.ajustar_comision(text, bigint, bigint, timestamptz, text) to service_role;

-- ═══════════════ 6. cambiar el % de salones que ya llegaron

create function public.cambiar_porcentaje_de_salones(
  p_referido uuid,
  p_cuentas uuid[],      -- null = todos los suyos
  p_pct numeric,
  p_motivo text,
  p_admin uuid,
  p_admin_email text
)
returns table (cuenta_id uuid, cuenta_nombre text, pct_antes numeric, pct_despues numeric)
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_lote uuid := gen_random_uuid();
  v_alta record;
  v_antes numeric(5, 2);
  v_ahora timestamptz;
  v_escritas int := 0;
begin
  -- SIN MOTIVO NO HAY CAMBIO: lo comprueba aquí y, además, la tabla.
  if p_motivo is null or char_length(btrim(p_motivo)) < 10 then
    raise exception 'cambiar_porcentaje_de_salones: hace falta un motivo escrito' using errcode = '22023';
  end if;
  if p_pct is null or p_pct < 0 or p_pct > 100 then
    raise exception 'cambiar_porcentaje_de_salones: porcentaje fuera de 0-100' using errcode = '22023';
  end if;
  if p_admin is null or coalesce(btrim(p_admin_email), '') = '' then
    raise exception 'cambiar_porcentaje_de_salones: falta quién lo hace' using errcode = '22023';
  end if;
  if p_cuentas is not null and (cardinality(p_cuentas) = 0 or exists (
      select 1 from unnest(p_cuentas) x
      where not exists (select 1 from public.salones_de_un_referido(p_referido) a where a.cuenta_id = x))) then
    raise exception 'cambiar_porcentaje_de_salones: algún salón no es de este referido' using errcode = '22023';
  end if;

  -- Solo los salones que cuentan (un alta deshecha no es de nadie).
  for v_alta in
    select a.cuenta_id, a.cuenta_nombre from public.salones_de_un_referido(p_referido) a
    where p_cuentas is null or a.cuenta_id = any (p_cuentas)
    order by a.cuenta_id
  loop
    -- El mismo candado que el devengo: un pago de este salón a la vez espera.
    perform pg_advisory_xact_lock(hashtextextended('porcentaje:' || v_alta.cuenta_id::text, 0));
    -- La hora DE VERDAD de la escritura, tomada ya con el candado: un pago
    -- cobrado antes de este instante lleva el % de antes; uno después, este.
    v_ahora := clock_timestamp();
    v_antes := public.porcentaje_del_salon(v_alta.cuenta_id, v_ahora);
    if v_antes = p_pct then
      continue;  -- ya va a ese %: no es un cambio
    end if;
    insert into public.cambios_de_porcentaje
      (lote, referido_id, cuenta_id, cuenta_nombre, pct_antes, pct_despues, motivo, hecho_por, hecho_por_email, hecho_at)
    values (v_lote, p_referido, v_alta.cuenta_id, v_alta.cuenta_nombre, v_antes, p_pct, btrim(p_motivo),
            p_admin, p_admin_email, v_ahora);
    v_escritas := v_escritas + 1;
    cuenta_id := v_alta.cuenta_id; cuenta_nombre := v_alta.cuenta_nombre;
    pct_antes := v_antes; pct_despues := p_pct::numeric(5, 2);
    return next;
  end loop;
end;
$$;

comment on function public.cambiar_porcentaje_de_salones(uuid, uuid[], numeric, text, uuid, text) is
  'Cambia el % de salones que ya llegaron (0075), con motivo obligatorio. Vale para lo que generen '
  'desde ahora; lo devengado no se toca. Devuelve los salones que cambiaron (los que ya iban a ese % no).';

revoke all on function public.cambiar_porcentaje_de_salones(uuid, uuid[], numeric, text, uuid, text)
  from public, anon, authenticated;
grant execute on function public.cambiar_porcentaje_de_salones(uuid, uuid[], numeric, text, uuid, text)
  to service_role;

commit;
