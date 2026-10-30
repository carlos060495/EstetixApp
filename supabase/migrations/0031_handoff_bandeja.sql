-- Agente de WhatsApp · Paso a persona (handoff) y bandeja de conversaciones.
--
-- Sustituye el `is_paused` de la 0024 —un booleano que solo sabía decir «el bot
-- calla»— por un ESTADO que además sabe decir por qué calla, desde cuándo,
-- quién lo atiende y cómo terminó. Y añade el rol `agente` para los mensajes
-- que escribe una persona del salón desde la bandeja.
--
-- NO crea ninguna tabla: `push_subscriptions` es de la entrega (c) y no se
-- adelanta aquí. Una columna nueva hereda el grant de su tabla, así que la
-- migración no necesita grants (los tiene la 0024) ni tocar las políticas RLS,
-- que siguen filtrando por `salon_id`.

-- ---------------------------------------------------------------------------
-- 1. El estado de la conversación
-- ---------------------------------------------------------------------------
-- POR QUÉ UN ENUM Y NO EL BOOLEANO QUE YA HABÍA: con `is_paused` no se puede
-- distinguir «una persona está atendiendo esto ahora mismo» de «una persona lo
-- atendió y lo dio por cerrado» de «nadie lo atendió y se quedó sin ventana».
-- Los tres son «el bot no contesta» y los tres significan cosas opuestas para
-- el salón: el primero es trabajo pendiente, el segundo es trabajo hecho y el
-- tercero es un cliente al que se dejó tirado. La bandeja existe justamente
-- para separarlos, así que la diferencia tiene que estar en los datos.
--
--   bot       lo normal: contesta el asistente.
--   humano    hay una derivación viva. EL BOT CALLA. Es lo único que le tapa
--             la boca, y es lo que la bandeja enseña como pendiente.
--   resuelta  una persona lo dio por terminado. El bot vuelve a contestar.
--   expirada  se agotó el plazo sin que nadie lo atendiera. El bot vuelve a
--             contestar —dejar al cliente hablando con la pared para siempre
--             sería peor— pero queda marcado, porque es un fallo de atención y
--             no puede desaparecer como si nada hubiera pasado.
create type public.atencion_estado as enum ('bot', 'humano', 'resuelta', 'expirada');

alter table public.bot_sessions
  add column atencion public.atencion_estado not null default 'bot',

  -- Cuándo se derivó. Es el «lleva esperando X» de la bandeja.
  add column handoff_at timestamptz,

  -- 'cliente_lo_pide' | 'bot_lo_ofrece'. Texto y no enum a propósito: es
  -- ESTADÍSTICA de producto, no una máquina de estados, y va a crecer con
  -- motivos nuevos ('reclamacion', 'fuera_de_alcance'…) según se vea por dónde
  -- se deriva de verdad. Un enum obligaría a una migración por cada matiz nuevo
  -- y no protege nada: aquí nadie ramifica según este valor.
  add column handoff_motivo text,

  -- Quién la cogió. `users`, no `auth.users`: es el personal del salón, la
  -- misma tabla con la que la RLS resuelve el rol. `on delete set null` porque
  -- si el estilista se va del salón la conversación NO se puede ir con él.
  add column atendida_por uuid references public.users(id) on delete set null,

  add column resuelta_at timestamptz,
  add column expirada_at timestamptz,

  -- Cuándo escribió el CLIENTE por última vez. Columna aparte de `updated_at` y
  -- no derivada de ella, y esta es la decisión menos obvia del fichero:
  --
  --   · `updated_at` se mueve con CUALQUIER actividad, incluida la del salón
  --     contestando desde la bandeja. Sirve para ordenar la lista.
  --   · Esta solo se mueve cuando escribe el cliente, y es lo que abre la
  --     VENTANA DE 24 H de Meta: pasadas 24 h desde el último mensaje del
  --     cliente, la Cloud API deja de aceptar texto libre hacia ese número.
  --
  -- Si se usara `updated_at` para calcular la ventana, la propia respuesta del
  -- salón la reabriría sobre el papel, y la bandeja enseñaría un plazo que Meta
  -- no reconoce: el agente escribiría convencido de que llega a tiempo y el
  -- mensaje sería rechazado. Son dos relojes distintos porque miden dos cosas
  -- distintas.
  add column ultimo_mensaje_cliente_at timestamptz;

-- ---------------------------------------------------------------------------
-- 2. Traspaso de lo que ya había
-- ---------------------------------------------------------------------------
-- Una conversación pausada hoy es una conversación en manos de una persona. No
-- se sabe desde cuándo —el booleano no lo guardaba—, así que se usa `updated_at`
-- como mejor aproximación: es la última actividad conocida.
update public.bot_sessions
   set atencion = 'humano',
       handoff_at = updated_at
 where is_paused;

