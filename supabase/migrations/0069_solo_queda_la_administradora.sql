-- 0069 · Solo queda la administradora: se borran los roles muertos.
--
-- La decisión es de la 0012 (15-07-2026) y la puerta la cerró la 0063: solo la
-- administradora tiene cuenta de acceso; el equipo ficha en el quiosco con el
-- PIN de su ficha. Los roles `estilista` y `recepcionista` eran restos del
-- primer diseño que nadie quitó, y el código que los nombraba llegó a desviar
-- una entrega hacia un inicio de sesión para empleadas que no debe existir.
--
-- ═══ ANTES DE NADA: HAY DOS `pin_fichaje_hash` ═══
--
--   · `employees.pin_fichaje_hash` — EL DEL QUIOSCO. VIVO. Cada empleada ficha
--     con él (`routers/fichaje.py`), y los triggers de la 0044 lo comparten
--     entre las fichas de una misma persona. ESTA MIGRACIÓN NO LO TOCA.
--   · `users.pin_fichaje_hash` — muerto desde la 0006, cuando el PIN se fue a
--     `employees`. Es el que se quita aquí.
--
-- ═══ CÓMO SE DEMOSTRÓ QUE CADA COSA ESTABA MUERTA (05-10-2026) ═══
--
-- Recorriendo el código y la base, no de memoria; los escáneres son los guardas
-- (`backend/tests/roles_muertos.py`, `test_publico_declarado.py`,
-- `test_roles_muertos_base_real.py`, `frontend/src/lib/roles-muertos.ts`):
--
--   · LOS ROLES. Desde la 0063 el hook no da token, `_exigir_sesion_valida` no
--     acepta sesión y `salones_visibles` no da centros a quien no sea admin.
--     En producción (Carlos, 05-10-2026): 4 administradoras —una por cuenta— y
--     5 restos (4 estilistas, 1 recepcionista). Lo único que escribía esos
--     valores eran los seeds; ningún código ni función de la base.
--   · `users.pin_fichaje_hash`. Ningún código lo lee ni lo escribe (árbol de
--     sintaxis de todo `backend/app`), ninguna función de la base lo nombra
--     (`pg_proc`), y la sesión no lo puede leer desde la 0047. En producción
--     solo lo tienen 4 de los 5 restos (3 estilistas y la recepcionista), que
--     se borran aquí: al quitar la columna no se pierde nada vivo.
--   · LO QUE CUELGA DE LOS 5 RESTOS, caminando las claves ajenas hacia `users`:
--       - `user_salons` (CASCADE): sus centros. Se van con ellos.
--       - `employees.user_id` (SET NULL): fichas VIVAS. Se DESENLAZAN, a mano y
--         contándolas (paso 1), no se borran: ver «las fichas enlazadas».
--       - `bot_sessions.atendida_por` (SET NULL): conversaciones. Si alguna
--         apunta a un resto, la migración SE PARA sin tocar nada.
--       - Cualquier OTRA clave ajena hacia `users` —la que exista el día que se
--         ejecute, leída de `pg_constraint`, no de esta lista—: si alguna fila
--         apunta a un resto, SE PARA sin tocar nada. Solo se desenlaza lo que
--         se miró; lo que no se previó lo decide una persona, no la migración.
--       - Sin clave ajena, a propósito (0057/0066): `decisiones_de_dinero`,
--         `descargas_de_datos`, `correcciones_de_fichaje` (por `usuario_id`) y
--         `support_access_codes.created_by_user_id` guardan el nombre y siguen.
--       - Su usuario de Auth, si lo tuvieran: se borra aquí (y con él, en
--         cascada, sus identidades y sesiones). Un usuario de Auth huérfano es
--         justo lo que se está limpiando.
--   · `user_salons`, ENTERA. La escribía un trigger de `users`
--     (`sincronizar_user_salons`) y la leían tres cosas:
--       - la rama de `centro_activo` para quien no es admin (se quita del
--         backend en esta entrega);
--       - `retirar_el_acceso_de_la_ficha` (0062), solo después de comprobar que
--         la persona NO es la administradora: con un solo rol, nunca;
--       - la segunda rama de `salones_visibles`, que desde la 0063 exige
--         `rol = 'admin'`, y para una administradora es un SUBCONJUNTO de la
--         primera: sus filas de `user_salons` son centros de SU cuenta (lo
--         comprueba la comprobación 0: si hubiera una de otra cuenta, se para).
--     Y la descarga de datos, que decía «a qué centros entra cada usuario»: la
--     administradora entra en todos.
--
-- ═══ LO QUE HABÍA EN PRODUCCIÓN EL DÍA QUE SE DECIDIÓ (05-10-2026) ═══
--
-- Consultado por Carlos, en solo lectura, antes de desplegar:
--   · 4 administradoras (una por cuenta) y 5 filas con rol muerto
--     (4 estilistas, 1 recepcionista);
--   · NINGUNA de las 5 con usuario de Auth;
--   · 3 de las 5 con una ficha viva enlazada por `employees.user_id`: las
--     tres activas y en el mismo centro, el de demostración (las enlazó
--     `seed_prod.sql`). Las otras 2, sin ficha;
--   · 0 conversaciones del bot atendidas por ellas.
-- La primera versión de esta migración se paraba ante esas 3 fichas. Carlos
-- decidió desenlazarlas y seguir.
--
-- ═══ LAS FICHAS ENLAZADAS: SE DESENLAZAN, NO SE BORRAN ═══
--
-- Son personal activo y tienen que seguir funcionando igual. Quitarles el
-- enlace es seguro porque:
--   · ninguna ruta de la app escribe `employees.user_id` (árbol de sintaxis de
--     `backend/app`; el guarda de censo lo tiene en `solo_guion_o_relleno`): ninguna
--     ficha creada desde la app lo tiene, así que estas tres quedan igual que
--     todas las demás;
--   · el quiosco ficha contra `employees.pin_fichaje_hash`, que no se toca, y
--     «la misma persona en dos centros» (0044) va por `persona_id`, no por esto;
--   · lo único que lee la columna —`sobre_si_misma` y `_ficha_con_usuario` en
--     `routers/employees.py`, y `retirar_el_acceso_de_la_ficha`— solo tiene
--     sentido para la ficha de la ADMINISTRADORA, que no es ninguna de estas;
--   · ningún trigger de `employees` salta al cambiar `user_id` (los hay de
--     `persona_id`, `pin_fichaje_hash`, `estado` y borrado).
-- La clave ajena ya lo haría sola (SET NULL), pero en silencio. Se hace
-- explícito y la migración dice cuántas desenlazó (un NOTICE en su salida).
--
-- ═══ EL 403 DEL HOOK SE QUEDA ═══
--
-- Es la puerta cerrada. Con el tipo `user_rol` de un solo valor ya no hay fila
-- que llegue a él, pero NO se quita (Carlos): si algún día volviera a haber un
-- segundo rol, la puerta tiene que estar ya ahí. Lo vigila el guarda. El hook
-- se vuelve a crear AQUÍ con el mismo cuerpo, palabra por palabra, por una
-- razón técnica: declara una variable de tipo `user_rol`, y una conexión que
-- ya lo tuviera compilado (las de GoTrue son persistentes) guardaría el tipo
-- viejo, que esta migración borra. Recrearlo obliga a recompilarlo.
--
-- ═══ EL PERMISO DE ESCRIBIR `users.rol` ═══
--
-- La sesión lo tenía (0047, permiso por columna) y la política `users_all_admin`
-- la dejaba. Ningún código escribe `users` con la sesión: el alta y `centros`
-- lo hacen con service_role. Con un enum de un solo valor ya no puede hacer
-- daño, pero sobra: se quita.
--
-- ═══ SI SE EJECUTA DOS VECES ═══
--
-- No hace nada la segunda vez: no quedan filas que borrar ni fichas que
-- desenlazar (el aviso dice «0 fichas»), el cambio de tipo
-- solo se hace si el tipo todavía tiene más de un valor, y todo lo que se quita
-- lleva `if exists`. Las funciones se reemplazan por la misma versión.
--
-- ═══ LA REGLA DE LA COLUMNA OBLIGATORIA ═══
--
-- No añade columnas. Quita `users.pin_fichaje_hash`, que ningún backend
-- desplegado lee ni escribe, y la tabla `user_salons`, que el backend anterior
-- SÍ lee en `centro_activo` (solo para quien no es admin, que ya no existe) y
-- en la descarga de datos. Por eso:
--
-- ═══ ORDEN DE DESPLIEGUE: BACKEND → 0069 ═══
--
-- Primero el backend nuevo (que ya no nombra `user_salons` ni los roles: con la
-- base de hoy funciona igual), y después esta migración. Al revés, la descarga
-- de datos del backend anterior fallaría al leer una tabla que no existe.

