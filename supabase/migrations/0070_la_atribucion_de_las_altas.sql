-- 0070 · La atribución de las altas: qué referido trajo cada cuenta.
--
-- Hasta aquí el código de un referido se generaba al aprobarlo y no lo leía
-- nadie (0029). Si mañana un referido traía un salón, no quedaba constancia de
-- nada, y cada día sin esto es información que se pierde para siempre. Carlos,
-- 05-10-2026: la comisión es A PERPETUIDAD, así que de estas filas salen pagos
-- durante años.
--
-- ═══ LO QUE HAY ═══
--
--   · `altas_atribuidas`: una fila por cuenta que llegó con el código de un
--     referido APROBADO. El referido, la cuenta, el nombre de la cuenta copiado
--     dentro, el código tal como llegó y la fecha. Una por cuenta como mucho
--     (`una_atribucion_por_cuenta`): la primera gana.
--   · `codigos_que_no_valieron`: un código que llegó al alta y no valía (no
--     existe, o es de un referido pendiente o rechazado). El alta sigue igual:
--     un salón que quiere pagarnos no se queda fuera por un enlace mal copiado.
--     Pero queda escrito: el día que un referido diga «mi enlace no funciona»,
--     esto dice si tiene razón.
--   · `atribuir_alta(cuenta, código)`: mira el código y escribe UNA de las dos
--     cosas, en una sola transacción. La llama el alta (`routers/auth.py`) en
--     el mismo bloque que crea la cuenta.
--
-- ═══ INMUTABLES, COMO EL REGISTRO DE DINERO (0054) ═══
--
-- Sin UPDATE, sin DELETE y sin TRUNCATE para nadie: lo impiden los permisos
-- (service_role solo lee e inserta) y, por si un permiso se concede algún día
-- por descuido, un trigger que lo rechaza también para el superusuario.
--
-- ═══ SIN CLAVE AJENA A LA CUENTA ═══
--
-- Igual que los demás registros (0057, 0066): la cuenta se borra a los 90 días
-- (0068) y la comisión que ya se devengó sigue existiendo. Por eso el nombre de
-- la cuenta va copiado dentro, y `cuenta_id` es solo un dato. El plan de
-- borrado camina las claves ajenas: sin clave, no llega aquí. Lo vigilan
-- `test_nuestros_registros_base_real.py` (las dos tablas están en
-- `core/simulacro.SE_QUEDA`) y `test_la_atribucion_base_real.py`.
--
-- Al REFERIDO sí, y con `restrict`: un referido con altas no se puede borrar,
-- porque se llevaría por delante a quién hay que pagar.
--
-- ═══ EL CÓDIGO SE COMPARA SIN MAYÚSCULAS Y SIN ESPACIOS ALREDEDOR ═══
--
-- La gente lo copia y lo pega a mano, de un WhatsApp. Solo eso: no se quitan
-- caracteres de en medio. `normalizar_codigo_referido` del backend quita todo
-- lo que no sea del alfabeto, y aquí NO se usa a propósito: con una letra de
-- más o de menos podría convertir un código mal copiado en el de OTRA persona,
-- y la comisión es a perpetuidad. Un código que no casa se apunta como que no
-- valió, y eso se puede revisar; una atribución a quien no era, no.
-- El índice `referidos_codigo_normalizado` impide que dos códigos casen igual.
--
-- ═══ SI DOS ALTAS LLEGAN A LA VEZ CON EL MISMO CÓDIGO ═══
--
-- Cada alta crea su propia cuenta, así que son dos filas distintas y las dos se
-- escriben: los bloqueos `for share` sobre el referido son compatibles entre sí
-- y no se esperan. Lo que sí se ordena es el alta frente a un cambio del
-- referido: rechazarlo es un UPDATE, que espera a que terminen las atribuciones
-- en curso. Una atribución que empieza cuando el rechazo ya está CONFIRMADO lo
-- ve; una que llega mientras el rechazo todavía espera, no: para ella el
-- referido sigue aprobado, porque aún lo está. Medido en local con cuatro
-- sesiones (05-10-2026): A atribuye y se queda abierta 3 s; B (otra cuenta,
-- mismo código) termina en 0,1 s sin esperar; el rechazo espera hasta que A
-- confirma; D, que llegó con el rechazo pendiente, queda atribuida.
--
-- ═══ ORDEN DE DESPLIEGUE: 0070 → BACKEND → FRONTEND ═══
--
-- El backend nuevo llama a `atribuir_alta`: sin la función, un alta con código
-- fallaría. Y el frontend nuevo manda `codigo_referido`, que el backend viejo
-- rechaza (`extra = forbid`): un alta con código daría 422. La regla de la
-- columna obligatoria: no añade columnas a tablas que ya existen.
--
-- ═══ CÓMO SE PROBÓ ═══
--
-- En local, contra la base de verdad (`test_la_atribucion_base_real.py`): el
-- código con mayúsculas y espacios, el pendiente, el rechazado, el que no
-- existe, la segunda atribución de una misma cuenta, el UPDATE y el DELETE
-- rechazados, y el plan de borrado de una cuenta con su atribución. Cada guarda
-- con su control dentro de una transacción que se deshace.

