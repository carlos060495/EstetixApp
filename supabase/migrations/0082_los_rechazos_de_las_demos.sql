-- 0082 · Los rechazos de las demos: que el panel cuente cuántas veces no se
-- pudo abrir una, y por qué.
--
-- Carlos, 08-10-2026.
--
-- ═══ LO QUE PASÓ ═══
--
-- La portada decía «ahora mismo no hay sitio» con 3 de 200 vivas y 0 de 40 en
-- la última hora. El GoTrue de producción (v2.197) rechazaba la contraseña de
-- muestra de la 0081 al crear la administradora; la demo se deshacía sin dejar
-- rastro y el freno por IP, que la había contado al nacer, se llenaba solo: al
-- tercer clic, «no hay sitio». El panel no podía enseñarlo: un rechazo y un
-- alta fallida no dejaban ninguna cuenta. Eso es lo que arregla esta migración
-- (lo demás, el backend).
--
-- ═══ `rechazos_de_demos` ═══
--
-- Una fila por minuto y motivo, con cuántas veces (`veces`): no crece con cada
-- clic, crece como mucho 4 filas por minuto. Los motivos:
--
--   · `sin_sitio_vivas` — el tope de demos vivas a la vez.
--   · `sin_sitio_hora`  — el tope de demos creadas en la última hora.
--   · `sin_sitio_ip`    — el freno por conexión (lo cuenta el backend).
--   · `fallo_al_crear`  — se creó y hubo que deshacerla (`deshacer_demo`): lo
--     que nadie vio esta vez.
--
-- La apuntan `crear_demo` (los tres topes, en la misma transacción en la que
-- rechaza) y `deshacer_demo` (el fallo). Lo de más de 2 días se borra al
-- apuntar. Nadie la toca desde fuera: ni `service_role` escribe.
--
-- ═══ LO QUE NO CAMBIA ═══
--
-- Las cifras de los topes, cómo se cuentan, las demos, el barrido y su
-- interruptor. `crear_demo` y `deshacer_demo` hacen lo mismo que en la 0080;
-- solo apuntan. `estado_de_las_demos` devuelve además cuatro columnas.
--
-- ═══ ORDEN DE DESPLIEGUE: 0082 → BACKEND + FRONTEND (a la vez) ═══
--
-- Compatible con el backend desplegado: mismas firmas, y las columnas nuevas
-- del panel las ignora quien no las lee. Ningún paso en Stripe.

begin;

create table public.rechazos_de_demos (
  minuto timestamptz not null,
  motivo text not null check (motivo in ('sin_sitio_vivas', 'sin_sitio_hora', 'sin_sitio_ip', 'fallo_al_crear')),
  veces int not null default 1 check (veces > 0),
  primary key (minuto, motivo)
);

comment on table public.rechazos_de_demos is
  'Cuántas veces no se pudo abrir una demo, por minuto y motivo (0082). La apuntan crear_demo y deshacer_demo.';

alter table public.rechazos_de_demos enable row level security;
revoke all on public.rechazos_de_demos from public, anon, authenticated, service_role;
grant select on public.rechazos_de_demos to service_role;

create function public.apuntar_rechazo_de_demo(p_motivo text)
returns void
language sql
security definer
set search_path = public, pg_temp
as $$
  delete from public.rechazos_de_demos where minuto < now() - interval '2 days';
  insert into public.rechazos_de_demos as r (minuto, motivo, veces)
  values (date_trunc('minute', now()), p_motivo, 1)
  on conflict (minuto, motivo) do update set veces = r.veces + 1;
$$;

revoke all on function public.apuntar_rechazo_de_demo(text) from public, anon, authenticated, service_role;

-- `crear_demo`: la de la 0080, apuntando cada rechazo.
create or replace function public.crear_demo(p_navegador_hash text, p_correo text, p_puede_crear boolean default true)
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
    perform public.apuntar_rechazo_de_demo('sin_sitio_ip');
    resultado := 'sin_sitio_ip'; return next; return;
  end if;

  select * into v_topes from public.topes_de_demos where unico;
  if (select count(*) from public.demos d where d.caduca_at > now()) >= v_topes.vivas_max then
    perform public.apuntar_rechazo_de_demo('sin_sitio_vivas');
    resultado := 'sin_sitio_vivas'; return next; return;
  end if;
  if (select count(*) from public.demos d where d.creada_at > now() - interval '1 hour') >= v_topes.por_hora_max then
    perform public.apuntar_rechazo_de_demo('sin_sitio_hora');
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

-- `deshacer_demo`: la de la 0080, apuntando el fallo.
create or replace function public.deshacer_demo(p_cuenta uuid)
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
  perform public.apuntar_rechazo_de_demo('fallo_al_crear');
  return true;
end;
$$;

-- Lo que enseña el panel: lo de la 0080 y, de la última hora, los rechazos por
-- tope y las altas fallidas.
drop function public.estado_de_las_demos();
create function public.estado_de_las_demos()
returns table (vivas int, ultima_hora int, caducadas int, vivas_max int, por_hora_max int,
               por_ip_hora_max int, barrido_encendido boolean, tope_por_pasada int,
               rechazos_vivas int, rechazos_hora int, rechazos_ip int, fallos_al_crear int)
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  with r as (
    select motivo, sum(veces)::int as veces from public.rechazos_de_demos
    where minuto > now() - interval '1 hour' group by motivo
  )
  select (select count(*)::int from public.demos d where d.caduca_at > now()),
         (select count(*)::int from public.demos d where d.creada_at > now() - interval '1 hour'),
         (select count(*)::int from public.demos_caducadas()),
         t.vivas_max, t.por_hora_max, t.por_ip_hora_max, i.encendido, i.tope_por_pasada,
         coalesce((select veces from r where motivo = 'sin_sitio_vivas'), 0),
         coalesce((select veces from r where motivo = 'sin_sitio_hora'), 0),
         coalesce((select veces from r where motivo = 'sin_sitio_ip'), 0),
         coalesce((select veces from r where motivo = 'fallo_al_crear'), 0)
  from public.topes_de_demos t, public.barrido_demos_interruptor i
  where t.unico and i.unico;
$$;

revoke all on function public.estado_de_las_demos() from public, anon, authenticated;
grant execute on function public.estado_de_las_demos() to service_role;

commit;
