-- 0063 · Solo entra la administradora
--
-- LA DECISIÓN NO ES NUEVA: es la de la 0012 (15-07-2026), «el producto pasa a
-- modelo ADMIN-ONLY: solo el admin accede al SaaS; el resto del personal usa
-- únicamente el quiosco de fichaje con PIN». Aquella migración quitó la
-- invitación de equipo pero dejó intactos los roles `estilista` y
-- `recepcionista`, y con ellos la puerta abierta: un usuario con uno de esos
-- roles, si existe y sabe su contraseña, entra y ve clientas, citas y
-- conversaciones. Esta migración cierra esa puerta (Carlos, 04-10-2026).
--
-- NO QUITA LA MAQUINARIA MUERTA. El enum, las ramas de no-admin y
-- `users.pin_fichaje_hash` siguen ahí; quitarlos va después, en una entrega
-- propia. Aquí solo se cambia QUIÉN PASA.
--
-- ═══ DÓNDE, Y POR QUÉ AHÍ ═══
--
--   1. EN EL HOOK DEL TOKEN, que es la puerta de GoTrue. A un usuario de la app
--      que no es administradora el hook le DEVUELVE UN ERROR y GoTrue no emite
--      token: ni al iniciar sesión ni al refrescar una sesión ya abierta. No
--      hay otro sitio por donde salga un token de la app: la contraseña, el
--      enlace mágico y el refresco pasan todos por aquí. Ninguna versión del
--      backend o del frontend puede saltárselo, porque no participan.
--
--   2. EN LA FRONTERA DE LA BASE (`salones_visibles()` y `cuenta_actual()`): la
--      rama «el resto, solo sus centros» pasa a exigir también rol admin. Es lo
--      que cubre un token que ya estuviera emitido al aplicar esto (dura una
--      hora) o un hook apagado por error en el panel: la RLS no le enseña ni una
--      fila a quien no es administradora, la deje entrar quien la deje entrar.
--      La rama se queda —quitarla es la entrega de la limpieza— pero desde aquí
--      solo la cumple una administradora, que ya la cumplía por la primera.
--
--   3. El backend (`_exigir_sesion_valida`) pide además `rol = admin` en la
--      misma consulta que ya hace: un token viejo de una estilista recibe 401
--      «sesion_revocada» en la siguiente petición.
--
-- ═══ LO QUE SIGUE PASANDO, Y POR QUÉ ═══
--
--   · La administradora, en cualquiera de los centros de su cuenta: su fila es
--     `admin`, y el centro activo no pasa por el hook.
--   · El modo soporte y el bot de WhatsApp: los dos acuñan el token de la
--     ADMINISTRADORA de la cuenta (`acunar_token_tenant`, magiclink), así que el
--     hook ve una administradora. Comprobado de verdad en
--     `tests/test_solo_entra_la_administradora_base_real.py`.
--   · El panel de plataforma (Super Admin): no usa GoTrue; tiene su propio JWT.
--   · Una identidad SIN fila en `users` (un alta a medias): igual que antes,
--     token sin centro, y el backend la para con «Token sin contexto de
--     salón». No es una persona del equipo y no se toca.
--
-- ═══ LO QUE NO REVELA ═══
--
-- El hook solo corre DESPUÉS de comprobar la contraseña. Quien no la sabe
-- recibe de GoTrue lo de siempre («invalid_credentials»), exista la cuenta o
-- no. La pantalla de login enseña el mismo texto en los dos casos.
--
-- ═══ LA REGLA DE LA COLUMNA OBLIGATORIA ═══
--
-- No añade columnas.
--
-- ═══ ORDEN DE DESPLIEGUE: 0062 → 0063 → BACKEND → FRONTEND ═══
--
-- Va después de la 0062 porque redefine las tres funciones que la 0062 dejó
-- mirando `acceso_retirado_el`, y esa columna la crea la 0062. Con el backend
-- de antes funciona igual: no le cambia nada a una administradora.

begin;

create or replace function public.custom_access_token_hook(event jsonb)
returns jsonb
language plpgsql
stable
as $$
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
$$;

create or replace function public.salones_visibles()
returns uuid[]
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  -- SECURITY DEFINER NO ES OPCIONAL: ver 0039. Desde la 0063 las DOS ramas
  -- exigen `rol = 'admin'`: solo entra la administradora (0012). La segunda
  -- rama se quiere quitar en la limpieza de los roles; mientras, ya no la
  -- cumple nadie que no cumpla la primera.
  select coalesce(
    array(
      select s.id from public.salons s
      join public.users u on u.account_id = s.account_id
      where u.auth_user_id = auth.uid() and u.rol = 'admin'
        and u.acceso_retirado_el is null
      union
      select us.salon_id from public.user_salons us
      join public.users u on u.id = us.user_id
      where u.auth_user_id = auth.uid() and u.rol = 'admin'
        and u.acceso_retirado_el is null
    ),
    '{}'::uuid[]
  );
$$;

create or replace function public.cuenta_actual()
returns uuid
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select u.account_id from public.users u
  where u.auth_user_id = auth.uid()
    and u.rol = 'admin'
    and u.acceso_retirado_el is null
  limit 1;
$$;

commit;
