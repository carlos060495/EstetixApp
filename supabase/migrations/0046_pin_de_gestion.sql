-- 0046 · PIN de gestión
--
-- La sesión del salón es SIEMPRE la del administrador: así se trabaja en un
-- salón, con un dispositivo en el mostrador que abre el dueño. Agenda y Fichaje
-- quedan libres; todo lo demás pide un PIN de gestión, distinto del de fichaje.
--
-- ═══ ES UNA CORTINA, NO UNA PUERTA ═══
--
-- La sesión que hay detrás del PIN tiene permiso completo. Quien se empeñe
-- —con las herramientas del navegador, o llamando a la API con el token que ya
-- tiene el dispositivo— entra igual. El PIN impide ENTRAR SIN QUERER en
-- clientes, caja o ajustes desde el mostrador; no es una frontera de permisos y
-- NADIE debe tratarlo como tal. La frontera sigue siendo la de siempre: la
-- sesión, el backend y la RLS. Decisión tomada a propósito (Carlos, 15-09-2026).
--
-- Lo que SÍ es serio aquí es la llave: guardada con hash, fuera del alcance de
-- la sesión, con intentos limitados y con salida por correo si se olvida.
--
-- ═══ POR CUENTA, NO POR CENTRO ═══
--
-- Quien la pone es el dueño, y el dueño es la cuenta. Con dos centros sería un
-- PIN por local que recordar, y cambiar de centro no puede pedir otro.
--
-- ═══ LA REGLA DE LA COLUMNA OBLIGATORIA (supabase/README.md) ═══
--
-- `pin_gestion` es nueva y solo la escribe el backend nuevo. En
-- `pin_attempt_locks`, donde el quiosco inserta, NO se añade ninguna columna
-- obligatoria: `account_id` nace anulable, las dos que ya había DEJAN de ser
-- obligatorias y la clave nueva lleva valor por defecto. El backend anterior
-- sigue insertando exactamente lo mismo que hoy.

begin;

-- ═══════════════════════════════════════════════════ 1. la llave

create table public.pin_gestion (
  account_id uuid primary key references public.accounts(id) on delete cascade,
  -- Argon2id, igual que el PIN del quiosco (`core/security.hash_pin`).
  pin_hash text not null,
  -- «He olvidado el PIN»: el enlace del correo lleva un token aleatorio y aquí
  -- solo se guarda su SHA-256. Un solo uso (se borra al usarlo) y caduca.
  restablecer_token_hash text,
  restablecer_expira timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint pin_gestion_restablecer_coherente check (
    (restablecer_token_hash is null) = (restablecer_expira is null)
  )
);

comment on table public.pin_gestion is
  'PIN de gestión de la cuenta: la CORTINA que pide la aplicación al salir de '
  'Agenda o Fichaje. No es un permiso: la sesión tiene acceso completo con o sin '
  'él. Sin fila = sin PIN configurado = no se pide nada.';

create unique index pin_gestion_restablecer_idx
  on public.pin_gestion (restablecer_token_hash)
  where restablecer_token_hash is not null;

-- QUE NO SE PUEDA CONSULTAR. El token de la sesión vive en el navegador del
-- mostrador y PostgREST acepta ese token: si esta tabla fuera legible para
-- `authenticated`, cualquiera con las herramientas del navegador se llevaría el
-- hash y probaría las diez mil combinaciones en su casa, sin límite de
-- intentos. Por eso RLS activada SIN políticas y, además, sin permisos: la
-- lee y la escribe solo el backend con service_role.
--
-- (Es lo que NO tiene el PIN del quiosco: `employees.pin_fichaje_hash` sí es
-- legible con la sesión. Anotado como hallazgo, fuera de esta entrega.)
alter table public.pin_gestion enable row level security;
revoke all on public.pin_gestion from anon, authenticated;
grant select, insert, update, delete on public.pin_gestion to service_role;

-- ═══════════════════════════ 2. los intentos: `pin_attempt_locks`, reusada

-- La tabla del quiosco cuenta intentos por (centro, ficha). El PIN de gestión
-- no es de ninguna ficha, así que una fila puede ser de UNA de dos cosas: de
-- una ficha del quiosco, o de una cuenta. La clave compuesta no admite nulos, y
-- por eso pasa a ser un `id` y la unicidad de antes queda en un índice parcial.
alter table public.pin_attempt_locks drop constraint pin_attempt_locks_pkey;