begin;

-- ═══════════════ 1. las altas atribuidas

create table public.altas_atribuidas (
  id bigint generated always as identity primary key,
  referido_id uuid not null references public.referidos (id) on delete restrict,
  -- SIN clave ajena, a propósito: ver la cabecera.
  cuenta_id uuid not null,
  cuenta_nombre text not null,
  codigo_llegado text not null,
  atribuida_el timestamptz not null default now(),
  constraint una_atribucion_por_cuenta unique (cuenta_id)
);

comment on table public.altas_atribuidas is
  'Qué referido trajo cada cuenta (0070). De aquí salen comisiones a perpetuidad: '
  'no se puede editar ni borrar, y no cuelga de la cuenta (sobrevive a su borrado).';

create index altas_atribuidas_por_referido on public.altas_atribuidas (referido_id);

-- ═══════════════ 2. los códigos que no valieron

create table public.codigos_que_no_valieron (
  id bigint generated always as identity primary key,
  codigo_llegado text not null,
  motivo text not null check (motivo in ('no_existe', 'pendiente', 'rechazado')),
  llegado_el timestamptz not null default now()
);

comment on table public.codigos_que_no_valieron is
  'Un código de referido que llegó al alta y no valía (0070). El alta siguió igual. '
  'No se puede editar ni borrar.';

-- ═══════════════ 3. inmutables: permisos y trigger

revoke all on public.altas_atribuidas, public.codigos_que_no_valieron
  from anon, authenticated, service_role;
grant select, insert on public.altas_atribuidas, public.codigos_que_no_valieron to service_role;

alter table public.altas_atribuidas enable row level security;
alter table public.codigos_que_no_valieron enable row level security;

create function public.la_atribucion_no_se_toca()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  raise exception '% no se puede % : es un registro', tg_table_name, tg_op
    using errcode = '42501';
end;
$$;

create trigger altas_atribuidas_sin_update_ni_delete
  before update or delete on public.altas_atribuidas
  for each row execute function public.la_atribucion_no_se_toca();
create trigger altas_atribuidas_sin_truncate
  before truncate on public.altas_atribuidas
  for each statement execute function public.la_atribucion_no_se_toca();
create trigger codigos_que_no_valieron_sin_update_ni_delete
  before update or delete on public.codigos_que_no_valieron
  for each row execute function public.la_atribucion_no_se_toca();
create trigger codigos_que_no_valieron_sin_truncate
  before truncate on public.codigos_que_no_valieron
  for each statement execute function public.la_atribucion_no_se_toca();

-- ═══════════════ 4. dos códigos no pueden casar igual