begin;

-- ═══════════════ 0. comprobaciones: lo que no se previó, para sin tocar nada

do $$
declare
  n int;
  r record;
begin
  -- Hoy (05-10-2026) es 0. Por casualidad, no por diseño: la comprobación se queda.
  select count(*) into n from public.bot_sessions b
  where b.atendida_por in (select u.id from public.users u where u.rol::text <> 'admin');
  if n > 0 then
    raise exception '0069: % conversaciones las atendió un usuario con un rol muerto. No se borra nada: hay que decidir qué hacer con ellas.', n;
  end if;

  -- Cualquier otra clave ajena hacia `users`, leída del catálogo el día que se
  -- ejecute. Solo se exceptúan las dos que se miraron: `employees.user_id` (se
  -- desenlaza en el paso 1) y `user_salons` (se va en cascada).
  for r in
    select c.conname, c.conrelid::regclass as tabla, c.conkey,
           (select a.attname from pg_attribute a
            where a.attrelid = c.conrelid and a.attnum = c.conkey[1]) as columna
    from pg_constraint c
    where c.contype = 'f' and c.confrelid = 'public.users'::regclass
  loop
    continue when r.tabla = 'public.employees'::regclass and r.columna = 'user_id';
    continue when r.tabla = to_regclass('public.user_salons');
    if array_length(r.conkey, 1) <> 1 then
      raise exception '0069: la clave ajena % (%) tiene varias columnas y esta migración no sabe mirarla. No se borra nada.', r.conname, r.tabla;
    end if;
    execute format(
      'select count(*) from %s where %I in (select u.id from public.users u where u.rol::text <> ''admin'')',
      r.tabla, r.columna) into n;
    if n > 0 then
      raise exception '0069: % filas de %.% apuntan a usuarios con un rol muerto. No se borra nada: hay que decidir qué hacer con ellas.', n, r.tabla, r.columna;
    end if;
  end loop;

  if to_regclass('public.user_salons') is not null then
    execute $q$
      select count(*) from public.user_salons us
      join public.users u on u.id = us.user_id
      join public.salons s on s.id = us.salon_id
      where u.rol::text = 'admin' and s.account_id is distinct from u.account_id
    $q$ into n;
    if n > 0 then
      raise exception '0069: % filas de user_salons dan a una administradora un centro de OTRA cuenta. No se borra nada.', n;
    end if;
  end if;
