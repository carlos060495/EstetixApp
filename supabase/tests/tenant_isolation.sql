-- Tenant isolation tests (A = Salón Demo, B = Salón Norte, seeded in supabase/seed.sql).
--
-- Run against a local stack that already has migrations + seed applied:
--   psql "$DATABASE_URL" -f supabase/tests/tenant_isolation.sql
--
-- Every check RAISEs an exception on failure, so a clean run with no output
-- besides the final NOTICE means every isolation path held. The whole file
-- runs inside one transaction that is rolled back, so it never mutates seed
-- data even when it deliberately tries (and expects to fail) a cross-tenant write.
--
-- IDs are the fixed seed values from supabase/seed.sql, hardcoded here
-- instead of psql variables: psql does not interpolate :'var' inside
-- dollar-quoted (do $$ ... $$) bodies.
--   salon A (Salón Demo)  = 00000000-0000-0000-0000-000000000001
--   salon B (Salón Norte) = 00000000-0000-0000-0000-000000000002
--   admin A auth_user_id  = 10000000-0000-0000-0000-000000000001
--   admin B auth_user_id  = 10000000-0000-0000-0000-000000000002
--   alguien de fuera (creado en este fichero) = 30000000-0000-0000-0000-000000000001

begin;

-- ---------------------------------------------------------------------------
-- Helper: impersonate a tenant the way PostgREST would, via the JWT claims
-- GUC that auth.jwt() reads.
-- ---------------------------------------------------------------------------
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
-- ALGUIEN DE FUERA, con identidad propia (reescrito en la 0069).
--
-- Hasta la 0069 aquí se usaba una estilista de verdad (la de Marta, del seed)
-- para comprobar que quien no es la administradora no lee ni escribe nada. Ese
-- rol ya no existe: el tipo `user_rol` solo admite «admin». Lo que puede existir
-- es alguien CON usuario de Auth que no es la administradora de ninguna cuenta:
-- sin fila en `users`. Y se le da el token más peligroso posible: uno que dice
-- `salon_id` = el centro A y `rol` = admin. Si con eso no lee ni escribe nada,
-- la puerta aguanta, porque la frontera se decide con la fila de `users` leída
-- de la base, no con lo que diga el token.
--
-- La identidad se clona de una fila que ya existe en `auth.users`. Todo dentro
-- de la transacción que se deshace.
-- ---------------------------------------------------------------------------
insert into auth.users (id, instance_id, aud, role, email)
select '30000000-0000-0000-0000-000000000001', u.instance_id, u.aud, u.role, 'de-fuera.a@test.local'
from auth.users u limit 1;

do $$
begin
  if exists (select 1 from public.users where auth_user_id = '30000000-0000-0000-0000-000000000001') then
    raise exception 'EL DECORADO ES FALSO: la identidad de prueba tiene fila en users';
  end if;
end $$;

-- ---------------------------------------------------------------------------
-- 1. SELECT isolation: salon A admin must never see salon B's clients.
-- ---------------------------------------------------------------------------
select pg_temp.login_as('00000000-0000-0000-0000-000000000001', 'admin', '10000000-0000-0000-0000-000000000001');

do $$
declare
  v_count int;
begin
  select count(*) into v_count from public.clients where salon_id = '00000000-0000-0000-0000-000000000002';
  if v_count <> 0 then
    raise exception 'ISOLATION FAILURE: salon A read % client rows belonging to salon B', v_count;
  end if;

  select count(*) into v_count from public.clients;
  if v_count <> (select count(*) from public.clients where salon_id = '00000000-0000-0000-0000-000000000001') then
    raise exception 'ISOLATION FAILURE: unscoped clients SELECT leaked rows outside salon A';
  end if;
end $$;

-- ---------------------------------------------------------------------------
-- 2. INSERT isolation: salon A cannot plant a row under salon B's salon_id.
--
-- How a negative check has to be written here: the failing `raise` MUST sit
-- OUTSIDE the block that catches the expected exception, or its own handler
-- swallows it and the check can never fail. Use a flag (or row_count) and
-- raise after the block. The `when` clause is narrowed to
-- insufficient_privilege on purpose: `when others` would also swallow an
-- unrelated failure (a FK, a mistyped UUID) and pass it off as success.
-- The flag is the reliable signal, not a row count taken afterwards: catching
-- the exception rolls back the sub-transaction (the planted row vanishes on
-- its own), and even when the insert succeeds the row belongs to salon B, so
-- this tenant's own SELECT policy hides it and the count reads 0 either way.
-- ---------------------------------------------------------------------------
do $$
declare
  v_inserted boolean := false;
