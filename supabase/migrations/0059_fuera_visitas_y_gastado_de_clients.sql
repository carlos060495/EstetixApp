-- 0059 · Fuera `clients.visitas` y `clients.total_gastado`
--
-- Eran la copia que no mantenía nadie (ver 0058). Desde la 0058 la app lee
-- visitas y gastado de la vista `clientas_con_actividad`, calculados de las
-- citas completadas. Si estas columnas se quedaran, la próxima persona que
-- escribiera `select ... visitas from clients` volvería a enseñar un cero con
-- toda la naturalidad.
--
-- ═══ CÓMO SE COMPROBÓ QUE ESTÁN MUERTAS (01-10-2026, con el código de la 0058) ═══
--
--   · Código: `git grep -nIE "\b(visitas|total_gastado)\b"` sobre backend/app,
--     backend/scripts, frontend/src y las semillas. Lo que queda nombra el
--     CAMPO de la respuesta (que sale de la vista) o es un comentario; ningún
--     `select`, `insert` ni `update` contra `clients` las pide.
--   · Semillas: `seed.sql` y `seed_prod.sql` las rellenaban con cifras
--     inventadas (18 visitas, 1240,50 €). Se quitaron en el mismo cambio.
--   · Censo del backend (tests/columnas_sin_escritor.py): ni `clients.visitas`
--     ni `clients.total_gastado` aparecen como leídas; un test lo exige
--     (`test_las_visitas_ya_no_se_leen_de_la_tabla`).
--   · Base (local, con la 0058 aplicada), todas vacías:
--       information_schema.view_column_usage  → ninguna vista las usa
--                                                (la 0058 las CALCULA, no las copia)
--       pg_proc.prosrc                         → ninguna función las nombra
--       pg_constraint / pg_index               → ninguna restricción ni índice
--       pg_policies (qual / with_check)        → ninguna política
--     Si una dependencia se hubiera escapado, el `drop column` sin `cascade`
--     fallaría en vez de llevársela por delante.
--
-- ═══ LA REGLA DE LA COLUMNA OBLIGATORIA (supabase/README.md) ═══
--
-- Quita columnas, no añade: no aplica.
--
-- ═══ ORDEN DE DESPLIEGUE: 0058 → BACKEND → FRONTEND → 0059 ═══
--
-- DESPUÉS del backend y del frontend que leen de la vista. Con el backend
-- viejo desplegado, la lista de clientas pediría `clients.visitas` y PostgREST
-- contestaría 400 («column clients.visitas does not exist»).

begin;

alter table public.clients drop column visitas;
alter table public.clients drop column total_gastado;

commit;
