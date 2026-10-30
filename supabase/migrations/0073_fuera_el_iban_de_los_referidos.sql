-- 0073 · Fuera el IBAN de los referidos.
--
-- Carlos, 05-10-2026: los referidos cobran por Stripe Connect (0072). Stripe no
-- deja mandar dinero a un IBAN suelto, y guardarlo nos haría custodios de datos
-- bancarios para siempre. Se va `referidos.datos_pago` (texto libre: IBAN,
-- Bizum…) y `referido_de_la_sesion`, que lo devolvía, se rehace sin él.
--
-- En producción hay un referido y tiene el campo vacío (Carlos). La comprobación
-- 0 se PARA si hubiera alguno con contenido: un dato bancario no se borra en
-- silencio. Que no vuelva ningún dato bancario a la base lo vigila
-- `test_cobros_referido_base_real.py`, recorriendo sus columnas.
--
-- ═══ ORDEN DE DESPLIEGUE: 0072 → BACKEND → 0073 → FRONTEND ═══
--
-- El backend viejo LEE `datos_pago` (al entrar el referido y en el panel): esta
-- va después del backend nuevo, que ya no lo nombra. Si se ejecuta dos veces, la
-- segunda no hace nada (`if exists` y la función se rehace igual).

begin;

-- ═══════════════ 0. un dato bancario no se borra en silencio

do $$
declare
  n int;
begin
  -- Solo si la columna sigue ahí: la segunda vez ya no está y no hay nada que mirar.
  if not exists (select 1 from information_schema.columns
                 where table_schema = 'public' and table_name = 'referidos' and column_name = 'datos_pago') then
    return;
  end if;
  execute $q$select count(*) from public.referidos
             where nullif(btrim(coalesce(datos_pago, '')), '') is not null$q$ into n;
  if n > 0 then
    raise exception '0073: % referidos tienen datos de pago escritos. No se borra nada: hay que decidir qué hacer con ellos.', n;
  end if;
end $$;

-- ═══════════════ 1. fuera el IBAN

drop function if exists public.referido_de_la_sesion(text);
alter table public.referidos drop column if exists datos_pago;

create function public.referido_de_la_sesion(p_token_hash text)
returns table (id uuid, nombre text, email text, codigo text)
language sql
stable
set search_path = public, pg_temp
as $$
  select r.id, r.nombre, r.email, r.codigo
  from public.referido_sesiones s
  join public.referidos r on r.id = s.referido_id
  where s.token_hash = p_token_hash
    and s.caduca_at > now()
    and r.estado = 'aprobado'
    and r.password_hash is not null;
$$;

revoke all on function public.referido_de_la_sesion(text) from public, anon, authenticated;
grant execute on function public.referido_de_la_sesion(text) to service_role;

commit;
