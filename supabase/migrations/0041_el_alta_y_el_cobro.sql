-- 0041 · Que el alta y el cobro dejen de romperse por lo mismo
--
-- ═══════════════════════════════════════════════════════════════════════════
--  LA REGLA, porque van dos veces con el mismo mecanismo
--
--  UNA COLUMNA NUEVA SOBRE UNA TABLA EN LA QUE EL CÓDIGO DESPLEGADO INSERTA
--  ES OPCIONAL O TIENE VALOR POR DEFECTO.
--
--  Nunca `not null` a secas. Da igual que el backend nuevo la vaya a rellenar
--  siempre: entre que la migración corre y el backend se despliega —y otra vez
--  si hay que revertir— quien inserta es el código ANTERIOR, y ese no sabe de
--  la columna. Si no se puede evitar que sea obligatoria, la rellena un trigger
--  `before insert`, y el trigger entra EN LA MISMA MIGRACIÓN que la columna.
--
--  Ya ha pasado dos veces:
--    · 0039 — `salons.account_id not null`. El alta insertaba el salón sin
--      cuenta: nadie pudo registrarse hasta la 0040.
--    · 0040 — `subscriptions.account_id not null`. El webhook insertaba la
--      suscripción sin cuenta: el cliente pagaba en Stripe y se quedaba fuera.
--
--  Las dos veces «expand/contract» se aplicó a QUITAR columnas y no a AÑADIR
--  las obligatorias, que es la mitad que muerde. La regla está también en
--  `supabase/README.md` y la vigila
--  `backend/tests/test_regla_columna_obligatoria.py`, que falla si aparece una
--  columna obligatoria nueva sobre una tabla en la que el backend inserta y
--  nadie ha escrito el trigger.
-- ═══════════════════════════════════════════════════════════════════════════
--
-- Medido contra la base ya migrada, con el insert exacto del backend anterior:
--
--   ERROR: null value in column "account_id" of relation "subscriptions"
--          violates not-null constraint
--
-- Y en producción quedó así: `salons.plan = pro`, `accounts.plan = pro` —los
-- triggers de copia funcionaban— y CERO filas en `subscriptions`.

begin;

-- ═══════════════ 1. la suscripción entra sola en la cuenta de su salón

-- El mismo trigger que la 0040 le puso a `salons` y a `users`, para la tabla
-- que se quedó fuera. No es solo por lo de ahora: sin él, una vuelta atrás del
-- backend rompe las altas otra vez, y en silencio.
create or replace function public.suscripcion_entra_en_una_cuenta()
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

create trigger suscripcion_entra_en_una_cuenta
  before insert on public.subscriptions
  for each row execute function public.suscripcion_entra_en_una_cuenta();

comment on column public.subscriptions.account_id is
  'La cuenta que paga. Si quien inserta no la trae, la deduce del salón un '
  'trigger before insert: el backend anterior no sabe de esta columna, y una '
  'vuelta atrás del despliegue no puede volver a romper el cobro.';

-- ═══════════════ 2. la auditoría deja de impedir borrar un salón

-- QUÉ PASABA. `admin_logs.target_salon_id` tenía `on delete set null`, y
-- `admin_logs` tiene dos triggers que la hacen de solo-añadir. Al borrar un
-- salón, la clave ajena intentaba el UPDATE y el guarda lo rechazaba:
--
--   ERROR: admin_logs is append-only: UPDATE is not allowed
--   CONTEXT: SQL statement "UPDATE ONLY "public"."admin_logs"
--            SET "target_salon_id" = NULL WHERE ..."
--
-- Así que no se podía borrar ningún salón que hubiera pasado por soporte —lo
-- que incluye el día que un cliente ejerza su derecho de supresión.
--
-- LOS GUARDAS NO SE TOCAN. Lo que sobra es la clave ajena: era ella la que
-- estaba mal. Un registro de auditoría es un hecho histórico, y un `set null`
-- lo que hace es BORRAR la prueba de sobre qué salón se actuó justo cuando ese
-- salón desaparece — que es cuando más falta hace. La columna se queda como
-- uuid suelto: apunta a algo que pudo dejar de existir, y eso es exactamente
-- lo que un log tiene que poder decir.
alter table public.admin_logs drop constraint admin_logs_target_salon_id_fkey;

comment on column public.admin_logs.target_salon_id is
  'El salón sobre el que se actuó. SIN clave ajena a propósito: la tabla es de '
  'solo-añadir, y un `on delete set null` borraba el rastro al borrar el salón '
  '—además de chocar con los guardas y dejar el salón sin poder borrarse.';

commit;