-- Y lo mismo con la ventana de Meta: hasta ahora el único mensaje que movía
-- `updated_at` era el del cliente (lo escribe el webhook al guardar el
-- entrante), así que para las filas que ya existen las dos fechas coinciden. A
-- partir de esta migración dejan de hacerlo, y por eso son dos columnas.
update public.bot_sessions
   set ultimo_mensaje_cliente_at = updated_at;

-- El booleano se va: dejarlo sería tener dos fuentes de verdad para «¿calla el
-- bot?» y garantizar que algún día discrepen.
alter table public.bot_sessions drop column is_paused;

-- ---------------------------------------------------------------------------
-- 3. El índice de la bandeja
-- ---------------------------------------------------------------------------
-- La consulta de la bandeja es «las de MI salón que están en manos de una
-- persona, la que lleva más esperando arriba». PARCIAL (`where atencion =
-- 'humano'`) porque es exactamente la fila que se busca: el índice ocupa lo que
-- ocupan las conversaciones pendientes —unas pocas por salón— y no lo que ocupa
-- el histórico entero, que es lo que crece sin parar.
--
-- `handoff_at asc`: la que lleva más esperando va PRIMERO. Es una cola de
-- atención, no un muro de novedades; ordenarla por lo más reciente dejaría al
-- que más tiempo lleva esperando en el fondo de la lista, que es justo el que
-- no puede esperar más.
create index bot_sessions_bandeja
  on public.bot_sessions (salon_id, handoff_at asc)
  where atencion = 'humano';

-- ---------------------------------------------------------------------------
-- 4. El rol `agente`
-- ---------------------------------------------------------------------------
-- Lo que escribe una PERSONA del salón desde la bandeja. No es `model` —no lo
-- dijo el bot— ni `system` —no es una nota nuestra, es un mensaje real que el
-- cliente ha leído— ni `user` —no lo escribió el cliente—.
--
-- Y no es un detalle de contabilidad: cuando la conversación vuelve al bot
-- (resuelta o expirada), el bot TIENE que saber lo que la persona ya le
-- prometió al cliente, o volverá a ofrecer una hora que su compañera acaba de
-- descartar. Al hablar con Gemini estos turnos se traducen a `model` —la API
-- solo entiende `user` y `model`—, y esa traducción vive en UN solo sitio,
-- `_historial()` en app/routers/webhooks_whatsapp.py. Guardarlos ya como
-- `model` habría hecho imposible distinguir en la bandeja quién dijo qué.
--
-- `add value` fuera de cualquier uso en esta misma migración: Postgres permite
-- ampliar un enum dentro de una transacción (12+) pero no USAR el valor nuevo
-- hasta que confirma. Aquí no se usa, así que no hay problema.
alter type public.bot_role add value 'agente';

-- ---------------------------------------------------------------------------
-- 5. El plazo de la derivación, por salón
-- ---------------------------------------------------------------------------
-- Cuánto aguanta una derivación sin que nadie la atienda antes de darse por
-- expirada y devolverle la palabra al bot.
--
-- 24 h POR DEFECTO Y NO ALGO CORTO: el caso que manda es el mensaje de las
-- 21:00 de un salón que cierra a las 20:00. Tiene que seguir esperando cuando
-- alguien abra por la mañana; un plazo de dos o tres horas lo mataría de
-- madrugada y por la mañana no habría nada que atender. Se mide desde el ÚLTIMO
-- MENSAJE DEL CLIENTE, no desde la derivación, para que una conversación en la
-- que el agente y el cliente se están escribiendo no expire en mitad de la
-- charla.
--
-- EL TOPE DE 24 NO ES REDONDEO: es la ventana de atención al cliente de Meta.
-- Pasada, la Cloud API ya no acepta texto libre hacia ese número, así que una
-- derivación «viva» de 30 h sería una que nadie puede contestar aunque quiera:
-- el salón vería trabajo pendiente que el sistema no le deja hacer. El check lo
-- impide en la base de datos y no solo en la aplicación.
--
-- Todavía no hay pantalla para cambiarlo (no entra en esta entrega). Con el
-- valor por defecto el comportamiento es el que se acaba de describir, así que
-- la columna no cambia nada hasta que alguien la toque a mano o llegue su
-- ajuste.
alter table public.salon_whatsapp_config
  add column handoff_horas_limite int not null default 24
    check (handoff_horas_limite between 1 and 24);
