-- 0085 · Cada salón conecta su propio WhatsApp.
--
-- Carlos, 12-10-2026.
--
-- ═══ LO QUE HABÍA ═══
--
-- Un solo número, el de la plataforma, con un token único en el entorno
-- (WHATSAPP_ACCESS_TOKEN), y las filas de `salon_whatsapp_config` tecleadas a
-- mano por soporte. Una fila mal tecleada mandaba los mensajes de un salón a
-- otro. Y cuando Meta rechazaba un envío, lo único que quedaba era una línea
-- en un registro que nadie mira: un salón podía pasar días creyendo que su bot
-- atendía mientras no contestaba a nadie.
--
-- ═══ LO QUE CAMBIA ═══
--
--   · `whatsapp_credenciales`: el token de envío de CADA número, CIFRADO en la
--     aplicación (la clave vive en el entorno, WHATSAPP_CLAVE_TOKENS, nunca en
--     la base). Nadie la lee por PostgREST: ni `anon`, ni `authenticated` —ni
--     siquiera el propio salón—. Solo `service_role`, y el backend la lee en UN
--     sitio: `token_de_envio()` (app/core/whatsapp_cloud.py), por salón y
--     número a la vez.
--   · Las filas de `salon_whatsapp_config` YA NO LAS ESCRIBE NADIE A MANO desde
--     la aplicación: se crean con `conectar_whatsapp`, que solo puede llamar el
--     backend DESPUÉS de que Meta haya confirmado que ese número es de quien lo
--     conecta. `authenticated` pierde INSERT y DELETE, y UPDATE se queda solo
--     en las columnas de comportamiento (tono, políticas, plazo de derivación,
--     el sello del aviso, el interruptor y el estado del último envío).
--   · DESCONECTAR deja la fila con `phone_number_id` a NULL: el número queda
--     libre para quien lo conecte después, el salón conserva su tono y sus
--     políticas, y el reparto —que busca por `phone_number_id = X`— no la ve.
--     Por eso `phone_number_id` deja de ser NOT NULL (el unique sigue: varios
--     NULL no chocan).
--   · Si OTRO salón conecta un número que ya estaba en una fila, gana el que
--     acaba de demostrar ante Meta que lo controla: la fila vieja se queda
--     desconectada y sin credenciales, en la misma transacción. Nunca hay dos
--     filas con el mismo número.
--   · CON PROBLEMA: cada rechazo de Meta queda en `whatsapp_envios_fallidos`, y
--     la fila del número guarda desde cuándo, el código y lo que dijo Meta.
--     El primer envío que vuelve a salir lo limpia. Lo apunta
--     `apuntar_envio`, que llama `enviar_texto` —el único sitio por el que sale
--     un WhatsApp—, así que ningún camino puede olvidarse de apuntarlo.
--   · Una demo no puede conectar nada: `conectar_whatsapp` lo comprueba también
--     aquí, además de la ruta.
--
-- ═══ LO QUE NO CAMBIA ═══
--
-- El reparto de los mensajes entrantes por `phone_number_id`. Las filas que hoy
-- existen se quedan tal cual, sin credenciales: siguen enviando con el token
-- global como RESPALDO hasta que su salón se conecte por el camino nuevo.
--
-- ═══ ORDEN DE DESPLIEGUE: 0085 → BACKEND + FRONTEND (a la vez) ═══
--
-- El backend desplegado sigue funcionando con ella: no escribe filas de esta
-- tabla, lee las columnas que ya había y envía con el token global.

begin;

-- ═══════════════ 1. el directorio: desconectado = sin número

alter table public.salon_whatsapp_config
  alter column phone_number_id drop not null,
  add column waba_id text,
  add column conectado_at timestamptz,
  add column problema_desde timestamptz,
  add column problema_codigo text,
  add column problema_detalle text,
  add constraint salon_whatsapp_config_problema_breve check (length(problema_detalle) <= 500);

comment on column public.salon_whatsapp_config.phone_number_id is
  'El número de Meta que atiende esta fila. NULL = desconectado (0085): el número queda libre y el reparto no la ve.';
comment on column public.salon_whatsapp_config.problema_desde is
  'Desde cuándo Meta rechaza los envíos de este número (0085). NULL = el último envío salió.';

