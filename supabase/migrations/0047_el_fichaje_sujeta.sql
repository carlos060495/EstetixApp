-- 0047 · El registro de fichajes vuelve a probar algo
--
-- Tres agujeros medidos en local el 15-09-2026, que juntos hacían que el
-- registro de fichajes —con el que se paga la nómina— no probara nada:
--
--   1. EL LÍMITE DE INTENTOS DEL QUIOSCO NO AGUANTABA RÁFAGAS. El backend hacía
--      «leer si hay bloqueo, comprobar el PIN, apuntar el fallo»: tres ráfagas
--      de 12 PINES malos comprobaron 36 y dejaron el contador en 3, y una cuarta
--      con el PIN bueno entre once malos fichó de verdad.
--   2. LA SESIÓN PODÍA LEER LOS HASHES de los PIN (`employees.pin_fichaje_hash`,
--      y el antiguo `users.pin_fichaje_hash`): quien tenga el dispositivo del
--      mostrador se los lleva y prueba las diez mil combinaciones en su casa.
--   3. LA SESIÓN PODÍA ESCRIBIR FICHAJES directamente en `time_entries`, sin
--      quiosco y sin PIN (medido: 201 con la entrada de otra persona).
--
-- ═══ ORDEN DE DESPLIEGUE: ESTA MIGRACIÓN VA DESPUÉS DEL BACKEND ═══
--
-- Al revés que casi siempre, y a propósito. Aquí no se AÑADE nada que el
-- backend nuevo necesite: se QUITAN permisos que el backend ANTERIOR usa. El
-- backend anterior pide `pin_fichaje_hash` con la sesión en el listado de
-- empleados; con esta migración puesta, ese listado —y con él la agenda—
-- respondería 403 hasta que llegara el backend nuevo.
--
-- El backend nuevo funciona CON y SIN esta migración: lee `tiene_pin` y
-- escribe el hash con service_role, pide columnas explícitas a `employees`, y
-- si la función de intentos todavía no existe usa el camino antiguo.
--
-- MARCHA ATRÁS del backend con esta migración puesta: primero se devuelven los
-- permisos (bloque «Para deshacer» al final), luego se revierte el backend.
--
-- ═══ LA REGLA DE LA COLUMNA OBLIGATORIA (supabase/README.md) ═══
--
-- No se añade ninguna columna.
--
-- ═══ UNA COLUMNA NUEVA EN `employees` O `users` NECESITA SU GRANT ═══
--
-- Desde aquí la sesión ve esas dos tablas COLUMNA A COLUMNA. Una columna que
-- se añada después nace invisible para la sesión, y cualquier `select` que la
-- pida responde 403. Hay que darle `grant select (columna)` —y `insert`/`update`
-- si la sesión la escribe— en la misma migración que la crea. Lo vigila
-- `backend/tests/test_fichaje_sujeta.py`.

begin;

-- ═══════════════════════ 1. contar el intento del quiosco ANTES de comprobarlo

-- La misma forma que `pin_gestion_intento` (0046), que ya se midió contra una
-- ráfaga: se apunta el intento con la fila bloqueada y después se comprueba el
-- PIN. Si es correcto, el backend borra la fila.
--
-- LA ESCALADA, CON TOPE EN 15 MINUTOS: cada cinco fallos, espera de 1, 5 y 15
-- minutos, y a partir de ahí se queda en 15. Antes eran cinco fallos y un
-- minuto, sin más: a cinco por minuto, las diez mil combinaciones de una
-- compañera se sacan en una noche.
--
-- POR QUÉ 15 Y NO MÁS (Carlos, 16-09-2026). Con 5 intentos cada 15 minutos,
-- probar las diez mil combinaciones lleva unas 500 horas: el ataque ya es
-- inviable sin llegar a una hora. Y el tope cambia el otro lado: quien bloquee a
-- una compañera tecleando mal su PIN a propósito le cuesta un cuarto de hora, no
-- media mañana. Cuando el ataque ya es imposible, alargar la espera solo castiga
-- a quien lo sufre. (El PIN de gestión, 0046, sigue subiendo a 60.)
--
-- La salida de la espera es la del administrador: al ponerle o generarle un PIN
-- nuevo, la espera se borra (routers/employees.py).
create or replace function public.pin_fichaje_intento(p_salon_id uuid, p_employee_id uuid)
returns table (permitido boolean, bloqueado_hasta timestamptz, fallos integer)
language plpgsql
set search_path = public, pg_temp
as $$
declare
  v_fila public.pin_attempt_locks%rowtype;
  v_ahora timestamptz := now();
  v_cuenta integer;
  v_hasta timestamptz;
