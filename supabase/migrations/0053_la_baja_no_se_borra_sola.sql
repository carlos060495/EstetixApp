-- 0053 · Una cancelación no se borra sola
--
-- Medido contra Stripe (28-09-2026): con una baja ya confirmada, «Cambiar a
-- Pro» creaba el calendario de la bajada, Stripe quitaba la marca de baja al
-- crearlo, y el salón que se iba volvía a estar suscrito sin haberlo pedido.
-- La alarma de la 0052 NO sonó: vigilaba una PETICIÓN (`cancelacion_pedida`),
-- y una petición caduca al confirmarse. Lo que hay que vigilar es la VOLUNTAD
-- del salón.
--
-- ═══ LA VOLUNTAD ═══
--
--   · `baja_solicitada_at` — cuándo pidió irse el salón. NULL = no ha pedido
--     irse (o ya se ha resuelto).
--   · `baja_solicitada_origen` — por dónde: 'app' (la baja de Ajustes o del
--     muro de suspensión) o 'stripe' (la vio llegar el webhook sin que la
--     pidiera nuestra app: el portal de clientes, mientras siga abierto).
--
-- SOLO LA BORRAN DOS COSAS: reanudar desde nuestra app, o que la suscripción
-- termine de verdad (`canceled`). Ni un cambio de plan, ni un evento de Stripe,
-- ni nuestro propio código por otra vía. Lo vigila
-- `backend/tests/test_la_voluntad_de_irse.py`, que recorre el código.
--
-- Si Stripe dice que ya no se cancela mientras esta voluntad dice que sí, gana
-- Stripe en lo que se escribe —el salón conserva el servicio, que es la
-- dirección segura— y salta `alarma_cancelacion` (0052) con los dos valores y
-- el origen del cambio.
--
-- `cancelacion_pedida` (0052) deja de usarse con esta entrega. NO se borra
-- aquí: una columna que se va, se va DESPUÉS del código que la nombra, en su
-- propia migración.
--
-- ═══ LA REGLA DE LA COLUMNA OBLIGATORIA (supabase/README.md) ═══
--
-- Las dos son OPCIONALES y sin valor por defecto: el backend desplegado hoy
-- sigue escribiendo suscripciones sin conocerlas.
--
-- ═══ ORDEN DE DESPLIEGUE: LA MIGRACIÓN VA ANTES DEL BACKEND ═══
--
-- El backend nuevo las escribe, el webhook incluido. Con el backend nuevo y sin
-- la migración, el webhook de Stripe contestaría 500.
--
--   1. supabase db push (0053)   2. backend   3. frontend
--
-- ═══ LAS CANCELACIONES QUE YA EXISTEN ═══
--
-- Las suscripciones que hoy ya llevan la marca de baja se quedan con la voluntad
-- apuntada: es lo que pidieron. Si no se hiciera, la alarma no las vigilaría
-- hasta su próximo cambio. Se marcan con origen 'stripe' porque no se sabe por
-- dónde llegaron.
--
-- ═══ PERMISOS ═══
--
-- `subscriptions` tiene los permisos por TABLA: la sesión lee y escribe
-- service_role, como el resto de lo que decide el dinero.
--
-- Vuelta atrás:
--   alter table public.subscriptions
--     drop column baja_solicitada_at, drop column baja_solicitada_origen;

begin;

alter table public.subscriptions
  add column baja_solicitada_at timestamptz,
  add column baja_solicitada_origen text;

comment on column public.subscriptions.baja_solicitada_at is
  'Cuándo pidió irse el salón. Solo lo borran reanudar desde la app o el fin '
  'real de la suscripción. NULL = no ha pedido irse.';
comment on column public.subscriptions.baja_solicitada_origen is
  'Por dónde pidió irse: app (Ajustes o el muro de suspensión) o stripe (lo '
  'trajo el webhook sin pasar por la app, p. ej. el portal de clientes).';

update public.subscriptions
set baja_solicitada_at = now(),
    baja_solicitada_origen = 'stripe'
where cancel_at_period_end is true
  and estado not in ('canceled', 'incomplete_expired');

commit;
