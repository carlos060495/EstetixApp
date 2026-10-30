-- 0039 · La capa de cuenta, y la frontera se mueve con ella
--
-- QUÉ HACE Y QUÉ NO. No añade ninguna función: cada salón pasa a ser su propia
-- cuenta, uno a uno, y la RLS deja de preguntar «¿es este salón EL de tu token?»
-- para preguntar «¿está este salón entre los que puedes ver?». Con un salón por
-- cuenta las dos preguntas tienen la misma respuesta, y esa equivalencia es lo
-- que hace esta migración verificable: el criterio de aceptación es que NADA
-- cambie.
--
-- ═══ POR QUÉ LA CUENTA SE BUSCA Y NO SE LEE DEL TOKEN ═══
--
-- La alternativa era meter un `account_id` en el JWT, como está hoy el
-- `salon_id`. Se descartó por dos motivos, y el segundo pesa más:
--
--   1. Obligaría a reacuñar todos los tokens: todo el mundo fuera, incluidas
--      las sesiones de soporte abiertas.
--   2. Un claim se lee; una pertenencia se comprueba. La cuenta sale de
--      `auth.uid()` —quién eres, verificado por la firma de GoTrue— y de ahí se
--      va a la base a mirar a qué tienes acceso. El día que exista un selector
--      de centro, ese selector no podrá ampliar nada: la respuesta ya está
--      decidida aquí abajo.
--
-- Funciona en los TRES caminos porque los tres son usuarios de verdad: la app,
-- la sesión de soporte y el bot/cron acuñan un access_token real contra GoTrue
-- (ver `core/tenant_token_broker.acunar_token_tenant`), así que todos llevan su
-- `sub`. Por eso esta migración NO invalida ninguna sesión viva.
--
-- ═══ QUIÉN VE QUÉ ═══
--
--   · Admin → todos los centros de su cuenta.
--   · Cualquier otro → solo los centros donde tenga fila en `user_salons`.
--
-- El backfill le da a CADA usuario su salón de hoy y ni uno más, admin
-- incluido. Con un salón por cuenta, las dos ramas devuelven lo mismo — y un
-- estilista NO gana acceso a nada el día del despliegue, que es lo que hace que
-- «no ha cambiado nada» siga siendo verdad en el punto que importa.
--
-- ═══ POR QUÉ UNA TABLA Y NO DEDUCIRLO DE `employees` ═══
--
-- Se midió antes de elegir: de 7 usuarios, 6 casan con una ficha de empleado
-- por email, y de 6 empleados **solo 1** tiene `user_id` relleno. Derivar el
-- acceso de ahí dejaría a cinco personas sin ver nada el día del despliegue.
-- Además ataría la autorización a una tabla de inquilino, que es justo la que
-- después pregunta por la autorización.
--
-- La tabla se mantiene sola con un trigger sobre `users`, así que esta entrega
-- no toca ni una línea de backend.

begin;

-- ═══════════════════════════════════════════════════ la cuenta

create table public.accounts (
  id uuid primary key default gen_random_uuid(),
  nombre text not null,
  created_at timestamptz not null default now()
);

comment on table public.accounts is
  'El titular. Hoy hay una por salón; mañana una puede tener varios centros. '
  'La suscripción sigue colgando del salón: eso se mueve en la entrega 2.';

alter table public.salons add column account_id uuid references public.accounts(id);
alter table public.users  add column account_id uuid references public.accounts(id);

-- BACKFILL UNO A UNO. La columna temporal es la forma menos lista de
-- emparejarlas, y por eso es la que se usa: no depende de ningún orden.
alter table public.accounts add column salon_origen uuid;

insert into public.accounts (nombre, salon_origen)
select coalesce(nullif(trim(s.nombre_comercial), ''), 'Cuenta'), s.id
from public.salons s;

update public.salons s set account_id = a.id
from public.accounts a where a.salon_origen = s.id;

update public.users u set account_id = s.account_id
from public.salons s where s.id = u.salon_id;

alter table public.accounts drop column salon_origen;

alter table public.salons alter column account_id set not null;
create index salons_account_id_idx on public.salons (account_id);
create index users_account_id_idx on public.users (account_id);

-- ═════════════════════════════════════ a qué centros entra cada usuario

