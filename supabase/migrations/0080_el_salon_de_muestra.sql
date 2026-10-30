-- 0080 · El salón de muestra: una demo por visitante, que vive 24 horas.
--
-- Carlos, 07-10-2026. Un vendedor entra a un salón y no tiene nada que
-- enseñar, y quien llega a la web no ve nada sin dar su tarjeta. Desde la
-- portada, un botón abre un salón de muestra: el SUYO, creado en ese momento,
-- con datos inventados dentro. Nunca una demo compartida.
--
-- ═══ QUÉ ES UNA DEMO ═══
--
--   · Una cuenta con `accounts.es_demo = true`. Lo pone `crear_demo` al
--     nacer y NO CAMBIA NUNCA (trigger): una cuenta de verdad no se convierte
--     en demo —el barrido la borraría— ni una demo en cliente.
--   · Su ciclo de vida, en `demos`: de qué navegador es (el HASH de un token
--     aleatorio que el backend guarda en una cookie), cuándo nace y cuándo
--     caduca (24 horas después). Solo `service_role` la lee.
--   · Un centro, una administradora (con su usuario de Auth, que crea el
--     backend con un correo de un dominio que no existe) y datos inventados:
--     horario, servicios, empleadas, clientas (correos de `example.com`, sin
--     teléfono), citas repartidas por la agenda, caja, gastos y stock.
--
-- ═══ VOLVER ES VOLVER A LA MISMA ═══
--
-- `crear_demo(hash)` mira primero si ese navegador tiene una demo VIVA: si la
-- tiene, la devuelve (`ya_tiene`) y no crea nada. Un clic no crea una demo; la
-- crea un navegador que no tiene ninguna viva. Caducada (24 h), ya no vale: el
-- siguiente clic crea otra. Y una sesión de una demo caducada no consigue
-- token (el gancho del token, más abajo) ni pasa por el backend.
--
-- ═══ LO QUE UNA DEMO NO PUEDE HACER (lo de la base) ═══
--
-- Ni suscripción, ni atribución de un referido, ni comisión: un trigger en
-- cada una de esas tablas rechaza la fila de una cuenta demo
-- (`una_demo_no_es_un_cliente`). Lo que sale al mundo (correo, WhatsApp,
-- Stripe) lo corta el backend, en cada salida (`core/demo.py`). Y no ve datos
-- de otro salón: es una administradora como cualquier otra, con la RLS de
-- siempre; lo vigila `test_el_salon_de_muestra_base_real.py` recorriendo todas
-- las tablas.
--
-- ═══ LOS TOPES: EN LA BASE, EN UN SOLO SITIO, Y LOS CAMBIA EL OWNER ═══
--
-- `topes_de_demos` (una fila): cuántas demos pueden estar vivas a la vez,
-- cuántas se pueden crear por hora en total y cuántas puede crear UNA IP por
-- hora. Los cambia el owner desde el panel con
-- motivo (`cambiar_tope_de_demos`), y cada cambio queda en
-- `cambios_de_tope_de_demos`, que no se edita ni se borra (como el mínimo de
-- pago, 0077). Pasado un tope, `crear_demo` contesta `sin_sitio_vivas`,
-- `sin_sitio_hora` o `sin_sitio_ip`, y el botón dice que lo intente en un rato
-- (lo mismo para los tres: nunca qué tope saltó). Ningún tope vive en el
-- código: lo vigila `test_los_topes_de_las_demos_viven_en_la_base.py`.
--
-- EL FRENO POR IP lo cuenta el backend, con la misma ventana deslizante que el
-- freno del código de vendedor (`VentanaPorIP`, 60 minutos): sin guardar la IP
-- en la base. La cifra la lee de aquí en cada petición y le pasa a
-- `crear_demo` si esa IP ya no puede CREAR (`p_puede_crear`). Volver a la demo
-- viva no cuenta ni se frena. El tope global por hora sigue igual: el de la IP
-- es para que una sola persona no se lo coma entero.
--
-- ═══ EL BARRIDO: CON LA FORMA DEL BORRADO DE 90 DÍAS (0068) ═══
--
--   · NACE APAGADO: `barrido_demos_interruptor`, una fila. Se enciende a mano,
--     como postgres, diciendo quién y por qué; cada cambio queda en
--     `barrido_demos_interruptor_historial`, que no se edita ni se borra.
--     Apagado, `barrer_demos()` se NIEGA con un error (55000).
--   · ENSAYO: `ensayo_del_barrido()` enseña qué borraría, sin borrar nada (es
--     `stable`: Postgres no le deja borrar). Funciona encendido o apagado.
--   · TOPE POR PASADA: en el interruptor (`tope_por_pasada`). Si hay más
--     caducadas, borra las más antiguas hasta el tope y deja el resto para la
--     pasada siguiente.
--   · CONSTANCIA: `barridos_de_demos`, una fila por pasada: cuándo, cuántas
--     borró, cuántas quedaron y cuáles (solo sus ids). No se edita ni se borra.
--   · SOLO DEMOS: la selección pide `es_demo` y CADA borrado vuelve a mirarlo,
--     con la cuenta bloqueada, y lanza si no lo es (`borrar_una_demo`). Ni
--     aunque se le pida una cuenta de verdad.
--   · LO QUE SE BORRA es el plan de la 0068 (`plan_de_borrado`), el mismo que
--     el del borrado a 90 días: la cuenta, sus centros, su usuario de Auth y
--     todo lo que cuelga de ellos. No se escribe en `cuentas_borradas`: una
--     demo no era un cliente.
--
-- ═══ LO QUE NO SE TOCA ═══
--
-- El cálculo de la comisión, los pagos a Stripe, las atribuciones, los plazos
-- de 30 y 90 días y el borrado de los 90 días. De la 0068 solo se LLAMA a
-- `plan_de_borrado`, sin cambiarlo. Del gancho del token (0069) se rehace el
-- mismo cuerpo con UNA guarda más, marcada.
--
-- COLUMNAS OBLIGATORIAS: `accounts.es_demo` nace `not null` con valor por
-- defecto `false`, que es lo que son todas las que hay. Las tablas nuevas: sus
-- columnas `not null` las escriben las funciones de aquí.
--
-- ═══ ORDEN DE DESPLIEGUE: 0080 → BACKEND + FRONTEND (a la vez) ═══
--
-- Compatible con el backend desplegado: no cambia nada de lo que ya llama. El
-- barrido nace apagado y nadie lo llama hasta que se programe la tarea.
-- Ningún paso en Stripe.
--
-- Probado en local, contra la base de verdad: `test_el_salon_de_muestra_base_real.py`.