-- Nadie crea ni borra filas desde la aplicación: solo `conectar_whatsapp` y
-- `desconectar_whatsapp`. UPDATE, solo en lo que el salón (o su bot) cambia.
revoke insert, update, delete on public.salon_whatsapp_config from authenticated;
grant update (bot_tono, bot_politicas, handoff_horas_limite, ultimo_aviso_handoff_at, is_active)
  on public.salon_whatsapp_config to authenticated;

-- ═══════════════ 2. las credenciales: nadie las lee salvo el backend

create table public.whatsapp_credenciales (
  config_id uuid primary key references public.salon_whatsapp_config (id) on delete cascade,
  token_cifrado text not null,
  pin_cifrado text,
  creado_at timestamptz not null default now()
);

comment on table public.whatsapp_credenciales is
  'El token de envío de cada número, CIFRADO con WHATSAPP_CLAVE_TOKENS (0085). Sin permisos para anon ni '
  'authenticated: ni el propio salón lo lee. El backend lo lee en token_de_envio(), por salón y número.';

alter table public.whatsapp_credenciales enable row level security;
revoke all on public.whatsapp_credenciales from public, anon, authenticated, service_role;
grant select on public.whatsapp_credenciales to service_role;

-- ═══════════════ 3. los envíos que Meta rechazó

create table public.whatsapp_envios_fallidos (
  id bigint generated always as identity primary key,
  salon_id uuid not null references public.salons (id) on delete cascade,
  config_id uuid references public.salon_whatsapp_config (id) on delete set null,
  phone_number_id text,
  ocurrido_at timestamptz not null default now(),
  http_status int,
  codigo text,
  detalle text check (length(detalle) <= 500)
);

comment on table public.whatsapp_envios_fallidos is
  'Cada envío de WhatsApp que no salió (0085), con lo que dijo Meta. Lo escribe apuntar_envio; el salón lo lee.';

create index whatsapp_envios_fallidos_salon_idx on public.whatsapp_envios_fallidos (salon_id, ocurrido_at desc);

alter table public.whatsapp_envios_fallidos enable row level security;
revoke all on public.whatsapp_envios_fallidos from public, anon, authenticated, service_role;
grant select on public.whatsapp_envios_fallidos to authenticated, service_role;
-- La misma frontera que el resto de lo del bot (0039): los centros de la cuenta.
create policy "whatsapp_envios_fallidos_select" on public.whatsapp_envios_fallidos
  for select using (salon_id = any (public.salones_visibles()));

-- ═══════════════ 4. conectar: solo el backend, después de preguntarle a Meta

create function public.conectar_whatsapp(
  p_salon uuid, p_phone_number_id text, p_waba_id text, p_display text, p_token_cifrado text, p_pin_cifrado text
)
returns uuid
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_config uuid;
begin
  if p_salon is null or coalesce(btrim(p_phone_number_id), '') = '' or coalesce(p_token_cifrado, '') = '' then
    raise exception 'conectar_whatsapp: faltan el salón, el número o el token' using errcode = '22023';
  end if;
  if exists (select 1 from public.salons s join public.accounts a on a.id = s.account_id
             where s.id = p_salon and a.es_demo) then
    raise exception 'conectar_whatsapp: un salón de muestra no conecta WhatsApp' using errcode = '42501';
  end if;
  if not exists (select 1 from public.salons where id = p_salon) then
    raise exception 'conectar_whatsapp: el salón % no existe', p_salon using errcode = '22023';
  end if;

  -- Un número a la vez: dos conexiones simultáneas del mismo número no se pisan.
  perform pg_advisory_xact_lock(hashtext('conectar_whatsapp:' || p_phone_number_id));

  -- EL NÚMERO CAMBIA DE MANOS: si estaba en la fila de OTRO salón, esa fila se
  -- queda desconectada y sin credenciales. Gana quien acaba de demostrar ante
  -- Meta que lo controla (el backend no llama aquí sin esa comprobación).
  delete from public.whatsapp_credenciales k
  using public.salon_whatsapp_config c
  where k.config_id = c.id and c.phone_number_id = p_phone_number_id and c.salon_id <> p_salon;
  update public.salon_whatsapp_config
     set phone_number_id = null, display_phone_number = null, waba_id = null, is_active = false,
         conectado_at = null, problema_desde = null, problema_codigo = null, problema_detalle = null
   where phone_number_id = p_phone_number_id and salon_id <> p_salon;

  -- Su fila con ese número; si no, una suya desconectada (conserva su tono y
  -- sus políticas); si no, una nueva.
  select id into v_config from public.salon_whatsapp_config
   where salon_id = p_salon and phone_number_id = p_phone_number_id;
  if v_config is null then
    select id into v_config from public.salon_whatsapp_config
     where salon_id = p_salon and phone_number_id is null order by created_at limit 1;
  end if;
  if v_config is null then
    insert into public.salon_whatsapp_config (salon_id, phone_number_id, is_active)
    values (p_salon, p_phone_number_id, true) returning id into v_config;
  end if;

  update public.salon_whatsapp_config
     set phone_number_id = p_phone_number_id, waba_id = p_waba_id, display_phone_number = p_display,
         is_active = true, conectado_at = now(),
         problema_desde = null, problema_codigo = null, problema_detalle = null
   where id = v_config;

  insert into public.whatsapp_credenciales (config_id, token_cifrado, pin_cifrado)
  values (v_config, p_token_cifrado, p_pin_cifrado)
  on conflict (config_id) do update
    set token_cifrado = excluded.token_cifrado, pin_cifrado = excluded.pin_cifrado, creado_at = now();

  return v_config;