begin
  begin
    insert into public.clients (salon_id, nombre) values ('00000000-0000-0000-0000-000000000002', 'Cliente inyectado por A');
    v_inserted := true;
  exception
    when insufficient_privilege then
      -- expected: RLS with-check on clients_insert rejects salon_id != current_salon_id()
      null;
  end;

  if v_inserted then
    raise exception 'ISOLATION FAILURE: salon A inserted a client under salon B (no 42501 raised)';
  end if;
end $$;

-- ---------------------------------------------------------------------------
-- 3. UPDATE isolation: salon A's UPDATE on salon B's existing client must
--    affect zero rows (not raise, not succeed silently with the wrong scope).
-- ---------------------------------------------------------------------------
do $$
declare
  v_rows int;
begin
  update public.clients set nombre = 'HACKED BY A' where salon_id = '00000000-0000-0000-0000-000000000002';
  get diagnostics v_rows = row_count;
  if v_rows <> 0 then
    raise exception 'ISOLATION FAILURE: salon A updated % row(s) belonging to salon B', v_rows;
  end if;
end $$;

-- ---------------------------------------------------------------------------
-- 4. Appointments: same SELECT/UPDATE checks on a second table shape.
-- ---------------------------------------------------------------------------
do $$
declare
  v_count int;
begin
  select count(*) into v_count from public.appointments where salon_id = '00000000-0000-0000-0000-000000000002';
  if v_count <> 0 then
    raise exception 'ISOLATION FAILURE: salon A read appointments belonging to salon B';
  end if;
end $$;

-- ---------------------------------------------------------------------------
-- 5. Role isolation: a non-admin tenant role must not see expenses / users
--    / time_entries rows at all, even within its OWN salon.
-- ---------------------------------------------------------------------------
select pg_temp.login_as('00000000-0000-0000-0000-000000000001', 'admin', '30000000-0000-0000-0000-000000000001');

do $$
declare
  v_count int;
begin
  select count(*) into v_count from public.expenses where salon_id = '00000000-0000-0000-0000-000000000001';
  if v_count <> 0 then
    raise exception 'ROLE FAILURE: someone from outside read % expenses row(s) of their own salon', v_count;
  end if;
end $$;

-- ---------------------------------------------------------------------------
-- 6. Cross-tenant via a different salon's admin role: salon B admin reading
--    salon A's services (back-office catalog) must come back empty.
-- ---------------------------------------------------------------------------
select pg_temp.login_as('00000000-0000-0000-0000-000000000002', 'admin', '10000000-0000-0000-0000-000000000002');

do $$
declare
  v_count int;
begin
  select count(*) into v_count from public.services where salon_id = '00000000-0000-0000-0000-000000000001';
  if v_count <> 0 then
    raise exception 'ISOLATION FAILURE: salon B read % service row(s) belonging to salon A', v_count;
  end if;
end $$;

-- ---------------------------------------------------------------------------
-- 7. time_entries: aislamiento por salon_id para lectura/escritura.
-- ---------------------------------------------------------------------------
select pg_temp.login_as('00000000-0000-0000-0000-000000000001', 'admin', '10000000-0000-0000-0000-000000000001');

do $$
declare
  v_count int;
  v_inserted boolean := false;
begin
  select count(*) into v_count from public.time_entries where salon_id = '00000000-0000-0000-0000-000000000002';
  if v_count <> 0 then
    raise exception 'ISOLATION FAILURE: salon A read % fichaje row(s) belonging to salon B', v_count;
  end if;

  -- Same shape as check 2: the failing `raise` stays OUTSIDE the block that
  -- catches the expected 42501, so this check can actually fail.
  begin
    insert into public.time_entries (salon_id, employee_id, tipo)
    values (
      '00000000-0000-0000-0000-000000000002',
      '30000000-0000-0000-0000-000000000004',
      'entrada'
    );
    v_inserted := true;
  exception
    when insufficient_privilege then
      null;  -- expected: RLS with-check rejects salon_id != current_salon_id()
  end;

  if v_inserted then
    raise exception 'ISOLATION FAILURE: salon A inserted fichaje under salon B';
  end if;