begin;

-- ═══════════════ 1. qué cuenta es una demo, para siempre

alter table public.accounts add column es_demo boolean not null default false;

comment on column public.accounts.es_demo is
  'Salón de muestra (0080). Lo pone crear_demo al nacer y no cambia nunca. Una demo no es un cliente.';

create function public.es_demo_no_cambia()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if new.es_demo is distinct from old.es_demo then
    raise exception 'accounts.es_demo no cambia: una demo no se convierte en cliente ni al revés' using errcode = '42501';
  end if;
  return new;
end;
$$;

create trigger accounts_es_demo_no_cambia
  before update of es_demo on public.accounts
  for each row execute function public.es_demo_no_cambia();

-- ═══════════════ 2. su ciclo de vida

create table public.demos (
  account_id uuid primary key references public.accounts (id) on delete cascade,
  -- sha256 del token de la cookie del navegador. El token, nunca.
  navegador_hash text not null check (navegador_hash ~ '^[0-9a-f]{64}$'),
  creada_at timestamptz not null default now(),
  caduca_at timestamptz not null,
  -- El usuario de Auth de su administradora. Nulo un instante: entre crearla y
  -- que el backend le cree el usuario.
  auth_user_id uuid,
  constraint una_demo_caduca_despues_de_nacer check (caduca_at > creada_at)
);

create index demos_por_navegador on public.demos (navegador_hash, caduca_at desc);
create index demos_por_caducidad on public.demos (caduca_at);
create index demos_por_creacion on public.demos (creada_at);

comment on table public.demos is
  'Los salones de muestra (0080): de qué navegador, cuándo nacen y cuándo caducan (24 h). Solo service_role.';

alter table public.demos enable row level security;
revoke all on public.demos from public, anon, authenticated, service_role;
grant select on public.demos to service_role;

-- ═══════════════ 3. los topes: una fila, los cambia el owner con motivo

create table public.topes_de_demos (
  unico boolean primary key default true check (unico),
  vivas_max int not null check (vivas_max > 0),
  por_hora_max int not null check (por_hora_max > 0),
  -- Cuántas puede crear UNA IP por hora (la cuenta el backend, ver la cabecera).
  por_ip_hora_max int not null check (por_ip_hora_max > 0)
);

-- LAS CIFRAS VIVEN AQUÍ y solo aquí. Medidas para el arranque: un vendedor
-- enseña una o dos al día, y la web pública trae pocas visitas todavía.
insert into public.topes_de_demos (vivas_max, por_hora_max, por_ip_hora_max) values (200, 40, 3);

comment on table public.topes_de_demos is
  'Cuántas demos pueden estar vivas a la vez, cuántas se crean por hora y cuántas por IP y hora (0080). '
  'Los cambia el owner con cambiar_tope_de_demos (motivo y registro).';

create table public.cambios_de_tope_de_demos (
  id bigint generated always as identity primary key,
  tope text not null check (tope in ('vivas_max', 'por_hora_max', 'por_ip_hora_max')),
  antes int not null,
  despues int not null check (despues > 0),
  motivo text not null check (char_length(btrim(motivo)) >= 10),
  hecho_por uuid not null,
  hecho_por_email text not null check (btrim(hecho_por_email) <> ''),
  hecho_at timestamptz not null default now(),
  constraint un_cambio_de_tope_cambia_algo check (antes <> despues)
);

comment on table public.cambios_de_tope_de_demos is
  'Cada cambio de un tope de las demos (0080): quién, cuándo, de cuánto a cuánto y por qué. No se edita ni se borra.';

alter table public.topes_de_demos enable row level security;
alter table public.cambios_de_tope_de_demos enable row level security;
revoke all on public.topes_de_demos, public.cambios_de_tope_de_demos from public, anon, authenticated, service_role;
grant select on public.topes_de_demos, public.cambios_de_tope_de_demos to service_role;

create trigger cambios_de_tope_de_demos_sin_update_ni_delete
  before update or delete on public.cambios_de_tope_de_demos
  for each row execute function public.las_comisiones_no_se_tocan();
create trigger cambios_de_tope_de_demos_sin_truncate
  before truncate on public.cambios_de_tope_de_demos
  for each statement execute function public.las_comisiones_no_se_tocan();

create function public.cambiar_tope_de_demos(p_tope text, p_valor int, p_motivo text, p_admin uuid, p_admin_email text)
returns table (resultado text, antes int, despues int)
language plpgsql
security definer
set search_path = public, pg_temp
as $$
#variable_conflict use_column
declare
  v_antes int;
