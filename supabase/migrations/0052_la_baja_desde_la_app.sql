-- 0052 · La baja desde la app
--
-- Hasta hoy un salón solo podía darse de baja desde el portal de Stripe. A
-- partir de aquí lo hace desde Ajustes → Suscripción, a FIN DE PERIODO y
-- nunca en el acto: nadie paga por lo que ya no tiene y nadie pierde lo que ya
-- pagó. Esta migración guarda las tres cosas que ese camino necesita y que no
-- tenían dónde vivir.
--
-- ═══ 1. LA BAJADA APARCADA ═══
--
--   · `bajada_aparcada_plan` / `bajada_aparcada_intervalo`
--
-- Stripe no deja marcar una baja a fin de periodo en una suscripción que tiene
-- un calendario (la bajada programada de la 0049): contesta que la cancelación
-- se gestiona desde el calendario. Decidido (Carlos, 25-09-2026): la baja
-- SUELTA primero el calendario y cancela por el camino normal, para que haya un
-- solo camino de cancelación.
--
-- Pero quien cancela y se arrepiente tiene que recuperar su bajada: si no, al
-- mes siguiente pagaría el plan caro sin que nadie se lo hubiera dicho. Así que
-- ANTES de soltar el calendario la bajada se copia aquí, y al reanudar se
-- vuelve a programar desde aquí. No se reaprovecha `plan_programado`: esa
-- columna dice «esto va a pasar» y la pantalla la anuncia; una bajada aparcada
-- NO va a pasar mientras siga la baja, y anunciarla sería mentir.
--
-- ═══ 2. LO QUE PEDIMOS, MIENTRAS STRIPE LO CONFIRMA ═══
--
--   · `cancelacion_pedida` — lo que escribimos al confirmar (true al cancelar,
--     false al reanudar). NULL cuando Stripe ya lo ha confirmado.
--
-- La verdad es Stripe. Lo que se escribe al confirmar es PROVISIONAL, para que
-- la pantalla no tenga que esperar al webhook; el webhook manda y corrige.
-- Esta columna es la que deja saber si lo que llega de Stripe CONFIRMA lo que
-- pedimos o lo CONTRADICE.
--
-- ═══ 3. LA ALARMA ═══
--
--   · `alarma_cancelacion` — texto con los dos valores cuando Stripe contradijo
--     lo que pedimos. NULL es lo normal.
--
-- Si discrepan gana Stripe (el webhook escribe lo suyo), y queda esta alarma
-- con lo que pedimos y lo que dijo Stripe. La enseña el vigía del panel de
-- plataforma (`routers/admin_plataforma.py`), en el bloque de Alertas. Se
-- guarda en la fila y no solo en el log porque un log no lo lee nadie, y
-- porque una vez corregido por el webhook la divergencia ya no se ve al
-- comparar: sin esto, el fallo se borraría solo.
--
-- ═══ LA REGLA DE LA COLUMNA OBLIGATORIA (supabase/README.md) ═══
--
-- Las cuatro son OPCIONALES y sin valor por defecto: el backend desplegado hoy
-- inserta y actualiza suscripciones sin conocerlas y sigue funcionando.
--
-- ═══ ORDEN DE DESPLIEGUE: LA MIGRACIÓN VA ANTES DEL BACKEND ═══
--
-- El backend nuevo escribe estas columnas (el webhook incluido). Con el backend
-- nuevo y sin la migración, PostgREST rechazaría la escritura entera de la
-- suscripción y el webhook de Stripe contestaría 500. Al revés no pasa nada.
-- Y el frontend, después del backend: sin él, el enlace llamaría a rutas que
-- no existen.
--
--   1. supabase db push (0052)   2. backend   3. frontend
--
-- ═══ PERMISOS ═══
--
-- `subscriptions` tiene los permisos por TABLA, así que las columnas nacen con
-- los de siempre: la sesión LEE y las escribe service_role, como el resto de
-- lo que decide el dinero.
--
-- Vuelta atrás:
--   alter table public.subscriptions
--     drop column bajada_aparcada_plan, drop column bajada_aparcada_intervalo,
--     drop column cancelacion_pedida, drop column alarma_cancelacion;

begin;

alter table public.subscriptions
  add column bajada_aparcada_plan text,
  add column bajada_aparcada_intervalo text,
  add column cancelacion_pedida boolean,
  add column alarma_cancelacion text;

comment on column public.subscriptions.bajada_aparcada_plan is
  'La bajada programada que una baja dejó aparcada. Se vuelve a programar al '
  'reanudar. NULL si no hay ninguna.';
comment on column public.subscriptions.bajada_aparcada_intervalo is
  'El intervalo de la bajada aparcada.';
comment on column public.subscriptions.cancelacion_pedida is
  'Lo que pedimos a Stripe al cancelar (true) o reanudar (false), mientras '
  'Stripe no lo confirma. NULL = confirmado o nada pedido.';
comment on column public.subscriptions.alarma_cancelacion is
  'Stripe contradijo lo que pedimos: los dos valores. NULL es lo normal.';

commit;