end $$;

-- ═══════════════ 1. desenlazar las fichas; después, los cinco restos y su usuario de Auth

-- Explícito y contado, aunque la clave ajena lo haría sola (ver la cabecera).
-- En producción, el 05-10-2026, eran 3.
do $$
declare
  n int;
begin
  update public.employees set user_id = null
  where user_id in (select u.id from public.users u where u.rol::text <> 'admin');
  get diagnostics n = row_count;
  raise notice '0069: % fichas desenlazadas de un usuario con rol muerto (siguen como estaban, con su PIN).', n;
end $$;

delete from auth.users
where id in (select u.auth_user_id from public.users u
             where u.rol::text <> 'admin' and u.auth_user_id is not null);

delete from public.users where rol::text <> 'admin';

-- ═══════════════ 2. el tipo: solo «admin»

do $$
begin
  if (select count(*) from pg_enum where enumtypid = 'public.user_rol'::regtype) > 1 then
    alter type public.user_rol rename to user_rol_0069;
    create type public.user_rol as enum ('admin');
    alter table public.users alter column rol drop default;
    alter table public.users alter column rol type public.user_rol using rol::text::public.user_rol;
    alter table public.users alter column rol set default 'admin';
    drop type public.user_rol_0069;
  end if;
end $$;

comment on type public.user_rol is
  'Solo «admin» (0069): solo la administradora tiene cuenta de acceso. Las empleadas fichan en el quiosco con el PIN de su ficha.';

