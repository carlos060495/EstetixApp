-- ¿Está el entorno local sano, o solo lo parece?
--
--   psql "$DATABASE_URL" -f supabase/tests/entorno_local.sql
--
-- POR QUÉ EXISTE ESTE FICHERO. Un local con los permisos mal no falla de forma
-- reconocible: PostgREST responde «403 permission denied» con la RLS perfecta,
-- y el arreglo evidente —un `grant` masivo sobre todas las tablas— cambia un
-- problema por otro peor, porque devuelve permisos que varias migraciones
-- quitan A PROPÓSITO. El entorno queda MÁS PERMISIVO QUE PRODUCCIÓN y deja de
-- servir para razonar sobre esas barreras: es un local que dice que sí a cosas
-- a las que producción dice que no.
--
-- Así que se comprueban las dos mitades, y ninguna vale sin la otra:
--   1. Que los roles de PostgREST PUEDEN hacer lo que la aplicación necesita.
--   2. Que NO pueden hacer lo que las migraciones les prohibieron.
--
-- No toca nada: solo lee catálogos. No hace falta transacción.

-- ---------------------------------------------------------------------------
-- 1. Lo que la aplicación necesita poder hacer
-- ---------------------------------------------------------------------------
-- El síntoma clásico del entorno roto: `service_role` sin SELECT sobre las
-- tablas del inquilino. Es lo que tumbó el quiosco de fichaje en local y lo que
-- impidió medir nada durante un rato, porque el 403 se parece a un fallo de RLS
-- y no lo es.
--
-- Se miran las tablas por las que entra el quiosco (service_role) y la app
-- (authenticated). Si falta cualquiera de estos permisos, el entorno no sirve.
do $$
declare
  v_faltan text;
begin
  select string_agg(format('%s → %s (%s)', t.rol, t.tabla, t.priv), e'\n  · ')
    into v_faltan
    from (
      select r.rol, x.tabla, p.priv
        from (values ('service_role'), ('authenticated')) as r(rol)
        cross join (values
          ('employees'), ('time_entries'), ('pin_attempt_locks'),
          ('salons'), ('users'), ('appointments'), ('clients'),
          ('bot_sessions'), ('bot_messages'), ('salon_whatsapp_config')
        ) as x(tabla)
        cross join (values ('SELECT'), ('INSERT'), ('UPDATE'), ('DELETE')) as p(priv)
       -- Borrar un centro es solo nuestro (0067): la sesión no tiene DELETE
       -- sobre `salons`, y el bloque 2 comprueba que no lo tenga.
       where not (r.rol = 'authenticated' and x.tabla = 'salons' and p.priv = 'DELETE')
         and not exists (
         select 1 from information_schema.role_table_grants g
          where g.table_schema = 'public'
            and g.table_name = x.tabla
            and g.grantee = r.rol
            and g.privilege_type = p.priv
       )
    ) t;

  if v_faltan is not null then
    raise exception e'ENTORNO ROTO: faltan permisos que la aplicación necesita:\n  · %', v_faltan
      using hint = 'El local viene de un volumen viejo. NO lo arregles con un grant masivo: '
                   'destrúyelo y vuelve a levantarlo con '
                   '`npx supabase stop --no-backup && npx supabase start`.';
  end if;
end $$;

-- ---------------------------------------------------------------------------
-- 2. Lo que las migraciones les prohibieron
-- ---------------------------------------------------------------------------
-- La contraprueba, y la que caza el «arreglo» del grant masivo. Sin ella, un
-- local con todos los permisos abiertos pasaría el bloque de arriba con nota y
-- seguiría siendo inútil para comprobar cualquier cosa sobre estas barreras.
--
--   admin_logs (0004)                       append-only: ni UPDATE ni DELETE.
--   support_access_codes,
--   impersonation_sessions (0021)           el inquilino ni los crea ni los toca.
--   referidos, plataforma_config (0029)     son de PLATAFORMA: un token de
--                                           salón no pinta nada ahí.
--   salons (0067)                           borrar un centro es solo nuestro:
--                                           ni DELETE ni TRUNCATE.
do $$
declare
  v_sobran text;
begin
  select string_agg(format('%s puede %s sobre %s', g.grantee, g.privilege_type, g.table_name), e'\n  · ')
    into v_sobran
    from information_schema.role_table_grants g
   where g.table_schema = 'public'
     and g.grantee in ('anon', 'authenticated')
     and (
       (g.table_name in ('admin_logs') and g.privilege_type in ('UPDATE', 'DELETE'))
       or (g.table_name in ('support_access_codes', 'impersonation_sessions')
           and g.privilege_type in ('UPDATE', 'DELETE'))
       or (g.table_name in ('referidos', 'plataforma_config')
           and g.privilege_type in ('SELECT', 'INSERT', 'UPDATE', 'DELETE'))
       or (g.table_name = 'salons' and g.privilege_type in ('DELETE', 'TRUNCATE'))
     );

  if v_sobran is not null then
    raise exception e'ENTORNO MÁS PERMISIVO QUE PRODUCCIÓN:\n  · %', v_sobran
      using hint = 'Alguien ha corrido un `grant ... on all tables`. Ese comando deshace '
                   'los revoke de las migraciones 0004, 0021, 0029 y 0067. Vuelve a levantar el '
                   'local desde cero: `npx supabase stop --no-backup && npx supabase start`.';
  end if;
end $$;

-- ---------------------------------------------------------------------------
-- 3. Que las migraciones estén todas y el seed haya entrado
-- ---------------------------------------------------------------------------
-- Un `db reset` que falle a mitad —el seed reventó durante cuatro migraciones
-- por pedir una columna eliminada— deja un local silenciosamente incompleto:
-- las tablas están, los datos no, y nadie se entera hasta que abre una pantalla
-- que no tiene nada que enseñar.
do $$
declare
  v_migraciones int;
  v_empleados int;
begin
  select count(*) into v_migraciones from supabase_migrations.schema_migrations;
  if v_migraciones < 41 then
    raise exception 'ENTORNO INCOMPLETO: solo % migraciones aplicadas', v_migraciones
      using hint = 'Ejecuta `npx supabase db reset` y mira si alguna falla.';
  end if;

  select count(*) into v_empleados from public.employees;
  if v_empleados = 0 then
    raise exception 'SEED SIN APLICAR: no hay ni un empleado'
      using hint = 'El seed falló (mira la salida de `db reset`). Sin datos no se puede '
                   'abrir ninguna pantalla en local, que es como se cazan los fallos de '
                   'esquema antes de que lleguen a producción.';
  end if;
end $$;

do $$
begin
  raise notice 'ENTORNO LOCAL: OK';
end $$;
