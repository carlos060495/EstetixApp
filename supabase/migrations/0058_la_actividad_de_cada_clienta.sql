-- 0058 · La actividad de cada clienta, calculada de lo que pasó
--
-- «Visitas» y «Total gastado» eran columnas de `clients` (0002, `default 0`)
-- que la app LEÍA y que NO ESCRIBÍA NADIE: ni el backend, ni un trigger, ni la
-- importación (que las excluye a propósito). Enseñaban cero, o el número que
-- alguien puso a mano un día. Y el filtro «Dormidos» («5 visitas o menos»)
-- marcaba con eso a todo el mundo.
--
-- ═══ LO QUE HAY AHORA: UNA VISTA, NO UNA COPIA ═══
--
-- `clientas_con_actividad` calcula, CADA VEZ QUE SE LEE, de las citas:
--
--   · visitas        — citas de la clienta en estado 'completada'.
--   · total_gastado  — Σ del precio del servicio de esas citas.
--   · ultima_visita  — inicio de la última cita completada (NULL = ninguna).
--
-- No es una columna que un trigger mantenga al día: un dato con dos copias
-- acaba divergiendo (nos pasó con el CIF y con los embeds). Si una cita cambia
-- de estado, la vista ya lo dice en la siguiente lectura.
--
-- LO QUE NO PUEDE CONTAR, y queda dicho en el reporte:
--   · Las ventas de producto (`incomes`) no llevan clienta: no se le pueden
--     atribuir a nadie. «Total gastado» es lo gastado EN SERVICIOS.
--   · El precio es el ACTUAL del servicio: las citas no guardan a qué precio se
--     cobraron. Si un servicio sube, el gastado de antes sube con él.
--
-- `security_invoker = true`: la vista se evalúa con los permisos y la RLS de
-- quien la consulta, igual que `clients` y `appointments`. Una sesión de salón
-- ve sus clientas y nada más. Y solo lleva las columnas que la app lee: ni el
-- token de baja de los recordatorios ni el teléfono normalizado.
--
-- ═══ LA REGLA DE LA COLUMNA OBLIGATORIA (supabase/README.md) ═══
--
-- No añade columnas a ninguna tabla: no aplica.
--
-- ═══ ORDEN DE DESPLIEGUE: 0058 → BACKEND → FRONTEND → 0059 ═══
--
-- El backend nuevo lee la vista; sin ella, la lista de clientas daría error.
-- Las columnas viejas se borran DESPUÉS, en la 0059, cuando ya no las nombra
-- ningún código desplegado.

begin;

create view public.clientas_con_actividad
with (security_invoker = true) as
select
  c.id,
  c.salon_id,
  c.nombre,
  c.telefono,
  c.email,
  c.cumpleanos,
  c.alergias,
  c.notas,
  c.vip,
  c.created_at,
  c.recordatorios_baja_at,
  coalesce(a.visitas, 0)::integer as visitas,
  coalesce(a.gastado, 0)::numeric(10, 2) as total_gastado,
  a.ultima_visita
from public.clients c
left join lateral (
  select
    count(*) as visitas,
    sum(s.precio) as gastado,
    max(ap.inicio) as ultima_visita
  from public.appointments ap
  left join public.services s on s.id = ap.service_id
  where ap.client_id = c.id
    and ap.estado = 'completada'
) a on true;

comment on view public.clientas_con_actividad is
  'Cada clienta con sus visitas, lo gastado en servicios y su última visita, '
  'calculado de las citas completadas al leer (0058). No es una copia.';

-- Lo que la vista necesita para no recorrer todas las citas por cada clienta.
create index appointments_completadas_por_clienta
  on public.appointments (client_id, inicio)
  where estado = 'completada';

grant select on public.clientas_con_actividad to authenticated, service_role;

commit;