begin
  if p_tope is null or p_tope not in ('vivas_max', 'por_hora_max', 'por_ip_hora_max') then
    raise exception 'cambiar_tope_de_demos: el tope % no existe', p_tope using errcode = '22023';
  end if;
  if p_valor is null or p_valor <= 0 then
    raise exception 'cambiar_tope_de_demos: el tope tiene que ser mayor que cero' using errcode = '22023';
  end if;
  if p_motivo is null or char_length(btrim(p_motivo)) < 10 then
    raise exception 'cambiar_tope_de_demos: hace falta un motivo escrito' using errcode = '22023';
  end if;
  if p_admin is null or coalesce(btrim(p_admin_email), '') = '' then
    raise exception 'cambiar_tope_de_demos: falta quién lo hace' using errcode = '22023';
  end if;
  -- Con la fila bloqueada: dos cambios a la vez, uno detrás del otro. SIN SQL
  -- dinámico, a propósito: así cada columna tiene un escritor que se ve
  -- (`test_columnas_leidas_que_nadie_escribe.py`).
  select case p_tope when 'vivas_max' then t.vivas_max when 'por_hora_max' then t.por_hora_max
                     else t.por_ip_hora_max end
  into v_antes from public.topes_de_demos t where t.unico for update;
  if v_antes = p_valor then
    resultado := 'ya_es'; antes := v_antes; despues := p_valor; return next; return;
  end if;
  update public.topes_de_demos t set
    vivas_max = case when p_tope = 'vivas_max' then p_valor else t.vivas_max end,
    por_hora_max = case when p_tope = 'por_hora_max' then p_valor else t.por_hora_max end,
    por_ip_hora_max = case when p_tope = 'por_ip_hora_max' then p_valor else t.por_ip_hora_max end
  where t.unico;
  insert into public.cambios_de_tope_de_demos (tope, antes, despues, motivo, hecho_por, hecho_por_email)
  values (p_tope, v_antes, p_valor, btrim(p_motivo), p_admin, btrim(p_admin_email));
  resultado := 'cambiado'; antes := v_antes; despues := p_valor; return next;
end;
$$;

revoke all on function public.cambiar_tope_de_demos(text, int, text, uuid, text) from public, anon, authenticated;
grant execute on function public.cambiar_tope_de_demos(text, int, text, uuid, text) to service_role;

-- ═══════════════ 4. una demo no es un cliente: ni suscripción, ni atribución, ni comisión

create function public.una_demo_no_es_un_cliente()
returns trigger
language plpgsql
set search_path = public, pg_temp
as $$
declare
  -- Por su nombre, sin nombrar la columna: `subscriptions` la llama account_id
  -- y las de referidos cuenta_id.
  v_cuenta uuid := coalesce(to_jsonb(new) ->> 'account_id', to_jsonb(new) ->> 'cuenta_id')::uuid;
begin
  if exists (select 1 from public.accounts a where a.id = v_cuenta and a.es_demo) then
    raise exception 'una demo no es un cliente: no tiene % (0080)', tg_table_name using errcode = '42501';
  end if;
  return new;
end;
$$;

create trigger subscriptions_una_demo_no_es_un_cliente
  before insert or update of account_id on public.subscriptions
  for each row execute function public.una_demo_no_es_un_cliente();
create trigger altas_atribuidas_una_demo_no_es_un_cliente
  before insert on public.altas_atribuidas
  for each row execute function public.una_demo_no_es_un_cliente();
create trigger comisiones_una_demo_no_es_un_cliente
  before insert on public.comisiones
  for each row execute function public.una_demo_no_es_un_cliente();

-- ═══════════════ 5. crear (o volver a) la demo de un navegador

create function public.demo_viva_de(p_navegador_hash text)
returns table (account_id uuid, salon_id uuid, auth_user_id uuid, caduca_at timestamptz)
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select d.account_id, (select s.id from public.salons s where s.account_id = d.account_id order by s.created_at limit 1),
         d.auth_user_id, d.caduca_at
  from public.demos d
  where d.navegador_hash = p_navegador_hash and d.caduca_at > now()
  order by d.caduca_at desc
  limit 1;
$$;

revoke all on function public.demo_viva_de(text) from public, anon, authenticated;
grant execute on function public.demo_viva_de(text) to service_role;

-- Los datos inventados de un salón recién creado. Solo la llama crear_demo.
create function public.sembrar_demo(p_salon uuid)
returns void
language plpgsql
set search_path = public, pg_temp
as $$
declare
  z constant text := 'Europe/Madrid';
  hoy date := (now() at time zone z)::date;
  c_pelo uuid; c_estetica uuid; c_unas uuid; c_prod uuid;
  emp uuid[]; srv uuid[]; cli uuid[]; prod uuid[];
  e int; d int; dia date; hora time; s uuid; dur int; n int := 0; estado public.cita_estado; inicio timestamptz;
