-- Reestructuración de precios — la moneda de cada suscripción.
--
-- Con EUR y USD en la tarifa, saber el plan ya no basta para saber cuánto paga
-- un salón: 39 EUR y 42 USD son el mismo plan Pro y dos importes distintos.
--
-- POR QUÉ HACE FALTA GUARDARLA Y NO DEDUCIRLA:
--
--   1. UNA SUSCRIPCIÓN DE STRIPE NO CAMBIA DE MONEDA. Al cambiar de plan hay
--      que pedirle a Stripe el price de LA MISMA moneda con la que se creó, o
--      rechaza la operación. El backend necesita saber cuál es sin preguntarle
--      al navegador: la cookie del visitante dice lo que quiere ver, no lo que
--      firmó en su día.
--   2. EL MRR. Sumar euros y dólares como si fueran lo mismo daría un número
--      que no significa nada. Con la moneda en la fila, la conversión se hace
--      con un tipo fijo (ver app/billing/precios.py) y el panel puede seguir
--      diciendo «estimado» con honestidad.
--
-- NULL = no se sabe, y es el valor de arranque para todas las filas que ya
-- existen. NO se rellena con 'eur' a lo bruto: aunque hoy todas lo sean, un
-- valor inventado se vuelve indistinguible de uno comprobado en cuanto pasa una
-- semana. El webhook lo va escribiendo con lo que diga Stripe en la primera
-- renovación o en el primer cambio de cada salón, y hasta entonces el código
-- trata el NULL como «asume euros» en UN solo sitio, documentado.
--
-- `text` y no un enum: el catálogo de monedas de Stripe tiene más de cien
-- entradas y no somos nosotros quien decide cuáles existen. El CHECK acota a
-- las dos que la aplicación sabe vender, que es donde sí mandamos.
alter table public.subscriptions
  add column moneda text
  check (moneda is null or moneda in ('eur', 'usd'));

-- Sin índice: `subscriptions` tiene `unique (salon_id)` y siempre se lee por
-- salón. La moneda se lee de una fila que ya se tiene en la mano.
