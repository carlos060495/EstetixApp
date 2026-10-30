-- 0038 · Cuántas veces se ha intentado mandar un recordatorio
--
-- Existe por una sola decisión: cuándo dejar de reintentar. Desde que un fallo
-- SEGURO suelta el sello y la pasada siguiente vuelve a intentarlo, hace falta
-- un tope — si no, un correo que no existe se reintentaría cada 15 minutos
-- hasta la hora de la cita, gastando llamadas y ensuciando el vigía.
--
-- POR QUÉ UNA COLUMNA Y NO DEDUCIRLO DEL TEXTO DEL ERROR: porque el texto es
-- para leerlo, no para contarlo. El día que alguien cambie la redacción no
-- puede cambiar cuántas veces se reintenta un envío.
--
-- Se lee en la misma consulta que ya trae la cita, así que contar no cuesta un
-- viaje más.
alter table public.appointments
  add column recordatorio_intentos int not null default 0;

comment on column public.appointments.recordatorio_intentos is
  'Envíos de recordatorio intentados. Solo crece con fallos SEGUROS (los que '
  'sabemos que no salieron); un fallo ambiguo deja el sello puesto y no se '
  'reintenta. Ver app/tareas/recordatorios.MAX_INTENTOS_ENVIO.';
