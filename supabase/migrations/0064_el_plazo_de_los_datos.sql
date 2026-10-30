-- 0064 · El plazo de los datos: el reloj y los avisos. NO BORRA NADA.
--
-- El diálogo de la baja promete desde septiembre: «Tus datos se conservan 90
-- días y después se borran». Para que sea verdad hacen falta dos cosas, y esta
-- migración solo trae la primera: saber CUÁNDO empieza el plazo, y avisar.
-- El borrado es otra entrega, se construye en seco primero y no existe todavía.
--
-- ═══ CUÁNDO EMPIEZA EL RELOJ ═══
--
-- Cuando la suscripción TERMINA DE VERDAD (Stripe la deja en `canceled` o
-- `incomplete_expired`), no cuando se pide la baja: entre una y otra la cuenta
-- sigue pagada y trabajando. Hasta ahora no quedaba constancia de ese momento
-- —el estado se sobrescribe y no hay fecha—, así que el reloj no tenía de dónde
-- empezar. `terminada_at` es eso. Lo escribe el backend con la fecha que da
-- Stripe (`ended_at`), que no cambia aunque el evento llegue dos veces.
--
-- EL RELOJ SE PARA SI VUELVE. Si la cuenta reactiva (una suscripción nueva en
-- `trialing` o `active`), `terminada_at` y los avisos vuelven a NULL: no hay
-- plazo, ni contador, ni correos.
--
-- ═══ LOS AVISOS ═══
--
-- Tres correos: al terminar, cuando quedan 30 días y cuando quedan 7. Cada uno
-- se SELLA antes de mandarse con un UPDATE condicional (`… is null`), como los
-- recordatorios de cita: dos pasadas del cron a la vez no mandan dos correos.
--
-- ═══ SIN RELLENAR LO QUE YA EXISTE ═══
--
-- Las suscripciones que ya estén en `canceled` antes de esta migración se
-- quedan con `terminada_at` vacío: no se sabe cuándo terminaron, y poner una
-- fecha a ojo podría mandar un aviso de «quedan 7 días» a alguien que no ha
-- recibido ninguno. Según Carlos (04-10-2026) ninguna cuenta se ha cancelado de
-- verdad todavía; si alguna aparece, el informe de esta entrega dice qué hacer.
--
-- ═══ LA REGLA DE LA COLUMNA OBLIGATORIA ═══
--
-- `subscriptions` es una tabla en la que el backend inserta (el webhook). Las
-- cuatro columnas son ANULABLES y sin valor por defecto: NULL es «no ha
-- terminado» y «no se ha mandado», que es lo que inserta el backend anterior.
--
-- ═══ ORDEN DE DESPLIEGUE: 0064 → BACKEND → FRONTEND ═══
--
-- El backend nuevo escribe y lee estas columnas: sin ellas, el webhook de
-- Stripe fallaría al guardar una suscripción. El backend anterior no las toca.

begin;

alter table public.subscriptions
  add column terminada_at timestamptz,
  add column aviso_terminada_at timestamptz,
  add column aviso_30_at timestamptz,
  add column aviso_7_at timestamptz;

comment on column public.subscriptions.terminada_at is
  'Cuándo terminó DE VERDAD la suscripción (canceled / incomplete_expired), según '
  'Stripe. Empieza el plazo de 90 días de los datos. NULL = no ha terminado, o '
  'ha vuelto. Lo escribe el backend.';
comment on column public.subscriptions.aviso_terminada_at is
  'Sello del correo «tu suscripción ha terminado; tus datos se borran el …».';
comment on column public.subscriptions.aviso_30_at is
  'Sello del correo «quedan 30 días».';
comment on column public.subscriptions.aviso_7_at is
  'Sello del correo «quedan 7 días».';

-- Las que tienen el reloj en marcha: las únicas que mira la tarea.
create index subscriptions_terminada_at_idx on public.subscriptions (terminada_at)
  where terminada_at is not null;

commit;
