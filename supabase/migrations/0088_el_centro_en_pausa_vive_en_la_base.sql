-- 0088 · El centro en pausa vive en la base.
--
-- Carlos, 14-10-2026.
--
-- ═══ EL AGUJERO ═══
--
-- Al bajar a un plan con menos centros, el que sobra queda EN PAUSA. Esa pausa
-- solo la hacía cumplir el backend (`core/security.py`, `CORTES_EN_LA_BASE`
-- decía `centro_en_pausa: None`). La web no habla con Supabase —todo va por el
-- backend—, pero el navegador guarda el token de la titular, y con él cualquiera
-- puede hablarle a PostgREST directamente (corregido el 20-10-2026: antes este
-- párrafo decía que la web lo hacía). Por esa vía el centro en pausa seguía
-- abierto. Es el
-- mismo hueco que la 0086 cerró para el impago, en el único sitio donde
-- quedaba.
--
-- ═══ LO QUE CAMBIA ═══
--
--   · `centros_del_plan(plan)`: cuántos centros incluye cada plan. ESPEJO de
--     `CENTROS_POR_PLAN` (`billing/plans.py`); un guarda los compara. Un plan
--     que no conocemos devuelve NULL, y NULL no pausa nada: es la misma
--     dirección de fallo que `lo_que_incluye` («no se sabe» no es «no»).
--   · `centros_en_pausa(cuenta)`: los centros que el plan deja fuera, con EL
--     MISMO reparto que `lo_que_incluye`: primero el marcado al bajar
--     (`sigue_al_bajar`), después el más antiguo, a igualdad el id menor, y
--     nunca ninguno. Un guarda lo compara con Python caso por caso.
--   · `centros_que_trabajan()` deja FUERA los centros en pausa. Todas las
--     políticas que ya preguntan a esa función (0086) cortan solas: escribir
--     en un centro en pausa da 403, y leer su caja, su stock o sus clientas
--     no devuelve ni una fila. La lectura de la agenda NO pregunta a esa
--     función (0086): la agenda de un centro en pausa se sigue viendo, pasada
--     y futura, y con ella el nombre y las alergias de quien tiene cita.
--     LEER DA 200 VACÍO Y NO 403, decidido (Carlos, 14-10-2026): un 403 al
--     leer obligaría a mirar cada fila por separado, que es el coste que se
--     quitó en la frontera de RLS.
--   · EL FICHAJE: en un centro en pausa no se entra, pero quien estaba dentro
--     cierra su jornada. El trigger de la 0086 corta solo la `entrada`.
--   · EL REGISTRO (`decisiones_de_dinero`, 0054): columna `centros_en_pausa`
--     —qué centros quedaron en pausa con esa decisión— y el suceso
--     `elige_centro`, que es la titular cambiando cuál se queda. Sigue sin
--     poderse editar ni borrar (permisos + trigger de la 0054). Los ids van
--     sin clave ajena, como el resto del registro (0057): la fila tiene que
--     sobrevivir al centro.
--
-- ═══ LO QUE NO CAMBIA ═══
--
--   · `salones_visibles()`: las suites de aislamiento siguen preguntando lo
--     mismo y pasan sin tocarlas.
--   · SOPORTE ve todo, igual que en una cuenta cortada.
--   · Los recordatorios de las clientas salen con service_role: siguen saliendo
--     del centro en pausa. La clienta no tiene nada que ver con el plan.
--   · Nada se borra: la pausa se CALCULA en cada consulta. Al subir de plan o
--     volver a pagar, el centro vuelve tal cual sin que nadie lo reactive.
--
-- ═══ ORDEN DE DESPLIEGUE: 0088 → BACKEND + FRONTEND (a la vez) ═══
--
-- Con la 0088 y el backend viejo nada se rompe: el backend viejo ya no dejaba
-- trabajar en un centro en pausa, así que no le pide a la base nada que ahora
-- se niegue. El backend nuevo escribe `centros_en_pausa` y `elige_centro`: sin
-- la 0088, esas filas del registro fallarían.
--
-- Para deshacer (primero el backend anterior):
--   restaurar `centros_que_trabajan()` y `el_fichaje_exige_cuenta_que_trabaja()`
--   de la 0086; drop function centros_en_pausa(uuid), centros_del_plan(text);
--   alter table decisiones_de_dinero drop column centros_en_pausa y devolver el
--   check de `suceso` al de la 0054 (solo si no hay filas `elige_centro`).

begin;

-- ═══════════════ 1. cuántos centros incluye cada plan

create function public.centros_del_plan(p_plan text)
returns int
language sql
immutable
set search_path = ''
as $$
  -- ESPEJO de CENTROS_POR_PLAN (backend/app/billing/plans.py). Lo vigila
  -- tests/test_el_centro_en_pausa_vive_en_la_base_base_real.py.
  select case p_plan when 'basico' then 1 when 'pro' then 1 when 'elite' then 2 end;
$$;

comment on function public.centros_del_plan(text) is
  'Cuántos centros incluye el plan (0088). Espejo de CENTROS_POR_PLAN; NULL = plan desconocido = no pausa nada.';

