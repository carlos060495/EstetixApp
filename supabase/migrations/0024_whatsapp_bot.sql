-- Agente de WhatsApp · Fase 1a — base del webhook (recibir, identificar, guardar).
--
-- Tres tablas:
--   salon_whatsapp_config  el DIRECTORIO: de qué salón es un número de Meta.
--   bot_sessions           una conversación: un salón y un teléfono de cliente.
--   bot_messages           el historial de esa conversación.
--
-- Todavía NO hay respuesta de IA (Fase 1b) ni handoff a una persona (1c). Las
-- columnas que esas fases necesitan y que ya se pueden decidir sin adivinar
-- —`is_paused`, `context_summary`, el rol `model`— se crean aquí para no tener
-- que migrar la tabla en cuanto lleguen.

-- ---------------------------------------------------------------------------
-- 1. Directorio de números
-- ---------------------------------------------------------------------------
-- Es la ÚNICA tabla que el webhook consulta antes de saber de qué salón habla
-- —resolver `phone_number_id -> salon_id` es precisamente lo que le dice cuál
-- es el salón—, así que se lee con service_role. A propósito no guarda ningún
-- dato de negocio: solo el enrutado. Todo lo demás pasa ya por un token del
-- salón con RLS (ver app/core/bot_tenant_token.py).
--
-- TOKEN DE ENVÍO — decisión para el MVP: NO vive aquí. Va en la variable de
-- entorno WHATSAPP_ACCESS_TOKEN, una sola para toda la instalación, porque en
-- el MVP el número de WhatsApp es de EstetixApp y los salones cuelgan de él.
-- El día que cada salón traiga su propio número con su propio token, este es el
-- sitio: se añade `access_token_cifrado text` (cifrado en la aplicación, con la
-- clave en el entorno — nunca en claro en la BD) y se cambia UN solo punto,
-- `token_de_envio()` en app/core/whatsapp_cloud.py, que ya existe hoy con esa
-- forma justamente para que el cambio sea ese y no un rastreo por el código.
create table public.salon_whatsapp_config (
  id uuid primary key default gen_random_uuid(),
  salon_id uuid not null references public.salons(id) on delete cascade,

  -- El identificador que Meta pone en cada webhook (`metadata.phone_number_id`).
  -- Único en toda la instalación: un mismo número de Meta no puede atender a
  -- dos salones, porque al llegar un mensaje no habría forma de decidir de
  -- quién es. Es la clave por la que entra el webhook.
  phone_number_id text not null unique,

  -- El número tal y como se le enseña a una persona (+34...). Informativo.
  display_phone_number text,

  -- Apagado de emergencia por salón, sin borrar la fila ni perder el historial.
  is_active boolean not null default true,

  created_at timestamptz not null default now()
);

-- No se pone `unique (salon_id)`: un salón con dos sedes y dos números es un
-- caso real, y el enrutado ya está garantizado por el unique de arriba.
create index on public.salon_whatsapp_config (salon_id);

-- ---------------------------------------------------------------------------
-- 2. Conversaciones
-- ---------------------------------------------------------------------------
create table public.bot_sessions (
  id uuid primary key default gen_random_uuid(),
  salon_id uuid not null references public.salons(id) on delete cascade,

  -- En formato E.164 sin '+', que es como lo manda Meta (`wa_id`). No se
  -- normaliza en la BD: se guarda lo que manda Meta para que la sesión que
  -- busca el webhook sea siempre la misma que creó.
  customer_phone text not null,

  -- Handoff (Fase 1c): con la conversación en pausa el bot sigue GUARDANDO
  -- todo lo que llega —el historial no se rompe— pero no contesta, porque hay
  -- una persona del salón atendiendo.
  is_paused boolean not null default false,

  -- Resumen que la Fase 1b irá manteniendo para no reenviarle a Gemini la
  -- conversación entera en cada turno. Nulo mientras nadie lo escriba.
  context_summary text,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  -- Una conversación por cliente y salón. Es además la clave del upsert con el
  -- que el webhook hace «búscala o créala» en una sola sentencia: comprobar y
  -- luego insertar tiene carrera con dos mensajes que entran a la vez.
  unique (salon_id, customer_phone)
);

create index on public.bot_sessions (salon_id, updated_at desc);

