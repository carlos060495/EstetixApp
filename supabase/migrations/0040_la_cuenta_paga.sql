-- 0040 · Lo que se paga cuelga de la cuenta
--
-- La suscripción, el plan, el cliente de Stripe, la suspensión, el CIF y la
-- marca suben del centro a la cuenta. Un Elite con dos centros paga una vez.
--
-- ═══ Y DE PASO, EL ALTA, QUE ESTÁ ROTA ═══
--
-- La 0039 dejó `salons.account_id` en not null y no tocó backend, pero el alta
-- inserta el salón SIN cuenta (auth.py:396). Medido contra la base local
-- reproduciendo el insert exacto del alta:
--
--   ERROR: null value in column "account_id" of relation "salons"
--          violates not-null constraint
--
-- Es decir: desde que se desplegó la 0039, nadie puede registrarse. Se arregla
-- aquí y en la base —no en el backend— porque la migración se aplica ANTES que
-- el backend: puesto en el backend, el alta seguiría rota durante todo el hueco
-- entre una cosa y la otra. Puesto aquí, se arregla en cuanto corre.
--
-- ═══ AÑADE Y RELLENA. NO QUITA NADA ═══
--
-- Las columnas viejas de `salons` se quedan donde están y siguen siendo verdad.
-- Quitarlas es otra migración, cuando ya no las lea nadie.
--
-- LAS DOS COPIAS SE MANTIENEN SOLAS, EN LOS DOS SENTIDOS. Mientras dure la
-- ventana hay dos escritores posibles y no se sabe cuál va a escribir:
--
--   · Antes de desplegar, el backend VIEJO escribe en `salons` — ahí pone el
--     webhook de Stripe el plan, y ahí pone soporte la suspensión.
--   · Después de desplegar, el backend NUEVO escribe en `accounts`.
--   · Y si hubiera que revertir el despliegue, se vuelve al primero.
--
-- Un trigger en cada lado copia al otro. No se llaman en bucle porque cada uno
-- lleva un `is distinct from`: si el otro lado ya dice lo mismo, la copia no
-- escribe ninguna fila, y un update de cero filas no dispara nada. La cadena
-- muere en dos saltos, escriba quien escriba. Los dos triggers no tienen más
-- trabajo que este y mueren en la migración que quite las columnas.
--
-- ═══ STRIPE ═══
--
-- `subscriptions` gana `account_id` y CONSERVA `salon_id`. En Stripe se AÑADE
-- `account_id` al metadata sin tocar el `salon_id` que ya está: el código nuevo
-- lee solo la llave nueva y la vieja se queda ahí sin molestar. Ni rama de
-- compatibilidad en el webhook, ni un instante en que llegue un evento con una
-- llave que el código desplegado no entienda.

begin;

-- ═══════════════════════════════ lo que sube a la cuenta

alter table public.accounts
  add column plan public.plan_tipo not null default 'basico',
  add column pending_plan public.plan_tipo,
  add column stripe_customer_id text,
  add column suspendido boolean not null default false,
  add column suspendido_at timestamptz,
  add column suspendido_por uuid,
  add column cif text,
  add column logo_url text,
  add column accent_color text not null default '#C9A84C';

comment on column public.accounts.suspendido is
  'Se suspende a quien no paga, y eso apaga TODOS sus centros. Antes vivía en '
  'salons.suspendido, donde apagaba solo uno.';
comment on column public.accounts.accent_color is
  'La marca es de la cuenta: un dueño con dos centros tiene un logo y un color, '
  'no dos.';

-- BACKFILL desde su centro. Hoy hay exactamente uno por cuenta, así que el
-- `distinct on` no desempata nada: está para que la sentencia sea válida pase
-- lo que pase, no para elegir.
update public.accounts a set
  plan               = s.plan,
  pending_plan       = s.pending_plan,
  stripe_customer_id = s.stripe_customer_id,
  suspendido         = s.suspendido,
  suspendido_at      = s.suspendido_at,
  suspendido_por     = s.suspendido_por,
  cif                = s.cif,
  logo_url           = s.logo_url,
  accent_color       = s.accent_color
from (
  select distinct on (account_id) account_id, plan, pending_plan,
         stripe_customer_id, suspendido, suspendido_at, suspendido_por,
         cif, logo_url, accent_color
  from public.salons order by account_id, id
) s
where s.account_id = a.id;

create index accounts_stripe_customer_id_idx on public.accounts (stripe_customer_id);

-- ═══════════════════════════════ la suscripción

