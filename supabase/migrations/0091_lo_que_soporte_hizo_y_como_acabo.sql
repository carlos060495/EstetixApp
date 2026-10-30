-- 0091 · LO QUE SOPORTE HIZO, Y CÓMO ACABÓ (Carlos, 20-10-2026).
--
-- ═══ EL RESULTADO ═══
--
-- Desde esta entrega lo que hace soporte se apunta ANTES de hacerlo, y sin su
-- fila no se hace. Esa fila no puede saber cómo acabó, y `admin_logs` no admite
-- UPDATE (0004). El resultado va en una fila aparte, UNA por apunte, que
-- tampoco se puede cambiar ni borrar: completar es insertarla una vez. Si
-- insertarla falla, el apunte se queda sin resultado y así se lee: «se intentó,
-- no sabemos cómo acabó». La acción no depende de que se pueda escribir.
--
--   · Acciones: `{"status": 201}`.
--   · Entrada de soporte: `{"aviso": "entregado" | "sin_destinatario" | <error>}`,
--     el correo al dueño, que sale DESPUÉS de que la entrada esté apuntada.
--
-- ═══ LAS LECTURAS, AGRUPADAS AL MOSTRARLAS ═══
--
-- Cada lectura de datos de personas sigue siendo su propia fila (y sin ella no
-- se lee). `admin_logs_vista` las junta por sesión y ruta para leerlas: una fila
-- con `veces` y la hora de la primera y de la última. No se pierde ninguna: si
-- soporte leyó clientas cuarenta veces, `veces` es 40. Lo demás pasa tal cual,
-- con su resultado al lado.
--
-- Solo `service_role` (el panel). Ni la tabla ni la vista llevan `salon_id` de
-- inquilino: son del panel, como `admin_logs`.
--
-- ═══ ORDEN DE DESPLIEGUE: 0091 → backend → frontend ═══
--
-- Con la 0091 y el backend viejo no pasa nada: nadie escribe la tabla y la
-- vista enseña lo mismo que la tabla, con las lecturas juntas. El backend nuevo
-- sin la 0091 no puede: completar fallaría (se traga) y el panel leería una
-- vista que no existe.
--
-- Para deshacer (primero el backend anterior): drop view; drop table.

create table public.admin_logs_resultado (
  admin_log_id uuid primary key references public.admin_logs(id),
  detalle jsonb not null,
  el timestamptz not null default now()
);

comment on table public.admin_logs_resultado is
  'Cómo acabó lo apuntado en admin_logs (0091). Una por apunte; solo se añade. Sin fila: no se sabe.';

create or replace function public.admin_logs_resultado_sin_cambios()
returns trigger
language plpgsql
as $$
begin
  raise exception 'admin_logs_resultado is append-only: % is not allowed', tg_op;
end;
$$;

create trigger admin_logs_resultado_no_update
  before update on public.admin_logs_resultado
  for each row execute function public.admin_logs_resultado_sin_cambios();

create trigger admin_logs_resultado_no_delete
  before delete on public.admin_logs_resultado
  for each row execute function public.admin_logs_resultado_sin_cambios();

alter table public.admin_logs_resultado enable row level security;
revoke all on public.admin_logs_resultado from public, anon, authenticated;
grant select, insert on public.admin_logs_resultado to service_role;

create view public.admin_logs_vista with (security_invoker = true) as
  select
    l.id,
    l.admin_id,
    l.accion,
    l.target_salon_id,
    l.detalle,
    l.ip,
    l.timestamp,
    l.timestamp as ultima,
    1::bigint as veces,
    r.detalle as resultado
  from public.admin_logs l
  left join public.admin_logs_resultado r on r.admin_log_id = l.id
  where l.accion <> 'impersonation_read'
  union all
  select
    (array_agg(l.id order by l.timestamp))[1],
    l.admin_id,
    'impersonation_read',
    l.target_salon_id,
    (array_agg(l.detalle order by l.timestamp))[1],
    (array_agg(l.ip order by l.timestamp desc))[1],
    min(l.timestamp),
    max(l.timestamp),
    count(*),
    null::jsonb
  from public.admin_logs l
  where l.accion = 'impersonation_read'
  group by l.admin_id, l.target_salon_id, l.detalle ->> 'sid', l.detalle ->> 'path';

comment on view public.admin_logs_vista is
  'admin_logs para leer (0091): las lecturas de soporte juntas por sesión y ruta (veces, primera, última); lo demás con su resultado.';

revoke all on public.admin_logs_vista from public, anon, authenticated;
grant select on public.admin_logs_vista to service_role;
