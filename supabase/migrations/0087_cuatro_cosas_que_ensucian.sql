-- 0087 · Cuatro cosas que ensucian. LA PARTE DE LA BASE.
--
-- Carlos, 14-10-2026.
--
-- ═══ 1. UN ALTA SIN CONFIRMAR YA NO COGE EL CORREO PARA SIEMPRE ═══
--
-- Quien empezaba un alta y no confirmaba el correo dejaba su ficha en `users`,
-- y el alta mira `users` para decir «ese email ya está en uso». Medido en
-- local el 14-10: el alta a medias deja 7 filas (cuenta, centro, ficha y, en
-- Auth, la identidad, su `identities`, su token de un solo uso y su apunte de
-- auditoría) y el segundo intento con el mismo correo contesta 409. Para
-- siempre: no había nada que lo borrara.
--
-- AHORA CADUCA A LOS 7 DÍAS (`plazo_alta_sin_confirmar`). Por qué siete:
--   · el enlace de confirmación vale como mucho 24 horas: pasado ese día, ese
--     alta ya no la puede terminar nadie; es peso muerto;
--   · siete días cubren un fin de semana, un correo que se fue a spam y que
--     soporte le mande un enlace nuevo a mano;
--   · y la persona que vuelve no espera meses: mientras dura, el alta le dice
--     QUÉ PASA y QUÉ DÍA queda libre su correo (`alta_pendiente_del_correo`).
--
-- QUÉ ES UN ALTA SIN CONFIRMAR, y la función se niega a tocar cualquier otra
-- cosa (`es_alta_sin_confirmar`): ni demo ni suscripción; al menos una ficha y
-- TODAS enlazadas a una identidad de Auth que nunca confirmó el correo ni
-- entró nunca; y la cuenta nacida hace más del plazo.
--
-- QUÉ SE BORRA: lo que diga `plan_de_borrado` (0068/0083), el mismo plan del
-- borrado a 90 días y de las demos, sin cambiarlo. Pero ANTES se mira lo que
-- tendría que borrar: si hay una sola fila en `public` que no sea la cuenta,
-- el centro o la ficha (una clienta, una cita, un logo), no es un alta a
-- medias y NO SE TOCA. Así este borrado no puede llevarse nada de un salón que
-- trabajó, aunque mañana algo cambie y la condición de arriba se quede corta.
--
-- NO ES EL BORRADO A 90 DÍAS: no apunta en `cuentas_borradas` (esa cuenta
-- nunca existió de verdad para nadie) y no mira su interruptor. Sí comparte su
-- forma: una cuenta por subtransacción, tope por pasada y constancia.
--
-- LA ATRIBUCIÓN SE QUEDA, ANULADA (Carlos, 14-10-2026). `altas_atribuidas` es
-- inmutable y de ella sale el dinero de otra persona: no se borra ni se
-- cambia. La constancia de la caducidad (`altas_sin_confirmar_caducadas`)
-- apunta qué atribución deja anulada y POR QUÉ, y la vista
-- `atribuciones_anuladas` la enseña junto a ella. En el panel del referido deja
-- de contar SIN TOCAR NINGUNA FUNCIÓN: todas sus lecturas (`altas_que_cuentan`,
-- `portal_del_referido`, `salones_de_un_referido`…) solo cuentan un alta si su
-- cuenta existe o consta en `cuentas_borradas`, y esta no está en ninguna.
-- Económicamente da igual: una cuenta sin confirmar nunca pagó.
--
-- ═══ 3. BORRAR UNA CLIENTA NO SE LLEVA SUS CITAS NI SU CAJA ═══
--
-- Ya era así —medido el 14-10—: `appointments.client_id` es ON DELETE SET
-- NULL, así que sus citas se quedan como «Cliente Express», y ningún ingreso
-- apunta a una clienta. Lo único que se va con ella es su ficha y sus
-- consentimientos. Aquí se DECLARA ese borrado en cascada (comentario
-- `FICHA DE LA CLIENTA` en la clave), y el guarda
-- `test_borrar_una_clienta_no_borra_trabajo_base_real.py` exige que toda clave
-- hacia `clients` sea SET NULL salvo las declaradas.
--
-- ═══ ORDEN DE DESPLIEGUE: 0087 → commit → Vercel + Railway a la vez ═══
--
-- Antes de aplicarla, contar en producción las altas que caducarían (la
-- consulta va en el reporte). Con la 0087 y el backend viejo no pasa nada: la
-- caducidad la llama el cron del backend nuevo.

