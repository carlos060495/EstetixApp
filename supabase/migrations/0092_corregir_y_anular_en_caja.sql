-- 0092 · Caja: corregir y anular, con motivo y registro. NO BORRA NADA.
--
-- VA ANTES DE DESPLEGAR EL BACKEND: el backend nuevo llama a las dos funciones
-- de aquí y lee las columnas nuevas. El backend viejo sigue funcionando con
-- ella puesta (solo añade columnas; quita el UPDATE y el DELETE de la sesión
-- sobre ingresos y gastos, que el backend viejo solo usaba en dos rutas que
-- ninguna pantalla llamaba).
--
-- Carlos (29-10-2026): un ingreso o un gasto equivocado se CORRIGE, y en vez de
-- borrarse se ANULA: la fila se queda, deja de sumar, y las cuentas cuadran.
-- Las dos cosas con motivo obligatorio y registro que no se puede editar ni
-- borrar: valor anterior, nuevo, quién y cuándo. Es la regla de todo lo que
-- toca dinero (la misma de las correcciones de fichaje, 0061).
--
-- ═══ LO QUE NO SE CORRIGE AQUÍ ═══
--
-- Los ingresos por SERVICIOS no son filas de esta tabla: salen de las citas
-- completadas (0007). Se corrigen en su cita; dos puertas a la misma verdad
-- acabarían diciendo cosas distintas.
--
-- ═══ EL STOCK ═══
--
-- Anular una VENTA de producto devuelve sus unidades; anular una COMPRA las
-- resta, y si no hay bastantes SE PARA (antes parar que dejar un número
-- imposible). Corregir la cantidad ajusta la diferencia, con la misma parada.
--
-- Hasta hoy el gasto de una compra de stock no decía de qué producto era ni
-- cuántas unidades (solo «Compra de stock» y el importe). Desde aquí lo dice
-- (`expenses.product_id`, `expenses.cantidad`) y el backend lo rellena. Las
-- compras de antes no se pueden reconstruir: al anularlas se anula el dinero y
-- el stock no se toca. Cuando el stock NO se mueve en un movimiento de stock,
-- el registro lo deja ESCRITO, con el porqué (`stock_sin_ajustar`): dentro de
-- medio año, quien compare la caja con el stock tiene que leerlo, no deducirlo.
--
-- ═══ POR QUÉ NO SE PUEDE SALTAR EL REGISTRO ═══
--
-- La sesión (`authenticated`) pierde el UPDATE y el DELETE sobre `incomes` y
-- `expenses`: con ellos, un PATCH directo a PostgREST cambiaría un importe sin
-- dejar rastro. Corregir y anular pasa solo por las dos funciones de abajo,
-- que solo puede llamar `service_role` (el backend, después de comprobar que
-- el movimiento es del centro de quien pregunta). Y una fila anulada ya no se
-- cambia: lo impide un trigger, también para `service_role`.

begin;

-- ───────────────────────────────────────────────────────────────────────────
-- 1. La compra dice de qué producto es y cuántas unidades
-- ───────────────────────────────────────────────────────────────────────────
alter table public.expenses
  add column product_id uuid references public.products(id) on delete set null,
  add column cantidad integer check (cantidad is null or cantidad > 0);

comment on column public.expenses.product_id is
  'El producto de una compra de stock (0092). NULL en un gasto normal, en una compra anterior a la 0092, o si el producto se borró después (entonces `cantidad` sigue puesta).';
comment on column public.expenses.cantidad is
  'Unidades de una compra de stock (0092). NULL en un gasto normal o en una compra anterior a la 0092.';

-- ───────────────────────────────────────────────────────────────────────────
-- 2. La anulación vive en la fila: deja de sumar, pero no desaparece
-- ───────────────────────────────────────────────────────────────────────────
alter table public.incomes
  add column anulado_at timestamptz,
  add column anulado_motivo text,
  add constraint incomes_anulacion_con_motivo
    check ((anulado_at is null) = (anulado_motivo is null));

alter table public.expenses
  add column anulado_at timestamptz,
  add column anulado_motivo text,
  add constraint expenses_anulacion_con_motivo
    check ((anulado_at is null) = (anulado_motivo is null));

