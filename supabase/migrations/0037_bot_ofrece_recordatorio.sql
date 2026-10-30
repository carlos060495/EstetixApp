-- 0037 · El bot de WhatsApp ofrece el recordatorio por email
--
-- El widget público ya pide el correo cuando el salón tiene los recordatorios
-- encendidos; el bot no lo pedía, así que a quien reservaba por WhatsApp no se
-- le podía avisar nunca. Esta migración añade LO ÚNICO que ese ofrecimiento
-- necesita guardar y que no se puede deducir de lo que ya hay: que alguien ha
-- dicho que no.
--
-- POR QUÉ HACE FALTA UNA COLUMNA NUEVA Y NO VALE `recordatorios_baja_at`.
-- Se parecen y no son lo mismo:
--
--   · `recordatorios_baja_at` lo pone el CLIENTE desde el pie de un correo que
--     ya ha recibido. Dice «no me mandes esto». Tiene peso: mientras esté
--     puesta, el cron no le escribe pase lo que pase.
--   · Esta dice «no te doy mi correo», y solo apaga la PREGUNTA del bot.
--
-- Meter las dos en la misma columna tendría una consecuencia muy fea y muy
-- silenciosa: un «no, gracias» de pasada por WhatsApp dejaría a esa persona
-- marcada como dada de baja, y el día que el salón le apuntara el correo a mano
-- desde el panel —una acción legítima, hecha con la persona delante— no le
-- llegaría ningún recordatorio y nadie sabría por qué. Un no a una pregunta no
-- puede valer como una baja que esa persona no ha pedido.
--
-- TIMESTAMP Y NO BOOLEANO, aunque solo se lea como «hay algo / no hay nada»:
-- cuesta lo mismo, tiene la forma de su columna hermana de al lado y contesta
-- «¿cuándo dijo que no?», que es la pregunta que se hace el día que se cambie
-- cómo se ofrece y haya que decidir a quién se le vuelve a preguntar.
alter table public.clients
  add column recordatorios_email_rechazado_at timestamptz;

comment on column public.clients.recordatorios_email_rechazado_at is
  'Cuándo dijo el cliente que NO quiere dar su correo para los recordatorios. '
  'Apaga solo la PREGUNTA del bot; no es una baja. La baja es recordatorios_baja_at.';

-- SIN ÍNDICE, a propósito. Solo se lee por `id` de una ficha concreta, en el
-- momento de confirmar una cita: nunca se busca «todos los que dijeron que no».
-- Un índice aquí sería peso en cada escritura de `clients` a cambio de nada.
