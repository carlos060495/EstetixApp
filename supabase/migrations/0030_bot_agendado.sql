-- Agente de WhatsApp · Fase 2 — agendado real de citas.
--
-- Cuatro cosas, y ninguna crea tabla nueva:
--   1. El CANDADO de concurrencia sobre `appointments` (lo más importante).
--   2. `appointments.bot_session_id`  de qué conversación salió la cita.
--   3. `bot_sessions.propuesta_pendiente`  la cita resumida a la espera del «sí».
--   4. `clients.telefono_digitos`  para reconciliar el CRM con lo que manda Meta.
--
-- Al no haber tablas nuevas no hacen falta grants: las cuatro tablas ya los
-- tienen de sus migraciones (0002 y 0024), y una columna nueva hereda el grant
-- de su tabla. Las políticas RLS existentes tampoco cambian: siguen filtrando
-- por `salon_id`, que es lo que aísla al inquilino.

-- ---------------------------------------------------------------------------
-- 0. Comprobación previa: ¿hay ya citas solapadas?
-- ---------------------------------------------------------------------------
-- Un EXCLUDE no admite NOT VALID: o los datos están limpios o la migración
-- falla. Y falla con un mensaje de Postgres que solo nombra UNA fila, lo que
-- deja al operador adivinando cuántas más hay detrás.
--
-- Así que se mira antes y se aborta con la lista entera. Es a propósito que
-- esto NO arregle nada por su cuenta: dos citas solapadas son dos clientas
-- citadas a la vez con la misma persona, y eso lo resuelve el salón decidiendo
-- cuál mueve, no una migración borrando filas.
do $$
declare
  solapes text;
  cuantos int;
begin
  select count(*), string_agg(
           format('  · empleado %s: %s (%s → %s) choca con %s (%s → %s)',
                  a.employee_id, a.id, a.inicio, a.fin, b.id, b.inicio, b.fin),
           e'\n' order by a.inicio)
    into cuantos, solapes
    from public.appointments a
    join public.appointments b
      on a.employee_id = b.employee_id
     and a.id < b.id
     and tstzrange(a.inicio, a.fin) && tstzrange(b.inicio, b.fin)
   where a.employee_id is not null
     and a.estado not in ('cancelada', 'no_show')
     and b.estado not in ('cancelada', 'no_show');

  if cuantos > 0 then
    raise exception e'Hay % pares de citas solapadas; el candado no se puede activar hasta resolverlos:\n%', cuantos, solapes
      using hint = 'Mueve o cancela una de cada par desde la agenda y vuelve a aplicar la migración.';
  end if;
end $$;

-- ---------------------------------------------------------------------------
-- 1. El candado: un empleado no puede estar en dos citas a la vez
-- ---------------------------------------------------------------------------
-- POR QUÉ EN LA BASE DE DATOS Y NO EN EL CÓDIGO: `_assert_no_overlap` (hoy en
-- routers/appointments.py) consulta y LUEGO inserta. Entre las dos cosas cabe
-- otra petición entera, así que dos clientes que piden el mismo hueco a la vez
-- pasan los dos la comprobación y se agendan los dos. Con el bot contestando
-- por WhatsApp eso deja de ser teórico: dos personas escribiendo a la vez es
-- el caso normal, no la excepción.
--
-- La comprobación en Python NO se quita: sigue siendo la que produce el
-- mensaje bonito («El empleado ya tiene una cita en ese horario») en el 99,9 %
-- de los casos. Esto es la red que cierra el 0,1 % restante, y es la única que
-- de verdad no se puede colar.
--
-- `btree_gist` (para poder mezclar el `=` de un uuid con el `&&` de un rango en
-- el mismo índice) ya lo instaló la migración 0023.
--
-- El WHERE deja fuera dos casos a propósito:
--   · `employee_id is null` — una cita sin empleado asignado no ocupa a nadie.
--   · canceladas y no_show — no ocupan hueco, exactamente el mismo criterio que
--     ya usan `_assert_no_overlap` y la rejilla de la agenda. Sin esto, cancelar
--     una cita no liberaría la hora.
alter table public.appointments
  add constraint appointments_sin_solape
  exclude using gist (
    employee_id with =,
    tstzrange(inicio, fin) with &&
  )
  where (employee_id is not null and estado not in ('cancelada', 'no_show'));

