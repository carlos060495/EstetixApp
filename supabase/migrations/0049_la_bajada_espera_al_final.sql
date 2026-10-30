-- 0049 · La bajada espera al final del periodo pagado
--
-- Hoy un cambio de plan se aplica en el acto: se cobra el mes entero y las
-- funciones se quitan el día 3. A partir de aquí las bajadas se PROGRAMAN para
-- el final del periodo pagado —con un calendario de suscripción de Stripe— y
-- las subidas siguen aplicándose al momento.
--
-- Lo que se guarda aquí es la INTENCIÓN, no el plan:
--
--   · `plan_programado` / `intervalo_programado` — a qué se pasa.
--   · `cambio_programado_para` — cuándo: el fin del periodo ya pagado.
--   · `stripe_schedule_id` — el calendario que lo hará, para poder anularlo.
--
-- ═══ EL SENTIDO DEL FALLO ═══
--
-- `accounts.plan` NO se toca al programar. Lo escribe un solo sitio
-- (`_escribir_suscripcion`) y solo con lo que Stripe dice de la suscripción de
-- verdad. Si el calendario no llega a dispararse, Stripe sigue cobrando el plan
-- viejo y nosotros seguimos dándolo: un mes regalado, que es el lado barato del
-- error. El caro sería dejar a alguien en Básico habiendo pagado Pro. Ver
-- `app/billing/cambios_de_plan.py`.
--
-- ═══ `salons.sigue_al_bajar`: QUÉ CENTRO SE QUEDA ═══
--
-- Cuando un Elite con dos centros baja, elige en esa misma pantalla cuál se
-- queda. B2a solo lo GUARDA; congelar el otro es B3a, que actuará sobre esto.
--
-- VA EN EL CENTRO Y NO EN LA CUENTA, y no es un detalle de estilo: la primera
-- versión de esta migración ponía `accounts.centro_que_sigue uuid references
-- salons(id)`, y eso crea una SEGUNDA relación entre `salons` y `accounts`.
-- PostgREST deja entonces de saber por cuál embeber y responde 300 (PGRST201)
-- a `salons?select=...,accounts(...)`, que es la consulta de `/auth/me`, del
-- widget, del bot, del límite de empleados y del panel. Medido contra el stack
-- local: la aplicación entera dejaba de cargar. Una marca en el propio centro
-- no crea ninguna relación nueva.
--
-- La marca la limpia el borrado del centro sin ayuda de nadie (se va con la
-- fila), y el índice único parcial garantiza que no haya dos elegidos en la
-- misma cuenta: la invariante vive en la base, no en el código que escribe.
--
-- ═══ LA REGLA DE LA COLUMNA OBLIGATORIA (supabase/README.md) ═══
--
-- Las cuatro de `subscriptions` son OPCIONALES y sin valor por defecto.
-- `salons.sigue_al_bajar` es `not null` pero con DEFAULT false, que es lo que
-- la regla exige: el backend desplegado hoy inserta centros sin conocerla y
-- sigue funcionando.
--
-- ═══ ORDEN DE DESPLIEGUE: LA MIGRACIÓN VA ANTES DEL BACKEND ═══
--
-- El backend nuevo escribe estas columnas. Con el backend nuevo y sin la
-- migración, PostgREST rechazaría la escritura entera de la suscripción y el
-- webhook de Stripe contestaría 500. Al revés no pasa nada.
--
-- ═══ PERMISOS ═══
--
-- `subscriptions` y `salons` tienen los permisos por TABLA (no están entre las
-- de la 0047), así que las columnas nuevas nacen con los de siempre: la sesión
-- LEE (las necesitan la pantalla de Suscripción y la de Centros) y la marca la
-- escribe service_role, como el resto de lo que decide el dinero.
--
-- Para deshacer:
--   alter table public.subscriptions
--     drop column plan_programado, drop column intervalo_programado,
--     drop column cambio_programado_para, drop column stripe_schedule_id;
--   drop index if exists public.un_solo_centro_elegido_por_cuenta;
--   alter table public.salons drop column sigue_al_bajar;
-- (primero el backend anterior: el nuevo escribe estas columnas).

alter table public.subscriptions
  add column plan_programado public.plan_tipo,
  add column intervalo_programado text,
  add column cambio_programado_para timestamptz,
  add column stripe_schedule_id text;

alter table public.salons
  add column sigue_al_bajar boolean not null default false;

-- UNA CUENTA, UN CENTRO ELEGIDO. Sin esto, dos escrituras a la vez —dos
-- pestañas, dos clics— podrían dejar los dos centros marcados, y entonces la
-- congelación no sabría cuál pausar.
create unique index un_solo_centro_elegido_por_cuenta
  on public.salons (account_id)
  where sigue_al_bajar;

comment on column public.subscriptions.plan_programado is
  'Plan al que pasa al final del periodo pagado. Es una intención: no da ni '
  'quita funciones. Vacío = no hay cambio programado.';

comment on column public.subscriptions.stripe_schedule_id is
  'El calendario de suscripción de Stripe que aplicará el cambio. Se guarda '
  'para poder anularlo (release) si se sube de plan o se cambia de idea.';

comment on column public.salons.sigue_al_bajar is
  'Este es el centro que se queda activo cuando el plan admite menos de los '
  'que tiene la cuenta. Lo elige la titular al bajar de plan (B2a); lo aplica '
  'la congelación (B3a). Como mucho uno por cuenta (índice único parcial).';
