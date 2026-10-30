-- 0072 · Cobrar con Stripe Connect: la cuenta de Stripe de cada referido.
--
-- Carlos, 05-10-2026: los referidos cobran por Stripe Connect. Stripe no deja
-- mandar dinero a un IBAN suelto, y guardarlo nosotros nos haría custodios de
-- datos bancarios para siempre. Con Connect el referido le da sus datos a
-- Stripe y nosotros no los vemos; la identidad y lo fiscal los verifica Stripe.
--
-- El IBAN (`referidos.datos_pago`) se va en la 0073, DESPUÉS del backend.
--
-- ═══ LO QUE ENTRA: `referido_cobros` ═══
--
--   · `cuenta_stripe`: el identificador de su cuenta de Connect (`acct_…`).
--     Ninguna ruta lo devuelve: solo lo usa el backend para hablar con Stripe.
--   · `copia_*`: una COPIA de lo último que dijo Stripe sobre esa cuenta, con
--     la fecha en que lo dijo (`copia_de`) y la fecha en que nos llegó
--     (`copia_recibida_at`). Sirve para ENSEÑAR en qué punto está. NO es la
--     verdad —la verdad está en Stripe y la copia puede estar vieja— y nada
--     decide con ella si se le puede pagar: eso se le pregunta a Stripe en el
--     momento (`core/cobros_referido.se_le_puede_pagar`). Lo vigila
--     `tests/test_cobros_referido.py`.
--   · NINGÚN DATO BANCARIO, ni los últimos dígitos: «cobras en la cuenta
--     terminada en ••34» se le pide a Stripe al pintar el portal. Lo vigila
--     `test_cobros_referido_base_real.py`, recorriendo las columnas de la base.
--
-- Una copia más vieja no pisa a una más nueva: el backend solo escribe si
-- `copia_de` es posterior a la que hay (los webhooks no llegan en orden).
--
-- ═══ ORDEN DE DESPLIEGUE: 0072 → BACKEND → 0073 → FRONTEND ═══
--
-- Partida en dos porque cada mitad necesita un orden distinto: esta crea lo
-- que el backend nuevo LEE (`referido_cobros`), así que va antes; la 0073 quita
-- lo que el backend viejo lee (`datos_pago`), así que va después. Solo añade:
-- una tabla nueva, sin columnas obligatorias en tablas que ya existen.

begin;

-- ═══════════════ 2. su cuenta de Stripe, y la copia de lo que dice Stripe

create table public.referido_cobros (
  referido_id uuid primary key references public.referidos (id) on delete cascade,
  cuenta_stripe text not null unique,
  creada_at timestamptz not null default now(),
  -- COPIA de lo último que dijo Stripe. Para enseñarlo, nunca para decidir.
  copia_transferencias text,          -- capabilities.transfers: active · inactive · pending
  copia_cobros_activos boolean,       -- payouts_enabled
  copia_pide text[] not null default '{}',  -- requirements.currently_due + past_due
  copia_pausado_por text,             -- requirements.disabled_reason
  copia_fecha_limite timestamptz,     -- requirements.current_deadline
  copia_de timestamptz,               -- cuándo lo dijo Stripe
  copia_recibida_at timestamptz       -- cuándo nos llegó
);

comment on table public.referido_cobros is
  'La cuenta de Stripe Connect de cada referido (0072) y una COPIA de su estado, con fecha. '
  'La copia es para enseñar; la decisión de pagar se le pregunta a Stripe. Sin datos bancarios.';

alter table public.referido_cobros enable row level security;
revoke all on public.referido_cobros from anon, authenticated, service_role;
grant select, insert, update, delete on public.referido_cobros to service_role;

commit;