-- ───────────────────────────────────────────────────────────────────────────
-- 3. El registro
-- ───────────────────────────────────────────────────────────────────────────
create table public.correcciones_de_caja (
  id bigint generated always as identity primary key,
  -- SIN CLAVES AJENAS, como todo registro (0057): no puede depender de que siga
  -- existiendo lo que nombra. Por eso lleva la cuenta y la foto de los nombres.
  account_id uuid not null,
  salon_id uuid not null,
  cuenta_nombre text,
  centro_nombre text,
  ocurrido_at timestamptz not null default now(),
  movimiento text not null check (movimiento in ('ingreso', 'gasto')),
  -- Sin clave ajena: el registro no depende de la fila que cuenta.
  movimiento_id uuid not null,
  accion text not null check (accion in ('corrige', 'anula')),
  -- Solo los campos que cambian. Al anular, `antes` es la fila entera.
  antes jsonb not null,
  despues jsonb,
  motivo text not null check (char_length(btrim(motivo)) >= 10),
  -- Lo que se hizo con el stock, si se movió: {product_id, producto, unidades}
  -- (unidades con signo: + devuelve, − resta).
  stock_movido jsonb,
  -- Si era un movimiento de stock y las unidades NO se movieron, por qué.
  stock_sin_ajustar text
    check (stock_sin_ajustar in ('compra_sin_producto', 'producto_borrado', 'otro')),
  stock_sin_ajustar_detalle text,
  usuario_id uuid not null,
  usuario_nombre text,
  usuario_email text,
  constraint correcciones_de_caja_forma check (
    (accion = 'corrige' and despues is not null) or
    (accion = 'anula' and despues is null)
  ),
  constraint correcciones_de_caja_sin_ajustar_dice_por_que check (
    (stock_sin_ajustar is null) = (stock_sin_ajustar_detalle is null)
  ),
  constraint correcciones_de_caja_o_se_movio_o_se_dice check (
    stock_movido is null or stock_sin_ajustar is null
  )
);

comment on table public.correcciones_de_caja is
  'Cada corrección o anulación de un ingreso o un gasto (0092): valor anterior, '
  'nuevo, motivo, quién y cuándo, y qué pasó con el stock. Sin claves ajenas; no se '
  'puede editar, y solo se borra cuando su centro ya no existe (0067).';

create index correcciones_de_caja_por_cuenta
  on public.correcciones_de_caja (account_id, ocurrido_at);
create index correcciones_de_caja_por_movimiento
  on public.correcciones_de_caja (salon_id, movimiento, movimiento_id, ocurrido_at);
create unique index correcciones_de_caja_una_anulacion
  on public.correcciones_de_caja (movimiento, movimiento_id)
  where accion = 'anula';

revoke all on public.correcciones_de_caja from anon, authenticated, service_role;
grant select, insert on public.correcciones_de_caja to service_role;
alter table public.correcciones_de_caja enable row level security;

-- Igual que 0061 + 0067: UPDATE y TRUNCATE, nunca. DELETE, solo si su centro
-- ya no existe (`se_borra_solo_sin_su_centro`, 0067): así lo encuentra y lo
-- borra el borrado a 90 días (0068), después de los centros.
create function public.correcciones_de_caja_no_se_tocan()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  raise exception 'correcciones_de_caja no se puede % : es un registro', tg_op
    using errcode = '42501';
end;
$$;

create trigger correcciones_de_caja_sin_update
  before update on public.correcciones_de_caja
  for each row execute function public.correcciones_de_caja_no_se_tocan();
create trigger correcciones_de_caja_sin_truncate
  before truncate on public.correcciones_de_caja
  for each statement execute function public.correcciones_de_caja_no_se_tocan();
create trigger correcciones_de_caja_se_van_con_su_centro
  before delete on public.correcciones_de_caja
  for each row execute function public.se_borra_solo_sin_su_centro();

-- ───────────────────────────────────────────────────────────────────────────
-- 4. Una fila anulada no se cambia; y la sesión no cambia ni borra ninguna
-- ───────────────────────────────────────────────────────────────────────────
create function public.lo_anulado_no_se_cambia()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if old.anulado_at is not null then
    raise exception '% ya está anulado: no se puede cambiar', tg_table_name
      using errcode = '42501';
  end if;
  return new;
end;
$$;

create trigger incomes_lo_anulado_no_se_cambia
  before update on public.incomes
  for each row execute function public.lo_anulado_no_se_cambia();