begin;

-- ═══════════════ 1. el plazo, en un solo sitio

create function public.plazo_alta_sin_confirmar()
returns interval
language sql
immutable
set search_path = ''
as $$ select interval '7 days' $$;

comment on function public.plazo_alta_sin_confirmar() is
  'Cuánto vive un alta sin confirmar antes de caducar (0087). Lo lee también el alta, para decir qué día queda libre el correo.';

grant execute on function public.plazo_alta_sin_confirmar() to service_role;

-- ═══════════════ 2. la constancia: inmutable y sin datos personales

create table public.altas_sin_confirmar_caducadas (
  id bigint generated always as identity primary key,
  -- SIN clave ajena a la cuenta: la cuenta ya no existe. Es solo un dato.
  cuenta_id uuid not null unique,
  cuenta_creada_at timestamptz not null,
  caducada_at timestamptz not null default now(),
  -- La atribución que queda anulada, si vino por un código. Sin clave ajena,
  -- como todo registro (0057, 0066, 0070): un registro no cuelga de nada, y
  -- una clave hacia `altas_atribuidas` impediría hasta su TRUNCATE rechazado
  -- con su propio motivo. Las atribuciones no se borran: el id no se queda solo.
  atribucion_id bigint,
  motivo text not null
);

comment on table public.altas_sin_confirmar_caducadas is
  'Altas que nadie confirmó y caducaron (0087): una fila por cuenta borrada, sin datos personales. Si la '
  'cuenta llegó con un código de referido, `atribucion_id` dice qué atribución queda ANULADA y `motivo` por qué. '
  'No se puede editar ni borrar.';

alter table public.altas_sin_confirmar_caducadas enable row level security;
revoke all on public.altas_sin_confirmar_caducadas from anon, authenticated, service_role;
grant select on public.altas_sin_confirmar_caducadas to service_role;

create function public.altas_sin_confirmar_caducadas_no_se_tocan()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  raise exception 'altas_sin_confirmar_caducadas no se puede % : es un registro', tg_op using errcode = '42501';
end;
$$;

create trigger altas_sin_confirmar_caducadas_sin_update_ni_delete
  before update or delete on public.altas_sin_confirmar_caducadas
  for each row execute function public.altas_sin_confirmar_caducadas_no_se_tocan();
create trigger altas_sin_confirmar_caducadas_sin_truncate
  before truncate on public.altas_sin_confirmar_caducadas
  for each statement execute function public.altas_sin_confirmar_caducadas_no_se_tocan();

-- La atribución, con lo que la anula al lado: quien mire la fila entiende que no es un error.
create view public.atribuciones_anuladas
with (security_invoker = true)
as
select a.id as atribucion_id, a.referido_id, a.cuenta_id, a.cuenta_nombre, a.atribuida_el,
       c.caducada_at as anulada_el, c.motivo
from public.altas_atribuidas a
join public.altas_sin_confirmar_caducadas c on c.atribucion_id = a.id;

comment on view public.atribuciones_anuladas is
  'Atribuciones de altas que nunca se confirmaron (0087). La atribución sigue intacta; esta vista dice por qué no cuenta.';

revoke all on public.atribuciones_anuladas from anon, authenticated, service_role;
grant select on public.atribuciones_anuladas to service_role;

-- ═══════════════ 3. qué es un alta sin confirmar

