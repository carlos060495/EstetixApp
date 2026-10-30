-- 0062 · Dar de baja: la gente se va, no se borra
--
-- Hasta aquí «Eliminar» borraba la ficha y, con ella, en cascada, su registro
-- de jornada (`time_entries`) y sus ausencias; las citas que atendió se
-- quedaban sin profesional. Y si la ficha estaba unida a un usuario de la app,
-- ese usuario seguía pudiendo entrar: alguien a quien habían echado seguía
-- viendo la agenda, las clientas y sus teléfonos.
--
-- ═══ LO QUE CAMBIA ═══
--
--   1. Un estado nuevo, 'baja' («se fue»), al lado de 'pausa' («vuelve»). La
--      ficha se queda: sus fichajes, sus ausencias, sus correcciones y las
--      citas que atendió siguen apuntando a una ficha que existe, y su
--      historial se puede seguir abriendo para enseñárselo a ella o a la
--      Inspección. `baja_el` dice cuándo.
--
--   2. LA BASE SUJETA LA HISTORIA. `time_entries`, `employee_schedule_exceptions`
--      y `appointments` pasan de CASCADE / SET NULL a RESTRICT: borrar la ficha
--      de alguien con historia lo rechaza la base, venga de la app, del panel
--      de Supabase o de un script. El backend solo borra del todo a quien no
--      tiene nada; a los demás los da de baja. Las citas POR VENIR se quedan
--      sin profesional antes de dar de baja o de borrar (lo hace el backend,
--      después de que quien administra las haya visto), así que las únicas que
--      sujetan son las pasadas. `employee_schedule`, `employee_services` y
--      `pin_attempt_locks` siguen en cascada: son configuración, no historia.
--      El guarda `backend/tests/test_la_historia_no_se_borra.py` lee estas
--      claves del esquema y se pone rojo si una vuelve a borrar en cascada o
--      si aparece una clave nueva hacia `employees` sin clasificar.
--
--   3. DAR DE BAJA QUITA EL ACCESO, Y LO QUITA LA BASE (ver 4). Si la ficha está
--      unida a un usuario y a ese usuario no le queda ninguna otra ficha viva
--      en la cuenta, `users.acceso_retirado_el` se rellena y desde ese momento:
--        · `salones_visibles()` y `cuenta_actual()` no le devuelven nada, así
--          que la RLS no le enseña una sola fila aunque su token siga firmado;
--        · el backend lo trata como sesión revocada (401, «sesion_revocada»);
--        · el hook del token deja de ponerle el centro en los tokens nuevos.
--      El backend, además, lo banea en Auth para que no pueda volver a entrar
--      ni refrescar la sesión. Medido el 02-10-2026 contra GoTrue v2.192: el
--      baneo corta el login y el refresco, pero un token ya emitido sigue
--      siendo válido para GoTrue hasta que caduca (una hora). Por eso la
--      cerradura de verdad es ESTA columna, y el baneo es la segunda.
--      Si le queda otra ficha viva en otro centro, solo pierde ESTE centro: se
--      le quita de `user_salons` y, si era su centro de siempre, pasa a serlo
--      el de la otra ficha (sus tokens viejos dejan de valer y vuelve a entrar).
--
--   4. UN TRIGGER, NO UNA LLAMADA DESDE EL BACKEND. Igual que `user_salons`
--      (0039): un camino nuevo que dé de baja —un script, otra pantalla, el
--      panel de plataforma— no puede olvidarse de quitar el acceso, porque no
--      depende de acordarse. Lo mismo al borrar del todo una ficha con usuario.
--      A la administradora de la cuenta no se le quita el acceso así: el
--      trigger se niega (y el backend lo explica antes con palabras).
--
-- ═══ LA BAJA ES DE UNA SOLA DIRECCIÓN ═══
--
-- `baja_el` solo lo escribe `service_role` (la sesión no tiene permiso sobre
-- esa columna) y la restricción `employees_baja_coherente` ata el estado a la
-- columna. O sea que desde la app nadie puede dar de baja sin pasar por el
-- backend, ni sacar a alguien de la baja cambiándole el estado. Si vuelve a
-- trabajar aquí, se le hace una ficha nueva. Volver de la baja no entra en
-- esta entrega.
--
-- ═══ LA REGLA DE LA COLUMNA OBLIGATORIA (supabase/README.md) ═══
--
-- `employees.baja_el` y `users.acceso_retirado_el` son ANULABLES y sin valor
-- por defecto: NULL es «no se ha ido» y «tiene acceso», que es lo que inserta
-- el código anterior sin saberlo. La restricción nueva la cumple todo lo que
-- ya existe (nadie está en baja) y todo lo que inserta el backend anterior.
--
-- El valor 'baja' se añade al enum pero NO se usa como enum en esta migración:
-- Postgres deja ampliarlo dentro de la transacción y no usarlo hasta que
-- confirma. La restricción compara `estado::text`, y las funciones plpgsql no
-- resuelven el literal hasta que corren.
--
-- ═══ ORDEN DE DESPLIEGUE: 0062 → BACKEND → FRONTEND ═══
--
-- La migración deja la base funcionando con el backend que hay: no escribe
-- 'baja', así que no hay ninguna ficha en baja que su esquema no sepa leer.
-- Lo único que cambia para él es que borrar a alguien con historia ahora lo
-- rechaza la base (su DELETE contesta un error en vez de destruir el
-- registro de jornada), que es justo lo que se quiere.
-- El backend nuevo necesita la columna y el valor: va DESPUÉS.

