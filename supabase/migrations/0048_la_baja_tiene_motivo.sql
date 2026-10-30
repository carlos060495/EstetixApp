-- 0048 · La baja tiene motivo
--
-- Medido el 16-09-2026: una cuenta cancelada por impago y una que se dio de
-- baja queda IDÉNTICA en la base (`canceled · elite · basico`). Stripe manda el
-- motivo en `cancellation_details.reason` y el webhook lo tiraba. Son dos
-- situaciones distintas y se van a tratar distinto: a quien se le cayó la
-- tarjeta se le recupera; a quien se fue, no se le persigue.
--
-- Qué guarda: el motivo TAL CUAL lo manda Stripe. Hoy son tres
-- (`cancellation_requested`, `payment_failed`, `payment_disputed`), pero Stripe
-- puede añadir más, y por eso NO hay `check`: un motivo nuevo haría fallar el
-- webhook con un 500 y Stripe lo reintentaría para siempre sin que nadie
-- pudiera entrar. Lo que no se reconozca se enseña como «otro motivo».
--
-- Vacío significa «Stripe no ha dado motivo»: una suscripción viva, o una que
-- pasa a `unpaid` —ahí Stripe no cancela, así que tampoco da motivo; el estado
-- ya dice que es impago—. Si alguien anula la baja antes de que surta efecto,
-- Stripe borra el motivo y el webhook lo borra también.
--
-- ═══ LA REGLA DE LA COLUMNA OBLIGATORIA (supabase/README.md) ═══
--
-- La columna es OPCIONAL y sin valor por defecto. El webhook desplegado hoy
-- inserta en `subscriptions` sin saber de ella, y sigue funcionando.
--
-- ═══ ORDEN DE DESPLIEGUE: LA MIGRACIÓN VA ANTES DEL BACKEND ═══
--
-- El backend nuevo escribe `motivo_baja`. Con el backend nuevo y sin esta
-- migración, PostgREST rechazaría la escritura de la suscripción entera
-- («column does not exist») y el webhook contestaría 500. Al revés no pasa
-- nada: el backend anterior no conoce la columna y no la toca.
--
-- ═══ PERMISOS ═══
--
-- `subscriptions` tiene los permisos por TABLA, no por columna (no está entre
-- las tablas de la 0047), así que la columna nace con los mismos que las
-- demás. La RLS no cambia: la sesión la lee con su política de siempre y solo
-- service_role escribe.
--
-- Para deshacer:
--   alter table public.subscriptions drop column motivo_baja;
-- (primero el backend anterior: el nuevo escribe esta columna).

alter table public.subscriptions
  add column motivo_baja text;

comment on column public.subscriptions.motivo_baja is
  'cancellation_details.reason de Stripe, tal cual (cancellation_requested, '
  'payment_failed, payment_disputed…). Vacío: Stripe no ha dado motivo.';