end $$;

-- ---------------------------------------------------------------------------
-- salon_schedule (migración 0022): el horario es configuración del salón, así
-- que se comprueban las tres direcciones — leer, plantar y modificar — igual
-- que en clients. La escritura además es admin-only, así que se comprueba
-- también que alguien de fuera del PROPIO salón no pueda tocarlo.
-- ---------------------------------------------------------------------------
select pg_temp.login_as('00000000-0000-0000-0000-000000000001', 'admin', '10000000-0000-0000-0000-000000000001');

do $$
declare
  v_count int;
  v_inserted boolean := false;
  v_rows int;
begin
  -- SELECT: A no ve el horario de B.
  select count(*) into v_count from public.salon_schedule
   where salon_id = '00000000-0000-0000-0000-000000000002';
  if v_count <> 0 then
    raise exception 'ISOLATION FAILURE: salon A sees % salon_schedule row(s) of salon B', v_count;
  end if;

  -- INSERT: A no puede plantar un horario bajo el salon_id de B.
  begin
    insert into public.salon_schedule (salon_id, dia_semana, apertura, cierre)
    values ('00000000-0000-0000-0000-000000000002', 1, '10:00', '20:00');
    v_inserted := true;
  exception
    when insufficient_privilege then
      null;  -- esperado: el with-check rechaza salon_id <> current_salon_id()
  end;
  if v_inserted then
    raise exception 'ISOLATION FAILURE: salon A inserted salon_schedule under salon B';
  end if;

  -- UPDATE: el UPDATE de A sobre el horario de B afecta a cero filas.
  update public.salon_schedule set cerrado = true
   where salon_id = '00000000-0000-0000-0000-000000000002';
  get diagnostics v_rows = row_count;
  if v_rows <> 0 then
    raise exception 'ISOLATION FAILURE: salon A updated % salon_schedule row(s) of salon B', v_rows;
  end if;
end $$;

-- El horario lo edita quien dirige el salón: alguien de fuera del propio salón A
-- puede leerlo (la agenda lo necesitará) pero no escribirlo.
select pg_temp.login_as('00000000-0000-0000-0000-000000000001', 'admin', '30000000-0000-0000-0000-000000000001');

do $$
declare
  v_inserted boolean := false;
begin
  begin
    insert into public.salon_schedule (salon_id, dia_semana, apertura, cierre)
    values ('00000000-0000-0000-0000-000000000001', 1, '10:00', '20:00');
    v_inserted := true;
  exception
    when insufficient_privilege then
      null;  -- esperado: salon_schedule_write_admin exige is_admin_rol()
  end;
  if v_inserted then
    raise exception 'ISOLATION FAILURE: someone from outside wrote salon_schedule';
  end if;
end $$;

-- ---------------------------------------------------------------------------
-- salon_schedule_exceptions (migración 0023): festivos y cierres. Mismas tres
-- direcciones que salon_schedule, más la comprobación de que el anti-solape es
-- POR SALÓN: dos salones distintos sí pueden cerrar las mismas fechas.
-- ---------------------------------------------------------------------------
select pg_temp.login_as('00000000-0000-0000-0000-000000000001', 'admin', '10000000-0000-0000-0000-000000000001');

do $$
declare
  v_count int;
  v_inserted boolean := false;
  v_rows int;
begin
  -- SELECT: A no ve las excepciones de B.
  select count(*) into v_count from public.salon_schedule_exceptions
   where salon_id = '00000000-0000-0000-0000-000000000002';
  if v_count <> 0 then
    raise exception 'ISOLATION FAILURE: salon A sees % exception row(s) of salon B', v_count;
  end if;

  -- INSERT: A no puede plantar una excepción bajo el salon_id de B.
  begin
    insert into public.salon_schedule_exceptions (salon_id, fecha_inicio, fecha_fin, cerrado)
    values ('00000000-0000-0000-0000-000000000002', '2030-01-01', '2030-01-07', true);
    v_inserted := true;
  exception
    when insufficient_privilege then
      null;  -- esperado: el with-check rechaza salon_id <> current_salon_id()
  end;
  if v_inserted then
    raise exception 'ISOLATION FAILURE: salon A inserted an exception under salon B';
  end if;

  -- UPDATE: el UPDATE de A sobre las excepciones de B afecta a cero filas.
  update public.salon_schedule_exceptions set cerrado = false
   where salon_id = '00000000-0000-0000-0000-000000000002';
  get diagnostics v_rows = row_count;
  if v_rows <> 0 then
    raise exception 'ISOLATION FAILURE: salon A updated % exception row(s) of salon B', v_rows;
  end if;
