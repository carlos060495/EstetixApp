-- Paso 16a: persistir el plan elegido en signup hasta confirmar email y
-- completar Stripe Checkout del trial.

alter table public.salons
  add column pending_plan plan_tipo;
