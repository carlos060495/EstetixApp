-- RLS y grants del registro de consentimiento de soporte (migración 0021).
--
-- Lo que se fija aquí: el admin del salón LEE y CREA su registro de
-- consentimiento, pero no lo REESCRIBE. Las dos tablas son la prueba de que el
-- acceso fue consentido y de cuándo terminó; si su propio sujeto pudiera
-- hacerles UPDATE, no probarían nada.
--
-- Ejecutar contra una pila local con migraciones + seed aplicados:
--   psql "$DATABASE_URL" -f supabase/tests/support_access_rls.sql
--
-- OJO: si después de `supabase db reset` se ha hecho el `grant ... on all tables
-- in schema public to authenticated` que necesita PostgREST en local, hay que
-- volver a ejecutar los dos `revoke` de la 0021 antes de correr esto. Ese grant
-- los deshace, y sin ellos las comprobaciones 3, 4 y 5 fallan a propósito
-- diciendo justo eso.
--
-- CÓMO SE ESCRIBE UN CASO NEGATIVO AQUÍ (importante, es fácil equivocarse):
-- nunca poner el `raise exception` del fallo DENTRO del bloque que caza la
-- excepción esperada — su propio manejador se lo traga y el test pasa siempre.
-- Se usa una bandera (o el row_count) y se lanza FUERA del bloque. Y el `when`
-- se acota a `insufficient_privilege`: con `when others` se colaría también un
-- fallo ajeno (una FK, un UUID mal tecleado) haciéndose pasar por éxito.
--
-- Mismo contrato que tenant_isolation.sql: cada comprobación RAISE si falla, y
-- todo el fichero va dentro de una transacción que termina en rollback, así que
-- ni las filas de apoyo ni los intentos de escritura sobreviven.
--
-- IDs fijos del seed (supabase/seed.sql):
--   salon A (Salón Demo)  = 00000000-0000-0000-0000-000000000001
--   salon B (Salón Norte) = 00000000-0000-0000-0000-000000000002
--   admin A auth_user_id  = 10000000-0000-0000-0000-000000000001

begin;

-- Mismo helper que tenant_isolation.sql: suplanta a un inquilino por el GUC de
-- claims que lee auth.jwt(), que es exactamente lo que hace PostgREST.
create or replace function pg_temp.login_as(p_salon_id uuid, p_rol text, p_user_id uuid)
returns void language plpgsql as $$
begin
  set local role authenticated;
  perform set_config('request.jwt.claims',
    json_build_object('sub', p_user_id, 'salon_id', p_salon_id, 'rol', p_rol)::text,
    true);
end;
$$;

-- ---------------------------------------------------------------------------
-- Filas de apoyo, creadas como superusuario (se van con el rollback).
-- ---------------------------------------------------------------------------
insert into public.admin_users (id, email, nombre, rol, password_hash)
values ('11111111-1111-1111-1111-111111111111', 'owner@estetixapp.test', 'Owner', 'owner', 'x');

insert into public.support_access_codes (id, salon_id, code_hash, expires_at)
values ('cccccccc-0000-0000-0000-000000000001',
        '00000000-0000-0000-0000-000000000001',
        'hash-de-prueba',
        now() + interval '15 minutes');

insert into public.impersonation_sessions
  (id, admin_id, admin_email, salon_id, code_id, tenant_auth_user_id, tenant_email, expires_at)
values ('55555555-0000-0000-0000-000000000001',
        '11111111-1111-1111-1111-111111111111', 'owner@estetixapp.test',
        '00000000-0000-0000-0000-000000000001',
        'cccccccc-0000-0000-0000-000000000001',
        '10000000-0000-0000-0000-000000000001', 'duena@salon.test',
        now() + interval '30 minutes');

select pg_temp.login_as('00000000-0000-0000-0000-000000000001', 'admin', '10000000-0000-0000-0000-000000000001');

-- ---------------------------------------------------------------------------
-- 1. support_access_codes: el salón SÍ puede leer lo suyo.
-- ---------------------------------------------------------------------------
do $$
declare
  v_count int;