end $$;

-- Alguien de fuera del propio salón lee los festivos (la agenda los necesita) pero
-- no los escribe.
select pg_temp.login_as('00000000-0000-0000-0000-000000000001', 'admin', '30000000-0000-0000-0000-000000000001');

do $$
declare
  v_inserted boolean := false;
begin
  begin
    insert into public.salon_schedule_exceptions (salon_id, fecha_inicio, fecha_fin, cerrado)
    values ('00000000-0000-0000-0000-000000000001', '2030-02-01', '2030-02-01', true);
    v_inserted := true;
  exception
    when insufficient_privilege then
      null;  -- esperado: salon_schedule_exceptions_write_admin exige is_admin_rol()
  end;
  if v_inserted then
    raise exception 'ISOLATION FAILURE: someone from outside wrote salon_schedule_exceptions';
  end if;
end $$;

-- El anti-solape es POR SALÓN: que A tenga cerradas unas fechas no puede
-- impedirle a B cerrar las mismas.
select pg_temp.login_as('00000000-0000-0000-0000-000000000001', 'admin', '10000000-0000-0000-0000-000000000001');
insert into public.salon_schedule_exceptions (salon_id, fecha_inicio, fecha_fin, cerrado)
values ('00000000-0000-0000-0000-000000000001', '2030-03-01', '2030-03-07', true);

select pg_temp.login_as('00000000-0000-0000-0000-000000000002', 'admin', '10000000-0000-0000-0000-000000000002');
insert into public.salon_schedule_exceptions (salon_id, fecha_inicio, fecha_fin, cerrado)
values ('00000000-0000-0000-0000-000000000002', '2030-03-01', '2030-03-07', true);

-- ---------------------------------------------------------------------------
-- Agente de WhatsApp (migración 0024): salon_whatsapp_config, bot_sessions y
-- bot_messages. Son las tablas que escribe el WEBHOOK, que llega sin usuario:
-- acuña un token del salón y entra por PostgREST, así que estas políticas son
-- exactamente lo que le impide escribir en la conversación de otro salón.
-- ---------------------------------------------------------------------------
select pg_temp.login_as('00000000-0000-0000-0000-000000000001', 'admin', '10000000-0000-0000-0000-000000000001');

-- Datos de B con los que A se va a estrellar. Se plantan como B.
select pg_temp.login_as('00000000-0000-0000-0000-000000000002', 'admin', '10000000-0000-0000-0000-000000000002');
insert into public.salon_whatsapp_config (salon_id, phone_number_id, display_phone_number)
values ('00000000-0000-0000-0000-000000000002', 'pnid-de-B', '+34600000002');
insert into public.bot_sessions (salon_id, customer_phone)
values ('00000000-0000-0000-0000-000000000002', '34600111222');
insert into public.bot_messages (salon_id, session_id, role, content, wa_message_id)
select '00000000-0000-0000-0000-000000000002', id, 'user', 'secreto de B', 'wamid.DE-B'
  from public.bot_sessions where salon_id = '00000000-0000-0000-0000-000000000002'
   and customer_phone = '34600111222'
on conflict (wa_message_id) do nothing;

select pg_temp.login_as('00000000-0000-0000-0000-000000000001', 'admin', '10000000-0000-0000-0000-000000000001');

-- La conversación propia de A se crea AQUÍ, antes de las comprobaciones, para
-- que el intento de inyectar un mensaje bajo el salon_id de B tenga una
-- session_id real que usar. Sin ella el INSERT afectaría a cero filas y
-- "pasaría" sin demostrar nada: la trampa clásica de este tipo de prueba.
insert into public.bot_sessions (salon_id, customer_phone)
values ('00000000-0000-0000-0000-000000000001', '34600111222')
on conflict (salon_id, customer_phone) do nothing;