alter table public.subscriptions
  add column account_id uuid references public.accounts(id) on delete cascade;

update public.subscriptions sub set account_id = s.account_id
from public.salons s where s.id = sub.salon_id;

alter table public.subscriptions alter column account_id set not null;
create unique index subscriptions_account_id_key on public.subscriptions (account_id);

comment on column public.subscriptions.salon_id is
  'SE QUEDA por la ventana de despliegue: el backend viejo todavía busca por '
  'aquí. Lo quita la migración que contrae, no esta.';

-- ═══════════════════════════════ el alta vuelve a funcionar

-- UN SALÓN SIN CUENTA DICHA *ES* UNA CUENTA NUEVA. Así el alta no tiene que
-- saber nada de cuentas —y hoy no sabe—, y la regla «todo salón tiene cuenta»
-- se cumple porque no hay forma de saltársela, no porque alguien se acuerde.
-- Es el mismo criterio con el que la 0039 mantiene `user_salons`.
create or replace function public.salon_entra_en_una_cuenta()
returns trigger language plpgsql security definer
set search_path = public, pg_temp as $$
begin
  if new.account_id is null then
    insert into public.accounts (nombre, plan, pending_plan, stripe_customer_id,
                                 suspendido, suspendido_at, suspendido_por,
                                 cif, logo_url, accent_color)
    values (coalesce(nullif(trim(new.nombre_comercial), ''), 'Cuenta'),
            new.plan, new.pending_plan, new.stripe_customer_id,
            new.suspendido, new.suspendido_at, new.suspendido_por,
            new.cif, new.logo_url, new.accent_color)
    returning id into new.account_id;
  else
    -- Centro que entra en una cuenta que YA existe: manda la cuenta. Sin esto,
    -- el centro nuevo nacería en 'basico' con la cuenta en 'elite' y las dos
    -- copias empezarían discrepando.
    select a.plan, a.pending_plan, a.stripe_customer_id, a.suspendido,
           a.suspendido_at, a.suspendido_por, a.cif, a.logo_url, a.accent_color
      into new.plan, new.pending_plan, new.stripe_customer_id, new.suspendido,
           new.suspendido_at, new.suspendido_por, new.cif, new.logo_url,
           new.accent_color
    from public.accounts a where a.id = new.account_id;
  end if;
  return new;
end;
$$;

create trigger salon_entra_en_una_cuenta
  before insert on public.salons
  for each row execute function public.salon_entra_en_una_cuenta();

-- Y el usuario, a la cuenta de su salón. Sin esto un admin recién registrado
-- tiene `account_id` nulo, y la rama de admin de `salones_visibles()` —que
-- empareja por cuenta— no le devuelve ni su propio centro.
create or replace function public.usuario_entra_en_una_cuenta()
returns trigger language plpgsql security definer
set search_path = public, pg_temp as $$
begin
  if new.account_id is null and new.salon_id is not null then
    select s.account_id into new.account_id
    from public.salons s where s.id = new.salon_id;
  end if;
  return new;
end;
$$;

create trigger usuario_entra_en_una_cuenta
  before insert on public.users
  for each row execute function public.usuario_entra_en_una_cuenta();

-- `users.account_id` se queda ANULABLE a propósito. Ponerle un not null es
-- exactamente lo que rompió el alta en la 0039: una restricción nueva sobre un
-- camino que nadie volvió a recorrer antes de desplegar.

-- ═══════════════════ las dos copias, sincronizadas mientras dure

create or replace function public.copiar_de_salon_a_cuenta()
returns trigger language plpgsql security definer
set search_path = public, pg_temp as $$
begin
  update public.accounts a set
    plan = new.plan, pending_plan = new.pending_plan,
    stripe_customer_id = new.stripe_customer_id,
    suspendido = new.suspendido, suspendido_at = new.suspendido_at,
    suspendido_por = new.suspendido_por, cif = new.cif,
    logo_url = new.logo_url, accent_color = new.accent_color
  where a.id = new.account_id
    -- LA GUARDA QUE CIERRA EL BUCLE: si ya dice lo mismo, no se escribe ninguna
    -- fila, y el trigger del otro lado no llega a dispararse.
    and (a.plan, a.pending_plan, a.stripe_customer_id, a.suspendido,
         a.suspendido_at, a.suspendido_por, a.cif, a.logo_url, a.accent_color)
        is distinct from
        (new.plan, new.pending_plan, new.stripe_customer_id, new.suspendido,
         new.suspendido_at, new.suspendido_por, new.cif, new.logo_url,
         new.accent_color);
  return new;
