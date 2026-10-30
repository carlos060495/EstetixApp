-- Paso 14a: Stripe (modo TEST). El plan del salón es fuente de verdad DESDE
-- Stripe: SOLO el webhook (service role) escribe salons.plan y subscriptions.
-- La RLS de subscriptions (0003) ya es SELECT admin-only y SIN política de
-- escritura, así que ningún JWT de tenant puede alterar su plan.

-- subscriptions: alinear con el modelo de Stripe.
alter table public.subscriptions
  rename column periodo_fin to current_period_end;

alter table public.subscriptions
  add column stripe_customer_id text,
  add column intervalo text,
  add column cancel_at_period_end boolean not null default false;

-- Estados que reporta Stripe: active/trialing/past_due/canceled/unpaid/...
alter table public.subscriptions
  alter column estado set default 'active';

-- Un salón tiene como mucho UNA suscripción (el webhook la actualiza in-place).
alter table public.subscriptions
  add constraint subscriptions_salon_id_key unique (salon_id);

-- Idempotencia de webhooks: cada event.id se procesa una sola vez.
create table public.stripe_events (
  event_id text primary key,
  processed_at timestamptz not null default now()
);
-- RLS ON SIN políticas: solo el service role (secret key) lee/escribe.
alter table public.stripe_events enable row level security;