create trigger expenses_lo_anulado_no_se_cambia
  before update on public.expenses
  for each row execute function public.lo_anulado_no_se_cambia();

revoke update, delete on public.incomes from anon, authenticated;
revoke update, delete on public.expenses from anon, authenticated;

-- ───────────────────────────────────────────────────────────────────────────
-- 5. Anular
-- ───────────────────────────────────────────────────────────────────────────
-- Errores con nombre (errcode P0001, el mensaje es el código; `hint` lleva
-- los datos en JSON: el cliente del backend registra code, message y hint): no_encontrado · ya_anulado · stock_insuficiente.
create function public.anular_movimiento_de_caja(
  p_salon uuid,
  p_movimiento text,
  p_id uuid,
  p_motivo text,
  p_usuario uuid,
  p_usuario_nombre text,
  p_usuario_email text
) returns jsonb
language plpgsql
set search_path = ''
as $$
declare
  v_fila jsonb;
  v_tipo text;
  v_categoria text;
  v_product uuid;
  v_cantidad integer;
  v_es_de_stock boolean;
  v_signo integer;
  v_prod_id uuid;
  v_prod_nombre text;
  v_prod_stock integer;
  v_movido jsonb;
  v_sin text;
  v_sin_detalle text;
begin
  if p_movimiento = 'ingreso' then
    select to_jsonb(i), i.tipo::text, null, i.product_id, i.cantidad
      into v_fila, v_tipo, v_categoria, v_product, v_cantidad
      from public.incomes i
     where i.id = p_id and i.salon_id = p_salon
       for update;
    v_es_de_stock := v_tipo = 'producto';
    v_signo := 1;   -- anular una venta DEVUELVE las unidades
  elsif p_movimiento = 'gasto' then
    select to_jsonb(e), e.tipo::text, e.categoria, e.product_id, e.cantidad
      into v_fila, v_tipo, v_categoria, v_product, v_cantidad
      from public.expenses e
     where e.id = p_id and e.salon_id = p_salon
       for update;
    v_es_de_stock := v_categoria = 'Compra de stock' or v_product is not null;
    v_signo := -1;  -- anular una compra RESTA las unidades
  else
    raise exception 'no_encontrado' using errcode = 'P0001';
  end if;

  if v_fila is null then
    raise exception 'no_encontrado' using errcode = 'P0001';
  end if;
  if v_fila->>'anulado_at' is not null then
    raise exception 'ya_anulado' using errcode = 'P0001';
  end if;

  if v_es_de_stock then
    if v_product is not null and v_cantidad is not null then
      select p.id, p.nombre, p.stock into v_prod_id, v_prod_nombre, v_prod_stock
        from public.products p
       where p.id = v_product and p.salon_id = p_salon
         for update;
    end if;

    if v_prod_id is not null then
      if v_prod_stock + v_signo * v_cantidad < 0 then
        raise exception 'stock_insuficiente' using errcode = 'P0001',
          hint = jsonb_build_object('producto', v_prod_nombre, 'stock', v_prod_stock,
                                      'unidades', v_cantidad)::text;
      end if;
      update public.products set stock = stock + v_signo * v_cantidad where id = v_prod_id;
      v_movido := jsonb_build_object('product_id', v_prod_id, 'producto', v_prod_nombre,
                                     'unidades', v_signo * v_cantidad);
    elsif v_cantidad is not null then
      v_sin := 'producto_borrado';
      v_sin_detalle := format(
        'El producto de este movimiento se borró después: no hay a qué %s las %s unidades.',
        case when v_signo > 0 then 'devolver' else 'restar' end, v_cantidad);
    elsif p_movimiento = 'gasto' then
      v_sin := 'compra_sin_producto';
      v_sin_detalle :=
        'Compra anterior al registro de productos: no dice de qué producto ni cuántas unidades, así que el stock no se cambió.';
    else
      v_sin := 'otro';
      v_sin_detalle := 'Venta sin cantidad ni producto: el stock no se cambió.';
    end if;
  end if;

  if p_movimiento = 'ingreso' then
    update public.incomes set anulado_at = now(), anulado_motivo = btrim(p_motivo) where id = p_id;
  else
    update public.expenses set anulado_at = now(), anulado_motivo = btrim(p_motivo) where id = p_id;
  end if;

  insert into public.correcciones_de_caja
    (account_id, salon_id, cuenta_nombre, centro_nombre, movimiento, movimiento_id, accion, antes, despues,
     motivo, stock_movido, stock_sin_ajustar, stock_sin_ajustar_detalle,
     usuario_id, usuario_nombre, usuario_email)
  select s.account_id, s.id, a.nombre, s.nombre_comercial, p_movimiento, p_id, 'anula', v_fila, null,
         btrim(p_motivo), v_movido, v_sin, v_sin_detalle,
         p_usuario, p_usuario_nombre, p_usuario_email
    from public.salons s join public.accounts a on a.id = s.account_id
   where s.id = p_salon;

  return jsonb_build_object('stock_movido', v_movido, 'stock_sin_ajustar', v_sin,
                            'stock_sin_ajustar_detalle', v_sin_detalle);