create function public.es_alta_sin_confirmar(p_cuenta uuid, p_ahora timestamptz default now())
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1 from public.accounts a
    where a.id = p_cuenta
      and not a.es_demo
      and a.created_at <= p_ahora - public.plazo_alta_sin_confirmar()
      and not exists (select 1 from public.subscriptions s where s.account_id = a.id)
      and exists (select 1 from public.users u where u.account_id = a.id)
      -- TODAS sus fichas, de una identidad que nunca confirmó ni entró.
      and not exists (
        select 1 from public.users u
        left join auth.users au on au.id = u.auth_user_id
        where u.account_id = a.id
          and (au.id is null or au.email_confirmed_at is not null or au.confirmed_at is not null
               or au.last_sign_in_at is not null)
      )
  );
$$;

revoke all on function public.es_alta_sin_confirmar(uuid, timestamptz) from public, anon, authenticated;
grant execute on function public.es_alta_sin_confirmar(uuid, timestamptz) to service_role;

-- Las que caducarían ahora. Para contar antes de borrar y para el panel.
create function public.altas_sin_confirmar_vencidas(p_ahora timestamptz default now())
returns table (account_id uuid, creada_at timestamptz, con_atribucion boolean)
language sql
stable
security definer
set search_path = ''
as $$
  select a.id, a.created_at, exists (select 1 from public.altas_atribuidas t where t.cuenta_id = a.id)
  from public.accounts a
  where public.es_alta_sin_confirmar(a.id, p_ahora)
  order by a.created_at, a.id;
$$;

revoke all on function public.altas_sin_confirmar_vencidas(timestamptz) from public, anon, authenticated;
grant execute on function public.altas_sin_confirmar_vencidas(timestamptz) to service_role;

-- Para el alta: ¿este correo está cogido por un alta sin confirmar, y qué día queda libre?
create function public.alta_pendiente_del_correo(p_email text)
returns table (pendiente boolean, libre_el timestamptz)
language sql
stable
security definer
set search_path = ''
as $$
  select true, a.created_at + public.plazo_alta_sin_confirmar()
  from public.users u
  join public.accounts a on a.id = u.account_id
  join auth.users au on au.id = u.auth_user_id
  where lower(u.email) = lower(btrim(p_email))
    and not a.es_demo
    and au.email_confirmed_at is null and au.confirmed_at is null and au.last_sign_in_at is null
    and not exists (select 1 from public.subscriptions s where s.account_id = a.id)
  order by a.created_at desc
  limit 1;
$$;

revoke all on function public.alta_pendiente_del_correo(text) from public, anon, authenticated;
grant execute on function public.alta_pendiente_del_correo(text) to service_role;

-- ═══════════════ 4. caducarlas: una por subtransacción, con tope y constancia

create function public.caducar_altas_sin_confirmar(p_ahora timestamptz default now(), p_tope int default 50)
returns table (caducadas int, saltadas int, quedan int)
language plpgsql
volatile
security definer
set search_path = public, pg_temp
as $$
declare
  v_cuenta record;
  pl record;
  paso jsonb;
  plan jsonb;
  n bigint;
  v_ajeno text;
  v_atribucion bigint;
  v_hechas int := 0;
  v_saltadas int := 0;
