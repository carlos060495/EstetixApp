-- 0079 · Lote de arreglos pequeños del programa de referidos.
--
-- Carlos, 07-10-2026. Tres cosas de la base (la cuarta, el ojo de las
-- contraseñas, es solo del frontend):
--
-- ═══ 1. LO QUE DECLARA QUIEN SE APUNTA (`tipo_declarado`, `nombre_comercial`) ═══
--
-- El formulario público pregunta si es una persona o una empresa y, si es
-- empresa, su nombre comercial. ES UNA DECLARACIÓN, NO UNA DECISIÓN: lo que
-- escribe un desconocido no fija nada. `referidos.tipo` (individual | agencia)
-- lo sigue eligiendo el panel al aprobar, y la pantalla de aprobación enseña lo
-- declarado al lado. El motivo: una agencia que se llame «María Pérez» se
-- aprobaba como persona sin pestañear, y luego no podía crear trabajadores.
--
--   · Columnas nuevas, NULAS en todos los que ya existen (no se sabe y no se
--     inventa). Lo declarado no se puede cambiar después (trigger): es lo que
--     la persona escribió, no un dato que se mantenga.
--   · UNA AGENCIA SOLO EXISTE TRAS UNA APROBACIÓN: `tipo = 'agencia'` exige
--     `aprobado_at` (restricción `una_agencia_la_decide_el_panel`). El registro
--     público no escribe ni `tipo` ni `aprobado_at`; lo vigila el backend
--     (`test_lo_que_declara_quien_se_apunta.py`) y esto lo vigila la base.
--
-- ═══ 2. CONVERTIR UN REFERIDO (`cambiar_tipo_de_referido`) ═══
--
-- Solo el owner (lo impone la ruta), con motivo escrito y registro de quién y
-- cuándo (`cambios_de_tipo`, inmutable como `cambios_de_minimo`).
--
--   · LA FUNCIÓN TOCA UNA SOLA COLUMNA: `referidos.tipo`. Mismo código, mismas
--     atribuciones, mismo porcentaje (el suyo y los congelados), mismo
--     devengado, misma cuenta de Stripe, mismo saldo. Es la misma contraparte;
--     lo único que gana una agencia es poder crear trabajadores. Sus salones de
--     antes se quedan sin trabajador, que es la verdad: los trajo ella misma.
--     Lo vigila `test_lo_que_declara_quien_se_apunta_base_real.py`, recorriendo
--     TODAS las tablas que apuntan a `referidos` (de `pg_constraint`).
--   · DE AGENCIA A PERSONA, SOLO SIN TRABAJADORES ACTIVOS. Si le quedan, no se
--     convierte y se dice cuántos (`tiene_trabajadores`). Los dados de baja no
--     cuentan: siguen en su historia, su código ya no atribuye y su enlace ya
--     no abre.
--   · El trigger de la 0077 (una agencia con trabajadores sigue siéndolo) pasa
--     a mirar solo los ACTIVOS, que es la regla de Carlos.
--   · LA CARRERA: dar de alta un trabajador mientras la agencia pasa a persona.
--     `alta_de_trabajador` mira el tipo sin candado, así que un INSERT en
--     `trabajadores_de_agencia` comprueba ahora, con candado compartido sobre
--     la fila del referido, que sigue siendo agencia (trigger). La conversión
--     toma el candado exclusivo: una espera a la otra.
--
-- ═══ 3. UN REFERIDO QUE TAMBIÉN ES DUEÑO DE UN SALÓN (`referidos_duenos_de_salon`) ═══
--
-- Si el correo de quien se apunta coincide con el de la administradora de un
-- salón, el panel lo avisa en la pantalla de aprobación y dice de qué salón.
-- No se bloquea (hay casos legítimos), pero el owner tiene que verlo: si no,
-- es una clienta haciéndose su propio descuento del 10 %.
--
--   · Se DESCUBRE recorriendo las administradoras que existen (`users`, rol
--     admin) con los dos correos que tienen: el de su ficha y el de su usuario
--     de Auth (el que usa para entrar). Sin mayúsculas ni espacios alrededor.
--     Ninguna lista escrita a mano.
--
-- ═══ LO QUE NO SE TOCA ═══
--
-- El cálculo de la comisión, los pagos a Stripe, las atribuciones ya creadas,
-- los plazos de 30 y 90 días y el borrado de los 90 días. Ninguna función de
-- dinero se rehace aquí.
--
-- COLUMNAS OBLIGATORIAS: ninguna nueva en tablas que ya existen. La tabla nueva
-- (`cambios_de_tipo`): todas sus columnas `not null` las escribe la función
-- de aquí.
--
-- ═══ ORDEN DE DESPLIEGUE: 0079 → BACKEND + FRONTEND (a la vez) ═══
--
-- Compatible con el backend desplegado: no cambia la firma ni la respuesta de
-- nada de lo que ya llama. Ningún paso en Stripe. SE PARA si hubiera una
-- agencia sin aprobación (la restricción no entraría): no debería haber
-- ninguna, porque el único escritor de `tipo` es la aprobación.
--
-- Probado en local, contra la base de verdad:
-- `test_lo_que_declara_quien_se_apunta_base_real.py`.

