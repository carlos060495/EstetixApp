-- 0086 · Que no se pueda usar sin pagar. LA FRONTERA ES LA BASE.
--
-- Carlos, 13-10-2026.
--
-- ═══ EL AGUJERO ═══
--
-- El muro de «tu suscripción ha terminado» solo existía en la pantalla, y la
-- suspensión solo en el backend. Medido contra el Supabase local el 13-10-2026
-- con la sesión real de una titular, pidiendo a la base directamente
-- (`/rest/v1/clients`, con la clave pública que lleva la web):
--
--   · cuenta SUSPENDIDA      → el backend, 403; la base, 200 con sus 11 clientas
--                              y 201 al escribir una nueva;
--   · suscripción CANCELADA  → 200 y 201 por las dos puertas;
--   · recién creada SIN TARJETA → 200 y 201 por las dos puertas.
--
-- Todas las políticas preguntaban a `salones_visibles()`, que solo mira QUIÉN
-- es (la administradora de la cuenta), no si la cuenta trabaja.
--
-- ═══ LO QUE CAMBIA ═══
--
--   · `cuenta_trabaja(cuenta)`: EL predicado, escrito una vez. Trabaja la cuenta
--     que no está suspendida y tiene una suscripción viva —`active`,
--     `trialing` o `past_due` (Stripe reintentando: el periodo de gracia NO
--     corta)— o es una demo sin caducar. Todo lo demás está cortado: cancelada,
--     impagada, nunca activada (`incomplete`, `incomplete_expired` o sin fila).
--     Su espejo en Python es `billing/estado_suscripcion.cuenta_trabaja`, y un
--     guarda los compara estado por estado contra esta función.
--   · `centros_que_trabajan()`: los mismos centros que `salones_visibles()`, si
--     la cuenta trabaja. Si NO trabaja, LANZA 42501 `cuenta_cortada` —PostgREST
--     contesta 403—: un corte que devuelve una lista vacía con un 200 se parece
--     demasiado a «no tienes clientas». `salones_visibles()` NO SE TOCA: las
--     pruebas de aislamiento siguen preguntando lo mismo y pasan sin cambios.
--   · TODAS las políticas que preguntaban a `salones_visibles()` pasan a
--     `centros_que_trabajan()`, generadas del catálogo (`pg_policies`), no a
--     mano. Menos dos clases, cada una con su comentario en la política:
--       - LECTURA DE LA AGENDA: el SELECT de lo que pinta la agenda (citas,
--         fichas de empleada con su puesto y sus horarios, servicios, el horario
--         del centro, el propio centro y las fichas de `users` de la cuenta). Una cuenta cortada VE su
--         agenda entera; no la toca.
--       - SALIDA: lo que no es usar la aplicación sino poder salir de ella o
--         pedir ayuda (ver quién de soporte ha entrado, darle un código).
--   · SIN POLÍTICAS `ALL`: se parten en INSERT, UPDATE y DELETE (y SELECT si la
--     tabla no tenía otro). Una `ALL` también vale para leer, y así una lectura
--     de la agenda acabaría preguntando a la función que corta.
--   · LAS CLIENTAS: su ficha (teléfono, correo, notas, historial) queda cortada.
--     Lo que protege a quien se sienta en la silla —el NOMBRE y las ALERGIAS—
--     sigue a la vista por `clientas_de_la_agenda()`, y solo de las clientas
--     que tienen alguna cita. La regla (Carlos): lo que protege a la clienta se
--     ve; lo que sirve para seguir facturando, no.
--   · EL FICHAJE se escribe con service_role (no tiene política de escritura),
--     así que la RLS no lo alcanza: lo corta un trigger, que vale para todos.
--   · SOPORTE SÍ ENTRA en una cuenta cortada o suspendida, como hasta ahora
--     (`test_soporte_si_entra_en_un_salon_suspendido`). Su sesión es una sesión
--     real de la titular; la base la reconoce por su `session_id`, que el
--     backend apunta en `impersonation_sessions.auth_session_id` al acuñarla.
--     La titular no puede escribir en esa tabla.
--   · EL RELOJ DE LOS 90 DÍAS empieza el día que deja de pagar, por los tres
--     caminos: el primer cobro fallido (`subscriptions.primer_impago_at`, nueva),
--     el día que se acaba lo pagado y el de la baja (`terminada_at`, de antes).
--     Con una suscripción `active` o `trialing` no hay reloj, aunque quede una
--     fecha vieja: el webhook las borra al volver, y esto no se fía de que lo
--     haya hecho.
--   · `subscriptions.proximo_intento_at`: cuándo vuelve a intentar cobrar
--     Stripe, tal cual lo dice Stripe. Es lo que enseña la barra del periodo de
--     gracia. NULL con la cuenta en `past_due` = ya no quedan intentos.
--
-- ═══ ORDEN DE DESPLIEGUE: 0086 → BACKEND + FRONTEND (a la vez) ═══
--
-- Con la 0086 y el backend viejo: soporte deja de ver los datos de una cuenta
-- cortada (el backend viejo no apunta `auth_session_id`) y los recordatorios
-- de una cuenta cortada fallan (el backend viejo los lee con la sesión de la
-- titular). Una cuenta que paga no nota nada. Por eso van seguidos.