-- ---------------------------------------------------------------------------
-- 2. De qué conversación salió la cita
-- ---------------------------------------------------------------------------
-- El ORIGEN de la cita no va en `canal`: ahí ya está 'whatsapp', pero ese valor
-- también lo usa la recepcionista cuando apunta a mano una reserva que le
-- llegó por WhatsApp. Son dos cosas distintas y el salón necesita
-- distinguirlas.
--
-- Con la sesión enlazada se sabe tres cosas de una vez: que la creó el bot (no
-- es null), de qué conversación salió (para leerla) y con qué cliente (para la
-- fase de cancelar/reprogramar, en la que el bot tendrá que saber de qué cita
-- le están hablando). Un booleano `creada_por_bot` solo habría dado la primera.
--
-- `on delete set null` y no `cascade`: si algún día se purga una conversación
-- antigua, la CITA no se puede ir con ella. Perder una cita agendada por borrar
-- un historial de chat sería un desastre; perder la traza es solo una lástima.
alter table public.appointments
  add column bot_session_id uuid references public.bot_sessions(id) on delete set null;

create index on public.appointments (bot_session_id);

-- ---------------------------------------------------------------------------
-- 3. La propuesta a la espera de confirmación
-- ---------------------------------------------------------------------------
-- El bot NO crea la cita hasta que el cliente confirma un resumen. Entre el
-- resumen y el «sí» pasa al menos un mensaje de WhatsApp —o diez minutos—, así
-- que la propuesta tiene que sobrevivir fuera del proceso.
--
-- POR QUÉ AQUÍ Y NO EN EL HISTORIAL DE LA CONVERSACIÓN: el historial es texto
-- que se le pasa al modelo, y el modelo podría leerlo mal, resumirlo o
-- inventarse un detalle al releerlo. Esto es la propuesta con sus ids ya
-- resueltos y validados por NOSOTROS. Al confirmar, el servidor lee de aquí y
-- no de lo que diga el modelo: es lo que hace imposible que una cita se cree
-- con datos alucinados.
--
-- jsonb y no columnas sueltas porque es un objeto efímero de una sola pieza
-- —se escribe entero, se lee entero y se borra entero— y porque su forma va a
-- cambiar cuando lleguen cancelar y reprogramar. Su contrato vive en
-- app/core/bot_herramientas.py, que es quien lo escribe y quien lo lee.
alter table public.bot_sessions
  add column propuesta_pendiente jsonb;

-- ---------------------------------------------------------------------------
-- 4. Teléfono comparable
-- ---------------------------------------------------------------------------
-- EL PROBLEMA: el CRM guarda el teléfono tal y como lo teclea una persona
-- («600 11 22 33», «+34 600 112 233», «600112233») y Meta lo manda en E.164 sin
-- el más («34600112233»). Buscar por igualdad no encuentra nada, y no encontrar
-- al cliente significa DUPLICAR su ficha en cada reserva del bot —y, en la fase
-- de cancelar, no encontrar sus citas—.
--
-- SE LLAMA `telefono_digitos` Y NO `telefono_e164` A PROPÓSITO: quitar los no
-- dígitos no produce un E.164. «600112233» sigue sin prefijo de país después de
-- pasar por aquí. Ponerle ese nombre sería mentir sobre lo que contiene y
-- llevaría a comparar contra él dando por hecho que ya está normalizado, que es
-- exactamente el error que esta columna existe para no cometer.
--
-- Lo que hace esta columna es una sola cosa: quitar el RUIDO de formato
-- (espacios, puntos, guiones, paréntesis, el más) para que quede algo estable
-- contra lo que comparar. La reconciliación de verdad —saber que «600112233» y
-- «34600112233» son el mismo número— se hace en Python con la librería
-- `phonenumbers`, que sabe de prefijos de país y de prefijos troncales, y que
-- genera las variantes que se buscan contra esta columna. Ver
-- app/core/telefonos.py. Aquí no se asume ningún país.
--
-- `generated always as … stored`: se mantiene sola. Si se calculara en la
-- aplicación habría que acordarse de recalcularla en cada sitio que escriba un
-- teléfono (alta de cliente, edición, importación) y bastaría olvidarlo en uno
-- para que ese cliente dejara de ser localizable. `regexp_replace` es
-- inmutable, que es lo que exige una columna generada.
alter table public.clients
  add column telefono_digitos text
  generated always as (
    nullif(regexp_replace(coalesce(telefono, ''), '\D', '', 'g'), '')
  ) stored;

-- Se busca SIEMPRE acotando por salón (la RLS lo exige igualmente), así que el
-- índice lleva el salon_id delante.
create index on public.clients (salon_id, telefono_digitos);