begin;

-- ═══════════════ 1. lo que declara quien se apunta

alter table public.referidos
  add column tipo_declarado text check (tipo_declarado in ('persona', 'empresa')),
  add column nombre_comercial text check (char_length(btrim(nombre_comercial)) between 2 and 120),
  add constraint nombre_comercial_solo_de_empresa check (nombre_comercial is null or tipo_declarado = 'empresa'),
  add constraint una_agencia_la_decide_el_panel check (tipo = 'individual' or aprobado_at is not null);

comment on column public.referidos.tipo_declarado is
  'persona | empresa (0079): lo que DECLARÓ en el formulario. No fija nada: el tipo lo elige el panel al aprobar.';
comment on column public.referidos.nombre_comercial is
  'El nombre comercial que declaró, si dijo que es una empresa (0079).';

create function public.lo_declarado_no_se_cambia()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if new.tipo_declarado is distinct from old.tipo_declarado
     or new.nombre_comercial is distinct from old.nombre_comercial then
    raise exception 'lo que declaró quien se apuntó no se cambia' using errcode = '42501';
  end if;
  return new;
end;
$$;

create trigger referidos_lo_declarado_no_se_cambia
  before update of tipo_declarado, nombre_comercial on public.referidos
  for each row execute function public.lo_declarado_no_se_cambia();

-- ═══════════════ 2. convertir: el registro, las reglas y la función

create table public.cambios_de_tipo (
  id bigint generated always as identity primary key,
  -- SIN clave ajena, como todo registro (0066, `test_los_registros_sobreviven.py`):
  -- un registro no cuelga de nada. (Los referidos no se borran nunca, de todos modos.)
  referido_id uuid not null,
  tipo_antes text not null check (tipo_antes in ('individual', 'agencia')),
  tipo_despues text not null check (tipo_despues in ('individual', 'agencia')),
  motivo text not null check (char_length(btrim(motivo)) >= 10),
  -- El admin y su correo copiado, sin clave ajena (como todo nombre de un registro, 0057).
  hecho_por uuid not null,
  hecho_por_email text not null check (btrim(hecho_por_email) <> ''),
  hecho_at timestamptz not null default now(),
  constraint un_cambio_de_tipo_cambia_algo check (tipo_antes <> tipo_despues)
);

create index cambios_de_tipo_por_referido on public.cambios_de_tipo (referido_id, hecho_at desc);

comment on table public.cambios_de_tipo is
  'Cada conversión de un referido entre persona y agencia (0079): quién, cuándo y por qué. '
  'No se puede editar ni borrar.';

revoke all on public.cambios_de_tipo from public, anon, authenticated, service_role;
grant select on public.cambios_de_tipo to service_role;
alter table public.cambios_de_tipo enable row level security;

create trigger cambios_de_tipo_sin_update_ni_delete
  before update or delete on public.cambios_de_tipo
  for each row execute function public.las_comisiones_no_se_tocan();
create trigger cambios_de_tipo_sin_truncate
  before truncate on public.cambios_de_tipo
  for each statement execute function public.las_comisiones_no_se_tocan();

-- La regla de la 0077, con la de Carlos: solo cuentan los trabajadores ACTIVOS.
create or replace function public.una_agencia_con_trabajadores_sigue_siendolo()
returns trigger
language plpgsql
set search_path = public, pg_temp
as $$
begin
  if old.tipo = 'agencia' and new.tipo <> 'agencia'
     and exists (select 1 from public.trabajadores_de_agencia t
                 where t.agencia_id = new.id and t.de_baja_at is null) then
    raise exception 'una agencia con trabajadores activos no puede pasar a ser individual' using errcode = '23514';
  end if;
  return new;
end;
$$;

-- Un trabajador solo entra en una agencia que SIGUE siéndolo: con candado
-- compartido, para que una conversión a la vez espere (ver la cabecera).
create function public.un_trabajador_solo_entra_en_una_agencia()
returns trigger
language plpgsql
set search_path = public, pg_temp
as $$
begin
  perform 1 from public.referidos r where r.id = new.agencia_id and r.tipo = 'agencia' for share;
  if not found then
    raise exception 'un trabajador solo entra en una agencia' using errcode = '23514';
  end if;
  return new;