begin
  -- Horario del salón: de lunes a viernes de 10 a 20, sábado de 10 a 14.
  insert into public.salon_schedule (salon_id, dia_semana, cerrado, apertura, cierre)
  select p_salon, g, g = 7, time '10:00', case when g = 6 then time '14:00' else time '20:00' end
  from generate_series(1, 7) g;

  insert into public.service_categories (salon_id, nombre, tipo, color) values
    (p_salon, 'Peluquería', 'servicio', '#C9A84C') returning id into c_pelo;
  insert into public.service_categories (salon_id, nombre, tipo, color) values
    (p_salon, 'Estética', 'servicio', '#8FB3D9') returning id into c_estetica;
  insert into public.service_categories (salon_id, nombre, tipo, color) values
    (p_salon, 'Uñas', 'servicio', '#D98FB0') returning id into c_unas;
  insert into public.service_categories (salon_id, nombre, tipo, color) values
    (p_salon, 'Productos', 'producto', '#9AD9A6') returning id into c_prod;

  with nuevos as (
    insert into public.services (salon_id, category_id, nombre, precio, duracion_min, visible_online)
    values (p_salon, c_pelo, 'Corte y peinado', 28, 45, true),
           (p_salon, c_pelo, 'Tinte raíz', 42, 75, true),
           (p_salon, c_pelo, 'Mechas balayage', 95, 120, true),
           (p_salon, c_estetica, 'Limpieza facial', 48, 60, true),
           (p_salon, c_estetica, 'Depilación piernas', 25, 30, true),
           (p_salon, c_estetica, 'Diseño de cejas', 14, 15, true),
           (p_salon, c_unas, 'Manicura semipermanente', 22, 45, true),
           (p_salon, c_unas, 'Pedicura spa', 35, 60, true)
    returning id, nombre
  ) select array_agg(id order by array_position(array['Corte y peinado', 'Tinte raíz', 'Mechas balayage', 'Limpieza facial',
      'Depilación piernas', 'Diseño de cejas', 'Manicura semipermanente', 'Pedicura spa'], nombre)) into srv from nuevos;

  with nuevas as (
    insert into public.employees (salon_id, nombre, comision_tipo, comision_servicios, estado)
    values (p_salon, 'Lucía Navarro', 'porcentaje', 10, 'activo'),
           (p_salon, 'Marta Ibáñez', 'porcentaje', 10, 'activo'),
           (p_salon, 'Sara Delgado', 'porcentaje', 8, 'activo')
    returning id, nombre
  ) select array_agg(id order by array_position(array['Lucía Navarro', 'Marta Ibáñez', 'Sara Delgado'], nombre)) into emp from nuevas;

  insert into public.employee_schedule (employee_id, dia_semana, cerrado, apertura, cierre)
  select x, g, g = 7, time '10:00', case when g = 6 then time '14:00' else time '20:00' end
  from unnest(emp) x, generate_series(1, 7) g;

  -- Lucía, peluquería; Marta, estética; Sara, uñas y cejas.
  insert into public.employee_services (employee_id, service_id)
  select emp[1], srv[i] from generate_series(1, 3) i
  union all select emp[2], srv[i] from generate_series(4, 6) i
  union all select emp[3], srv[i] from unnest(array[6, 7, 8]) i;

  -- CLIENTAS INVENTADAS. Correos de example.com (reservado: no es de nadie) y
  -- SIN teléfono. Un número inventado también es de alguien.
  with nuevas as (
    insert into public.clients (salon_id, nombre, email, alergias, notas, vip)
    select p_salon, x.nombre, x.correo, x.alergias, x.notas, x.vip
    from (values
      ('Laura Gómez', 'laura.gomez@example.com', null, 'Prefiere las citas de tarde.', true),
      ('Carmen Ruiz', 'carmen.ruiz@example.com', 'Alergia al látex', null, false),
      ('Elena Martín', 'elena.martin@example.com', null, null, false),
      ('Paula Serrano', 'paula.serrano@example.com', null, 'Viene con su hija.', false),
      ('Andrea López', 'andrea.lopez@example.com', null, null, true),
      ('Nuria Castro', 'nuria.castro@example.com', 'Piel sensible', null, false),
      ('Isabel Ortega', 'isabel.ortega@example.com', null, null, false),
      ('Raquel Molina', 'raquel.molina@example.com', null, 'Tono habitual: 7.1', false),
      ('Silvia Romero', 'silvia.romero@example.com', null, null, false),
      ('Beatriz Gil', 'beatriz.gil@example.com', null, null, false),
      ('Patricia Vidal', 'patricia.vidal@example.com', null, null, false),
      ('Cristina Rubio', 'cristina.rubio@example.com', null, null, false)
    ) as x (nombre, correo, alergias, notas, vip)
    returning id, nombre
  ) select array_agg(id order by nombre) into cli from nuevas;

  -- CITAS: de hace cuatro días a dentro de cinco, cada empleada con su hueco
  -- seguido (nunca se pisan) y solo dentro del horario.
  for d in -4 .. 5 loop
    dia := hoy + d;
    continue when extract(isodow from dia) = 7;
    for e in 1 .. 3 loop
      hora := time '10:00' + make_interval(mins => 15 * ((e + d + 8) % 4));
      loop
        s := case e when 1 then srv[1 + (n % 3)] when 2 then srv[4 + (n % 3)] else srv[(array[6, 7, 8])[1 + (n % 3)]] end;
        select duracion_min into dur from public.services where id = s;
        exit when hora + make_interval(mins => dur)
                  > case when extract(isodow from dia) = 6 then time '14:00' else time '20:00' end;
        inicio := (dia + hora) at time zone z;
        estado := case
          when inicio + make_interval(mins => dur) < now() then
            case when n % 11 = 5 then 'no_show' when n % 13 = 7 then 'cancelada' else 'completada' end
          when n % 3 = 0 then 'pendiente' else 'confirmada' end;
        insert into public.appointments (salon_id, client_id, employee_id, service_id, inicio, fin, estado, canal)
        values (p_salon, cli[1 + (n * 7 + e) % array_length(cli, 1)], emp[e], s, inicio,
                inicio + make_interval(mins => dur), estado,
                (array['manual', 'online', 'whatsapp', 'manual']::public.cita_canal[])[1 + n % 4]);
        n := n + 1;
        -- Un hueco libre de vez en cuando: una agenda llena del todo no es real.
        hora := hora + make_interval(mins => dur + case when n % 3 = 0 then 45 else 15 end);
      end loop;
    end loop;
  end loop;

  -- STOCK: dos por debajo del mínimo, para que se vea el aviso.
  with nuevos as (
    insert into public.products (salon_id, category_id, nombre, marca, precio, precio_coste, stock, stock_min,
                                 venta_publico, consumo_interno)
    values (p_salon, c_prod, 'Champú reparador 300 ml', 'Aurea', 18.5, 8.2, 14, 5, true, false),
           (p_salon, c_prod, 'Mascarilla hidratante', 'Aurea', 22, 9.9, 3, 5, true, false),
           (p_salon, c_prod, 'Sérum facial vitamina C', 'Lumen', 34, 15, 7, 3, true, false),
           (p_salon, c_prod, 'Esmalte semipermanente', 'Nácar', 9.9, 3.1, 2, 6, false, true),
           (p_salon, c_prod, 'Crema de manos', 'Lumen', 12, 4.8, 20, 4, true, false),
           (p_salon, c_prod, 'Tinte 7.1 rubio ceniza', 'Aurea', 7.5, 3.4, 11, 6, false, true)
    returning id, nombre
  ) select array_agg(id order by array_position(array['Champú reparador 300 ml', 'Mascarilla hidratante',
      'Sérum facial vitamina C', 'Esmalte semipermanente', 'Crema de manos', 'Tinte 7.1 rubio ceniza'], nombre)) into prod from nuevos;

  -- CAJA: ventas de producto de los últimos días.
  insert into public.incomes (salon_id, fecha, concepto, tipo, importe, product_id, cantidad)
  values (p_salon, hoy - 4, 'Champú reparador 300 ml', 'producto', 37, prod[1], 2),
         (p_salon, hoy - 3, 'Sérum facial vitamina C', 'producto', 34, prod[3], 1),
         (p_salon, hoy - 2, 'Crema de manos', 'producto', 24, prod[5], 2),
         (p_salon, hoy - 1, 'Mascarilla hidratante', 'producto', 22, prod[2], 1),
         (p_salon, hoy - 1, 'Bono regalo', 'otro', 60, null, 1);

  insert into public.providers (salon_id, nombre, categoria, notas)
  values (p_salon, 'Distribuciones Aurea', 'Peluquería', 'Pedido mínimo 150 €.'),
         (p_salon, 'Lumen Cosmética', 'Estética', null);

  insert into public.expenses (salon_id, fecha, categoria, tipo, estado, importe)
  values (p_salon, date_trunc('month', hoy)::date, 'Alquiler', 'fijo', 'pagado', 950),
         (p_salon, hoy - 3, 'Productos', 'variable', 'pendiente', 184.6),
         (p_salon, hoy - 6, 'Luz', 'fijo', 'pagado', 132.4);