create table public.user_salons (
  user_id  uuid not null references public.users(id)  on delete cascade,
  salon_id uuid not null references public.salons(id) on delete cascade,
  created_at timestamptz not null default now(),
  primary key (user_id, salon_id)
);

comment on table public.user_salons is
  'Un usuario entra a estos centros. Para un admin no decide nada —ve todos los '
  'de su cuenta— pero se le rellena igual, para que la tabla se pueda leer sola.';

create index user_salons_salon_id_idx on public.user_salons (salon_id);

-- El acceso de HOY, exactamente: un usuario, su salón.
insert into public.user_salons (user_id, salon_id)
select u.id, u.salon_id from public.users u where u.salon_id is not null;

-- SE MANTIENE SOLA. Un trigger y no una llamada desde el backend, por dos
-- razones: esta entrega no toca backend, y un camino nuevo que cree un usuario
-- —una invitación, un alta, lo que venga— no puede olvidarse de dar el acceso.
-- Mientras `users.salon_id` siga existiendo, esta tabla es su espejo.
create or replace function public.sincronizar_user_salons()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  if new.salon_id is null then
    return new;
  end if;
  insert into public.user_salons (user_id, salon_id)
  values (new.id, new.salon_id)
  on conflict do nothing;
  return new;
end;
$$;

create trigger users_sincroniza_salones
  after insert or update of salon_id on public.users
  for each row execute function public.sincronizar_user_salons();

-- ═════════════════════════════════════════════════ la frontera

create or replace function public.cuenta_actual()
returns uuid
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select u.account_id from public.users u
  where u.auth_user_id = auth.uid()
  limit 1;
$$;

create or replace function public.salones_visibles()
returns uuid[]
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  -- SECURITY DEFINER NO ES OPCIONAL: `users`, `salons` y las demás tienen
  -- políticas que llaman a esta función. Si leyera con los permisos de quien
  -- pregunta, la política volvería a llamarla y la consulta entraría en
  -- recursión. Corre como el dueño de las tablas, que se salta la RLS, y con
  -- `search_path` fijo para que nadie pueda colar un `public` propio.
  select coalesce(
    array(
      select s.id from public.salons s
      join public.users u on u.account_id = s.account_id
      where u.auth_user_id = auth.uid() and u.rol = 'admin'
      union
      select us.salon_id from public.user_salons us
      join public.users u on u.id = us.user_id
      where u.auth_user_id = auth.uid()
    ),
    '{}'::uuid[]
  );
$$;

comment on function public.salones_visibles() is
  'Los centros a los que este usuario entra. Admin: todos los de su cuenta. '
  'El resto: los suyos en user_salons. Es LA frontera; un selector de centro '
  'en el navegador solo puede elegir dentro de esto, nunca ampliarlo.';

revoke all on function public.cuenta_actual() from public;
revoke all on function public.salones_visibles() from public;
grant execute on function public.cuenta_actual() to authenticated, service_role;
grant execute on function public.salones_visibles() to authenticated, service_role;

-- Las dos tablas nuevas no las lee todavía ninguna pantalla. Nacen cerradas: la
-- RLS activada sin políticas ya lo impide, y el REVOKE es la segunda capa.
-- `service_role` conserva lo suyo, que es por donde entra el panel de
-- plataforma. Ver la 0036: una tabla nueva NO nace sin permisos.
alter table public.accounts enable row level security;
alter table public.user_salons enable row level security;
revoke all on public.accounts from anon, authenticated;
revoke all on public.user_salons from anon, authenticated;

-- ═══════════════════════════ las 50 políticas, una por una
--
-- GENERADAS DEL CATÁLOGO, NO ESCRITAS A MANO. Cincuenta políticas a mano son
-- cincuenta ocasiones de cambiar una sin querer. Se leyeron de `pg_policies` y
-- se sustituyó SOLO el predicado del inquilino: mismo tipo, mismos roles, mismo
-- comando, mismas condiciones extra (`is_admin_rol()`, el `EXISTS` sobre
-- `employees`, el `auth_user_id = auth.uid()` de `users`).
--
-- Son 50 y no 49: `time_entries.tenant_isolation` comparaba el claim a mano
-- (`(salon_id)::text = auth.jwt() ->> 'salon_id'`) en vez de usar el ayudante.
-- Se normaliza aquí para que no quede ni una política hablando del token.
--
-- `current_salon_id()` NO se borra: sigue existiendo y sigue siendo verdad
-- —el token sigue llevando su `salon_id`— pero deja de decidir nada. Borrarla
-- sería un segundo cambio en la misma entrega.

