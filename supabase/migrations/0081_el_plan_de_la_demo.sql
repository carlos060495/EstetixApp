-- 0081 · El salón de muestra: elegir el plan dentro, y enseñar sus secretos.
--
-- Carlos, 08-10-2026.
--
-- ═══ 1. EL PLAN DE UNA DEMO (`cambiar_plan_de_demo`) ═══
--
-- Dentro de una demo hay un selector Básico · Pro · Elite. Un vendedor pone
-- Básico, el salón echa de menos algo, y pulsa Pro para enseñárselo
-- funcionando: la carencia se ve, no se cuenta.
--
--   · CAMBIA `accounts.plan` Y NADA MÁS. Es la MISMA columna que escribe el
--     webhook de Stripe para un cliente de verdad, así que lo que se tapa en
--     cada plan lo decide el mismo mecanismo de siempre (`require_plan` en el
--     backend, el plan de la sesión en la pantalla). No hay una simulación
--     aparte: si la demo enseñara otra cosa de lo que verá el cliente al
--     pagar, mentiría.
--   · SOLO UNA DEMO: con la cuenta bloqueada, si no es `es_demo` lanza y no
--     toca nada. Para una cuenta de verdad el plan solo llega desde Stripe;
--     lo vigila `test_el_plan_de_la_demo.py` (backend) y
--     `test_el_plan_de_la_demo_base_real.py` (base), descubriendo quién
--     escribe el plan.
--   · No toca Stripe, no crea suscripción, ni atribución, ni comisión (las
--     puertas de la 0080 siguen ahí). Se queda sin plan pendiente.
--
-- ═══ 2. LA CONTRASEÑA DE MUESTRA (`demos.contrasena_de_muestra`) ═══
--
-- Descargar los datos pide la contraseña de quien entra. En una demo nadie la
-- sabía: la ponía el backend al azar y terminaba en error. Ahora la contraseña
-- de su administradora se guarda aquí y la pantalla la enseña, para que la
-- descarga se pueda probar. Dentro de una demo no hay nada que proteger: los
-- datos son inventados, el correo de su administradora no existe y la fila se
-- va con la demo. Solo `service_role` la lee; el backend solo la da a la sesión
-- de ESA demo (`/auth/me`). Las demos de antes de la 0081 no la tienen: ahí la
-- pantalla dice que no se puede.
--
-- ═══ LO QUE NO SE TOCA ═══
--
-- El cálculo de la comisión, los pagos a Stripe, las atribuciones, los plazos,
-- el borrado de los 90 días, los topes de las demos y el barrido.
--
-- ═══ ORDEN DE DESPLIEGUE: 0081 → BACKEND + FRONTEND (a la vez) ═══
--
-- Compatible con el backend desplegado: `enlazar_demo` gana un parámetro con
-- valor por defecto (el backend de antes lo llama con dos y sigue igual).
-- Ningún paso en Stripe.

begin;

alter table public.demos add column contrasena_de_muestra text;

comment on column public.demos.contrasena_de_muestra is
  'La contraseña de la administradora de la demo (0081), para enseñarla en pantalla. Solo service_role.';

-- `enlazar_demo` con la contraseña de muestra (por defecto, ninguna).
drop function public.enlazar_demo(uuid, uuid);
create function public.enlazar_demo(p_cuenta uuid, p_auth_user uuid, p_contrasena text default null)
returns boolean
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  if not exists (select 1 from public.accounts a where a.id = p_cuenta and a.es_demo) then
    raise exception 'enlazar_demo: % no es una demo', p_cuenta using errcode = '42501';
  end if;
  update public.demos set auth_user_id = p_auth_user, contrasena_de_muestra = p_contrasena
  where account_id = p_cuenta and auth_user_id is null;
  if not found then
    raise exception 'enlazar_demo: la demo % ya tiene usuario', p_cuenta using errcode = '55000';
  end if;
  update public.users set auth_user_id = p_auth_user where account_id = p_cuenta and auth_user_id is null;
  return true;
end;
$$;

revoke all on function public.enlazar_demo(uuid, uuid, text) from public, anon, authenticated;
grant execute on function public.enlazar_demo(uuid, uuid, text) to service_role;

create function public.cambiar_plan_de_demo(p_cuenta uuid, p_plan public.plan_tipo)
returns public.plan_tipo
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  if p_plan is null then
    raise exception 'cambiar_plan_de_demo: falta el plan' using errcode = '22023';
  end if;
  -- SOLO UNA DEMO, con la cuenta bloqueada. Una de verdad no se toca.
  perform 1 from public.accounts a where a.id = p_cuenta and a.es_demo for update;
  if not found then
    raise exception 'cambiar_plan_de_demo: % NO es una demo; su plan solo llega de Stripe', p_cuenta
      using errcode = '42501';
  end if;
  -- La misma columna que escribe el webhook de Stripe: lo que se tapa en cada
  -- plan lo decide el mecanismo de siempre.
  update public.accounts set plan = p_plan, pending_plan = null where id = p_cuenta;
  return p_plan;
end;
$$;

comment on function public.cambiar_plan_de_demo(uuid, public.plan_tipo) is
  'El plan de un salón de muestra (0081): cambia accounts.plan, sin Stripe. Solo una demo; una cuenta de verdad, no.';

revoke all on function public.cambiar_plan_de_demo(uuid, public.plan_tipo) from public, anon, authenticated;
grant execute on function public.cambiar_plan_de_demo(uuid, public.plan_tipo) to service_role;

commit;