alter type public.empleado_estado add value if not exists 'baja';

begin;

-- ═══════════════ 1. la baja en la ficha

alter table public.employees add column baja_el timestamptz;

comment on column public.employees.baja_el is
  'Cuándo se dio de baja («se fue»). NULL = activa o en pausa. Solo la escribe '
  'el backend con service_role; la ata a `estado` employees_baja_coherente.';

alter table public.employees add constraint employees_baja_coherente
  check ((estado::text = 'baja') = (baja_el is not null));

-- La sesión lee la columna (la lista enseña «se fue el …»), pero no la escribe.
grant select (baja_el) on public.employees to authenticated;

-- ═══════════════ 2. el acceso retirado en el usuario

alter table public.users add column acceso_retirado_el timestamptz;

comment on column public.users.acceso_retirado_el is
  'Cuándo se le quitó el acceso a la app al darle de baja. NULL = tiene acceso. '
  'Con valor, salones_visibles() y cuenta_actual() no le devuelven nada y el '
  'backend trata su sesión como revocada. Lo rellena el trigger de la baja.';

-- La sesión la LEE y no la escribe: que nadie pueda devolverse el acceso. Leerla
-- no enseña nada que la RLS no deje ver ya (solo usuarios de su cuenta), y sin el
-- grant cualquier `select` de `users` que la pidiera respondería 403 (lo vigila
-- tests/test_fichaje_sujeta.py, desde la 0047).
grant select (acceso_retirado_el) on public.users to authenticated;

-- ═══════════════ 3. la base sujeta la historia

alter table public.time_entries drop constraint time_entries_employee_id_fkey;
alter table public.time_entries add constraint time_entries_employee_id_fkey
  foreign key (employee_id) references public.employees(id) on delete restrict;

alter table public.employee_schedule_exceptions
  drop constraint employee_schedule_exceptions_employee_id_fkey;
alter table public.employee_schedule_exceptions
  add constraint employee_schedule_exceptions_employee_id_fkey
  foreign key (employee_id) references public.employees(id) on delete restrict;

alter table public.appointments drop constraint appointments_employee_id_fkey;
alter table public.appointments add constraint appointments_employee_id_fkey
  foreign key (employee_id) references public.employees(id) on delete restrict;

-- ═══════════════ 4. quitar el acceso lo hace la base

