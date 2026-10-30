-- 0043 · En qué centro estaba trabajando cada persona
--
-- La Fase A dejó la frontera en la cuenta: las 50 políticas de inquilino
-- filtran por `salon_id = any(salones_visibles())`, y `current_salon_id()` se
-- quedó SIN UN SOLO USO —cero políticas, cero valores por defecto, cero
-- funciones—. O sea que el claim `salon_id` del token ya no decide nada.
--
-- Eso es lo que permite que exista un selector de centro sin volver a iniciar
-- sesión: el centro activo viaja en una cabecera, el backend comprueba que sea
-- uno de los tuyos, y la RLS sigue siendo la frontera de verdad. Si alguien
-- manipula la cabecera, la base de datos sigue diciendo que no.
--
-- LO QUE AÑADE ESTA MIGRACIÓN es solo dónde se recuerda esa elección.
--
-- ¿POR QUÉ EN LA BASE Y NO EN EL NAVEGADOR? Porque «por usuario» no es lo
-- mismo que «por navegador». Guardado aquí, quien trabaja desde el móvil y
-- desde el mostrador abre los dos donde lo dejó; y `/auth/me` puede devolverlo
-- en la PRIMERA respuesta, así que la aplicación nunca llega a pintarse en el
-- centro equivocado antes de corregirse. Ese parpadeo es justo el que acaba en
-- una cita agendada donde no toca.
--
-- ═══ LA REGLA (supabase/README.md) ═══
--
-- `users` es una tabla en la que el backend inserta (el alta), así que la
-- columna va ANULABLE y sin valor por defecto. Nula significa «abre donde
-- siempre», que es lo que hacen hoy todas las cuentas.

begin;

alter table public.users
  add column ultimo_centro_id uuid references public.salons(id) on delete set null;

comment on column public.users.ultimo_centro_id is
  'El centro en el que esta persona estaba trabajando. Es una PREFERENCIA, no '
  'un permiso: a qué centros puede entrar lo decide `salones_visibles()`, y '
  'esta columna se valida contra ella en cada petición. NULL = su centro de '
  'siempre. `on delete set null` porque al cerrar un centro la preferencia '
  'sobra, y perderla no le quita nada a nadie.';

create index users_ultimo_centro_id_idx on public.users (ultimo_centro_id);

-- NO SE PONE UN TOPE DE CENTROS EN LA BASE, y es deliberado. El máximo de dos
-- y el «solo Elite» dependen del PLAN, que cambia solo desde Stripe: un
-- `check` o un trigger que los mirara convertiría una bajada de plan en un
-- error de escritura en mitad del webhook. La regla vive en el backend, donde
-- se puede contestar un 409 con un motivo, y hay tests que la fijan.

commit;