end;
$$;

revoke all on function public.sembrar_demo(uuid) from public, anon, authenticated, service_role;

create function public.crear_demo(p_navegador_hash text, p_correo text, p_puede_crear boolean default true)
returns table (resultado text, account_id uuid, salon_id uuid, auth_user_id uuid, caduca_at timestamptz)
language plpgsql
security definer
set search_path = public, pg_temp
as $$
#variable_conflict use_column
declare
  v_topes public.topes_de_demos;
  v_cuenta uuid;
  v_salon uuid;
  v_caduca timestamptz := now() + interval '24 hours';
begin
  if p_navegador_hash is null or p_navegador_hash !~ '^[0-9a-f]{64}$' then
    raise exception 'crear_demo: el navegador viene sin su hash' using errcode = '22023';
  end if;
  if p_correo is null or position('@' in p_correo) < 2 then
    raise exception 'crear_demo: falta el correo de su administradora' using errcode = '22023';
  end if;

  -- Una creación a la vez: los topes se cuentan sin carreras.
  perform pg_advisory_xact_lock(hashtext('crear_demo'));

  -- VOLVER ES VOLVER A LA MISMA.
  select d.account_id, d.salon_id, d.auth_user_id, d.caduca_at into v_cuenta, v_salon, auth_user_id, caduca_at
  from public.demo_viva_de(p_navegador_hash) d;
  if v_cuenta is not null then
    resultado := 'ya_tiene'; account_id := v_cuenta; salon_id := v_salon; return next; return;
  end if;

  -- EL FRENO POR IP (lo cuenta el backend): esta IP ya creó las que podía.
  if not coalesce(p_puede_crear, false) then
    resultado := 'sin_sitio_ip'; return next; return;
  end if;

  select * into v_topes from public.topes_de_demos where unico;
  if (select count(*) from public.demos d where d.caduca_at > now()) >= v_topes.vivas_max then
    resultado := 'sin_sitio_vivas'; return next; return;
  end if;
  if (select count(*) from public.demos d where d.creada_at > now() - interval '1 hour') >= v_topes.por_hora_max then
    resultado := 'sin_sitio_hora'; return next; return;
  end if;

  insert into public.accounts (nombre, plan, es_demo) values ('Estética Aurora (muestra)', 'elite', true)
  returning id into v_cuenta;
  -- Sin teléfono, sin correo, sin página pública de reservas ni recordatorios:
  -- una demo no tiene a quién escribir ni por dónde la encuentren.
  insert into public.salons (account_id, nombre_comercial, direccion, zona_horaria, reservas_online_activo,
                             recordatorios_email_activo)
  values (v_cuenta, 'Estética Aurora (muestra)', 'Calle de la Muestra 1, Madrid', 'Europe/Madrid', false, false)
  returning id into v_salon;
  insert into public.users (salon_id, account_id, nombre, email, rol)
  values (v_salon, v_cuenta, 'Quien prueba la demo', lower(btrim(p_correo)), 'admin');
  insert into public.demos (account_id, navegador_hash, caduca_at) values (v_cuenta, p_navegador_hash, v_caduca);

  perform public.sembrar_demo(v_salon);

  resultado := 'creada'; account_id := v_cuenta; salon_id := v_salon; auth_user_id := null; caduca_at := v_caduca;
  return next;