begin
  if p_tope is null or p_tope < 1 then
    raise exception 'caducar_altas_sin_confirmar: el tope tiene que ser 1 o más' using errcode = '22023';
  end if;

  for v_cuenta in select * from public.altas_sin_confirmar_vencidas(p_ahora) limit p_tope loop
    begin
      -- La comprobación que no se salta nadie, con la cuenta bloqueada.
      perform 1 from public.accounts a where a.id = v_cuenta.account_id for update;
      if not public.es_alta_sin_confirmar(v_cuenta.account_id, p_ahora) then
        v_saltadas := v_saltadas + 1;
        continue;
      end if;

      -- LO QUE BORRARÍA EL PLAN, MIRADO ANTES. En `public` solo puede haber la
      -- cuenta, el centro y la ficha: cualquier otra fila es trabajo de alguien.
      plan := '[]'::jsonb;
      v_ajeno := null;
      for pl in select * from public.plan_de_borrado(v_cuenta.account_id) loop
        execute format('select count(*) from %s where %s', pl.tabla, pl.predicado) into n;
        if n > 0 and pl.tabla like 'public.%'
           and pl.tabla not in ('public.accounts', 'public.salons', 'public.users') then
          v_ajeno := pl.tabla;
          exit;
        end if;
        if pl.orden is not null then
          plan := plan || jsonb_build_object('tabla', pl.tabla, 'predicado', pl.predicado, 'orden', pl.orden);
        end if;
      end loop;
      if v_ajeno is null and exists (
        select 1 from storage.objects o
        where o.bucket_id = 'salon-assets'
          and split_part(o.name, '/', 1) in (select s.id::text from public.salons s where s.account_id = v_cuenta.account_id)
      ) then
        v_ajeno := 'storage.objects';
      end if;
      if v_ajeno is not null then
        raise warning 'caducar_altas_sin_confirmar: la cuenta % tiene filas en %; no es un alta a medias y no se toca.',
          v_cuenta.account_id, v_ajeno;
        v_saltadas := v_saltadas + 1;
        continue;
      end if;

      select t.id into v_atribucion from public.altas_atribuidas t where t.cuenta_id = v_cuenta.account_id;
      insert into public.altas_sin_confirmar_caducadas (cuenta_id, cuenta_creada_at, caducada_at, atribucion_id, motivo)
      values (
        v_cuenta.account_id, v_cuenta.creada_at, p_ahora, v_atribucion,
        'Alta que nadie confirmó en ' || public.plazo_alta_sin_confirmar()::text
          || ': se borró la cuenta a medias y su correo quedó libre para registrarse otra vez.'
          || case when v_atribucion is not null
                  then ' La atribución sigue en altas_atribuidas, ANULADA: la cuenta nunca existió '
                       || 'de verdad, nunca pagó y no cuenta en el panel del referido. No es un error.'
                  else '' end
      );

      for paso in select x from jsonb_array_elements(plan) x order by (x ->> 'orden')::int loop
        execute format('delete from %s where %s', paso ->> 'tabla', paso ->> 'predicado');
      end loop;
      for paso in select x from jsonb_array_elements(plan) x loop
        execute format('select count(*) from %s where %s', paso ->> 'tabla', paso ->> 'predicado') into n;
        if n > 0 then
          raise exception 'caducar_altas_sin_confirmar: la cuenta % dejó % filas en %', v_cuenta.account_id, n, paso ->> 'tabla';
        end if;
      end loop;
      v_hechas := v_hechas + 1;
    exception when others then
      -- UNA QUE FALLA NO PARA A LAS DEMÁS, y la suya se deshace entera
      -- (incluida su constancia): esto es una subtransacción.
      raise warning 'caducar_altas_sin_confirmar: la cuenta % no se pudo caducar: %', v_cuenta.account_id, sqlerrm;
      v_saltadas := v_saltadas + 1;
    end;
  end loop;

  return query select v_hechas, v_saltadas,
    (select count(*)::int from public.altas_sin_confirmar_vencidas(p_ahora));
end;
$$;

comment on function public.caducar_altas_sin_confirmar(timestamptz, int) is
  'Borra las altas que nadie confirmó en plazo_alta_sin_confirmar() (0087), como mucho p_tope por pasada. '
  'Deja constancia en altas_sin_confirmar_caducadas. Se niega con cualquier cuenta que tenga algo más que '
  'cuenta, centro y ficha. La llama el cron (tareas/altas_sin_confirmar.py).';

revoke all on function public.caducar_altas_sin_confirmar(timestamptz, int) from public, anon, authenticated;
grant execute on function public.caducar_altas_sin_confirmar(timestamptz, int) to service_role;

-- ═══════════════ 5. lo que se va con una clienta: su ficha, declarado

comment on constraint consents_client_id_fkey on public.consents is
  'FICHA DE LA CLIENTA (0087): sus consentimientos son parte de su ficha y se van con ella. Toda otra clave '
  'hacia clients es SET NULL: sus citas y su caja se quedan.';

commit;