begin;

-- ═══════════════ 1. las columnas

alter table public.subscriptions
  add column primer_impago_at timestamptz,
  add column proximo_intento_at timestamptz;

comment on column public.subscriptions.primer_impago_at is
  'El día del primer cobro fallido (0086). Arranca el reloj de los 90 días. Lo pone el webhook al '
  'entrar en past_due/unpaid y lo borra al volver a active/trialing.';
comment on column public.subscriptions.proximo_intento_at is
  'Cuándo vuelve a intentar cobrar Stripe (`next_payment_attempt`, 0086). NULL en past_due = no quedan '
  'intentos. Lo escribe el webhook de invoice.payment_failed.';

alter table public.impersonation_sessions add column auth_session_id uuid;

comment on column public.impersonation_sessions.auth_session_id is
  'El `session_id` de GoTrue del token que acuñó soporte (0086). Con él la base reconoce a soporte '
  'dentro de una cuenta cortada o suspendida.';

-- ═══════════════ 2. el predicado

create function public.cuenta_trabaja(p_cuenta uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1 from public.accounts a
    where a.id = p_cuenta
      and not a.suspendido
      and (
        (a.es_demo and exists (select 1 from public.demos d where d.account_id = a.id and d.caduca_at > now()))
        or exists (
          select 1 from public.subscriptions s
          where s.account_id = a.id and s.estado in ('active', 'trialing', 'past_due')
        )
      )
  );
$$;

comment on function public.cuenta_trabaja(uuid) is
  'EL predicado del corte (0086): sin suspender y con suscripción viva (active, trialing, past_due) o '
  'demo sin caducar. Espejo: billing/estado_suscripcion.cuenta_trabaja; el guarda los compara.';

revoke all on function public.cuenta_trabaja(uuid) from public, anon, authenticated;
grant execute on function public.cuenta_trabaja(uuid) to service_role;

create function public.centros_que_trabajan()
returns uuid[]
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_visibles uuid[] := public.salones_visibles();
  v_cuenta uuid;
begin
  if cardinality(v_visibles) = 0 then
    return v_visibles;
  end if;
  -- SOPORTE: su sesión es de la titular, y se la reconoce por el session_id
  -- que el backend apuntó al acuñarla. Mientras la sesión de soporte siga viva.
  if exists (
    select 1 from public.impersonation_sessions i
    where i.auth_session_id = nullif(auth.jwt() ->> 'session_id', '')::uuid
      and i.ended_at is null and i.expires_at > now()
      and i.salon_id = any (v_visibles)
  ) then
    return v_visibles;
  end if;
  -- Todos los centros visibles son de la misma cuenta: la de su administradora.
  select s.account_id into v_cuenta from public.salons s where s.id = v_visibles[1];
  if not public.cuenta_trabaja(v_cuenta) then
    raise exception 'cuenta_cortada'
      using errcode = '42501',
            hint = 'La cuenta no tiene una suscripción viva o está suspendida: se puede ver la agenda, '
                   'reactivar el plan, darse de baja y descargar los datos.';
  end if;
  return v_visibles;
end;
$$;

comment on function public.centros_que_trabajan() is
  'Los centros de salones_visibles() si la cuenta trabaja; si no, 42501 cuenta_cortada (0086). '
  'La pregunta de TODAS las políticas salvo la lectura de la agenda y las salidas.';

revoke all on function public.centros_que_trabajan() from public;
grant execute on function public.centros_que_trabajan() to anon, authenticated, service_role;

-- ═══════════════ 3. las políticas, del catálogo

do $$
declare
  p record;
  v_viejo constant text := 'salones_visibles()';
  v_nuevo constant text := '(( SELECT public.centros_que_trabajan() AS centros_que_trabajan))::uuid[]';
  -- Lo que pinta la agenda. Una cuenta cortada la ve entera.
  v_agenda constant text[] := array[
    'appointments', 'employees', 'employee_schedule', 'employee_schedule_exceptions', 'employee_services',
    'services', 'service_categories', 'salon_schedule', 'salon_schedule_exceptions', 'salons', 'users',
    -- El cargo de cada ficha, que la agenda enseña con su nombre.
    'puestos'];
  -- Salir o pedir ayuda no es usar la aplicación.
  v_salidas constant text[] := array[
    'impersonation_sessions_select_own', 'support_codes_admin_select_own', 'support_codes_admin_insert_own'];
  q text;
  c text;