end;
$$;

create or replace function public.copiar_de_cuenta_a_salones()
returns trigger language plpgsql security definer
set search_path = public, pg_temp as $$
begin
  update public.salons s set
    plan = new.plan, pending_plan = new.pending_plan,
    stripe_customer_id = new.stripe_customer_id,
    suspendido = new.suspendido, suspendido_at = new.suspendido_at,
    suspendido_por = new.suspendido_por, cif = new.cif,
    logo_url = new.logo_url, accent_color = new.accent_color
  where s.account_id = new.id
    and (s.plan, s.pending_plan, s.stripe_customer_id, s.suspendido,
         s.suspendido_at, s.suspendido_por, s.cif, s.logo_url, s.accent_color)
        is distinct from
        (new.plan, new.pending_plan, new.stripe_customer_id, new.suspendido,
         new.suspendido_at, new.suspendido_por, new.cif, new.logo_url,
         new.accent_color);
  return new;
end;
$$;

-- Solo `after update`. El alta ya la resuelve el trigger de arriba, y un
-- `after insert` aquí sería una escopeta cargada: el día que se abra un segundo
-- centro, sus valores por defecto pisarían el plan de la cuenta.
create trigger salon_copia_a_cuenta
  after update of plan, pending_plan, stripe_customer_id, suspendido,
    suspendido_at, suspendido_por, cif, logo_url, accent_color
  on public.salons for each row execute function public.copiar_de_salon_a_cuenta();

create trigger cuenta_copia_a_salones
  after update of plan, pending_plan, stripe_customer_id, suspendido,
    suspendido_at, suspendido_por, cif, logo_url, accent_color
  on public.accounts for each row execute function public.copiar_de_cuenta_a_salones();

-- ═══════════════ los guardas suben con las columnas que guardan

-- `salons` tiene dos triggers que impiden que un `authenticated` se cambie el
-- plan o se levante la suspensión él solo. Si las columnas suben y los guardas
-- no, subir la columna es abrir la puerta. Son los mismos, palabra por palabra,
-- sobre la tabla nueva.
create or replace function public.enforce_plan_source_of_truth_cuenta()
returns trigger language plpgsql as $$
begin
  if new.plan is distinct from old.plan and current_user = 'authenticated' then
    raise exception 'El plan de la cuenta solo se actualiza vía Stripe (webhook).'
      using errcode = 'insufficient_privilege';
  end if;
  return new;
end;
$$;

create trigger accounts_plan_service_role_only
  before update on public.accounts
  for each row execute function public.enforce_plan_source_of_truth_cuenta();

create or replace function public.enforce_suspension_service_role_only_cuenta()
returns trigger language plpgsql as $$
begin
  if current_user = 'authenticated' and (
       new.suspendido    is distinct from old.suspendido
    or new.suspendido_at is distinct from old.suspendido_at
    or new.suspendido_por is distinct from old.suspendido_por
  ) then
    raise exception 'La suspensión de una cuenta solo se cambia desde el panel de administración.'
      using errcode = 'insufficient_privilege';
  end if;
  return new;
end;
$$;

create trigger accounts_suspension_service_role_only
  before update on public.accounts
  for each row execute function public.enforce_suspension_service_role_only_cuenta();

-- Los dos triggers de copia son `security definer`, así que dentro de ellos
-- `current_user` es el dueño de la función y no `authenticated`: la
-- sincronización pasa por delante de los cuatro guardas, como debe.

-- ═══════════════════════════════ la cuenta se puede leer, y poco más

-- En la 0039 nació cerrada porque no la leía nadie. Ahora sí: el muro de plan y
-- el de suspensión preguntan por ella con el token del inquilino, y Ajustes
-- edita la marca. Se abre lo justo.
create policy "accounts_select_own" on public.accounts
  as permissive for select
  to public
  using (id = public.cuenta_actual());

create policy "accounts_update_admin" on public.accounts
  as permissive for update
  to public
  using (id = public.cuenta_actual() and public.is_admin_rol())
  with check (id = public.cuenta_actual() and public.is_admin_rol());

grant select on public.accounts to authenticated;

-- EL PERMISO ES POR COLUMNA, no por tabla. En `salons` el `grant update` es de
-- la tabla entera y lo que salva el plan es el trigger; aquí el `authenticated`
-- ni siquiera puede nombrar esas columnas en un update. El trigger de arriba
-- se queda como segunda vuelta, para el día que alguien amplíe este grant.
grant update (cif, logo_url, accent_color) on public.accounts to authenticated;

commit;