create unique index referidos_codigo_normalizado
  on public.referidos (upper(btrim(codigo)))
  where codigo is not null;

-- ═══════════════ 5. mirar el código y apuntarlo

create function public.atribuir_alta(p_cuenta uuid, p_codigo text)
returns text
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  -- Tal como llegó, sin tocar (solo acotado: viene de un enlace público).
  v_llegado text := left(p_codigo, 64);
  -- SIN MAYÚSCULAS Y SIN ESPACIOS ALREDEDOR, y nada más: ver la cabecera.
  -- `\s` no incluye el espacio duro (chr(160)), que es justo el que pega un móvil.
  v_buscado text := upper(regexp_replace(coalesce(p_codigo, ''), '^[\s' || chr(160) || ']+|[\s' || chr(160) || ']+$', '', 'g'));
  v_referido public.referidos;
  v_nombre text;
  v_escritas int;
begin
  if v_buscado = '' then
    return 'sin_codigo';
  end if;

  -- `for share`: un rechazo a la vez espera a que esta atribución termine.
  select * into v_referido from public.referidos r
  where upper(btrim(r.codigo)) = v_buscado
  for share;

  if not found then
    insert into public.codigos_que_no_valieron (codigo_llegado, motivo) values (v_llegado, 'no_existe');
    return 'no_existe';
  end if;
  if v_referido.estado::text <> 'aprobado' then
    insert into public.codigos_que_no_valieron (codigo_llegado, motivo)
    values (v_llegado, v_referido.estado::text);
    return v_referido.estado::text;
  end if;

  select a.nombre into v_nombre from public.accounts a where a.id = p_cuenta;
  if not found then
    raise exception 'atribuir_alta: la cuenta % no existe', p_cuenta;
  end if;

  -- Una por cuenta: si ya la tiene, gana la primera y no se escribe nada.
  insert into public.altas_atribuidas (referido_id, cuenta_id, cuenta_nombre, codigo_llegado)
  values (v_referido.id, p_cuenta, v_nombre, v_llegado)
  on conflict on constraint una_atribucion_por_cuenta do nothing;
  get diagnostics v_escritas = row_count;
  return case when v_escritas = 1 then 'atribuida' else 'ya_atribuida' end;
end;
$$;

comment on function public.atribuir_alta(uuid, text) is
  'Mira el código de referido que llegó al alta y escribe la atribución o la constancia '
  'de que no valió (0070). Devuelve atribuida | ya_atribuida | no_existe | pendiente | rechazado | sin_codigo.';

revoke all on function public.atribuir_alta(uuid, text) from public, anon, authenticated;
grant execute on function public.atribuir_alta(uuid, text) to service_role;

-- ═══════════════ 6. las altas que CUENTAN (Carlos, 05-10-2026)

-- Una atribución cuenta si su cuenta existe, o si consta como borrada en el
-- registro de los 90 días (`cuentas_borradas`, 0068). Un alta deshecha —falló
-- el correo de confirmación después de apuntarla— no deja ninguna de las dos
-- cosas (el alta borra la cuenta que creó al deshacerse: `routers/auth.py`),
-- así que no suma. Sin mutar ni borrar la atribución, que es inmutable: la
-- cuenta se arregla en este lado. Es la ÚNICA lectura del panel; contar las
-- filas de la tabla a ciegas lo vigila `test_la_atribucion_base_real.py`.
create function public.altas_que_cuentan()
returns table (referido_id uuid, altas bigint)
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select a.referido_id, count(*)
  from public.altas_atribuidas a
  where exists (select 1 from public.accounts c where c.id = a.cuenta_id)
     or exists (select 1 from public.cuentas_borradas b where b.account_id = a.cuenta_id)
  group by a.referido_id;
$$;

revoke all on function public.altas_que_cuentan() from public, anon, authenticated;
grant execute on function public.altas_que_cuentan() to service_role;

commit;