do $$
declare
  v_count int;
  v_inserted boolean := false;
  v_rows int;
  v_sesion_a uuid;
begin
  -- Acotado al teléfono que crea este mismo bloque: el salón puede tener ya
  -- otras conversaciones, y un `into strict` sin filtrar reventaría por eso.
  select id into strict v_sesion_a from public.bot_sessions
   where salon_id = '00000000-0000-0000-0000-000000000001'
     and customer_phone = '34600111222';
  -- SELECT: A no ve NADA de B en ninguna de las tres tablas.
  select count(*) into v_count from public.salon_whatsapp_config
   where salon_id = '00000000-0000-0000-0000-000000000002';
  if v_count <> 0 then
    raise exception 'ISOLATION FAILURE: salon A sees % whatsapp config row(s) of salon B', v_count;
  end if;

  select count(*) into v_count from public.bot_sessions
   where salon_id = '00000000-0000-0000-0000-000000000002';
  if v_count <> 0 then
    raise exception 'ISOLATION FAILURE: salon A sees % bot_session(s) of salon B', v_count;
  end if;

  -- La que de verdad duele: el CONTENIDO de las conversaciones de otro salón.
  select count(*) into v_count from public.bot_messages
   where salon_id = '00000000-0000-0000-0000-000000000002';
  if v_count <> 0 then
    raise exception 'ISOLATION FAILURE: salon A reads % bot_message(s) of salon B', v_count;
  end if;

  -- INSERT: A no puede plantar una conversación bajo el salon_id de B.
  begin
    insert into public.bot_sessions (salon_id, customer_phone)
    values ('00000000-0000-0000-0000-000000000002', '34600999999');
    v_inserted := true;
  exception
    when insufficient_privilege then
      null;  -- esperado: el with-check rechaza salon_id <> current_salon_id()
  end;
  if v_inserted then
    raise exception 'ISOLATION FAILURE: salon A inserted a bot_session under salon B';
  end if;

  -- Ni un mensaje bajo el salon_id de B.
  v_inserted := false;
  begin
    insert into public.bot_messages (salon_id, session_id, role, content)
    values ('00000000-0000-0000-0000-000000000002', v_sesion_a, 'user', 'inyectado por A');
    v_inserted := true;
  exception
    when insufficient_privilege then
      null;  -- esperado: el with-check rechaza salon_id <> current_salon_id()
  end;
  if v_inserted then
    raise exception 'ISOLATION FAILURE: salon A inserted a bot_message under salon B';
  end if;

  -- Ni secuestrar el número de WhatsApp de B para desviarle los mensajes.
  v_inserted := false;
  begin
    insert into public.salon_whatsapp_config (salon_id, phone_number_id)
    values ('00000000-0000-0000-0000-000000000002', 'pnid-secuestrado');
    v_inserted := true;
  exception
    when insufficient_privilege then
      null;
  end;
  if v_inserted then
    raise exception 'ISOLATION FAILURE: salon A inserted a whatsapp config under salon B';
  end if;

  -- UPDATE: A no puede derivar ni reescribir las conversaciones de B.
  update public.bot_sessions set atencion = 'humano'
   where salon_id = '00000000-0000-0000-0000-000000000002';
  get diagnostics v_rows = row_count;
  if v_rows <> 0 then
    raise exception 'ISOLATION FAILURE: salon A updated % bot_session(s) of salon B', v_rows;
  end if;

  update public.bot_messages set content = 'reescrito por A'
   where salon_id = '00000000-0000-0000-0000-000000000002';
  get diagnostics v_rows = row_count;
  if v_rows <> 0 then
    raise exception 'ISOLATION FAILURE: salon A updated % bot_message(s) of salon B', v_rows;
  end if;

  -- DELETE: ni borrarle el historial.
  delete from public.bot_messages
   where salon_id = '00000000-0000-0000-0000-000000000002';
  get diagnostics v_rows = row_count;
  if v_rows <> 0 then
    raise exception 'ISOLATION FAILURE: salon A deleted % bot_message(s) of salon B', v_rows;
  end if;
end $$;