-- ═══════════════ 2. el reparto

create function public.centros_en_pausa(p_cuenta uuid)
returns uuid[]
language sql
stable
security definer
set search_path = ''
as $$
  -- EL MISMO REPARTO QUE `lo_que_incluye` (billing/lo_que_incluye_el_plan.py):
  -- el marcado primero, después el más antiguo, a igualdad el id menor; los
  -- que caben se quedan y el resto, en pausa. Nunca cero activos.
  select coalesce(array_agg(x.id order by x.orden), '{}')
  from (
    select s.id,
           row_number() over (order by s.sigue_al_bajar desc, s.created_at, s.id::text collate "C") as orden,
           greatest(1, public.centros_del_plan(a.plan::text)) as tope
    from public.salons s
    join public.accounts a on a.id = s.account_id
    where s.account_id = p_cuenta
  ) x
  -- Plan desconocido → tope NULL → la comparación no es cierta → nada en pausa.
  where x.orden > x.tope;
$$;

comment on function public.centros_en_pausa(uuid) is
  'Los centros de la cuenta que el plan deja fuera (0088). Mismo reparto que lo_que_incluye; el guarda los compara.';

revoke all on function public.centros_en_pausa(uuid) from public, anon, authenticated;
grant execute on function public.centros_en_pausa(uuid) to service_role;

-- ═══════════════ 3. el corte: lo que trabaja, sin lo que está en pausa

create or replace function public.centros_que_trabajan()
returns uuid[]
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_visibles uuid[] := public.salones_visibles();
  v_cuenta uuid;
begin
  if cardinality(v_visibles) = 0 then
    return v_visibles;
  end if;
  -- SOPORTE: su sesión es de la titular, y se la reconoce por el session_id
  -- que el backend apuntó al acuñarla. Ve todo, también lo que está en pausa.
  if exists (
    select 1 from public.impersonation_sessions i
    where i.auth_session_id = nullif(auth.jwt() ->> 'session_id', '')::uuid
      and i.ended_at is null and i.expires_at > now()
      and i.salon_id = any (v_visibles)
  ) then
    return v_visibles;
  end if;
  -- Todos los centros visibles son de la misma cuenta: la de su administradora.
  select s.account_id into v_cuenta from public.salons s where s.id = v_visibles[1];
  if not public.cuenta_trabaja(v_cuenta) then
    raise exception 'cuenta_cortada'
      using errcode = '42501',
            hint = 'La cuenta no tiene una suscripción viva o está suspendida: se puede ver la agenda, '
                   'reactivar el plan, darse de baja y descargar los datos.';
  end if;
  -- EL CENTRO EN PAUSA (0088) no trabaja: fuera de la lista. Nunca se queda
  -- vacía para una administradora, porque el reparto deja siempre uno activo.
  return array(
    select c from unnest(v_visibles) c
    where c <> all (public.centros_en_pausa(v_cuenta))
  );
end;
$$;

comment on function public.centros_que_trabajan() is
  'Los centros de salones_visibles() que trabajan: la cuenta paga (si no, 42501 cuenta_cortada, 0086) y el '
  'plan los incluye (los de centros_en_pausa quedan fuera, 0088). La pregunta de TODAS las políticas salvo '
  'la lectura de la agenda y las salidas.';

-- ═══════════════ 4. el fichaje: no se entra, pero se sale

create or replace function public.el_fichaje_exige_cuenta_que_trabaja()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_cuenta uuid := (select s.account_id from public.salons s where s.id = new.salon_id);
begin
  if not public.cuenta_trabaja(v_cuenta) then
    raise exception 'cuenta_cortada: con la cuenta cortada no se ficha' using errcode = '42501';
  end if;
  -- EN UN CENTRO EN PAUSA NO SE ENTRA (0088). La pausa y la salida sí: quien
  -- estaba dentro tiene derecho a cerrar su jornada.
  if tg_op = 'INSERT' and new.tipo = 'entrada' and new.salon_id = any (public.centros_en_pausa(v_cuenta)) then
    raise exception 'centro_en_pausa: en un centro en pausa no se empieza jornada' using errcode = '42501';
  end if;
  return new;
end;
$$;

-- ═══════════════ 5. el registro

alter table public.decisiones_de_dinero add column centros_en_pausa uuid[];

comment on column public.decisiones_de_dinero.centros_en_pausa is
  'Los centros que quedaron (o quedarán, si es una bajada programada) en pausa con esta decisión (0088). '
  'Sin clave ajena: el registro sobrevive al centro.';

alter table public.decisiones_de_dinero drop constraint decisiones_de_dinero_suceso_check;
alter table public.decisiones_de_dinero add constraint decisiones_de_dinero_suceso_check
  check (suceso in ('baja', 'reanudacion', 'cambio_de_plan', 'anula_cambio_programado', 'elige_centro'));

-- El límite de «una vez cada 30 días» se pregunta por cuenta y suceso.
create index decisiones_de_dinero_elige_centro
  on public.decisiones_de_dinero (account_id, ocurrido_at desc)
  where suceso = 'elige_centro';

commit;
