-- 0056 · El rastro de las descargas de datos
--
-- Llevarse los datos de un salón (el ZIP de `POST /cuenta/datos/descarga`) saca
-- de la app los datos personales de todas sus clientas y de su equipo. Mirar
-- 300 fichas en pantalla se queda dentro; un fichero con las 300 sale y no
-- vuelve. Conviene poder demostrar cada salida, y ver a quien lo intenta.
--
-- ═══ QUÉ SE APUNTA ═══
--
-- Una fila por cada intento que llega a comprobar la contraseña:
--
--   · resultado — 'entregada' (el ZIP se armó y se envió), 'contrasena_incorrecta'
--                 (el servicio de autenticación la rechazó) o 'en_espera'
--                 (demasiados fallos seguidos: ni se llegó a comprobar). Los dos
--                 últimos son justo lo que interesa ver si alguien está probando.
--   · quién     — `usuario_id` (el usuario de la sesión, `users.id`).
--   · cuándo    — `ocurrido_at`.
--   · dónde     — `origen`: 'app' (Ajustes → RGPD) o 'muro_suspension'.
--   · cuánto    — `centros`, `filas` y `bytes`, solo en las entregadas.
--   · y los nombres de entonces: `cuenta_nombre`, `centro_nombre`,
--     `usuario_nombre` y `usuario_email` (la misma regla que la 0057).
--
-- SIN IP, como el rastro de dinero (0054) y por el mismo motivo: es el único
-- dato personal del conjunto y abriría un plazo de conservación que hoy no se
-- puede cumplir. Y NUNCA la contraseña ni nada derivado de ella.
--
-- NO ES `decisiones_de_dinero`: aquella registra decisiones de dinero, su
-- `suceso` solo admite bajas y cambios de plan, y su guarda busca escrituras
-- en Stripe. Mezclarlas debilitaría las dos.
--
-- ═══ NO SE PUEDE EDITAR NI BORRAR, Y LO IMPIDE LA BASE ═══
--
-- Las mismas tres capas que la 0054:
--
--   1. PERMISOS. Nadie tiene UPDATE, DELETE ni TRUNCATE. `service_role` solo
--      INSERT y SELECT; `authenticated` y `anon`, nada. Se revoca además de no
--      concederse, porque Supabase da todo por defecto a los roles de la API.
--   2. RLS activada y sin políticas: la sesión de un salón no ve ni una fila.
--      Lo lee solo el panel de plataforma, con `service_role`.
--   3. UN TRIGGER que revienta ante UPDATE, DELETE y TRUNCATE, para quien
--      tuviera el permiso por otra vía.
--
-- SIN CLAVES AJENAS, a propósito (medido el 01-10-2026). Un registro que no
-- se puede tocar y apunta con FK a `users`, `salons` o `accounts` IMPIDE
-- BORRARLOS: Postgres rechaza el DELETE mientras haya una fila que los
-- nombre, y `on delete set null` sería un UPDATE, que el trigger rechaza. Lo
-- cazó el desmontaje de un test (23503 al borrar el usuario). Los ids se
-- guardan tal cual; la fila sobrevive a lo que nombra, que es lo que se
-- quiere para poder demostrar una entrega después del borrado de la cuenta.
--
-- ═══ LA REGLA DE LA COLUMNA OBLIGATORIA (supabase/README.md) ═══
--
-- Tabla nueva: nadie la escribe hasta el backend nuevo.
--
-- ═══ ORDEN DE DESPLIEGUE: LA MIGRACIÓN VA ANTES DEL BACKEND ═══
--
--   1. supabase db push (0056)   2. backend   3. frontend
--
-- Sin la tabla, el backend nuevo no podría apuntar y la descarga contestaría
-- error: una salida de datos que no queda registrada no se entrega.

begin;

create table public.descargas_de_datos (
  id bigint generated always as identity primary key,
  account_id uuid not null,
  salon_id uuid,
  usuario_id uuid,
  ocurrido_at timestamptz not null default now(),
  resultado text not null
    check (resultado in ('entregada', 'contrasena_incorrecta', 'en_espera')),
  origen text not null check (origen in ('app', 'muro_suspension')),
  centros integer,
  filas integer,
  bytes integer,
  -- La instantánea (ver 0057): los nombres EN ESE MOMENTO, para que la fila
  -- siga diciendo quién fue cuando ya no exista.
  cuenta_nombre text,
  centro_nombre text,
  usuario_nombre text,
  usuario_email text
);

comment on table public.descargas_de_datos is
  'Cada intento de llevarse los datos de una cuenta: entregadas y rechazadas. '
  'No se puede editar ni borrar: lo impiden los permisos y un trigger (0056).';

create index descargas_de_datos_por_cuenta
  on public.descargas_de_datos (account_id, ocurrido_at);

-- ── 1. Permisos ───────────────────────────────────────────────────────────
revoke all on public.descargas_de_datos from anon, authenticated, service_role;
grant select, insert on public.descargas_de_datos to service_role;

-- ── 2. RLS sin políticas ──────────────────────────────────────────────────
alter table public.descargas_de_datos enable row level security;

-- ── 3. El trigger ─────────────────────────────────────────────────────────
create function public.descargas_de_datos_no_se_tocan()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  raise exception 'descargas_de_datos no se puede % : es un registro', tg_op
    using errcode = '42501';
end;
$$;

create trigger descargas_de_datos_sin_update_ni_delete
  before update or delete on public.descargas_de_datos
  for each row execute function public.descargas_de_datos_no_se_tocan();

create trigger descargas_de_datos_sin_truncate
  before truncate on public.descargas_de_datos
  for each statement execute function public.descargas_de_datos_no_se_tocan();

commit;