end;
$$;

comment on function public.crear_demo(text, text, boolean) is
  'La demo de un navegador (0080): la que ya tiene viva (ya_tiene), o una nueva con datos inventados (creada), '
  'o ninguna si se pasa un tope (sin_sitio_vivas | sin_sitio_hora | sin_sitio_ip).';

revoke all on function public.crear_demo(text, text, boolean) from public, anon, authenticated;
grant execute on function public.crear_demo(text, text, boolean) to service_role;

-- El usuario de Auth que acaba de crear el backend, enlazado a su ficha.
create function public.enlazar_demo(p_cuenta uuid, p_auth_user uuid)
returns boolean
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  if not exists (select 1 from public.accounts a where a.id = p_cuenta and a.es_demo) then
    raise exception 'enlazar_demo: % no es una demo', p_cuenta using errcode = '42501';
  end if;
  update public.demos set auth_user_id = p_auth_user where account_id = p_cuenta and auth_user_id is null;
  if not found then
    raise exception 'enlazar_demo: la demo % ya tiene usuario', p_cuenta using errcode = '55000';
  end if;
  update public.users set auth_user_id = p_auth_user where account_id = p_cuenta and auth_user_id is null;
  return true;
end;
$$;

revoke all on function public.enlazar_demo(uuid, uuid) from public, anon, authenticated;
grant execute on function public.enlazar_demo(uuid, uuid) to service_role;

-- ═══════════════ 6. borrar una demo: SOLO una demo, ni aunque se le pida otra

create function public.borrar_una_demo(p_cuenta uuid)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  pl record;
  paso jsonb;
  plan jsonb := '[]'::jsonb;
  filas jsonb := '{}'::jsonb;
  n bigint;
  v_centros uuid[];
begin
  -- LA COMPROBACIÓN QUE NO SE SALTA NADIE: con la cuenta bloqueada.
  perform 1 from public.accounts a where a.id = p_cuenta and a.es_demo for update;
  if not found then
    raise exception 'borrar_una_demo: la cuenta % NO es una demo. No se borra nada.', p_cuenta using errcode = '42501';
  end if;
  v_centros := array(select s.id from public.salons s where s.account_id = p_cuenta);
  select count(*) into n from storage.objects o
  where o.bucket_id = 'salon-assets' and split_part(o.name, '/', 1) = any (v_centros::text[]);
  if n > 0 then
    raise exception 'La demo % tiene % ficheros en Storage: una demo no sube ficheros. No se borra.', p_cuenta, n
      using errcode = '55000';
  end if;

  -- EL MISMO PLAN que el borrado a 90 días (0068), sin cambiarlo.
  for pl in select * from public.plan_de_borrado(p_cuenta) loop
    execute format('select count(*) from %s where %s', pl.tabla, pl.predicado) into n;
    filas := filas || jsonb_build_object(pl.tabla, n);
    if pl.orden is not null then
      plan := plan || jsonb_build_object('tabla', pl.tabla, 'predicado', pl.predicado, 'orden', pl.orden);
    end if;
  end loop;
  for paso in select x from jsonb_array_elements(plan) x order by (x ->> 'orden')::int loop
    execute format('delete from %s where %s', paso ->> 'tabla', paso ->> 'predicado');
  end loop;
  for paso in select x from jsonb_array_elements(plan) x loop
    execute format('select count(*) from %s where %s', paso ->> 'tabla', paso ->> 'predicado') into n;
    if n > 0 then
      raise exception 'El borrado de la demo % dejó % filas en %: no se borra nada.', p_cuenta, n, paso ->> 'tabla';
    end if;
  end loop;
  return filas;
end;
$$;

-- Solo la llaman las funciones de aquí (y postgres): nadie más borra.
revoke all on function public.borrar_una_demo(uuid) from public, anon, authenticated, service_role;

-- Si el backend no pudo crearle el usuario de Auth, la demo recién nacida se
-- deshace en el acto (y solo esa: sin usuario todavía).
create function public.deshacer_demo(p_cuenta uuid)
returns boolean
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  if not exists (select 1 from public.demos d where d.account_id = p_cuenta and d.auth_user_id is null) then
    raise exception 'deshacer_demo: % no es una demo recién nacida sin usuario', p_cuenta using errcode = '42501';
  end if;
  perform public.borrar_una_demo(p_cuenta);
  return true;
end;
$$;

revoke all on function public.deshacer_demo(uuid) from public, anon, authenticated;
grant execute on function public.deshacer_demo(uuid) to service_role;

-- ═══════════════ 7. el barrido: interruptor, ensayo, tope y constancia

