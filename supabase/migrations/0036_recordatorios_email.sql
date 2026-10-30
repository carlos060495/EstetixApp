-- Recordatorios automáticos por email.
--
-- QUÉ ABRE: un aviso a quien tiene cita mañana. Es la primera cosa del proyecto
-- que ocurre PORQUE SON LAS NUEVE y no porque alguien haya hecho algo, así que
-- trae consigo la infraestructura de tareas programadas que no existía.
--
-- LAS TRES PIEZAS QUE HACEN QUE ESTO SEA FIABLE, y conviene leerlas juntas:
--
--   · `recordatorio_enviado_at` — el sello. Se pone ANTES de mandar y con un
--     UPDATE condicional, de modo que dos réplicas compitiendo por la misma
--     cita no puedan mandar dos correos.
--   · `recordatorio_error`      — porque sellar antes de mandar tiene un precio:
--     si Resend falla, ese recordatorio se pierde. Lo que NO puede es perderse
--     EN SILENCIO. Aquí queda el motivo, y contar las filas con error es la
--     medida de cuántos se cayeron.
--   · `tareas_latido`           — un cron que no se ejecuta no produce errores,
--     produce nada. La única forma de ver ese silencio es que cada pasada deje
--     constancia de haber pasado.

-- ---------------------------------------------------------------------------
-- 1. El interruptor del salón
-- ---------------------------------------------------------------------------
-- EN `salons` Y NO EN `salon_whatsapp_config`, donde vive el resto de la
-- configuración del bot. El motivo no es de orden: esa tabla SOLO tiene fila
-- cuando soporte conecta un número de WhatsApp, y los recordatorios son de
-- todos los planes. Un salón de Básico que use solo el widget no tiene ninguna
-- fila ahí, así que guardarlo en esa tabla le habría quitado los recordatorios
-- sin que nadie se enterase.
--
-- APAGADO POR DEFECTO: encenderlo hace que se empiece a pedir el correo a los
-- clientes y que salgan avisos automáticos firmados por el salón. Eso lo
-- enciende quien lo haya decidido, no una migración.
alter table public.salons
  add column recordatorios_email_activo boolean not null default false;

-- Cuántas horas antes se avisa.
--
-- LA COLUMNA SÍ, LA PANTALLA NO, y a propósito: no hay nada en la interfaz que
-- la cambie porque es una casilla más que nadie ha pedido. Está aquí para que
-- el día que un salón quiera 48 h sea una pantalla y no una migración — el
-- mismo criterio que `politica_del_bot()`, que lleva desde el principio siendo
-- el punto único preparado para volverse configurable.
--
-- El CHECK acota a lo que tiene sentido: menos de 2 h no es un recordatorio,
-- es una prisa; más de una semana y nadie recuerda de qué le hablan.
alter table public.salons
  add column recordatorio_horas int not null default 24
  check (recordatorio_horas between 2 and 168);

-- ---------------------------------------------------------------------------
-- 2. El estado del recordatorio, en la CITA
-- ---------------------------------------------------------------------------
-- NULL = todavía no se ha intentado. Es el valor de arranque de todas las citas
-- que ya existen, y hace que la primera pasada no las considere pendientes por
-- error: el filtro por ventana de tiempo las deja fuera solas.
alter table public.appointments
  add column recordatorio_enviado_at timestamptz;

-- El motivo del fallo, cuando lo hay.
--
-- SELLAR ANTES DE MANDAR evita el correo duplicado, que molesta más que el
-- correo perdido. Pero deja un hueco: si Resend contesta mal, la cita queda
-- sellada y nadie recibió nada. Esta columna es lo que impide que ese hueco sea
-- invisible — `count(*) where recordatorio_error is not null` es exactamente
-- «cuántos se han caído», y el panel lo enseña.
--
-- Texto y no un booleano: saber que falló sirve para contar; saber POR QUÉ
-- falló sirve para arreglarlo. Se guarda acotado, sin volcar la excepción
-- entera.
alter table public.appointments
  add column recordatorio_error text;

-- El índice que usa el cron en cada pasada: «las de esta ventana que aún no
-- tienen recordatorio». Parcial, porque las que ya lo tienen no se vuelven a
-- mirar nunca y no hay por qué mantenerlas en el índice — que en una tabla de
-- citas es la inmensa mayoría de las filas.
create index appointments_recordatorio_pendiente_idx
  on public.appointments (salon_id, inicio)
  where recordatorio_enviado_at is null;