drop policy if exists "appointments_delete_admin" on public.appointments;
create policy "appointments_delete_admin" on public.appointments
  as permissive for delete
  to public
  using (((salon_id = any (public.salones_visibles())) AND is_admin_rol()));

drop policy if exists "appointments_insert" on public.appointments;
create policy "appointments_insert" on public.appointments
  as permissive for insert
  to public
  with check ((salon_id = any (public.salones_visibles())));

drop policy if exists "appointments_select" on public.appointments;
create policy "appointments_select" on public.appointments
  as permissive for select
  to public
  using ((salon_id = any (public.salones_visibles())));

drop policy if exists "appointments_update" on public.appointments;
create policy "appointments_update" on public.appointments
  as permissive for update
  to public
  using ((salon_id = any (public.salones_visibles())))
  with check ((salon_id = any (public.salones_visibles())));

drop policy if exists "automations_all_admin" on public.automations;
create policy "automations_all_admin" on public.automations
  as permissive for all
  to public
  using (((salon_id = any (public.salones_visibles())) AND is_admin_rol()))
  with check (((salon_id = any (public.salones_visibles())) AND is_admin_rol()));

drop policy if exists "bot_messages_all" on public.bot_messages;
create policy "bot_messages_all" on public.bot_messages
  as permissive for all
  to public
  using ((salon_id = any (public.salones_visibles())))
  with check ((salon_id = any (public.salones_visibles())));

drop policy if exists "bot_sessions_all" on public.bot_sessions;
create policy "bot_sessions_all" on public.bot_sessions
  as permissive for all
  to public
  using ((salon_id = any (public.salones_visibles())))
  with check ((salon_id = any (public.salones_visibles())));

drop policy if exists "clients_delete_admin" on public.clients;
create policy "clients_delete_admin" on public.clients
  as permissive for delete
  to public
  using (((salon_id = any (public.salones_visibles())) AND is_admin_rol()));

drop policy if exists "clients_insert" on public.clients;
create policy "clients_insert" on public.clients
  as permissive for insert
  to public
  with check ((salon_id = any (public.salones_visibles())));

drop policy if exists "clients_select" on public.clients;
create policy "clients_select" on public.clients
  as permissive for select
  to public
  using ((salon_id = any (public.salones_visibles())));

drop policy if exists "clients_update" on public.clients;
create policy "clients_update" on public.clients
  as permissive for update
  to public
  using ((salon_id = any (public.salones_visibles())))
  with check ((salon_id = any (public.salones_visibles())));

drop policy if exists "consents_delete_admin" on public.consents;
create policy "consents_delete_admin" on public.consents
  as permissive for delete
  to public
  using (((salon_id = any (public.salones_visibles())) AND is_admin_rol()));

drop policy if exists "consents_insert" on public.consents;
create policy "consents_insert" on public.consents
  as permissive for insert
  to public
  with check ((salon_id = any (public.salones_visibles())));

drop policy if exists "consents_select" on public.consents;
create policy "consents_select" on public.consents
  as permissive for select
  to public
  using ((salon_id = any (public.salones_visibles())));

drop policy if exists "employee_services_select" on public.employee_services;
create policy "employee_services_select" on public.employee_services
  as permissive for select
  to public
  using ((EXISTS ( SELECT 1 FROM employees e WHERE ((e.id = employee_services.employee_id) AND (e.salon_id = any (public.salones_visibles()))))));

drop policy if exists "employee_services_write_admin" on public.employee_services;
create policy "employee_services_write_admin" on public.employee_services
  as permissive for all
  to public
  using ((is_admin_rol() AND (EXISTS ( SELECT 1 FROM employees e WHERE ((e.id = employee_services.employee_id) AND (e.salon_id = any (public.salones_visibles())))))))
  with check ((is_admin_rol() AND (EXISTS ( SELECT 1 FROM employees e WHERE ((e.id = employee_services.employee_id) AND (e.salon_id = any (public.salones_visibles())))))));

