-- Agente de WhatsApp · Transparencia de IA (Reglamento (UE) 2024/1689, art. 50).
--
-- El art. 50.1 obliga a que una persona que interactúa con un sistema de IA lo
-- SEPA. El aviso va una vez por conversación: repetirlo en cada mensaje no
-- informa mejor —se convierte en ruido que se deja de leer— y el artículo pide
-- que la información se dé «de manera clara y distinguible», no constante.
--
-- Se guarda como columna y no se deduce mirando el historial por dos motivos:
--   1. Si se dedujera de «¿hay algún mensaje del bot en esta sesión?», una
--      conversación atendida por una persona (is_paused) y reanudada después
--      por el bot se quedaría SIN aviso, que es justo el caso en que más falta
--      hace: el cliente venía hablando con alguien de carne y hueso.
--   2. La fila de la sesión ya se lee en cada mensaje, así que comprobarlo no
--      cuesta una consulta más.
--
-- El aviso se marca cuando SE HA ENVIADO de verdad, no cuando se decide
-- mandarlo: si la Cloud API rechaza el envío, el cliente no lo ha visto y en el
-- siguiente mensaje hay que volver a intentarlo.
alter table public.bot_sessions
  add column aviso_ia_enviado boolean not null default false;

-- Las conversaciones que ya existían se quedan en `false` por el default: la
-- próxima vez que el bot conteste en ellas, avisará. Es lo correcto —nadie las
-- ha avisado todavía— y no hace falta backfill.

-- Sin cambios de RLS ni de grants: la tabla ya los tiene de la 0024, y añadir
-- una columna no los altera.
