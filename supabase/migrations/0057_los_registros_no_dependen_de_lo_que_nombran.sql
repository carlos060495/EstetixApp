-- 0057 · Los registros no dependen de lo que nombran
--
-- Un registro de auditoría no puede depender de que siga existiendo lo que
-- describe. El de dinero (0054) nació con claves ajenas a `accounts`, `salons` y
-- `users`, y eso, en una tabla que no se deja tocar, IMPIDE BORRARLOS:
--
--   · Postgres rechaza el DELETE de un usuario, un centro o una cuenta mientras
--     una fila del registro lo nombre (23503).
--   · `on delete set null` no lo arregla: es un UPDATE, y el trigger de la 0054
--     rechaza todo UPDATE.
--
-- Medido el 01-10-2026: lo cazó el desmontaje de un test al borrar un usuario.
-- Hoy la app no borra a nadie con filas en el registro, pero lo bloquearían un
-- borrado desde el panel de Supabase y el borrado a los 90 días.
--
-- ═══ LA REGLA, PARA LOS DOS REGISTROS ═══
--
--   1. SIN CLAVES AJENAS a usuarios, centros ni cuentas. Los ids se guardan
--      sueltos. El de descargas (0056) ya nace así; aquí se le quitan al de
--      dinero.
--   2. Y UNA INSTANTÁNEA DE LOS NOMBRES en el momento de escribir la fila:
--      `cuenta_nombre`, `centro_nombre`, `usuario_nombre` y `usuario_email`.
--      Dentro de 90 días esa fila de usuario ya no existirá, y el registro
--      tiene que seguir diciendo quién fue. Un rastro que se destruye borrando
--      a quien nombra no es un rastro.
--
-- Lo vigila `tests/test_los_registros_sobreviven.py`: descubre los registros
-- por su trigger (`*_no_se_tocan`) y se pone rojo si alguno vuelve a tener una
-- clave ajena. Y comprueba de verdad que se borran una empleada, un centro y una
-- cuenta con filas puestas, y que las filas siguen ahí y se leen.
--
-- ═══ LAS FILAS QUE YA HAY ═══
--
-- En producción la 0054 tiene 0 filas (comprobado el 01-10-2026), así que no hay
-- filas viejas sin instantánea. Las columnas nuevas se AÑADEN, no se rellenan:
-- rellenarlas sería un UPDATE, que el trigger rechaza, y un registro no se
-- reescribe. Si aparecieran filas sin instantánea, el panel busca el nombre de
-- ahora mientras el usuario exista.
--
-- ═══ LA REGLA DE LA COLUMNA OBLIGATORIA (supabase/README.md) ═══
--
-- Las cuatro columnas son OPCIONALES y sin valor por defecto: el backend
-- desplegado hoy sigue escribiendo filas sin conocerlas.
--
-- ═══ ORDEN DE DESPLIEGUE: 0056 → 0057 → BACKEND → FRONTEND ═══
--
-- El backend nuevo escribe la instantánea (sin estas columnas, su INSERT
-- fallaría) y el panel deja de pedir `users` por embed. Entre la 0057 y el
-- backend nuevo, el historial de dinero del PANEL DE PLATAFORMA contesta 502:
-- el backend de hoy lo pide con un embed que sin la clave ajena PostgREST no
-- puede resolver. Solo esa pantalla y solo ese rato; nada que vea un salón.
--
-- Vuelta atrás: volver a poner las claves ajenas solo es posible si no se ha
-- borrado nada de lo que nombran, y volvería a bloquear los borrados.

begin;

alter table public.decisiones_de_dinero
  drop constraint decisiones_de_dinero_account_id_fkey,
  drop constraint decisiones_de_dinero_salon_id_fkey,
  drop constraint decisiones_de_dinero_usuario_id_fkey,
  add column cuenta_nombre text,
  add column centro_nombre text,
  add column usuario_nombre text,
  add column usuario_email text;

comment on column public.decisiones_de_dinero.usuario_nombre is
  'El nombre de la persona cuando se escribió la fila. Sigue aquí aunque el usuario se borre (0057).';
comment on column public.decisiones_de_dinero.usuario_email is
  'El correo de la persona cuando se escribió la fila (0057).';

commit;