begin
  for p in
    select * from pg_policies
    where schemaname = 'public'
      and (coalesce(qual, '') || coalesce(with_check, '')) like '%' || v_viejo || '%'
    order by tablename, policyname
  loop
    if p.policyname = any (v_salidas) then
      execute format('comment on policy %I on public.%I is %L', p.policyname, p.tablename,
        'SALIDA (0086): salir de la aplicación o pedir ayuda a soporte no es usarla.');
      continue;
    end if;
    if p.cmd = 'SELECT' and p.tablename = any (v_agenda) then
      execute format('comment on policy %I on public.%I is %L', p.policyname, p.tablename,
        'LECTURA DE LA AGENDA (0086): una cuenta cortada ve su agenda entera; no la toca.');
      continue;
    end if;

    q := replace(p.qual, v_viejo, v_nuevo);
    c := replace(p.with_check, v_viejo, v_nuevo);

    if p.cmd = 'ALL' then
      execute format('create policy %I on public.%I for insert with check (%s)',
                     p.policyname || '_insert', p.tablename, coalesce(c, q));
      execute format('create policy %I on public.%I for update using (%s) with check (%s)',
                     p.policyname || '_update', p.tablename, q, coalesce(c, q));
      execute format('create policy %I on public.%I for delete using (%s)',
                     p.policyname || '_delete', p.tablename, q);
      if p.tablename = any (v_agenda) then
        -- LA LECTURA QUE YA DABA, TAL CUAL: en una tabla de la agenda, la ALL
        -- también dejaba leer, y partirla no puede quitarle a nadie una lectura
        -- que tenía (la administradora leía todas las fichas de `users` de su
        -- cuenta, no solo la suya).
        execute format('create policy %I on public.%I for select using (%s)',
                       p.policyname || '_select', p.tablename, p.qual);
        execute format('comment on policy %I on public.%I is %L', p.policyname || '_select', p.tablename,
          'LECTURA DE LA AGENDA (0086): una cuenta cortada ve su agenda entera; no la toca.');
      elsif not exists (
        select 1 from pg_policies x
        where x.schemaname = 'public' and x.tablename = p.tablename and x.cmd = 'SELECT'
      ) then
        execute format('create policy %I on public.%I for select using (%s)',
                       p.policyname || '_select', p.tablename, q);
      end if;
      execute format('drop policy %I on public.%I', p.policyname, p.tablename);
    elsif p.cmd = 'INSERT' then
      execute format('alter policy %I on public.%I with check (%s)', p.policyname, p.tablename, c);
    elsif p.cmd = 'UPDATE' then
      execute format('alter policy %I on public.%I using (%s) with check (%s)',
                     p.policyname, p.tablename, q, coalesce(c, q));
    else
      execute format('alter policy %I on public.%I using (%s)', p.policyname, p.tablename, q);
    end if;
  end loop;
end $$;

-- La cuenta misma: los ajustes de la cuenta tampoco se tocan cortada.
alter policy accounts_update_admin on public.accounts
  using ((id = public.cuenta_actual()) and public.is_admin_rol()
         and cardinality(( SELECT public.centros_que_trabajan() AS centros_que_trabajan)) > 0)
  with check ((id = public.cuenta_actual()) and public.is_admin_rol()
              and cardinality(( SELECT public.centros_que_trabajan() AS centros_que_trabajan)) > 0);

comment on policy accounts_select_own on public.accounts is
  'SALIDA (0086): una cuenta cortada tiene que poder saber de quién es y reactivarse.';
comment on policy subscriptions_select_admin on public.subscriptions is
  'SALIDA (0086): una cuenta cortada tiene que poder ver su suscripción para reactivarla o irse.';

-- ═══════════════ 4. las clientas de la agenda: lo que protege, nada más

create function public.clientas_de_la_agenda(p_ids uuid[])
returns table (id uuid, nombre text, alergias text)
language sql
stable
security definer
set search_path = ''
as $$
  -- NOMBRE Y ALERGIAS, y de nadie que no tenga una cita en la agenda. Ni el
  -- teléfono, ni el correo, ni las notas: eso es la ficha.
  select c.id, c.nombre, c.alergias
  from public.clients c
  where c.id = any (p_ids)
    and c.salon_id = any (public.salones_visibles())
    and exists (select 1 from public.appointments a where a.client_id = c.id)
  limit 1000;
$$;

comment on function public.clientas_de_la_agenda(uuid[]) is
  'LECTURA DE LA AGENDA (0086): nombre y alergias de las clientas con cita. Lo que protege a la '
  'clienta se ve aunque la cuenta esté cortada; lo que sirve para seguir facturando, no.';