begin
  select count(*) into v_count from public.support_access_codes
   where salon_id = '00000000-0000-0000-0000-000000000001';
  if v_count <> 1 then
    raise exception 'RLS FAILURE: el admin del salón no puede leer su propio código (% filas)', v_count;
  end if;
end $$;

-- ---------------------------------------------------------------------------
-- 2. support_access_codes: el salón SÍ puede crear un código propio. Es su
--    manera de dar el consentimiento y la ÚNICA escritura que le queda, así
--    que este es el punto donde más importa que el test sepa fallar.
-- ---------------------------------------------------------------------------
do $$
begin
  insert into public.support_access_codes (salon_id, code_hash, expires_at)
  values ('00000000-0000-0000-0000-000000000001', 'nuevo-hash', now() + interval '15 minutes');
exception
  when others then
    raise exception 'RLS FAILURE: el admin del salón no pudo crear su código (%)', sqlerrm;
end $$;

do $$
declare
  v_count int;
begin
  select count(*) into v_count from public.support_access_codes
   where salon_id = '00000000-0000-0000-0000-000000000001';
  if v_count <> 2 then
    raise exception 'RLS FAILURE: el código recién creado no está (% filas, esperaba 2)', v_count;
  end if;
end $$;

-- ---------------------------------------------------------------------------
-- 3. support_access_codes: el salón NO puede plantar un código bajo otro salón.
--
--    El `raise` va FUERA del bloque que caza la excepción esperada: dentro, su
--    propio manejador se lo tragaba y esta comprobación no podía fallar nunca.
--    La señal fiable es la bandera, no un recuento posterior: al cazar la
--    excepción PL/pgSQL revierte la sub-transacción (la fila desaparecería
--    sola) y, aunque el insert pase, la fila es del salón B y la política de
--    SELECT de este inquilino no la deja ver — contarla daría 0 igualmente.
-- ---------------------------------------------------------------------------
do $$
declare
  v_paso boolean := false;
begin
  begin
    insert into public.support_access_codes (salon_id, code_hash, expires_at)
    values ('00000000-0000-0000-0000-000000000002', 'hash-inyectado', now() + interval '15 minutes');
    v_paso := true;
  exception
    when insufficient_privilege then
      null;  -- esperado: el with-check de support_codes_admin_insert_own
  end;

  if v_paso then
    raise exception
      'RLS FAILURE: el salón A creó un código bajo el salón B (el insert no lanzó 42501)';
  end if;
end $$;

-- ---------------------------------------------------------------------------
-- 4. support_access_codes: nada de UPDATE ni DELETE. Es la propiedad central.
--
--    Con el revoke de la 0021 esto lanza 42501 antes de mirar ninguna fila. Si
--    NO lanza, el mensaje distingue los dos grados del fallo: 0 filas = solo se
--    cayó la barrera de grants y aguanta la RLS; >0 filas = no queda nada.
-- ---------------------------------------------------------------------------
do $$
declare
  v_rows int := -1;
begin
  begin
    update public.support_access_codes
       set revoked_at = null, used_at = null, expires_at = now() + interval '10 years';
    get diagnostics v_rows = row_count;
  exception
    when insufficient_privilege then
      null;  -- esperado: revoke update on support_access_codes
  end;

  if v_rows >= 0 then
    raise exception
      'BARRERA CAÍDA: el UPDATE del salón sobre los códigos no lanzó 42501 y afectó a % fila(s). ¿Se deshizo el revoke de la 0021?',
      v_rows;
  end if;
end $$;

do $$
declare
  v_rows int := -1;
begin
  begin
    delete from public.support_access_codes
     where salon_id = '00000000-0000-0000-0000-000000000001';
    get diagnostics v_rows = row_count;
  exception
    when insufficient_privilege then
      null;  -- esperado: revoke delete on support_access_codes
  end;

  if v_rows >= 0 then
    raise exception
      'BARRERA CAÍDA: el DELETE del salón sobre los códigos no lanzó 42501 y afectó a % fila(s).',
      v_rows;
  end if;