end;
$$;

create trigger trabajadores_solo_en_una_agencia
  before insert on public.trabajadores_de_agencia
  for each row execute function public.un_trabajador_solo_entra_en_una_agencia();

create function public.cambiar_tipo_de_referido(
  p_referido uuid,
  p_tipo text,
  p_motivo text,
  p_admin uuid,
  p_admin_email text
)
returns table (resultado text, trabajadores_activos int)
language plpgsql
security definer
set search_path = public, pg_temp
as $$
#variable_conflict use_column
declare
  v_ref public.referidos;
  v_activos int;
begin
  if p_tipo is null or p_tipo not in ('individual', 'agencia') then
    raise exception 'cambiar_tipo_de_referido: tipo % no existe', p_tipo using errcode = '22023';
  end if;
  if p_motivo is null or char_length(btrim(p_motivo)) < 10 then
    raise exception 'cambiar_tipo_de_referido: hace falta un motivo escrito' using errcode = '22023';
  end if;
  if p_admin is null or coalesce(btrim(p_admin_email), '') = '' then
    raise exception 'cambiar_tipo_de_referido: falta quién lo hace' using errcode = '22023';
  end if;

  -- Candado exclusivo: un alta de trabajador a la vez espera (y al revés).
  select * into v_ref from public.referidos r where r.id = p_referido for update;
  if not found then
    resultado := 'no_existe'; return next; return;
  end if;
  if v_ref.estado <> 'aprobado' then
    -- Uno pendiente se decide al aprobarlo; uno rechazado, no se toca.
    resultado := 'no_aprobado'; return next; return;
  end if;
  if v_ref.tipo = p_tipo then
    resultado := 'ya_es'; return next; return;
  end if;
  if p_tipo = 'individual' then
    select count(*) into v_activos from public.trabajadores_de_agencia t
    where t.agencia_id = p_referido and t.de_baja_at is null;
    if v_activos > 0 then
      resultado := 'tiene_trabajadores'; trabajadores_activos := v_activos; return next; return;
    end if;
  end if;

  -- UNA SOLA COLUMNA. Nada de lo suyo cambia (ver la cabecera).
  update public.referidos set tipo = p_tipo where id = p_referido;
  insert into public.cambios_de_tipo (referido_id, tipo_antes, tipo_despues, motivo, hecho_por, hecho_por_email)
  values (p_referido, v_ref.tipo, p_tipo, btrim(p_motivo), p_admin, btrim(p_admin_email));

  resultado := 'cambiado'; trabajadores_activos := 0; return next;
end;
$$;

comment on function public.cambiar_tipo_de_referido(uuid, text, text, uuid, text) is
  'Convierte un referido aprobado entre persona y agencia (0079). Solo cambia `tipo`. Devuelve '
  'cambiado | no_existe | no_aprobado | ya_es | tiene_trabajadores (con cuántos activos le quedan).';

revoke all on function public.cambiar_tipo_de_referido(uuid, text, text, uuid, text) from public, anon, authenticated;
grant execute on function public.cambiar_tipo_de_referido(uuid, text, text, uuid, text) to service_role;

-- ═══════════════ 3. los referidos que también son dueños de un salón

create function public.referidos_duenos_de_salon()
returns table (referido_id uuid, cuenta_id uuid, cuenta_nombre text, salones text[])
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  -- Las administradoras que existen, con sus dos correos (ver la cabecera).
  with admins as (
    select distinct u.account_id, c.correo
    from public.users u
    left join auth.users au on au.id = u.auth_user_id
    cross join lateral (values (u.email), (au.email)) as c (correo)
    where u.rol = 'admin' and c.correo is not null and btrim(c.correo) <> ''
  )
  select distinct on (r.id, a.account_id) r.id, a.account_id, ac.nombre,
         array(select s.nombre_comercial from public.salons s where s.account_id = a.account_id
               order by s.created_at, s.id)
  from public.referidos r
  join admins a on lower(btrim(a.correo)) = lower(btrim(r.email))
  left join public.accounts ac on ac.id = a.account_id
  order by r.id, a.account_id;
$$;

comment on function public.referidos_duenos_de_salon() is
  'Los referidos cuyo correo es el de la administradora de un salón (0079), y de qué cuenta y centros. '
  'Para avisar al aprobar; no bloquea nada.';

revoke all on function public.referidos_duenos_de_salon() from public, anon, authenticated;
grant execute on function public.referidos_duenos_de_salon() to service_role;

commit;