-- El hook, el mismo cuerpo, para que ninguna conexión se quede con el tipo viejo.
create or replace function public.custom_access_token_hook(event jsonb)
 returns jsonb
 language plpgsql
 stable
as $function$
declare
  claims jsonb;
  v_salon_id uuid;
  v_rol public.user_rol;
  v_retirado timestamptz;
begin
  select salon_id, rol, acceso_retirado_el into v_salon_id, v_rol, v_retirado
  from public.users
  where auth_user_id = (event ->> 'user_id')::uuid
  limit 1;

  -- SOLO ENTRA LA ADMINISTRADORA. Con un error, GoTrue no emite el token: ni
  -- al iniciar sesión ni al refrescar. El texto no dice nada que quien ha
  -- llegado aquí —con la contraseña buena— no sepa ya.
  if found and v_rol <> 'admin' then
    return jsonb_build_object(
      'error', jsonb_build_object(
        'http_code', 403,
        'message', 'Solo la administradora del salón entra en la aplicación.'
      )
    );
  end if;

  claims := event -> 'claims';

  -- Sin centro en el token para quien se quedó sin acceso (0062).
  if v_salon_id is not null and v_retirado is null then
    claims := jsonb_set(claims, '{salon_id}', to_jsonb(v_salon_id::text));
    claims := jsonb_set(claims, '{rol}', to_jsonb(v_rol::text));
  end if;

  event := jsonb_set(event, '{claims}', claims);
  return event;
end;
$function$;

-- ═══════════════ 3. el PIN muerto (el de `users`; el de `employees` NO se toca)

alter table public.users drop column if exists pin_fichaje_hash;

-- ═══════════════ 4. `user_salons`, y lo que la escribía y la leía

drop trigger if exists users_sincroniza_salones on public.users;
drop function if exists public.sincronizar_user_salons();

-- La frontera: la administradora ve los centros de su cuenta. La segunda rama
-- (por `user_salons`) era, para ella, un subconjunto de esta.
create or replace function public.salones_visibles()
 returns uuid[]
 language sql
 stable security definer
 set search_path to 'public', 'pg_temp'
as $function$
  -- SECURITY DEFINER NO ES OPCIONAL: ver 0039. Solo la administradora (0063,
  -- 0069): los centros de SU cuenta, mientras no se le haya retirado el acceso.
  select coalesce(
    array(
      select s.id from public.salons s
      join public.users u on u.account_id = s.account_id
      where u.auth_user_id = auth.uid() and u.rol = 'admin'
        and u.acceso_retirado_el is null
    ),
    '{}'::uuid[]
  );
$function$;

-- Dar de baja (o borrar) la ficha de la administradora sigue sin poder quitarle
-- el acceso. Lo que hacía para una empleada CON cuenta ya no tiene a quién.
create or replace function public.retirar_el_acceso_de_la_ficha()
 returns trigger
 language plpgsql
 security definer
 set search_path to 'public', 'pg_temp'
as $function$
declare
  ficha public.employees;
begin
  ficha := case when tg_op = 'DELETE' then old else new end;

  if tg_op = 'UPDATE' and not (new.estado::text = 'baja' and old.estado::text <> 'baja') then
    return new;
  end if;
  if ficha.user_id is null then
    return ficha;
  end if;
  if not exists (select 1 from public.users where id = ficha.user_id) then
    return ficha;
  end if;

  -- Una ficha con usuario es la de la administradora: es el único que hay (0069).
  raise exception 'No se puede quitar el acceso a la administradora de la cuenta dándole de baja.'
    using errcode = 'P0001', hint = 'baja_de_administradora';
end;
$function$;

drop table if exists public.user_salons;

-- ═══════════════ 5. el permiso de escribir el rol

revoke insert (rol), update (rol) on public.users from authenticated;

commit;