end;
$$;

comment on function public.conectar_whatsapp(uuid, text, text, text, text, text) is
  'Da de alta (o vuelve a dar) un número de WhatsApp de un salón con su token cifrado (0085). Solo el backend, '
  'y solo después de que Meta haya confirmado el número. Si el número estaba en otro salón, ese lo pierde.';

revoke all on function public.conectar_whatsapp(uuid, text, text, text, text, text) from public, anon, authenticated;
grant execute on function public.conectar_whatsapp(uuid, text, text, text, text, text) to service_role;

-- ═══════════════ 5. desconectar: deja de atender y retira las credenciales

create function public.desconectar_whatsapp(p_salon uuid, p_config uuid)
returns table (waba_id text, waba_sigue_en_uso boolean)
language plpgsql
security definer
set search_path = public, pg_temp
as $$
#variable_conflict use_column
declare
  v_waba text;
begin
  select c.waba_id into v_waba from public.salon_whatsapp_config c
   where c.id = p_config and c.salon_id = p_salon and c.phone_number_id is not null
   for update;
  if not found then
    raise exception 'desconectar_whatsapp: ese número no está conectado en este salón' using errcode = '42501';
  end if;
  delete from public.whatsapp_credenciales k where k.config_id = p_config;
  update public.salon_whatsapp_config
     set phone_number_id = null, display_phone_number = null, waba_id = null, is_active = false,
         conectado_at = null, problema_desde = null, problema_codigo = null, problema_detalle = null
   where id = p_config;
  waba_id := v_waba;
  waba_sigue_en_uso := v_waba is not null and exists (
    select 1 from public.salon_whatsapp_config c where c.waba_id = v_waba and c.phone_number_id is not null);
  return next;
end;
$$;

revoke all on function public.desconectar_whatsapp(uuid, uuid) from public, anon, authenticated;
grant execute on function public.desconectar_whatsapp(uuid, uuid) to service_role;

-- ═══════════════ 6. lo que pasó con un envío: lo apunta `enviar_texto`

create function public.apuntar_envio(
  p_salon uuid, p_phone_number_id text, p_ok boolean, p_http int, p_codigo text, p_detalle text
)
returns void
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_config uuid;
begin
  select id into v_config from public.salon_whatsapp_config
   where salon_id = p_salon and phone_number_id = p_phone_number_id;
  if p_ok then
    update public.salon_whatsapp_config
       set problema_desde = null, problema_codigo = null, problema_detalle = null
     where id = v_config and problema_desde is not null;
    return;
  end if;
  -- Lo de más de 90 días se va al apuntar: el estado vive en la fila, esto es el detalle.
  delete from public.whatsapp_envios_fallidos where salon_id = p_salon and ocurrido_at < now() - interval '90 days';
  insert into public.whatsapp_envios_fallidos (salon_id, config_id, phone_number_id, http_status, codigo, detalle)
  values (p_salon, v_config, p_phone_number_id, p_http, p_codigo, left(p_detalle, 500));
  update public.salon_whatsapp_config
     set problema_desde = coalesce(problema_desde, now()), problema_codigo = p_codigo,
         problema_detalle = left(p_detalle, 500)
   where id = v_config;
end;
$$;

revoke all on function public.apuntar_envio(uuid, text, boolean, int, text, text) from public, anon, authenticated;
grant execute on function public.apuntar_envio(uuid, text, boolean, int, text, text) to service_role;

commit;
