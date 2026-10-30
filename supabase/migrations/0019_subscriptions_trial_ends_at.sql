-- Paso trial wall: guardar fin de prueba como fuente de verdad editable.
alter table public.subscriptions
  add column if not exists trial_ends_at timestamptz;
