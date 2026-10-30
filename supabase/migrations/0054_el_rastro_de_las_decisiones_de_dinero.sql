-- 0054 · El rastro de las decisiones de dinero
--
-- Hasta hoy no quedaba rastro de quién decide sobre el dinero de una cuenta:
-- Stripe guarda que lo pidió nuestra clave de servidor, no una persona, y
-- nuestra base solo guarda el estado final. Una baja hecha desde el muro de
-- suspensión es exactamente la que alguien reclamará algún día.
--
-- ═══ QUÉ SE APUNTA ═══
--
-- Una fila por cada decisión de dinero sobre una cuenta: cancelar, reanudar,
-- cambiar de plan (subir o bajar, al momento o programado) y anular un cambio
-- programado. Son la misma clase de suceso y comparten tabla.
--
--   · quién   — `usuario_id` (el usuario de la sesión, `users.id`). NULL cuando
--               no lo pidió nadie de la cuenta desde nuestra app: llegó por
--               Stripe, y entonces no hay forma de saber qué persona fue.
--   · cuándo  — `ocurrido_at`.
--   · dónde   — `origen` (ver abajo).
--   · el motivo, si lo hay; el plan de antes y el de después cuando aplica; y
--     la huella en Stripe: `stripe_peticion` (el `req_…` de nuestra llamada,
--     el mismo que trae después el evento en `request.id`) o
--     `stripe_evento` (el `evt_…` cuando la fila la escribe el webhook).
--
-- ORIGEN. Lo que el backend sabe con certeza y nada más:
--   · 'app'                 — Ajustes → Suscripción.
--   · 'muro_suspension'     — la puerta del muro de suspensión.
--   · 'calendario'          — Stripe aplicando un cambio que programamos
--                             nosotros (la bajada llega al final del periodo).
--   · 'fuera_de_la_api'     — llegó por webhook sin `request.id`: el portal de
--                             clientes de Stripe (medido), o algo que Stripe
--                             hace solo.
--   · 'otra_api'            — llegó por webhook con `request.id` y sin nuestra
--                             firma: el panel de Stripe u otra integración.
-- El panel de Stripe NO se ha podido medir aparte (hace falta la sesión de
-- Carlos); si sus acciones no traen `request.id`, caerán en 'fuera_de_la_api'.
--
-- SIN IP, a propósito (Carlos, 28-09-2026). Es el único dato personal del
-- conjunto y abriría un plazo de conservación que hoy no se puede cumplir,
-- porque lo que borra solo todavía no existe. Quién, cuándo y desde dónde ya
-- contestan la pregunta.
--
-- ═══ NO SE PUEDE EDITAR NI BORRAR, Y LO IMPIDE LA BASE ═══
--
-- Un registro que nuestra propia app puede reescribir no es un registro. Tres
-- capas, de fuera adentro:
--
--   1. PERMISOS. Nadie tiene UPDATE, DELETE ni TRUNCATE. `service_role` —que es
--      el rol con el que escribe el backend— solo INSERT y SELECT;
--      `authenticated` y `anon`, nada. Se revoca explícitamente además de no
--      concederse, porque los privilegios por defecto de Supabase dan todo a
--      los roles de la API sobre las tablas nuevas de `public`.
--   2. RLS activada y sin ninguna política: la sesión de un salón no ve ni una
--      fila. Hoy lo lee solo el panel de plataforma, con `service_role`.
--   3. UN TRIGGER que revienta ante cualquier UPDATE o DELETE (y otro ante
--      TRUNCATE), para quien tuviera el permiso por otra vía. Solo el dueño de
--      la tabla podría quitarlo, y eso ya no es la aplicación.
--
-- Probado contra el Postgres local, como `service_role`: el INSERT pasa, el
-- UPDATE y el DELETE los rechaza el permiso (42501) y, con el permiso
-- concedido a propósito, los rechaza el trigger.
--
-- ═══ CUÁNDO SE ESCRIBE ═══
--
-- DESPUÉS de que Stripe acepte, nunca antes: la misma lección que la alarma.
-- Una fila apuntada antes de la confirmación podría contar algo que no pasó.
--
-- ═══ LA REGLA DE LA COLUMNA OBLIGATORIA (supabase/README.md) ═══
--
-- Tabla nueva: nadie la escribe hasta el backend nuevo, así que sus `not null`
-- no rompen nada de lo desplegado.
--
-- ═══ ORDEN DE DESPLIEGUE: LA MIGRACIÓN VA ANTES DEL BACKEND ═══
--
--   1. supabase db push (0054)   2. backend   3. frontend
--
-- El backend nuevo escribe aquí en cada decisión y en el webhook. Sin la tabla,
-- esas escrituras fallarían (el webhook contestaría 500).
--
-- Vuelta atrás: `drop table public.decisiones_de_dinero;` — perdiendo el
-- rastro, que es justo lo que esta tabla existe para no perder.

begin;

create table public.decisiones_de_dinero (
  id bigint generated always as identity primary key,
  account_id uuid not null references public.accounts (id),
  salon_id uuid references public.salons (id),
  ocurrido_at timestamptz not null default now(),
  suceso text not null
    check (suceso in ('baja', 'reanudacion', 'cambio_de_plan', 'anula_cambio_programado')),
  origen text not null
    check (origen in ('app', 'muro_suspension', 'calendario', 'fuera_de_la_api', 'otra_api')),
  usuario_id uuid references public.users (id),
  motivo text,
  plan_antes text,
  plan_despues text,
  programado_para timestamptz,
  stripe_peticion text,
  stripe_evento text
);

comment on table public.decisiones_de_dinero is
  'Cada decisión de dinero sobre una cuenta (baja, reanudación, cambio de plan). '
  'No se puede editar ni borrar: lo impiden los permisos y un trigger (0054).';

create index decisiones_de_dinero_por_cuenta
  on public.decisiones_de_dinero (account_id, ocurrido_at);

-- Un mismo evento de Stripe no deja dos filas, aunque Stripe lo reintente.
create unique index decisiones_de_dinero_un_evento
  on public.decisiones_de_dinero (stripe_evento, suceso)
  where stripe_evento is not null;

-- ── 1. Permisos ───────────────────────────────────────────────────────────
revoke all on public.decisiones_de_dinero from anon, authenticated, service_role;
grant select, insert on public.decisiones_de_dinero to service_role;

-- ── 2. RLS sin políticas ──────────────────────────────────────────────────
alter table public.decisiones_de_dinero enable row level security;

-- ── 3. El trigger ─────────────────────────────────────────────────────────
create function public.decisiones_de_dinero_no_se_tocan()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  raise exception 'decisiones_de_dinero no se puede % : es un registro', tg_op
    using errcode = '42501';
end;
$$;

create trigger decisiones_de_dinero_sin_update_ni_delete
  before update or delete on public.decisiones_de_dinero
  for each row execute function public.decisiones_de_dinero_no_se_tocan();

create trigger decisiones_de_dinero_sin_truncate
  before truncate on public.decisiones_de_dinero
  for each statement execute function public.decisiones_de_dinero_no_se_tocan();

commit;
