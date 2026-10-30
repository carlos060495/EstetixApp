-- 0051 · Una sola verdad para el CIF
--
-- `salons.cif` se va, y con ella el trozo de trigger que la mantenía viva.
--
-- ═══ POR QUÉ ═══
--
-- Desde la 0040 el CIF vive en `accounts`: la entidad que factura es la cuenta,
-- y un dueño con tres locales tiene un CIF, no tres. La columna vieja de
-- `salons` se quedó ahí, sincronizada por un par de triggers que copian en las
-- dos direcciones.
--
-- Eso no es un respaldo: es una SEGUNDA VERDAD esperando a divergir. Hoy
-- coinciden porque el trigger nunca falla; el día que falle una vez, tendremos
-- dos CIF distintos para la misma empresa y ninguna forma de saber cuál vale.
--
-- ═══ CÓMO SE COMPROBÓ QUE ESTÁ MUERTA ═══
--
-- No «se revisó y parece que no la usa nadie». Esto es lo que se hizo, sobre el
-- backend DESPLEGADO (el commit que está en producción, no el del portátil):
--
--   1. TODOS los `select` que nombran `cif`, buscados en el árbol de git:
--
--        git grep -n '"select":.*cif' HEAD -- backend/app
--
--      Devuelve UNO: `rgpd.py:72`, y es `"nombre_comercial,direccion,accounts(cif)"`
--      — un embed, o sea el CIF de la CUENTA. Los otros dos sitios que leen el
--      CIF (`salon.py` y `admin_tenants.py`) también lo piden como `accounts(...)`.
--
--   2. El ayudante por el que pasan esas lecturas, `app/core/cuenta.py`, tiene
--      un guarda propio: `campos()` revienta si le piden un nombre que no esté
--      en `CAMPOS_DE_LA_CUENTA`, y siempre construye `account_id,accounts(...)`.
--      Por construcción, esas lecturas no pueden acabar en `salons`.
--
--   3. Escrituras: hay DOS en todo el backend (`auth.py`, el alta; `salon.py`,
--      el perfil) y las dos escriben en `accounts`. Ninguna toca `salons.cif`.
--      El único INSERT que la rellenaba era `supabase/seed.sql`, que en esta
--      misma entrega deja de hacerlo.
--
--   4. Y en producción, solo lectura: de los 5 centros, CERO tenían un `cif`
--      distinto del de su cuenta. Es lo que se espera de una copia que no edita
--      nadie — y por eso quitarla no cambia ningún dato que alguien esté
--      leyendo.
--
--   5. Los PDF de RGPD, que son lo único que IMPRIME el CIF en un documento,
--      lo sacan de `accounts(cif)` (ver el punto 1). Siguen igual.
--
-- ═══ ORDEN DE DESPLIEGUE ═══
--
-- Al revés que una columna que nace: la que se va exige que el código que la
-- nombra ya NO esté desplegado. Aquí no hay nada que esperar —el backend
-- desplegado no la nombra, por los cinco puntos de arriba— así que:
--
--     ESTA MIGRACIÓN VA SOLA. Antes o después del backend, da igual.
--
-- Vuelta atrás: `alter table public.salons add column cif text;` la devuelve
-- vacía. Como no la lee nadie, no rompe nada; habría que volver a poner los
-- triggers de abajo si se quisiera la copia otra vez.
--
-- ═══ COLUMNAS NUEVAS ═══
--
-- Ninguna: esta migración solo quita. La regla de la columna obligatoria no
-- tiene nada que aplicar.

begin;

-- ── 1 · los triggers dejan de copiar una columna que va a desaparecer ─────
--
-- Se recrean los tres, palabra por palabra, SIN `cif`. Las otras ocho columnas
-- compartidas (plan, pending_plan, stripe_customer_id, la suspensión, el logo y
-- el color) siguen copiándose igual: esta migración va del CIF y de nada más.

create or replace function public.salon_entra_en_una_cuenta()
returns trigger language plpgsql security definer
set search_path = public, pg_temp as $$
begin
  if new.account_id is null then
    insert into public.accounts (nombre, plan, pending_plan, stripe_customer_id,
                                 suspendido, suspendido_at, suspendido_por,
                                 logo_url, accent_color)
    values (coalesce(nullif(trim(new.nombre_comercial), ''), 'Cuenta'),
            new.plan, new.pending_plan, new.stripe_customer_id,
            new.suspendido, new.suspendido_at, new.suspendido_por,
            new.logo_url, new.accent_color)
    returning id into new.account_id;
  else
    -- Centro que entra en una cuenta que YA existe: manda la cuenta.
    select a.plan, a.pending_plan, a.stripe_customer_id, a.suspendido,
           a.suspendido_at, a.suspendido_por, a.logo_url, a.accent_color
      into new.plan, new.pending_plan, new.stripe_customer_id, new.suspendido,
           new.suspendido_at, new.suspendido_por, new.logo_url,
           new.accent_color
    from public.accounts a where a.id = new.account_id;
  end if;
  return new;
end;
$$;

create or replace function public.copiar_de_salon_a_cuenta()
returns trigger language plpgsql security definer
set search_path = public, pg_temp as $$
begin
  update public.accounts a set
    plan = new.plan, pending_plan = new.pending_plan,
    stripe_customer_id = new.stripe_customer_id,
    suspendido = new.suspendido, suspendido_at = new.suspendido_at,
    suspendido_por = new.suspendido_por,
    logo_url = new.logo_url, accent_color = new.accent_color
  where a.id = new.account_id
    -- La guarda que cierra el bucle: si ya dice lo mismo, no se escribe ninguna
    -- fila y el trigger del otro lado no llega a dispararse.
    and (a.plan, a.pending_plan, a.stripe_customer_id, a.suspendido,
         a.suspendido_at, a.suspendido_por, a.logo_url, a.accent_color)
        is distinct from
        (new.plan, new.pending_plan, new.stripe_customer_id, new.suspendido,
         new.suspendido_at, new.suspendido_por, new.logo_url,
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
    suspendido_por = new.suspendido_por,
    logo_url = new.logo_url, accent_color = new.accent_color
  where s.account_id = new.id
    and (s.plan, s.pending_plan, s.stripe_customer_id, s.suspendido,
         s.suspendido_at, s.suspendido_por, s.logo_url, s.accent_color)
        is distinct from
        (new.plan, new.pending_plan, new.stripe_customer_id, new.suspendido,
         new.suspendido_at, new.suspendido_por, new.logo_url,
         new.accent_color);
  return new;
end;
$$;

-- Los `after update of (...)` llevan la lista de columnas dentro de la
-- definición del trigger, no de la función: hay que rehacerlos para que `cif`
-- salga de esa lista. Si no, el `drop column` de abajo falla por dependencia.
drop trigger if exists salon_copia_a_cuenta on public.salons;
create trigger salon_copia_a_cuenta
  after update of plan, pending_plan, stripe_customer_id, suspendido,
    suspendido_at, suspendido_por, logo_url, accent_color
  on public.salons for each row execute function public.copiar_de_salon_a_cuenta();

drop trigger if exists cuenta_copia_a_salones on public.accounts;
create trigger cuenta_copia_a_salones
  after update of plan, pending_plan, stripe_customer_id, suspendido,
    suspendido_at, suspendido_por, logo_url, accent_color
  on public.accounts for each row execute function public.copiar_de_cuenta_a_salones();

-- ── 2 · y ahora sí, la columna ────────────────────────────────────────────

alter table public.salons drop column cif;

commit;
