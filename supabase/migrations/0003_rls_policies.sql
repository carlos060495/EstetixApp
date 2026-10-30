-- Row Level Security. Default deny: RLS is enabled on every tenant table and
-- no table is usable until an explicit policy grants access.
--
-- Role enforcement note: RLS here enforces (a) tenant isolation on every
-- table (salon_id = current_salon_id()) and (b) row-level admin-only access
-- for whole tables that an estilista/recepcionista has no legitimate reason
-- to touch (users, expenses, automations, subscriptions, time_entries,
-- write access to employees/services/products/etc.). Column-level hiding
-- (e.g. an estilista seeing an employee's name but not their comision_*)
-- is NOT expressible in RLS and is enforced as layer 3 in the FastAPI
-- response schemas, per DIRECTRICES_PROYECTO.md §4.4.

alter table public.salons enable row level security;
alter table public.users enable row level security;
alter table public.clients enable row level security;
alter table public.service_categories enable row level security;
alter table public.services enable row level security;
alter table public.employees enable row level security;
alter table public.employee_services enable row level security;
alter table public.appointments enable row level security;
alter table public.products enable row level security;
alter table public.providers enable row level security;
alter table public.expenses enable row level security;
alter table public.time_entries enable row level security;
alter table public.automations enable row level security;
alter table public.subscriptions enable row level security;
alter table public.consents enable row level security;

-- salons: a tenant only ever sees/touches its own row.
create policy "salons_select_own" on public.salons
  for select using (id = public.current_salon_id());
create policy "salons_update_admin" on public.salons
  for update using (id = public.current_salon_id() and public.is_admin_rol())
  with check (id = public.current_salon_id() and public.is_admin_rol());

-- users: staff accounts/roles are an admin-only surface.
create policy "users_all_admin" on public.users
  for all using (salon_id = public.current_salon_id() and public.is_admin_rol())
  with check (salon_id = public.current_salon_id() and public.is_admin_rol());
create policy "users_select_self" on public.users
  for select using (salon_id = public.current_salon_id() and auth_user_id = auth.uid());

-- clients: CRM usable by every tenant role; deletion (hard, legal-relevant) admin only.
create policy "clients_select" on public.clients
  for select using (salon_id = public.current_salon_id());
create policy "clients_insert" on public.clients
  for insert with check (salon_id = public.current_salon_id());
create policy "clients_update" on public.clients
  for update using (salon_id = public.current_salon_id())
  with check (salon_id = public.current_salon_id());
create policy "clients_delete_admin" on public.clients
  for delete using (salon_id = public.current_salon_id() and public.is_admin_rol());

-- service_categories / services: catalog is admin-managed, readable by all tenant roles.
create policy "service_categories_select" on public.service_categories
  for select using (salon_id = public.current_salon_id());
create policy "service_categories_write_admin" on public.service_categories
  for all using (salon_id = public.current_salon_id() and public.is_admin_rol())
  with check (salon_id = public.current_salon_id() and public.is_admin_rol());

create policy "services_select" on public.services
  for select using (salon_id = public.current_salon_id());
create policy "services_write_admin" on public.services
  for all using (salon_id = public.current_salon_id() and public.is_admin_rol())
  with check (salon_id = public.current_salon_id() and public.is_admin_rol());

-- employees: roster readable by all tenant roles (needed for the agenda board),
-- written by admin only (comision_* fields live here).
create policy "employees_select" on public.employees
  for select using (salon_id = public.current_salon_id());
create policy "employees_write_admin" on public.employees
  for all using (salon_id = public.current_salon_id() and public.is_admin_rol())
  with check (salon_id = public.current_salon_id() and public.is_admin_rol());

create policy "employee_services_select" on public.employee_services
  for select using (
    exists (select 1 from public.employees e where e.id = employee_id and e.salon_id = public.current_salon_id())
  );
create policy "employee_services_write_admin" on public.employee_services
  for all using (
    public.is_admin_rol() and exists (select 1 from public.employees e where e.id = employee_id and e.salon_id = public.current_salon_id())
  )
  with check (
    public.is_admin_rol() and exists (select 1 from public.employees e where e.id = employee_id and e.salon_id = public.current_salon_id())
  );

-- appointments: the day-to-day surface for estilista/recepcionista; hard delete admin only.
create policy "appointments_select" on public.appointments
  for select using (salon_id = public.current_salon_id());
create policy "appointments_insert" on public.appointments
  for insert with check (salon_id = public.current_salon_id());
create policy "appointments_update" on public.appointments
  for update using (salon_id = public.current_salon_id())
  with check (salon_id = public.current_salon_id());
create policy "appointments_delete_admin" on public.appointments
  for delete using (salon_id = public.current_salon_id() and public.is_admin_rol());

-- products / providers: Stock & Proveedores is available to every tenant role.
create policy "products_select" on public.products
  for select using (salon_id = public.current_salon_id());
create policy "products_insert" on public.products
  for insert with check (salon_id = public.current_salon_id());
create policy "products_update" on public.products
  for update using (salon_id = public.current_salon_id())
  with check (salon_id = public.current_salon_id());
create policy "products_delete_admin" on public.products
  for delete using (salon_id = public.current_salon_id() and public.is_admin_rol());

create policy "providers_select" on public.providers
  for select using (salon_id = public.current_salon_id());
create policy "providers_insert" on public.providers
  for insert with check (salon_id = public.current_salon_id());
create policy "providers_update" on public.providers
  for update using (salon_id = public.current_salon_id())
  with check (salon_id = public.current_salon_id());
create policy "providers_delete_admin" on public.providers
  for delete using (salon_id = public.current_salon_id() and public.is_admin_rol());

-- expenses: finance is explicitly hidden from estilista/recepcionista (§4.4).
create policy "expenses_all_admin" on public.expenses
  for all using (salon_id = public.current_salon_id() and public.is_admin_rol())
  with check (salon_id = public.current_salon_id() and public.is_admin_rol());

-- time_entries: kiosk clock-ins are written by the backend service role
-- (no tenant JWT session at the kiosk). Authenticated tenant access is
-- read-only and admin-only (payroll-adjacent data).
create policy "time_entries_select_admin" on public.time_entries
  for select using (salon_id = public.current_salon_id() and public.is_admin_rol());

-- automations / subscriptions: back-office configuration, admin only.
create policy "automations_all_admin" on public.automations
  for all using (salon_id = public.current_salon_id() and public.is_admin_rol())
  with check (salon_id = public.current_salon_id() and public.is_admin_rol());

create policy "subscriptions_select_admin" on public.subscriptions
  for select using (salon_id = public.current_salon_id() and public.is_admin_rol());

-- consents: every tenant role can record a signed consent; only admin deletes (legal record).
create policy "consents_select" on public.consents
  for select using (salon_id = public.current_salon_id());
create policy "consents_insert" on public.consents
  for insert with check (salon_id = public.current_salon_id());
create policy "consents_delete_admin" on public.consents
  for delete using (salon_id = public.current_salon_id() and public.is_admin_rol());