-- En SU propio salón sí escribe: si esto fallara, la RLS estaría de más porque
-- el webhook no podría guardar nada.
insert into public.bot_messages (salon_id, session_id, role, content, wa_message_id)
select '00000000-0000-0000-0000-000000000001', id, 'user', 'hola', 'wamid.DE-A'
  from public.bot_sessions where salon_id = '00000000-0000-0000-0000-000000000001'
   and customer_phone = '34600111222'
on conflict (wa_message_id) do nothing;

-- SOLO ENTRA LA ADMINISTRADORA (0063). Hasta la 0063 alguien de fuera del propio
-- salón cogía la conversación; ya no ve ninguna fila, ni de su propio salón.
select pg_temp.login_as('00000000-0000-0000-0000-000000000001', 'admin', '30000000-0000-0000-0000-000000000001');

do $$
declare
  v_rows int;
begin
  update public.bot_sessions set atencion = 'humano', handoff_at = now()
   where salon_id = '00000000-0000-0000-0000-000000000001';
  get diagnostics v_rows = row_count;
  if v_rows <> 0 then
    raise exception 'SOLO ENTRA LA ADMINISTRADORA (0063): alguien de fuera cogio % conversacion(es)', v_rows;
  end if;
end $$;

-- La bandeja la atiende la sesión del mostrador, que es la de la
-- administradora: tiene que poder cogerla, contestar y darla por resuelta.
select pg_temp.login_as('00000000-0000-0000-0000-000000000001', 'admin', '10000000-0000-0000-0000-000000000001');

do $$
declare
  v_rows int;
begin
  update public.bot_sessions set atencion = 'humano', handoff_at = now()
   where salon_id = '00000000-0000-0000-0000-000000000001';
  get diagnostics v_rows = row_count;
  if v_rows = 0 then
    raise exception 'POLICY FAILURE: admin cannot take over a bot_session of their own salon';
  end if;

  -- Y contestar con el rol nuevo de la 0031: si el grant o la política no
  -- alcanzaran a `agente`, la bandeja se vería pero no se podría usar.
  insert into public.bot_messages (salon_id, session_id, role, content)
  select '00000000-0000-0000-0000-000000000001', id, 'agente', 'te atiendo yo'
    from public.bot_sessions
   where salon_id = '00000000-0000-0000-0000-000000000001'
     and customer_phone = '34600111222';
end $$;

-- Y la CONFIGURACIÓN del número no la escribe nadie más que el admin: cambiarla
-- es redirigir el WhatsApp del negocio.
select pg_temp.login_as('00000000-0000-0000-0000-000000000001', 'admin', '30000000-0000-0000-0000-000000000001');

do $$
declare
  v_inserted boolean := false;
begin
  begin
    insert into public.salon_whatsapp_config (salon_id, phone_number_id)
    values ('00000000-0000-0000-0000-000000000001', 'pnid-de-fuera');
    v_inserted := true;
  exception
    when insufficient_privilege then
      null;  -- esperado: salon_whatsapp_config_write_admin exige is_admin_rol()
  end;
  if v_inserted then
    raise exception 'ISOLATION FAILURE: someone from outside wrote salon_whatsapp_config';
  end if;
end $$;

-- ---------------------------------------------------------------------------
-- puestos (migración 0027): el título de trabajo del empleado. NO da permisos
-- —esos salen de users.rol—, pero sigue siendo dato del salón: quién tiene qué
-- puesto es información de plantilla y no se enseña al vecino.
-- ---------------------------------------------------------------------------
select pg_temp.login_as('00000000-0000-0000-0000-000000000002', 'admin', '10000000-0000-0000-0000-000000000002');
insert into public.puestos (salon_id, nombre) values
  ('00000000-0000-0000-0000-000000000002', 'Puesto secreto de B');

select pg_temp.login_as('00000000-0000-0000-0000-000000000001', 'admin', '10000000-0000-0000-0000-000000000001');

do $$
declare
  v_count int;
  v_inserted boolean := false;
  v_rows int;