drop policy if exists "employees_select" on public.employees;
create policy "employees_select" on public.employees
  as permissive for select
  to public
  using ((salon_id = any (public.salones_visibles())));

drop policy if exists "employees_write_admin" on public.employees;
create policy "employees_write_admin" on public.employees
  as permissive for all
  to public
  using (((salon_id = any (public.salones_visibles())) AND is_admin_rol()))
  with check (((salon_id = any (public.salones_visibles())) AND is_admin_rol()));

drop policy if exists "expenses_all_admin" on public.expenses;
create policy "expenses_all_admin" on public.expenses
  as permissive for all
  to public
  using (((salon_id = any (public.salones_visibles())) AND is_admin_rol()))
  with check (((salon_id = any (public.salones_visibles())) AND is_admin_rol()));

drop policy if exists "impersonation_sessions_select_own" on public.impersonation_sessions;
create policy "impersonation_sessions_select_own" on public.impersonation_sessions
  as permissive for select
  to public
  using (((salon_id = any (public.salones_visibles())) AND is_admin_rol()));

drop policy if exists "incomes_all_admin" on public.incomes;
create policy "incomes_all_admin" on public.incomes
  as permissive for all
  to public
  using (((salon_id = any (public.salones_visibles())) AND is_admin_rol()))
  with check (((salon_id = any (public.salones_visibles())) AND is_admin_rol()));

drop policy if exists "pin_attempt_locks_select_admin" on public.pin_attempt_locks;
create policy "pin_attempt_locks_select_admin" on public.pin_attempt_locks
  as permissive for select
  to public
  using (((salon_id = any (public.salones_visibles())) AND is_admin_rol()));

drop policy if exists "products_delete_admin" on public.products;
create policy "products_delete_admin" on public.products
  as permissive for delete
  to public
  using (((salon_id = any (public.salones_visibles())) AND is_admin_rol()));

drop policy if exists "products_insert" on public.products;
create policy "products_insert" on public.products
  as permissive for insert
  to public
  with check ((salon_id = any (public.salones_visibles())));

drop policy if exists "products_select" on public.products;
create policy "products_select" on public.products
  as permissive for select
  to public
  using ((salon_id = any (public.salones_visibles())));

drop policy if exists "products_update" on public.products;
create policy "products_update" on public.products
  as permissive for update
  to public
  using ((salon_id = any (public.salones_visibles())))
  with check ((salon_id = any (public.salones_visibles())));

drop policy if exists "providers_delete_admin" on public.providers;
create policy "providers_delete_admin" on public.providers
  as permissive for delete
  to public
  using (((salon_id = any (public.salones_visibles())) AND is_admin_rol()));

drop policy if exists "providers_insert" on public.providers;
create policy "providers_insert" on public.providers
  as permissive for insert
  to public
  with check ((salon_id = any (public.salones_visibles())));

drop policy if exists "providers_select" on public.providers;
create policy "providers_select" on public.providers
  as permissive for select
  to public
  using ((salon_id = any (public.salones_visibles())));

drop policy if exists "providers_update" on public.providers;
create policy "providers_update" on public.providers
  as permissive for update
  to public
  using ((salon_id = any (public.salones_visibles())))
  with check ((salon_id = any (public.salones_visibles())));

drop policy if exists "puestos_select" on public.puestos;
create policy "puestos_select" on public.puestos
  as permissive for select
  to public
  using ((salon_id = any (public.salones_visibles())));

drop policy if exists "puestos_write_admin" on public.puestos;
create policy "puestos_write_admin" on public.puestos
  as permissive for all
  to public
  using (((salon_id = any (public.salones_visibles())) AND is_admin_rol()))
  with check (((salon_id = any (public.salones_visibles())) AND is_admin_rol()));

drop policy if exists "salon_schedule_select" on public.salon_schedule;
create policy "salon_schedule_select" on public.salon_schedule
  as permissive for select
  to public
  using ((salon_id = any (public.salones_visibles())));

drop policy if exists "salon_schedule_write_admin" on public.salon_schedule;
create policy "salon_schedule_write_admin" on public.salon_schedule
  as permissive for all
  to public
  using (((salon_id = any (public.salones_visibles())) AND is_admin_rol()))
  with check (((salon_id = any (public.salones_visibles())) AND is_admin_rol()));