end $$;

-- ---------------------------------------------------------------------------
-- 5. impersonation_sessions: SELECT sí (hace falta para pintar "sesión de
--    soporte activa" en /soporte). INSERT y UPDATE no: el salón no abre ni
--    cierra sesiones, eso es siempre del panel/backend con service role.
-- ---------------------------------------------------------------------------
do $$
declare
  v_count int;
begin
  select count(*) into v_count from public.impersonation_sessions
   where salon_id = '00000000-0000-0000-0000-000000000001';
  if v_count <> 1 then
    raise exception 'RLS FAILURE: el admin del salón no ve su sesión de soporte (% filas)', v_count;
  end if;
end $$;

do $$
declare
  v_rows int := -1;
begin
  begin
    update public.impersonation_sessions
       set ended_at = null, ended_reason = 'nunca pasó nada';
    get diagnostics v_rows = row_count;
  exception
    when insufficient_privilege then
      null;  -- esperado: revoke update on impersonation_sessions
  end;

  if v_rows >= 0 then
    raise exception
      'BARRERA CAÍDA: el UPDATE del salón sobre las sesiones no lanzó 42501 y afectó a % fila(s). Podría reescribir el ended_reason.',
      v_rows;
  end if;
end $$;

do $$
declare
  v_paso boolean := false;
begin
  begin
    insert into public.impersonation_sessions
      (admin_id, admin_email, salon_id, code_id, tenant_auth_user_id, tenant_email, expires_at)
    values ('11111111-1111-1111-1111-111111111111', 'owner@estetixapp.test',
            '00000000-0000-0000-0000-000000000001', 'cccccccc-0000-0000-0000-000000000001',
            '10000000-0000-0000-0000-000000000001', 'duena@salon.test',
            now() + interval '30 minutes');
    v_paso := true;
  exception
    when insufficient_privilege then
      null;  -- esperado: revoke insert on impersonation_sessions
  end;

  if v_paso then
    raise exception 'BARRERA CAÍDA: el salón se inventó una sesión de soporte que nadie autorizó';
  end if;
end $$;

-- ---------------------------------------------------------------------------
-- 6. Cerrar el candado no puede romper el producto: la revocación real, que va
--    con service role desde app/routers/soporte.py, sigue funcionando — código
--    revocado y sesión cerrada con su motivo, guarda optimista incluida. Esta
--    es la comprobación de que el revoke no se llevó por delante a service_role.
-- ---------------------------------------------------------------------------
reset role;
set local role service_role;

do $$
declare
  v_rows int;
begin
  update public.support_access_codes
     set revoked_at = now()
   where id = 'cccccccc-0000-0000-0000-000000000001'
     and salon_id = '00000000-0000-0000-0000-000000000001';
  get diagnostics v_rows = row_count;
  if v_rows <> 1 then
    raise exception 'REVOCACIÓN ROTA: el service role revocó % código(s), esperaba 1', v_rows;
  end if;

  update public.impersonation_sessions
     set ended_at = now(), ended_reason = 'revocada_por_salon'
   where id = '55555555-0000-0000-0000-000000000001'
     and salon_id = '00000000-0000-0000-0000-000000000001'
     and ended_at is null;
  get diagnostics v_rows = row_count;
  if v_rows <> 1 then
    raise exception 'REVOCACIÓN ROTA: el service role cerró % sesión(es), esperaba 1', v_rows;
  end if;

  if not exists (
    select 1 from public.impersonation_sessions
     where id = '55555555-0000-0000-0000-000000000001'
       and ended_reason = 'revocada_por_salon'
  ) then
    raise exception 'REVOCACIÓN ROTA: la sesión no quedó cerrada con ended_reason';
  end if;
end $$;

reset role;

do $$
begin
  raise notice 'SUPPORT ACCESS RLS TESTS: ALL PASSED';
end $$;

rollback;