-- ---------------------------------------------------------------------------
-- 3. Mensajes
-- ---------------------------------------------------------------------------
-- Los roles son los de la API de Gemini, que es a quien se le va a pasar este
-- historial tal cual en la Fase 1b: `user` (el cliente), `model` (el bot),
-- `system` (notas del sistema, p. ej. «atendido por una persona») y `tool`
-- (llamadas a herramientas: consultar huecos, crear la cita). Se modela como
-- enum y no como text para que un rol mal escrito reviente al escribir y no
-- meses después, al mandarle a Gemini un historial que no sabe leer.
create type public.bot_role as enum ('user', 'model', 'system', 'tool');

create table public.bot_messages (
  id uuid primary key default gen_random_uuid(),
  salon_id uuid not null references public.salons(id) on delete cascade,

  -- La sesión se borra en cascada con el salón; el mensaje, con la sesión.
  session_id uuid not null references public.bot_sessions(id) on delete cascade,

  role public.bot_role not null,

  -- El texto del mensaje. Para los roles `tool` (y para un mensaje de
  -- WhatsApp que no sea texto: imagen, audio, ubicación…) el contenido
  -- estructurado va en `payload`, y `content` guarda la versión legible.
  content text not null,
  payload jsonb,

  -- El identificador de Meta (`wamid...`). ÚNICO: es toda la idempotencia.
  -- Meta reintenta el mismo webhook si tardamos o si respondemos != 2xx, y sin
  -- esto cada reintento duplicaría el mensaje en el historial (y, en la Fase
  -- 1b, pagaría otra llamada a Gemini y contestaría otra vez al cliente).
  --
  -- Es NULL para los mensajes que generamos nosotros antes de tener respuesta
  -- de la Cloud API; Postgres admite varios NULL en un unique, así que no
  -- estorban. Global y no por salón porque el wamid ya lo es: lo acuña Meta.
  wa_message_id text unique,

  created_at timestamptz not null default now()
);

-- El historial de una conversación se lee siempre en orden y completo.
create index on public.bot_messages (session_id, created_at);
create index on public.bot_messages (salon_id, created_at desc);

-- ---------------------------------------------------------------------------
-- RLS — el mismo patrón que salon_schedule (0022) y el resto del inquilino
-- ---------------------------------------------------------------------------
-- Aislamiento por el claim salon_id del token, NUNCA por auth.uid(). El webhook
-- no es una excepción: acuña un token real del salón (el mismo mecanismo que la
-- impersonación de Super Admin) y entra por PostgREST como un usuario más, de
-- modo que estas políticas se le aplican igual que a la app.
alter table public.salon_whatsapp_config enable row level security;
alter table public.bot_sessions enable row level security;
alter table public.bot_messages enable row level security;

-- Configuración del número: la ve todo el personal, la cambia solo el admin —
-- igual que el resto de la configuración del salón.
create policy "salon_whatsapp_config_select" on public.salon_whatsapp_config
  for select using (salon_id = public.current_salon_id());
create policy "salon_whatsapp_config_write_admin" on public.salon_whatsapp_config
  for all using (salon_id = public.current_salon_id() and public.is_admin_rol())
  with check (salon_id = public.current_salon_id() and public.is_admin_rol());

-- Conversaciones y mensajes: escritura para TODO el personal del salón, no solo
-- admin. Aquí la regla de salon_schedule no encaja: la bandeja de la Fase 1c la
-- atiende quien esté en el mostrador —normalmente un estilista— y tiene que
-- poder pausar la conversación y contestar. Y el propio webhook escribe con la
-- identidad del admin del salón, que también entra por esta política.
create policy "bot_sessions_all" on public.bot_sessions
  for all using (salon_id = public.current_salon_id())
  with check (salon_id = public.current_salon_id());

create policy "bot_messages_all" on public.bot_messages
  for all using (salon_id = public.current_salon_id())
  with check (salon_id = public.current_salon_id());

-- Permisos de tabla para los roles de PostgREST. Explícitos y no heredados del
-- entorno: en el Supabase local una tabla nueva de `public` nace SIN
-- select/insert/update/delete para `authenticated`, y PostgREST responde «403
-- permission denied for table ...» aunque la RLS esté perfecta (documentado en
-- supabase/README.md). Con el grant aquí la migración se basta a sí misma.
--
-- `anon` queda fuera: a estas tablas solo llega personal autenticado (la app) o
-- el webhook con un token de salón acuñado, que es `authenticated`.
grant select, insert, update, delete on public.salon_whatsapp_config to authenticated, service_role;
grant select, insert, update, delete on public.bot_sessions to authenticated, service_role;
grant select, insert, update, delete on public.bot_messages to authenticated, service_role;