create table public.barrido_demos_interruptor (
  unico boolean primary key default true check (unico),
  encendido boolean not null default false,
  tope_por_pasada int not null check (tope_por_pasada > 0),
  cambiado_at timestamptz not null default now(),
  cambiado_por text,
  motivo text,
  constraint encender_dice_quien_y_por_que check (
    not encendido or (nullif(btrim(cambiado_por), '') is not null and nullif(btrim(motivo), '') is not null)
  )
);

comment on table public.barrido_demos_interruptor is
  'El interruptor del barrido de demos (0080). Nace APAGADO. Se enciende a mano, como postgres: '
  'update public.barrido_demos_interruptor set encendido = true, cambiado_por = ''…'', motivo = ''…''; '
  'Cada cambio queda en barrido_demos_interruptor_historial.';

insert into public.barrido_demos_interruptor (encendido, tope_por_pasada, motivo) values (false, 50, 'nace apagado (0080)');

create table public.barrido_demos_interruptor_historial (
  id bigint generated always as identity primary key,
  encendido boolean not null,
  tope_por_pasada int not null,
  cambiado_at timestamptz not null,
  cambiado_por text,
  motivo text
);

alter table public.barrido_demos_interruptor enable row level security;
alter table public.barrido_demos_interruptor_historial enable row level security;
revoke all on public.barrido_demos_interruptor, public.barrido_demos_interruptor_historial
  from public, anon, authenticated, service_role;
-- El panel enseña si está encendido; cambiarlo, solo postgres.
grant select on public.barrido_demos_interruptor, public.barrido_demos_interruptor_historial to service_role;

create function public.barrido_demos_historial_no_se_toca()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  raise exception 'barrido_demos_interruptor_historial no se puede %: es un registro', tg_op using errcode = '42501';
end;
$$;

create trigger barrido_demos_historial_sin_update_ni_delete
  before update or delete on public.barrido_demos_interruptor_historial
  for each row execute function public.barrido_demos_historial_no_se_toca();
create trigger barrido_demos_historial_sin_truncate
  before truncate on public.barrido_demos_interruptor_historial
  for each statement execute function public.barrido_demos_historial_no_se_toca();

create function public.barrido_demos_interruptor_se_apunta()
returns trigger
language plpgsql
set search_path = public, pg_temp
as $$
begin
  if tg_op <> 'UPDATE' then
    raise exception 'barrido_demos_interruptor no se puede %: es una sola fila, solo se cambia', tg_op
      using errcode = '42501';
  end if;
  new.cambiado_at := now();
  insert into public.barrido_demos_interruptor_historial (encendido, tope_por_pasada, cambiado_at, cambiado_por, motivo)
  values (new.encendido, new.tope_por_pasada, new.cambiado_at, new.cambiado_por, new.motivo);
  return new;
end;
$$;

create trigger barrido_demos_interruptor_se_apunta
  before update or delete on public.barrido_demos_interruptor
  for each row execute function public.barrido_demos_interruptor_se_apunta();
create trigger barrido_demos_interruptor_sin_truncate
  before truncate on public.barrido_demos_interruptor
  for each statement execute function public.barrido_demos_interruptor_se_apunta();

create table public.barridos_de_demos (
  id bigint generated always as identity primary key,
  hecho_at timestamptz not null default now(),
  borradas int not null check (borradas >= 0),
  -- Las caducadas que se quedaron para la pasada siguiente (por el tope).
  quedan int not null check (quedan >= 0),
  -- Solo los ids: nada personal. Sin clave ajena: las cuentas ya no existen.
  cuentas uuid[] not null
);

comment on table public.barridos_de_demos is
  'La constancia del barrido de demos (0080): una fila por pasada. No se edita ni se borra.';

alter table public.barridos_de_demos enable row level security;
revoke all on public.barridos_de_demos from public, anon, authenticated, service_role;
grant select on public.barridos_de_demos to service_role;

create trigger barridos_de_demos_sin_update_ni_delete
  before update or delete on public.barridos_de_demos
  for each row execute function public.las_comisiones_no_se_tocan();
create trigger barridos_de_demos_sin_truncate
  before truncate on public.barridos_de_demos
  for each statement execute function public.las_comisiones_no_se_tocan();

-- LA SELECCIÓN, en un solo sitio: el ensayo y el barrido leen la misma.
create function public.demos_caducadas()
returns table (account_id uuid, creada_at timestamptz, caduca_at timestamptz, centro text)
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select d.account_id, d.creada_at, d.caduca_at,
         (select s.nombre_comercial from public.salons s where s.account_id = d.account_id limit 1)
  from public.demos d
  join public.accounts a on a.id = d.account_id and a.es_demo
  where d.caduca_at <= now()
  order by d.caduca_at, d.account_id;
$$;

revoke all on function public.demos_caducadas() from public, anon, authenticated, service_role;

-- EL ENSAYO: qué borraría la próxima pasada, sin borrar nada (stable: no puede).
create function public.ensayo_del_barrido()
returns table (account_id uuid, creada_at timestamptz, caduca_at timestamptz, centro text, entra_en_esta_pasada boolean)
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select c.account_id, c.creada_at, c.caduca_at, c.centro,
         row_number() over (order by c.caduca_at, c.account_id)
           <= (select i.tope_por_pasada from public.barrido_demos_interruptor i where i.unico)
  from public.demos_caducadas() c;
$$;

revoke all on function public.ensayo_del_barrido() from public, anon, authenticated;
grant execute on function public.ensayo_del_barrido() to service_role;