revoke all on function public.clientas_de_la_agenda(uuid[]) from public, anon;
grant execute on function public.clientas_de_la_agenda(uuid[]) to authenticated, service_role;

-- ═══════════════ 5. el fichaje: lo escribe service_role, lo corta un trigger

create function public.el_fichaje_exige_cuenta_que_trabaja()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if not public.cuenta_trabaja((select s.account_id from public.salons s where s.id = new.salon_id)) then
    raise exception 'cuenta_cortada: con la cuenta cortada no se ficha' using errcode = '42501';
  end if;
  return new;
end;
$$;

create trigger el_fichaje_exige_cuenta_que_trabaja
  before insert or update on public.time_entries
  for each row execute function public.el_fichaje_exige_cuenta_que_trabaja();

-- ═══════════════ 6. el reloj de los 90 días: desde que deja de pagar

create or replace function public.relojes_del_plazo(p_ahora timestamptz default now(), p_cuenta uuid default null)
returns table (
  account_id uuid, origen text, terminada_at timestamptz, fila_id uuid,
  aviso_terminada boolean, aviso_30 boolean, aviso_7 boolean,
  zona_horaria text, terminada_el date, se_borran_el date, dias_restantes int, fuera_de_plazo boolean
)
language sql
stable
security definer
set search_path = ''
as $$
  with constantes as (
    select 90 as plazo_dias, p.dias_de_prueba from public.plataforma_config p
  ),
  relojes as (
    -- POR SUSCRIPCIÓN, desde el PRIMERO de los dos días (0086): el primer cobro
    -- fallido o el día que terminó (se acabó lo pagado, o la baja). `least`
    -- ignora el que falte. Con la suscripción viva de verdad no hay reloj,
    -- aunque quede una fecha vieja.
    select s.account_id,
           case when s.primer_impago_at is not null then 'impago' else 'suscripcion' end as origen,
           least(s.primer_impago_at, s.terminada_at) as terminada_at, s.id as fila_id,
           s.aviso_terminada_at is not null as aviso_terminada,
           s.aviso_30_at is not null as aviso_30,
           s.aviso_7_at is not null as aviso_7
    from public.subscriptions s
    where s.estado not in ('active', 'trialing')
      and (s.primer_impago_at is not null
           or (s.terminada_at is not null and s.estado in ('canceled', 'incomplete_expired')))
    union all
    -- Sin pago: ninguna suscripción, y la prueba ya habría acabado. Días de 24
    -- horas exactas, como el `timedelta` de antes: no días de calendario.
    select a.id, 'sin_pago', a.created_at + c.dias_de_prueba * interval '24 hours', a.id,
           a.aviso_plazo_terminada_at is not null,
           a.aviso_plazo_30_at is not null,
           a.aviso_plazo_7_at is not null
    from public.accounts a cross join constantes c
    where not exists (select 1 from public.subscriptions s where s.account_id = a.id)
      and a.created_at + c.dias_de_prueba * interval '24 hours' <= p_ahora
  ),
  con_zona as (
    -- La zona de la cuenta: la del centro que se queda al bajar de plan (el
    -- marcado; si no, el más antiguo), como `core/zona_de_la_cuenta`. Sin
    -- centros, la península.
    select r.*, coalesce((
      select z.zona_horaria from public.salons z
      where z.account_id = r.account_id
      order by z.sigue_al_bajar desc, z.created_at, z.id::text collate "C"
      limit 1
    ), 'Europe/Madrid') as zona_horaria
    from relojes r
    where (p_cuenta is null or r.account_id = p_cuenta)
      -- Una cuenta ya borrada no vuelve a entrar: ni se borra dos veces ni se le escribe.
      and not exists (select 1 from public.cuentas_borradas b where b.account_id = r.account_id)
  )
  select z.account_id, z.origen, z.terminada_at, z.fila_id, z.aviso_terminada, z.aviso_30, z.aviso_7,
         z.zona_horaria,
         (z.terminada_at at time zone z.zona_horaria)::date,
         (z.terminada_at at time zone z.zona_horaria)::date + c.plazo_dias,
         ((z.terminada_at at time zone z.zona_horaria)::date + c.plazo_dias)
           - (p_ahora at time zone z.zona_horaria)::date,
         -- FUERA DE PLAZO = pasado el día del borrado. El día 90 todavía es
         -- suyo: es el que dicen los correos («se borrarán el …»), y ese día
         -- aún sale el aviso de 7. LA ÚNICA VEZ QUE SE ESCRIBE ESTA REGLA.
         (p_ahora at time zone z.zona_horaria)::date
           > (z.terminada_at at time zone z.zona_horaria)::date + c.plazo_dias
  from con_zona z cross join constantes c
  order by z.terminada_at, z.account_id;
$$;

commit;