alter table public.pin_attempt_locks
  add column id uuid not null default gen_random_uuid();
alter table public.pin_attempt_locks add primary key (id);

alter table public.pin_attempt_locks alter column salon_id drop not null;
alter table public.pin_attempt_locks alter column employee_id drop not null;
alter table public.pin_attempt_locks
  add column account_id uuid references public.accounts(id) on delete cascade;

-- Lo mismo que garantizaba la clave de antes, para las filas del quiosco.
create unique index pin_attempt_locks_ficha_idx
  on public.pin_attempt_locks (salon_id, employee_id)
  where employee_id is not null;

create unique index pin_attempt_locks_cuenta_idx
  on public.pin_attempt_locks (account_id)
  where account_id is not null;

alter table public.pin_attempt_locks add constraint pin_attempt_locks_un_ambito check (
  (employee_id is not null and salon_id is not null and account_id is null)
  or (employee_id is null and salon_id is null and account_id is not null)
);

comment on column public.pin_attempt_locks.account_id is
  'Intentos del PIN de gestión de esta cuenta. Excluye a salon_id/employee_id, '
  'que son los del quiosco.';

-- La política de lectura existente filtra por `salon_id`: una fila de cuenta
-- lo tiene a NULL y no la ve nadie con sesión. No se toca.

-- ═══════════════════════════ 3. contar el intento ANTES de comprobarlo

-- POR QUÉ UNA FUNCIÓN y no «leer, comprobar, apuntar» desde el backend, que es
-- lo que hace el quiosco. Entre leer que no hay bloqueo y apuntar el fallo pasa
-- la comprobación del hash, y en ese hueco caben cien peticiones en paralelo:
-- todas leen «sin bloqueo», todas prueban, y el límite de cinco se convierte
-- en cien por ráfaga. Aquí el intento se apunta ANTES de comprobar el PIN, con
-- la fila bloqueada, así que cada petición cuenta aunque lleguen juntas. Si el
-- PIN resulta correcto, el backend borra la fila.
--
-- LA ESCALADA. Cinco intentos y espera: 1 min, luego 5, luego 15, y a partir de
-- ahí 60 cada cinco. Diez mil combinaciones a cinco por hora son más de un año
-- de probar sin parar. Un día entero sin intentos olvida la escalada, para que
-- los despistes de un mes no acaben en esperas de una hora.
create or replace function public.pin_gestion_intento(p_account_id uuid)
returns table (permitido boolean, bloqueado_hasta timestamptz, fallos integer)
language plpgsql
set search_path = public, pg_temp
as $$
declare
  v_fila public.pin_attempt_locks%rowtype;
  v_ahora timestamptz := now();
  v_cuenta integer;
  v_hasta timestamptz;
begin
  insert into public.pin_attempt_locks (account_id, failed_count)
  values (p_account_id, 0)
  on conflict (account_id) where account_id is not null do nothing;

  select * into v_fila
  from public.pin_attempt_locks
  where account_id = p_account_id
  for update;

  if v_fila.locked_until is not null and v_fila.locked_until > v_ahora then
    return query select false, v_fila.locked_until, v_fila.failed_count;
    return;
  end if;

  v_cuenta := v_fila.failed_count;
  if v_fila.last_failed_at is not null and v_fila.last_failed_at < v_ahora - interval '24 hours' then
    v_cuenta := 0;
  end if;
  v_cuenta := v_cuenta + 1;

  v_hasta := null;
  if v_cuenta % 5 = 0 then
    v_hasta := v_ahora + make_interval(mins => case v_cuenta / 5
      when 1 then 1
      when 2 then 5
      when 3 then 15
      else 60
    end);
  end if;

  update public.pin_attempt_locks
  set failed_count = v_cuenta, last_failed_at = v_ahora, locked_until = v_hasta
  where account_id = p_account_id;

  return query select true, v_hasta, v_cuenta;
end;
$$;

comment on function public.pin_gestion_intento(uuid) is
  'Apunta un intento del PIN de gestión de la cuenta ANTES de comprobarlo. '
  'permitido=false: está en espera hasta bloqueado_hasta. Solo service_role.';

-- Una función nace ejecutable por PUBLIC. Si la pudiera llamar la sesión, desde
-- el navegador se podría dejar al dueño bloqueado a base de intentos falsos.
revoke all on function public.pin_gestion_intento(uuid) from public, anon, authenticated;
grant execute on function public.pin_gestion_intento(uuid) to service_role;

commit;