begin
  insert into public.pin_attempt_locks (salon_id, employee_id, failed_count)
  values (p_salon_id, p_employee_id, 0)
  on conflict (salon_id, employee_id) where employee_id is not null do nothing;

  select * into v_fila
  from public.pin_attempt_locks
  where salon_id = p_salon_id and employee_id = p_employee_id
  for update;

  if v_fila.locked_until is not null and v_fila.locked_until > v_ahora then
    return query select false, v_fila.locked_until, v_fila.failed_count;
    return;
  end if;

  v_cuenta := v_fila.failed_count;
  if v_fila.last_failed_at is not null and v_fila.last_failed_at < v_ahora - interval '24 hours' then
    v_cuenta := 0;
  end if;
  v_cuenta := v_cuenta + 1;

  v_hasta := null;
  if v_cuenta % 5 = 0 then
    v_hasta := v_ahora + make_interval(mins => case v_cuenta / 5
      when 1 then 1
      when 2 then 5
      else 15
    end);
  end if;

  update public.pin_attempt_locks
  set failed_count = v_cuenta, last_failed_at = v_ahora, locked_until = v_hasta
  where salon_id = p_salon_id and employee_id = p_employee_id;

  return query select true, v_hasta, v_cuenta;
end;
$$;

comment on function public.pin_fichaje_intento(uuid, uuid) is
  'Apunta un intento del PIN del quiosco ANTES de comprobarlo. permitido=false: '
  'la ficha está en espera hasta bloqueado_hasta. Solo service_role.';

-- Si la pudiera llamar la sesión, desde el navegador se dejaría a cualquier
-- compañera sin poder fichar a base de intentos falsos.
revoke all on function public.pin_fichaje_intento(uuid, uuid) from public, anon, authenticated;
grant execute on function public.pin_fichaje_intento(uuid, uuid) to service_role;

-- ═══════════════════════════════════════ 2. la sesión LEE fichajes, no los escribe

-- Quien escribe fichajes es el quiosco, y lo hace con service_role después de
-- comprobar el PIN (`routers/fichaje.py`). Nada del backend los escribe con la
-- sesión: se buscó, y la única lectura es el historial de Empleados, que sigue.
--
-- NO HAY CORRECCIÓN MANUAL DE FICHAJES, ni antes ni ahora. Anotado y aprobado
-- como forma, sin construir (Carlos, 16-09-2026): cuando haga falta será un
-- endpoint de administración que escribe con service_role, con MOTIVO
-- OBLIGATORIO, y que guarda en una tabla propia —que nadie puede editar ni
-- borrar— quién cambió qué fichaje, cuándo, el valor anterior y el nuevo. Es un
-- registro de nómina: corregirlo tiene que dejar rastro. Nunca volver a abrir
-- esta tabla a la sesión.
drop policy if exists "tenant_isolation" on public.time_entries;

create policy "time_entries_select" on public.time_entries
  as permissive for select
  to public
  using (salon_id = any (public.salones_visibles()));

revoke insert, update, delete, truncate on public.time_entries from anon, authenticated;

-- ═══════════════════════════════ 3. los hashes, fuera del alcance de la sesión

-- La RLS decide QUÉ FILAS; no sabe esconder una COLUMNA. Eso lo hacen los
-- permisos por columna: se retira el permiso de tabla y se devuelve columna a
-- columna, todas menos el hash. Medido antes de escribirlo: un `select=*`, un
-- filtro por la columna o un `insert`/`update` que devuelva la fila entera
-- responden 403; con la lista de columnas, funcionan. Por eso el backend nuevo
-- pide siempre columnas explícitas.
--
-- Los triggers de la 0044 que copian el PIN entre las fichas de una persona
-- son SECURITY DEFINER: siguen funcionando.

revoke select, insert, update on public.employees from anon, authenticated;

grant select (
  id, salon_id, user_id, nombre, comision_tipo, comision_servicios, comision_productos,
  estado, telefono, email, puesto_id, persona_id
) on public.employees to authenticated;

grant insert (
  id, salon_id, user_id, nombre, comision_tipo, comision_servicios, comision_productos,
  estado, telefono, email, puesto_id, persona_id
) on public.employees to authenticated;

grant update (
  salon_id, user_id, nombre, comision_tipo, comision_servicios, comision_productos,
  estado, telefono, email, puesto_id, persona_id
) on public.employees to authenticated;

-- `users.pin_fichaje_hash` es de antes de la 0027 y ya no lo usa nadie, pero
-- sigue teniendo hashes y la sesión de administrador los podía leer.
revoke select, insert, update on public.users from anon, authenticated;

grant select (
  id, salon_id, nombre, email, rol, auth_user_id, created_at, account_id, ultimo_centro_id
) on public.users to authenticated;

grant insert (
  id, salon_id, nombre, email, rol, auth_user_id, created_at, account_id, ultimo_centro_id
) on public.users to authenticated;

grant update (
  salon_id, nombre, email, rol, auth_user_id, account_id, ultimo_centro_id
) on public.users to authenticated;

commit;

-- ═══ Para deshacer los permisos (solo si hay que revertir el backend) ═══
--
--   grant select, insert, update on public.employees to authenticated;
--   grant select, insert, update on public.users to authenticated;
--   grant insert, update, delete on public.time_entries to authenticated;
--   create policy "tenant_isolation" on public.time_entries as permissive for all
--     to public using (salon_id = any (public.salones_visibles()))
--     with check (salon_id = any (public.salones_visibles()));
