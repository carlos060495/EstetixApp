-- 0055 · Fuera `cancelacion_pedida`
--
-- La columna de la 0052 que vigilaba una PETICIÓN y no la voluntad del salón.
-- La 0053 la dejó sin uso y avisó de que se iría en su propia migración,
-- DESPUÉS del código que la nombraba. Es esta.
--
-- ═══ CÓMO SE COMPROBÓ QUE ESTÁ MUERTA (medido el 28-09-2026) ═══
--
-- No basta con decirlo; esto es lo que se miró y lo que salió:
--
--   1. EL CÓDIGO. `git grep cancelacion_pedida` en backend/app, backend/tests,
--      backend/scripts y frontend/src, en el árbol de trabajo y en el commit
--      1961916: una sola aparición, y es un comentario de billing.py que cuenta
--      la historia. Ningún select, ningún update, ningún tipo del frontend.
--      Tampoco hay un `select=*` sobre subscriptions que la arrastrara.
--   2. LA BASE (local, con 0001–0054 aplicadas):
--        · pg_proc.prosrc ILIKE '%cancelacion_pedida%'            → 0 funciones
--        · information_schema.view_column_usage                    → 0 vistas
--        · pg_depend sobre la columna                              → 0 dependencias
--        · pg_policy (qual y with check) de subscriptions          → 0 políticas
--      Sin nada que la nombre, el DROP no necesita CASCADE, y se deja sin
--      CASCADE a propósito: si algo la nombrara, que falle en vez de arrastrarlo.
--   3. LOS DATOS. No se pierde nada que no se tenga en otro sitio: la baja la
--      cuenta ahora `baja_solicitada_at` (0053), y cada decisión deja fila en
--      `decisiones_de_dinero` (0054).
--
-- ═══ ORDEN DE DESPLIEGUE: VA DESPUÉS DEL BACKEND DE LA 0053 ═══
--
-- El backend de 7e3e91d y anteriores ESCRIBE esta columna (baja.py, billing.py
-- y el webhook). Con ese backend vivo, borrarla haría que la baja y el webhook
-- de Stripe contestaran error. Se aplica cuando en Railway corre un backend
-- igual o posterior a 1961916 —que ya no la nombra—; en la práctica, al final
-- de esta entrega: 0054 → backend → frontend → 0055.
--
-- ═══ LA REGLA DE LA COLUMNA OBLIGATORIA (supabase/README.md) ═══
--
-- No añade columnas: no aplica.

begin;

alter table public.subscriptions
  drop column cancelacion_pedida;

commit;