-- ---------------------------------------------------------------------------
-- 3. La baja del cliente
-- ---------------------------------------------------------------------------
-- SE PUEDE ESCRIBIR A CUALQUIER CORREO DE LA FICHA, porque avisar de una cita
-- que la persona ha pedido es un mensaje de SERVICIO y no marketing. Eso hace
-- que la salida sea obligatoria, no un detalle: quien no quiera recibirlos
-- tiene que poder decirlo sin depender de nadie.
--
-- Y LA BAJA NO BORRA EL CORREO. Son dos cosas distintas: el correo es cómo se
-- contacta con esa persona —el recibo, un cambio de hora, lo que venga—; esto
-- es una preferencia sobre UN tipo de aviso. Borrar el email para dejar de
-- recibir recordatorios le quitaría al cliente todo lo demás.
alter table public.clients
  add column recordatorios_baja_at timestamptz;

-- El identificador que viaja en el enlace del correo.
--
-- ALEATORIO Y NO EL `id` DEL CLIENTE: el enlace acaba en la bandeja de entrada,
-- se reenvía y se queda en el historial del navegador. Con el id real, cualquiera
-- que viera un enlace podría dar de baja a otro con solo cambiar un carácter…
-- y además tendría el identificador interno de un cliente del salón.
--
-- Se genera para todas las fichas, también las que ya existen: así el enlace
-- funciona desde el primer correo, sin un paso de relleno posterior que se
-- pueda olvidar.
alter table public.clients
  add column recordatorios_token uuid not null default gen_random_uuid();

-- La consulta de la baja es exactamente `where recordatorios_token = $1`, y
-- entra SIN sesión: es lo único que identifica a quien pulsa. Único además de
-- indexado, para que no puedan existir dos fichas con el mismo enlace.
create unique index clients_recordatorios_token_key
  on public.clients (recordatorios_token);

-- ---------------------------------------------------------------------------
-- 4. El latido de las tareas programadas
-- ---------------------------------------------------------------------------
-- TABLA DE PLATAFORMA, NO DE INQUILINO: no tiene `salon_id` porque no es de
-- ningún salón. Es la infraestructura la que late, y quien la mira es el panel
-- Super Admin. Misma naturaleza que las tablas de referidos.
--
-- POR QUÉ EXISTE: ningún programador de tareas avisa de que no ha sonado. Un
-- cron caído no genera errores; genera silencio, y el silencio se parece
-- muchísimo a «no había nada que hacer». Con esta tabla, «hace cuánto que no
-- pasa nada» es una consulta, y una consulta sí se puede pintar en rojo.
create table public.tareas_latido (
  -- El nombre de la tarea. Clave primaria porque hay una fila por tarea y no
  -- una por ejecución: lo que importa es CUÁNDO fue la última, no el histórico.
  -- Un log de ejecuciones crecería sin parar para responder a una pregunta que
  -- se contesta con una fila.
  tarea text primary key,
  ultima_at timestamptz not null,
  -- Lo que hizo esa pasada. Sirve para dos cosas: ver de un vistazo si el
  -- sistema está trabajando o dando vueltas en vacío, y notar una pasada que
  -- corre pero no encuentra nada porque algo se rompió aguas arriba.
  procesados int not null default 0,
  fallidos int not null default 0
);

-- NADIE DEL INQUILINO TOCA ESTO. La escribe el cron y la lee el panel Super
-- Admin, y los dos entran con `service_role`, que salta la RLS.
--
-- OJO CON LO QUE PARECE Y NO ES: en un proyecto de Supabase, una tabla nueva de
-- `public` NO nace sin permisos. Hay un `ALTER DEFAULT PRIVILEGES` que le
-- concede DML a `anon` y a `authenticated` automáticamente, así que dejarla
-- «sin tocar» la habría dejado escribible por cualquier usuario con sesión.
--
-- Lo que de verdad la protege es la RLS activada SIN NINGUNA POLÍTICA: eso es
-- denegar por defecto, y está comprobado —un usuario del inquilino no lee ni
-- una fila ni puede insertar—. El REVOKE de abajo es la segunda capa: quita lo
-- que nadie pidió, para que la protección no dependa de una sola cosa.
alter table public.tareas_latido enable row level security;
revoke all on public.tareas_latido from anon, authenticated;

-- ---------------------------------------------------------------------------
-- Lo que NO se añade
-- ---------------------------------------------------------------------------
-- Ninguna columna de «consentimiento» en `appointments`: no hace falta. Lo que
-- decide si se escribe es si hay correo en la ficha y si esa ficha no se ha
-- dado de baja. La casilla del widget y del bot sirve para CAPTAR el correo de
-- quien no está en el CRM, no para autorizar nada aparte.
--
-- Y ninguna cola de tareas: no se encola nada al reservar. Cada pasada PREGUNTA
-- qué citas entran en la ventana, así que una cita cancelada desaparece sola de
-- la consulta y una reprogramada se lee con su hora nueva. Una cola habría que
-- ir a corregirla en cada cambio, y el día que uno se escapara saldría un aviso
-- de una cita que ya no existe.
