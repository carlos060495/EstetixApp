-- Programa de referidos, Fase 1: modelo de datos, registro público y gestión
-- desde el panel Super Admin.
--
-- IMPORTANTE — ESTAS TABLAS NO SON DE UN SALÓN. Un referido es una entidad de
-- PLATAFORMA: una persona que recomienda EstetixApp y cobra una comisión. No
-- pertenece a ningún salón, así que NO lleva `salon_id` ni RLS por inquilino.
-- Mismo criterio que `admin_users` y `admin_logs` en la 0004.
--
-- Que no lleve RLS por salon_id no significa que quede abierta: se activa RLS
-- SIN NINGUNA política, que en Postgres significa "nadie pasa" salvo
-- service_role (que la salta por definición) y el superusuario. El backend
-- entra siempre con la secret key, detrás del guard de admin_users o del
-- endpoint público. Un token de salón —anon o authenticated— no puede leer ni
-- escribir aquí, y hay una contraprueba que lo comprueba contra Postgres.

create type referido_estado as enum ('pendiente', 'aprobado', 'rechazado');

create table public.referidos (
  id uuid primary key default gen_random_uuid(),
  nombre text not null,
  -- Único e insensible a mayúsculas: "Ana@x.com" y "ana@x.com" son la misma
  -- persona, y sin esto se colarían dos veces por el registro público.
  email text not null,
  -- Se genera AL APROBAR, no al registrarse: mientras está pendiente no hay
  -- nada que repartir, y un código emitido antes de tiempo puede acabar
  -- circulando por una cuenta que luego se rechaza.
  codigo text unique,
  -- NULL = "usa el % global". Se guarda como NULL en vez de copiar el global
  -- para que al cambiar el global cambien también los que no tienen pacto
  -- propio; copiarlo dejaría a todos congelados en el valor del día del alta.
  comision_pct numeric(5, 2) check (comision_pct >= 0 and comision_pct <= 100),
  estado referido_estado not null default 'pendiente',
  -- Texto libre a propósito: el pago es manual y cada uno cobra por donde
  -- puede (IBAN, PayPal, Bizum). Encajarlo en columnas obligaría a decidir hoy
  -- algo que todavía no sabemos.
  datos_pago text,
  created_at timestamptz not null default now(),
  aprobado_at timestamptz,
  aprobado_por uuid references public.admin_users(id) on delete set null
);

create unique index referidos_email_unico on public.referidos (lower(email));
create index referidos_estado on public.referidos (estado);

-- ── Configuración de plataforma ────────────────────────────────────────────
--
-- Tabla de UNA fila. El candado es `id boolean primary key default true` con
-- `check (id)`: solo existe el valor `true`, así que un segundo INSERT choca
-- con la clave primaria. Es más difícil de romper que una convención escrita
-- en un comentario, y no hace falta ningún trigger.
create table public.plataforma_config (
  id boolean primary key default true check (id),
  comision_referido_pct numeric(5, 2) not null default 10
    check (comision_referido_pct >= 0 and comision_referido_pct <= 100),
  actualizado_at timestamptz not null default now(),
  actualizado_por uuid references public.admin_users(id) on delete set null
);

insert into public.plataforma_config (id) values (true);

-- ── RLS: activada y sin políticas = cerrada a los tokens de salón ──────────
alter table public.referidos enable row level security;
alter table public.plataforma_config enable row level security;

-- Segunda barrera, a nivel de GRANT, por el mismo patrón que `admin_logs` en
-- la 0004 y los códigos de soporte en la 0021: si algún día alguien añadiera
-- una política por descuido, esto sigue negando el acceso a los roles del
-- salón. La defensa no depende de que nadie se equivoque una sola vez.
revoke all on public.referidos from anon, authenticated;
revoke all on public.plataforma_config from anon, authenticated;

-- Y el permiso de entrar, SOLO para service_role.
--
-- Esto no es opcional ni redundante: en este proyecto una tabla recién creada
-- nace SIN DML para ninguno de los roles de Supabase —comprobado: al crear una
-- tabla, `service_role` sale con REFERENCES/TRIGGER/TRUNCATE y nada más—, así
-- que sin esta línea el backend responde «permission denied for table
-- referidos» a todo. Es la misma razón por la que la 0022, la 0024 y la 0027
-- llevan su grant dentro; ver supabase/README.md.
--
-- La diferencia con aquellas: allí el grant iba a `authenticated, service_role`
-- porque eran tablas de un salón. Aquí NO se concede a `authenticated`, y ese
-- es justo el punto de toda la migración.
grant select, insert, update, delete on public.referidos to service_role;
grant select, update on public.plataforma_config to service_role;