create function public.barrer_demos()
returns table (borradas int, quedan int)
language plpgsql
security definer
set search_path = public, pg_temp
as $$
#variable_conflict use_column
declare
  v_inter public.barrido_demos_interruptor;
  v_cuentas uuid[];
  v_total int;
  c uuid;
begin
  -- 1. EL INTERRUPTOR: apagado, se niega (no devuelve cero).
  select * into v_inter from public.barrido_demos_interruptor where unico;
  if not coalesce(v_inter.encendido, false) then
    raise exception 'El barrido de demos está APAGADO: no se ha borrado nada.'
      using errcode = '55000',
            hint = 'Se enciende a mano, como postgres, en public.barrido_demos_interruptor (quién y por qué).';
  end if;

  -- Dos pasadas a la vez no: la segunda espera y ya no ve lo borrado.
  perform pg_advisory_xact_lock(hashtext('barrer_demos'));

  -- 2. EL TOPE: las más antiguas, hasta el tope; el resto, a la siguiente.
  select count(*) into v_total from public.demos_caducadas();
  v_cuentas := array(select d.account_id from public.demos_caducadas() d limit v_inter.tope_por_pasada);

  -- 3. Cada una, con la comprobación de que es una demo (borrar_una_demo).
  foreach c in array v_cuentas loop
    perform public.borrar_una_demo(c);
  end loop;

  -- 4. La constancia, en la misma transacción.
  insert into public.barridos_de_demos (borradas, quedan, cuentas)
  values (coalesce(array_length(v_cuentas, 1), 0), v_total - coalesce(array_length(v_cuentas, 1), 0), v_cuentas);

  borradas := coalesce(array_length(v_cuentas, 1), 0);
  quedan := v_total - borradas;
  return next;
end;
$$;

comment on function public.barrer_demos() is
  'EL BARRIDO DE DEMOS (0080). Nace apagado (barrido_demos_interruptor). Borra las demos caducadas, '
  'hasta el tope por pasada, con el plan de la 0068; solo demos (borrar_una_demo lo comprueba en cada una).';

revoke all on function public.barrer_demos() from public, anon, authenticated;
grant execute on function public.barrer_demos() to service_role;

-- Lo que enseña el panel: vivas, de la última hora, caducadas, los topes y el interruptor.
create function public.estado_de_las_demos()
returns table (vivas int, ultima_hora int, caducadas int, vivas_max int, por_hora_max int,
               por_ip_hora_max int, barrido_encendido boolean, tope_por_pasada int)
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select (select count(*)::int from public.demos d where d.caduca_at > now()),
         (select count(*)::int from public.demos d where d.creada_at > now() - interval '1 hour'),
         (select count(*)::int from public.demos_caducadas()),
         t.vivas_max, t.por_hora_max, t.por_ip_hora_max, i.encendido, i.tope_por_pasada
  from public.topes_de_demos t, public.barrido_demos_interruptor i
  where t.unico and i.unico;
$$;

revoke all on function public.estado_de_las_demos() from public, anon, authenticated;
grant execute on function public.estado_de_las_demos() to service_role;

-- ═══════════════ 8. el gancho del token: el de la 0069, con una guarda más

-- ¿Es este usuario el de una demo caducada? Para el gancho, que corre como
-- supabase_auth_admin y no lee `demos`.
create function public.demo_caducada_de(p_auth_user uuid)
returns boolean
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select exists (select 1 from public.demos d where d.auth_user_id = p_auth_user and d.caduca_at <= now());
$$;

revoke all on function public.demo_caducada_de(uuid) from public, anon, authenticated;
grant execute on function public.demo_caducada_de(uuid) to supabase_auth_admin, service_role;

-- EL CUERPO ES EL DE LA 0069, letra por letra, salvo el bloque marcado.
create or replace function public.custom_access_token_hook(event jsonb)
 returns jsonb
 language plpgsql
 stable
as $function$
declare
  claims jsonb;
  v_salon_id uuid;
  v_rol public.user_rol;
  v_retirado timestamptz;
begin
  select salon_id, rol, acceso_retirado_el into v_salon_id, v_rol, v_retirado
  from public.users
  where auth_user_id = (event ->> 'user_id')::uuid
  limit 1;

  -- SOLO ENTRA LA ADMINISTRADORA. Con un error, GoTrue no emite el token: ni
  -- al iniciar sesión ni al refrescar. El texto no dice nada que quien ha
  -- llegado aquí —con la contraseña buena— no sepa ya.
  if found and v_rol <> 'admin' then
    return jsonb_build_object(
      'error', jsonb_build_object(
        'http_code', 403,
        'message', 'Solo la administradora del salón entra en la aplicación.'
      )
    );
  end if;

  -- ── 0080 ── UNA DEMO CADUCADA NO CONSIGUE TOKEN, ni al entrar ni al refrescar.
  if public.demo_caducada_de((event ->> 'user_id')::uuid) then
    return jsonb_build_object(
      'error', jsonb_build_object(
        'http_code', 403,
        'message', 'Esta demostración ha terminado. Abre otra desde la portada.'
      )
    );
  end if;
  -- ── fin 0080 ──

  claims := event -> 'claims';

  -- Sin centro en el token para quien se quedó sin acceso (0062).
  if v_salon_id is not null and v_retirado is null then
    claims := jsonb_set(claims, '{salon_id}', to_jsonb(v_salon_id::text));
    claims := jsonb_set(claims, '{rol}', to_jsonb(v_rol::text));
  end if;

  event := jsonb_set(event, '{claims}', claims);
  return event;
end;
$function$;

commit;