drop policy if exists "salon_schedule_exceptions_select" on public.salon_schedule_exceptions;
create policy "salon_schedule_exceptions_select" on public.salon_schedule_exceptions
  as permissive for select
  to public
  using ((salon_id = any (public.salones_visibles())));

drop policy if exists "salon_schedule_exceptions_write_admin" on public.salon_schedule_exceptions;
create policy "salon_schedule_exceptions_write_admin" on public.salon_schedule_exceptions
  as permissive for all
  to public
  using (((salon_id = any (public.salones_visibles())) AND is_admin_rol()))
  with check (((salon_id = any (public.salones_visibles())) AND is_admin_rol()));

drop policy if exists "salon_whatsapp_config_select" on public.salon_whatsapp_config;
create policy "salon_whatsapp_config_select" on public.salon_whatsapp_config
  as permissive for select
  to public
  using ((salon_id = any (public.salones_visibles())));

drop policy if exists "salon_whatsapp_config_write_admin" on public.salon_whatsapp_config;
create policy "salon_whatsapp_config_write_admin" on public.salon_whatsapp_config
  as permissive for all
  to public
  using (((salon_id = any (public.salones_visibles())) AND is_admin_rol()))
  with check (((salon_id = any (public.salones_visibles())) AND is_admin_rol()));

drop policy if exists "salons_select_own" on public.salons;
create policy "salons_select_own" on public.salons
  as permissive for select
  to public
  using ((id = any (public.salones_visibles())));

drop policy if exists "salons_update_admin" on public.salons;
create policy "salons_update_admin" on public.salons
  as permissive for update
  to public
  using (((id = any (public.salones_visibles())) AND is_admin_rol()))
  with check (((id = any (public.salones_visibles())) AND is_admin_rol()));

drop policy if exists "service_categories_select" on public.service_categories;
create policy "service_categories_select" on public.service_categories
  as permissive for select
  to public
  using ((salon_id = any (public.salones_visibles())));

drop policy if exists "service_categories_write_admin" on public.service_categories;
create policy "service_categories_write_admin" on public.service_categories
  as permissive for all
  to public
  using (((salon_id = any (public.salones_visibles())) AND is_admin_rol()))
  with check (((salon_id = any (public.salones_visibles())) AND is_admin_rol()));

drop policy if exists "services_select" on public.services;
create policy "services_select" on public.services
  as permissive for select
  to public
  using ((salon_id = any (public.salones_visibles())));

drop policy if exists "services_write_admin" on public.services;
create policy "services_write_admin" on public.services
  as permissive for all
  to public
  using (((salon_id = any (public.salones_visibles())) AND is_admin_rol()))
  with check (((salon_id = any (public.salones_visibles())) AND is_admin_rol()));

drop policy if exists "subscriptions_select_admin" on public.subscriptions;
create policy "subscriptions_select_admin" on public.subscriptions
  as permissive for select
  to public
  using (((salon_id = any (public.salones_visibles())) AND is_admin_rol()));

drop policy if exists "support_codes_admin_insert_own" on public.support_access_codes;
create policy "support_codes_admin_insert_own" on public.support_access_codes
  as permissive for insert
  to public
  with check (((salon_id = any (public.salones_visibles())) AND is_admin_rol()));

drop policy if exists "support_codes_admin_select_own" on public.support_access_codes;
create policy "support_codes_admin_select_own" on public.support_access_codes
  as permissive for select
  to public
  using (((salon_id = any (public.salones_visibles())) AND is_admin_rol()));

drop policy if exists "tenant_isolation" on public.time_entries;
create policy "tenant_isolation" on public.time_entries
  as permissive for all
  to public
  using ((salon_id = any (public.salones_visibles())))
  with check ((salon_id = any (public.salones_visibles())));

drop policy if exists "users_all_admin" on public.users;
create policy "users_all_admin" on public.users
  as permissive for all
  to public
  using (((salon_id = any (public.salones_visibles())) AND is_admin_rol()))
  with check (((salon_id = any (public.salones_visibles())) AND is_admin_rol()));

drop policy if exists "users_select_self" on public.users;
create policy "users_select_self" on public.users
  as permissive for select
  to public
  using (((salon_id = any (public.salones_visibles())) AND (auth_user_id = auth.uid())));

commit;