create or replace function public.retirar_el_acceso_de_la_ficha()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  ficha public.employees;
  usuario public.users;
  otra_salon uuid;
begin
  ficha := case when tg_op = 'DELETE' then old else new end;

  if tg_op = 'UPDATE' and not (new.estado::text = 'baja' and old.estado::text <> 'baja') then
    return new;
  end if;
  if ficha.user_id is null then
    return ficha;
  end if;

  select * into usuario from public.users where id = ficha.user_id;
  if not found then
    return ficha;
  end if;

  if usuario.rol::text = 'admin' then
    raise exception 'No se puede quitar el acceso a la administradora de la cuenta dándole de baja.'
      using errcode = 'P0001', hint = 'baja_de_administradora';
  end if;

  -- ¿Le queda otra ficha viva (activa o en pausa) en la cuenta?
  select e.salon_id into otra_salon
  from public.employees e
  where e.user_id = ficha.user_id
    and e.id <> ficha.id
    and e.estado::text <> 'baja'
  order by e.id  -- `employees` no tiene `created_at`: basta con que sea siempre la misma
  limit 1;

  if otra_salon is null then
    update public.users set acceso_retirado_el = now()
    where id = usuario.id and acceso_retirado_el is null;
  else
    if usuario.salon_id = ficha.salon_id then
      update public.users set salon_id = otra_salon where id = usuario.id;
    end if;
    delete from public.user_salons
    where user_id = usuario.id and salon_id = ficha.salon_id;
  end if;

  return ficha;
end;
$$;

revoke all on function public.retirar_el_acceso_de_la_ficha() from public, anon, authenticated;

create trigger employees_baja_retira_el_acceso
  after update of estado on public.employees
  for each row execute function public.retirar_el_acceso_de_la_ficha();

create trigger employees_borrado_retira_el_acceso
  before delete on public.employees
  for each row execute function public.retirar_el_acceso_de_la_ficha();

-- ═══════════════ 5. la frontera deja de verle

create or replace function public.salones_visibles()
returns uuid[]
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  -- SECURITY DEFINER NO ES OPCIONAL: ver 0039. Lo nuevo es
  -- `acceso_retirado_el is null` en las dos ramas: a quien se le quitó el
  -- acceso no le queda ningún centro, aunque su token siga firmado.
  select coalesce(
    array(
      select s.id from public.salons s
      join public.users u on u.account_id = s.account_id
      where u.auth_user_id = auth.uid() and u.rol = 'admin'
        and u.acceso_retirado_el is null
      union
      select us.salon_id from public.user_salons us
      join public.users u on u.id = us.user_id
      where u.auth_user_id = auth.uid()
        and u.acceso_retirado_el is null
    ),
    '{}'::uuid[]
  );
$$;

create or replace function public.cuenta_actual()
returns uuid
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select u.account_id from public.users u
  where u.auth_user_id = auth.uid()
    and u.acceso_retirado_el is null
  limit 1;
$$;

create or replace function public.custom_access_token_hook(event jsonb)
returns jsonb
language plpgsql
stable
as $$
declare
  claims jsonb;
  v_salon_id uuid;
  v_rol public.user_rol;
begin
  -- Sin centro en el token para quien se quedó sin acceso: el backend contesta
  -- «Token sin contexto de salón». Es la tercera cerradura; las otras dos son
  -- la frontera de arriba y el baneo en Auth.
  select salon_id, rol into v_salon_id, v_rol
  from public.users
  where auth_user_id = (event ->> 'user_id')::uuid
    and acceso_retirado_el is null
  limit 1;

  claims := event -> 'claims';

  if v_salon_id is not null then
    claims := jsonb_set(claims, '{salon_id}', to_jsonb(v_salon_id::text));
    claims := jsonb_set(claims, '{rol}', to_jsonb(v_rol::text));
  end if;

  event := jsonb_set(event, '{claims}', claims);
  return event;
end;
$$;

commit;