end;
$$;

-- ───────────────────────────────────────────────────────────────────────────
-- 6. Corregir
-- ───────────────────────────────────────────────────────────────────────────
-- `p_cambios` trae solo los campos que se quieren cambiar (el backend ya los
-- ha validado). Se guardan en el registro los que de verdad cambian. Errores:
-- no_encontrado · ya_anulado · sin_cambios · campo_no_corregible ·
-- stock_insuficiente.
create function public.corregir_movimiento_de_caja(
  p_salon uuid,
  p_movimiento text,
  p_id uuid,
  p_cambios jsonb,
  p_motivo text,
  p_usuario uuid,
  p_usuario_nombre text,
  p_usuario_email text
) returns jsonb
language plpgsql
set search_path = ''
as $$
declare
  v_fila jsonb;
  v_nueva jsonb;
  v_permitidos text[];
  v_clave text;
  v_antes jsonb := '{}'::jsonb;
  v_despues jsonb := '{}'::jsonb;
  v_product uuid;
  v_vieja integer;
  v_nueva_cant integer;
  v_signo integer;
  v_prod_id uuid;
  v_prod_nombre text;
  v_prod_stock integer;
  v_movido jsonb;
begin
  if p_movimiento = 'ingreso' then
    select to_jsonb(i) into v_fila from public.incomes i
     where i.id = p_id and i.salon_id = p_salon for update;
  elsif p_movimiento = 'gasto' then
    select to_jsonb(e) into v_fila from public.expenses e
     where e.id = p_id and e.salon_id = p_salon for update;
  end if;
  if v_fila is null then
    raise exception 'no_encontrado' using errcode = 'P0001';
  end if;
  if v_fila->>'anulado_at' is not null then
    raise exception 'ya_anulado' using errcode = 'P0001';
  end if;

  -- Lo que se puede tocar depende de qué es. Una VENTA de producto: su importe
  -- lo fija el precio, así que se corrige la cantidad (y el importe sigue al
  -- precio por unidad de la venta). Un OTRO ingreso o un GASTO: sus campos.
  if p_movimiento = 'ingreso' and v_fila->>'tipo' = 'producto' then
    v_permitidos := array['fecha', 'concepto', 'cantidad'];
  elsif p_movimiento = 'ingreso' then
    v_permitidos := array['fecha', 'concepto', 'importe'];
  else
    v_permitidos := array['fecha', 'categoria', 'importe', 'tipo', 'estado', 'provider_id', 'cantidad'];
  end if;

  for v_clave in select jsonb_object_keys(p_cambios) loop
    if not v_clave = any (v_permitidos) then
      raise exception 'campo_no_corregible' using errcode = 'P0001', hint = v_clave;
    end if;
    if (v_fila -> v_clave) is distinct from (p_cambios -> v_clave)
       and not (v_clave = 'importe'
                and (v_fila->>'importe')::numeric = (p_cambios->>'importe')::numeric) then
      v_antes := v_antes || jsonb_build_object(v_clave, v_fila -> v_clave);
      v_despues := v_despues || jsonb_build_object(v_clave, p_cambios -> v_clave);
    end if;
  end loop;

  -- La cantidad mueve el stock (y solo si se sabe de qué producto es).
  if v_despues ? 'cantidad' then
    v_product := (v_fila->>'product_id')::uuid;
    v_vieja := (v_fila->>'cantidad')::integer;
    v_nueva_cant := (v_despues->>'cantidad')::integer;
    if v_product is null or v_vieja is null then
      raise exception 'campo_no_corregible' using errcode = 'P0001', hint = 'cantidad';
    end if;
    select p.id, p.nombre, p.stock into v_prod_id, v_prod_nombre, v_prod_stock
      from public.products p where p.id = v_product and p.salon_id = p_salon for update;
    if v_prod_id is null then
      raise exception 'campo_no_corregible' using errcode = 'P0001', hint = 'cantidad';
    end if;
    -- Venta: vender más unidades RESTA stock. Compra: comprar más SUMA.
    v_signo := case when p_movimiento = 'ingreso' then -1 else 1 end;
    if v_prod_stock + v_signo * (v_nueva_cant - v_vieja) < 0 then
      raise exception 'stock_insuficiente' using errcode = 'P0001',
        hint = jsonb_build_object('producto', v_prod_nombre, 'stock', v_prod_stock,
                                    'unidades', abs(v_nueva_cant - v_vieja))::text;
    end if;
    update public.products set stock = stock + v_signo * (v_nueva_cant - v_vieja) where id = v_prod_id;
    v_movido := jsonb_build_object('product_id', v_prod_id, 'producto', v_prod_nombre,
                                   'unidades', v_signo * (v_nueva_cant - v_vieja));
    -- El importe sigue a la cantidad al mismo precio por unidad, salvo que en
    -- un gasto se haya corregido también a mano.
    if not v_despues ? 'importe' then
      v_antes := v_antes || jsonb_build_object('importe', v_fila -> 'importe');
      v_despues := v_despues || jsonb_build_object('importe',
        round((v_fila->>'importe')::numeric / v_vieja * v_nueva_cant, 2));
    end if;
  end if;

  if v_despues = '{}'::jsonb then
    raise exception 'sin_cambios' using errcode = 'P0001';
  end if;

  if p_movimiento = 'ingreso' then
    update public.incomes i set
      fecha = coalesce((v_despues->>'fecha')::date, i.fecha),
      concepto = coalesce(v_despues->>'concepto', i.concepto),
      importe = coalesce((v_despues->>'importe')::numeric, i.importe),
      cantidad = coalesce((v_despues->>'cantidad')::integer, i.cantidad)
     where i.id = p_id
    returning to_jsonb(i) into v_nueva;
  else
    update public.expenses e set
      fecha = coalesce((v_despues->>'fecha')::date, e.fecha),
      categoria = coalesce(v_despues->>'categoria', e.categoria),
      importe = coalesce((v_despues->>'importe')::numeric, e.importe),
      tipo = coalesce((v_despues->>'tipo')::public.gasto_tipo, e.tipo),
      estado = coalesce((v_despues->>'estado')::public.gasto_estado, e.estado),
      provider_id = case when v_despues ? 'provider_id'
                         then (v_despues->>'provider_id')::uuid else e.provider_id end,
      cantidad = coalesce((v_despues->>'cantidad')::integer, e.cantidad)
     where e.id = p_id
    returning to_jsonb(e) into v_nueva;
  end if;

  insert into public.correcciones_de_caja
    (account_id, salon_id, cuenta_nombre, centro_nombre, movimiento, movimiento_id, accion, antes, despues,
     motivo, stock_movido, usuario_id, usuario_nombre, usuario_email)
  select s.account_id, s.id, a.nombre, s.nombre_comercial, p_movimiento, p_id, 'corrige', v_antes, v_despues,
         btrim(p_motivo), v_movido, p_usuario, p_usuario_nombre, p_usuario_email
    from public.salons s join public.accounts a on a.id = s.account_id
   where s.id = p_salon;

  return v_nueva;
end;
$$;

revoke all on function public.anular_movimiento_de_caja(uuid, text, uuid, text, uuid, text, text)
  from public, anon, authenticated;
revoke all on function public.corregir_movimiento_de_caja(uuid, text, uuid, jsonb, text, uuid, text, text)
  from public, anon, authenticated;
grant execute on function public.anular_movimiento_de_caja(uuid, text, uuid, text, uuid, text, text)
  to service_role;
grant execute on function public.corregir_movimiento_de_caja(uuid, text, uuid, jsonb, text, uuid, text, text)
  to service_role;

commit;
