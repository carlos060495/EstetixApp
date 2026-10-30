-- Agente de WhatsApp · Fase 1b — lo que el salón le dice al bot sobre sí mismo.
--
-- La base de conocimiento sale casi entera de tablas que YA existen (salons,
-- services, salon_schedule, salon_schedule_exceptions). Faltaban dos cosas que
-- no viven en ninguna: cómo debe SONAR el bot y qué políticas del salón debe
-- saber. Van aquí y no en `salons` porque son configuración del BOT, no del
-- negocio: si mañana se apaga el bot, estas dos columnas dejan de significar
-- nada, mientras que la dirección o el teléfono siguen siendo del salón.
--
-- Las dos son NULL por defecto a propósito: un salón que no las rellena tiene
-- un bot que funciona igual, con un tono neutro por defecto y sin políticas que
-- contar. Rellenarlas es una mejora, no un requisito para arrancar.

alter table public.salon_whatsapp_config
  -- Cómo habla: «cercano y con tuteo», «formal, de usted», «divertido»… Va en
  -- texto libre y no en un enum porque quien lo escribe es el dueño del salón
  -- describiendo su marca, y cualquier lista cerrada se le quedaría corta.
  add column bot_tono text,

  -- Políticas y datos que el bot debe saber y que no salen de ninguna tabla:
  -- cancelaciones, aparcamiento, formas de pago, si hace falta seña… Es lo que
  -- el dueño contestaría por WhatsApp diez veces al día.
  --
  -- OJO: este texto ENTRA EN EL PROMPT del modelo. Lo escribe el dueño del
  -- salón —que es quien manda sobre su propio bot—, así que no se trata como
  -- entrada hostil; lo que sí se trata así es el mensaje del cliente, que va
  -- envuelto en delimitadores (ver app/core/bot_prompt.py).
  add column bot_politicas text;

-- Tope de tamaño: la base de conocimiento se manda en CADA mensaje, así que un
-- texto sin límite es una factura sin límite. 2000 caracteres son de sobra para
-- las políticas de un salón y acotan el coste por conversación.
alter table public.salon_whatsapp_config
  add constraint salon_whatsapp_config_bot_tono_breve check (length(bot_tono) <= 200),
  add constraint salon_whatsapp_config_bot_politicas_breve check (length(bot_politicas) <= 2000);

-- Sin cambios de RLS ni de grants: la tabla ya los tiene de la 0024, y añadir
-- columnas no los altera. La política de escritura sigue siendo solo del admin,
-- que es lo correcto: el tono del bot es una decisión de marca.
