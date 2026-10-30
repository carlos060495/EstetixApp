-- 0074 · Los cobros de los referidos, en Accounts v2.
--
-- Carlos, 05-10-2026: en producción Stripe rechazó crear la cuenta por v1
-- («Stripe no longer recommends Accounts v1 for new Connect integrations»), y
-- se decidió rehacerlo sobre Accounts v2 sin dejar el camino v1.
--
-- EL IDENTIFICADOR NO CAMBIA: una cuenta v2 sigue siendo `acct_…`, y
-- `referido_cobros.cuenta_stripe` (texto) la guarda igual. No hay nada que
-- migrar en él: en producción hay 0 cuentas (ninguna llegó a crearse).
--
-- LO QUE SE VA: `copia_cobros_activos`, la copia de `payouts_enabled`. Es de
-- v1, la cuenta v2 no lo trae, y además no decía si se le podía transferir: lo
-- dice el estado de `stripe_transfers`, que ya está en `copia_transferencias`.
--
-- LO QUE CAMBIA DE SIGNIFICADO, SIN TOCAR LA COLUMNA (0 filas en producción):
--   · `copia_pausado_por` — antes `requirements.disabled_reason` (v1); ahora el
--     `status_details[].code` de `stripe_transfers` (requirements_past_due,
--     unsupported_country…).
--   · `copia_pide` — antes los nombres de campo de v1; ahora la `description`
--     de cada requisito que espera al referido (`awaiting_action_from = user`).
--
-- COLUMNAS OBLIGATORIAS: no añade ninguna; solo quita una que admitía nulos.
--
-- ═══ ORDEN DE DESPLIEGUE: BACKEND → 0074 ═══
--
-- El backend de v1 ESCRIBE `copia_cobros_activos` (al crear la cuenta, al
-- volver de Stripe y en el webhook): esta va DESPUÉS del backend v2, que ya no
-- la nombra. Si se ejecuta dos veces, la segunda no hace nada (`if exists`).
--
-- Probado en local: el backend v2 contra esta base (suite completa y
-- `test_cobros_referido_base_real.py`, que recorre las columnas de verdad).

begin;

alter table public.referido_cobros drop column if exists copia_cobros_activos;

comment on column public.referido_cobros.copia_pausado_por is
  'Copia de configuration.recipient.capabilities.stripe_balance.stripe_transfers.status_details[0].code (Accounts v2). Solo para enseñar.';
comment on column public.referido_cobros.copia_pide is
  'Copia de requirements.entries[].description con awaiting_action_from = user (Accounts v2). Solo para enseñar.';

commit;