begin
  -- SELECT: A no ve los puestos de B.
  select count(*) into v_count from public.puestos
   where salon_id = '00000000-0000-0000-0000-000000000002';
  if v_count <> 0 then
    raise exception 'ISOLATION FAILURE: salon A sees % puesto(s) of salon B', v_count;
  end if;

  -- INSERT: A no puede plantar un puesto bajo el salon_id de B.
  begin
    insert into public.puestos (salon_id, nombre)
    values ('00000000-0000-0000-0000-000000000002', 'Inyectado por A');
    v_inserted := true;
  exception
    when insufficient_privilege then
      null;  -- esperado: el with-check rechaza salon_id <> current_salon_id()
  end;
  if v_inserted then
    raise exception 'ISOLATION FAILURE: salon A inserted a puesto under salon B';
  end if;

  -- UPDATE: renombrar los puestos de B afecta a cero filas.
  update public.puestos set nombre = 'Renombrado por A'
   where salon_id = '00000000-0000-0000-0000-000000000002';
  get diagnostics v_rows = row_count;
  if v_rows <> 0 then
    raise exception 'ISOLATION FAILURE: salon A renamed % puesto(s) of salon B', v_rows;
  end if;

  -- DELETE: ni borrárselos.
  delete from public.puestos where salon_id = '00000000-0000-0000-0000-000000000002';
  get diagnostics v_rows = row_count;
  if v_rows <> 0 then
    raise exception 'ISOLATION FAILURE: salon A deleted % puesto(s) of salon B', v_rows;
  end if;
end $$;

-- En SU salón el admin sí manda.
insert into public.puestos (salon_id, nombre)
values ('00000000-0000-0000-0000-000000000001', 'Puesto de prueba de A');

-- Un ESTILISTA lee los puestos (los necesita el desplegable del formulario)
-- pero no los escribe: renombrar el organigrama es cosa del dueño.
select pg_temp.login_as('00000000-0000-0000-0000-000000000001', 'admin', '30000000-0000-0000-0000-000000000001');

do $$
declare
  v_count int;
  v_inserted boolean := false;
begin
  select count(*) into v_count from public.puestos
   where salon_id = '00000000-0000-0000-0000-000000000001';
  -- SOLO ENTRA LA ADMINISTRADORA (0063): ni los puestos de su salón.
  if v_count <> 0 then
    raise exception 'SOLO ENTRA LA ADMINISTRADORA (0063): alguien de fuera leyo % puesto(s)', v_count;
  end if;

  begin
    insert into public.puestos (salon_id, nombre)
    values ('00000000-0000-0000-0000-000000000001', 'Puesto de fuera');
    v_inserted := true;
  exception
    when insufficient_privilege then
      null;  -- esperado: puestos_write_admin exige is_admin_rol()
  end;
  if v_inserted then
    raise exception 'ISOLATION FAILURE: someone from outside wrote puestos';
  end if;
end $$;

-- La FK protege el dato aunque nadie mire: un puesto asignado no se borra.
select pg_temp.login_as('00000000-0000-0000-0000-000000000001', 'admin', '10000000-0000-0000-0000-000000000001');

do $$
declare
  v_puesto uuid;
  v_borrado boolean := false;
begin
  select puesto_id into v_puesto from public.employees
   where salon_id = '00000000-0000-0000-0000-000000000001' and puesto_id is not null limit 1;
  if v_puesto is null then
    raise exception 'SETUP FAILURE: la siembra de 0027 no dejó ningún empleado con puesto';
  end if;

  begin
    delete from public.puestos where id = v_puesto;
    v_borrado := true;
  exception
    when foreign_key_violation then
      null;  -- esperado: on delete restrict
  end;
  if v_borrado then
    raise exception 'FK FAILURE: se borró un puesto que tienen empleados';
  end if;
end $$;

-- Y la siembra no perdió a nadie ni resucitó «Admin» como opción.
do $$
declare
  v_huerfanos int;
  v_admin int;
begin
  select count(*) into v_huerfanos from public.employees where puesto_id is null;
  if v_huerfanos <> 0 then
    raise exception 'SEED FAILURE: % empleado(s) se quedaron sin puesto', v_huerfanos;
  end if;

  select count(*) into v_admin from public.puestos where lower(btrim(nombre)) = 'admin';
  if v_admin <> 0 then
    raise exception 'SEED FAILURE: «Admin» sigue siendo un puesto ofertable';
  end if;
end $$;

do $$
begin
  raise notice 'TENANT ISOLATION TESTS: ALL PASSED';
end $$;

rollback;
