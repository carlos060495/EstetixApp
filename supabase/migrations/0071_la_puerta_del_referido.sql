-- 0071 · La puerta del referido: su contraseña, su sesión, su portal.
--
-- Carlos, 05-10-2026: un referido entra a ver los salones que ha traído, y NO
-- entra por ninguna de las puertas que ya existen:
--
--   · NO por Supabase Auth. Desde la 0063 el hook no le da token a quien no es
--     la administradora de un salón, y eso no se toca.
--   · NO por `admin_users`. Ese panel ve todas las cuentas: un rol «referido»
--     ahí sería lo que se acaba de quitar (0069) con otro nombre.
--
-- Se copia la FORMA del panel de plataforma —una cookie propia, el origen
-- comprobado en lo que cambia algo— pero no su tabla. Todo lo de aquí es de
-- PLATAFORMA, como `referidos` (0029): RLS activada y SIN políticas, y permisos
-- solo para service_role. El referido no recibe ningún token que llegue a
-- Postgres: la frontera la pone el backend, en un solo sitio
-- (`core/sesion_referido.py`), con `referido_de_la_sesion` de aquí abajo.
--
-- ═══ LO QUE HAY ═══
--
--   · `referidos.password_hash`: argon2id, lo escribe SOLO `referido_usar_enlace`.
--     Nosotros no ponemos ni conocemos la contraseña: se la pone él con un
--     enlace de un solo uso que le llega por correo al aprobarlo.
--   · `referido_enlaces`: esos enlaces (el de bienvenida y el de recuperar), con
--     el HASH del token, nunca el token. Un solo uso, caducan, y pedir otro
--     anula los que estuvieran vivos.
--   · `referido_sesiones`: las sesiones, también por hash. El estado del
--     referido se mira en CADA petición: rechazarlo corta su sesión en la
--     siguiente, no al día siguiente.
--   · `referido_intentos`: los fallos al entrar, POR CORREO Y EXISTA O NO, con
--     la misma escalera que el PIN del quiosco (0047): cada 5 fallos, una
--     espera de 1, 5 y después 15 minutos. Contar también los correos que no
--     existen es lo que hace que «no existe» y «contraseña mala» se vean igual.
--
-- ═══ EL ENLACE, DOS VECES ═══
--
--   · Usado dos veces: la segunda no vale. `referido_usar_enlace` lo marca como
--     usado en la MISMA sentencia que lo busca libre (`update … where usado_at
--     is null … returning`): dos peticiones a la vez no pueden pasar las dos.
--   · Se pide otro antes de usar el primero: el primero queda anulado al crear
--     el segundo (`referido_nuevo_enlace`, en una transacción). Vale solo el
--     último.
--   · Al poner la contraseña se cierran todas sus sesiones abiertas.
--
-- ═══ ORDEN DE DESPLIEGUE: 0071 → BACKEND → FRONTEND ═══
--
-- El backend nuevo llama a estas funciones al aprobar un referido. La regla de
-- la columna obligatoria: `password_hash` y `contrasena_puesta_at` nacen
-- vacías y ningún backend desplegado las lee.

begin;

-- ═══════════════ 1. la contraseña

alter table public.referidos add column password_hash text;
alter table public.referidos add column contrasena_puesta_at timestamptz;

comment on column public.referidos.password_hash is
  'argon2id. La escribe SOLO referido_usar_enlace (0071). Ninguna respuesta del backend la devuelve.';

-- ═══════════════ 2. los enlaces de un solo uso

create table public.referido_enlaces (
  token_hash text primary key,
  referido_id uuid not null references public.referidos (id) on delete cascade,
  para text not null check (para in ('bienvenida', 'recuperar')),
  creado_at timestamptz not null default now(),
  caduca_at timestamptz not null,
  usado_at timestamptz,
  anulado_at timestamptz
);
create index referido_enlaces_por_referido on public.referido_enlaces (referido_id);

-- ═══════════════ 3. las sesiones

create table public.referido_sesiones (
  token_hash text primary key,
  referido_id uuid not null references public.referidos (id) on delete cascade,
  creada_at timestamptz not null default now(),
  caduca_at timestamptz not null
);
create index referido_sesiones_por_referido on public.referido_sesiones (referido_id);

-- ═══════════════ 4. los intentos de entrar, por correo, exista o no

create table public.referido_intentos (
  email text primary key,
  fallos integer not null default 0,
  ultimo_fallo_at timestamptz,
  bloqueado_hasta timestamptz
);

-- ═══════════════ 5. de plataforma: cerradas a todo token, solo service_role

alter table public.referido_enlaces enable row level security;
alter table public.referido_sesiones enable row level security;
alter table public.referido_intentos enable row level security;
revoke all on public.referido_enlaces, public.referido_sesiones, public.referido_intentos
  from anon, authenticated, service_role;
grant select, insert, update, delete on public.referido_enlaces, public.referido_sesiones,
  public.referido_intentos to service_role;

-- ═══════════════ 6. las funciones

-- Un intento de entrar se APUNTA ANTES de comprobar la contraseña, con la fila
-- bloqueada: una ráfaga de peticiones a la vez cuenta entera (la lección del
-- quiosco, 0047). Misma escalera: cada 5 fallos, 1, 5 y después 15 minutos.
create function public.referido_intento(p_email text)
returns table (permitido boolean, bloqueado_hasta timestamptz, fallos integer)
language plpgsql
set search_path = public, pg_temp
as $$
declare
  v_email text := lower(btrim(p_email));
  v_fila public.referido_intentos%rowtype;
  v_ahora timestamptz := now();
  v_cuenta integer;
  v_hasta timestamptz;
begin
  insert into public.referido_intentos (email) values (v_email) on conflict (email) do nothing;
  select * into v_fila from public.referido_intentos where email = v_email for update;

  if v_fila.bloqueado_hasta is not null and v_fila.bloqueado_hasta > v_ahora then
    return query select false, v_fila.bloqueado_hasta, v_fila.fallos;
    return;
  end if;

  v_cuenta := v_fila.fallos;
  if v_fila.ultimo_fallo_at is not null and v_fila.ultimo_fallo_at < v_ahora - interval '24 hours' then
    v_cuenta := 0;
  end if;
  v_cuenta := v_cuenta + 1;

  v_hasta := null;
  if v_cuenta % 5 = 0 then
    v_hasta := v_ahora + make_interval(mins => case v_cuenta / 5 when 1 then 1 when 2 then 5 else 15 end);
  end if;

  update public.referido_intentos
  set fallos = v_cuenta, ultimo_fallo_at = v_ahora, bloqueado_hasta = v_hasta
  where email = v_email;

  return query select true, v_hasta, v_cuenta;
end;
$$;

-- Entró: el contador vuelve a cero. Devuelve true (y no void) porque PostgREST
-- contesta vacío a una función void, y el cliente espera JSON.
create function public.referido_entrada_buena(p_email text)
returns boolean
language sql
set search_path = public, pg_temp
as $$
  delete from public.referido_intentos where email = lower(btrim(p_email));
  select true;
$$;

-- Un enlace nuevo ANULA los vivos de ese referido: vale solo el último.
create function public.referido_nuevo_enlace(p_referido uuid, p_para text, p_token_hash text, p_minutos integer)
returns boolean
language plpgsql
set search_path = public, pg_temp
as $$
begin
  update public.referido_enlaces set anulado_at = now()
  where referido_id = p_referido and usado_at is null and anulado_at is null;
  insert into public.referido_enlaces (token_hash, referido_id, para, caduca_at)
  values (p_token_hash, p_referido, p_para, now() + make_interval(mins => p_minutos));
  return true;
end;
$$;

-- Usar el enlace: se marca como usado EN LA MISMA SENTENCIA que lo encuentra
-- libre, así que dos usos a la vez no pueden pasar los dos. Solo para un
-- referido APROBADO. Pone la contraseña y cierra todas sus sesiones.
-- Devuelve el id del referido, o null si el enlace no vale.
create function public.referido_usar_enlace(p_token_hash text, p_password_hash text)
returns uuid
language plpgsql
set search_path = public, pg_temp
as $$
declare
  v_referido uuid;
begin
  update public.referido_enlaces e set usado_at = now()
  where e.token_hash = p_token_hash and e.usado_at is null and e.anulado_at is null
    and e.caduca_at > now()
    and exists (select 1 from public.referidos r where r.id = e.referido_id and r.estado = 'aprobado')
  returning e.referido_id into v_referido;

  if v_referido is null then
    return null;
  end if;

  update public.referidos set password_hash = p_password_hash, contrasena_puesta_at = now()
  where id = v_referido;
  delete from public.referido_sesiones where referido_id = v_referido;
  return v_referido;
end;
$$;

-- QUIÉN ES, en cada petición: la sesión viva de un referido APROBADO y con
-- contraseña. Es la única forma de que el backend sepa quién está al otro lado.
create function public.referido_de_la_sesion(p_token_hash text)
returns table (id uuid, nombre text, email text, codigo text, datos_pago text)
language sql
stable
set search_path = public, pg_temp
as $$
  select r.id, r.nombre, r.email, r.codigo, r.datos_pago
  from public.referido_sesiones s
  join public.referidos r on r.id = s.referido_id
  where s.token_hash = p_token_hash
    and s.caduca_at > now()
    and r.estado = 'aprobado'
    and r.password_hash is not null;
$$;

-- LO QUE VE: los salones que ha traído —los que cuentan, con la misma regla que
-- el panel (`altas_que_cuentan`, 0070)—, cuándo y si pagan. Nada más: ni
-- clientas, ni agendas, ni datos del salón. El nombre es el que se copió en la
-- atribución al darse de alta.
create function public.portal_del_referido(p_referido uuid)
returns table (cuenta_nombre text, atribuida_el timestamptz, situacion text)
language sql
stable
set search_path = public, pg_temp
as $$
  select a.cuenta_nombre, a.atribuida_el,
    case
      when not exists (select 1 from public.accounts c where c.id = a.cuenta_id) then 'cuenta_borrada'
      when exists (select 1 from public.subscriptions s where s.account_id = a.cuenta_id and s.estado = 'active') then 'paga'
      when exists (select 1 from public.subscriptions s where s.account_id = a.cuenta_id and s.estado = 'trialing') then 'en_prueba'
      when exists (select 1 from public.subscriptions s where s.account_id = a.cuenta_id
                   and s.estado in ('past_due', 'unpaid', 'incomplete')) then 'pago_pendiente'
      else 'no_paga'
    end
  from public.altas_atribuidas a
  where a.referido_id = p_referido
    and (exists (select 1 from public.accounts c where c.id = a.cuenta_id)
         or exists (select 1 from public.cuentas_borradas b where b.account_id = a.cuenta_id))
  order by a.atribuida_el desc;
$$;

revoke all on function public.referido_intento(text), public.referido_entrada_buena(text),
  public.referido_nuevo_enlace(uuid, text, text, integer), public.referido_usar_enlace(text, text),
  public.referido_de_la_sesion(text), public.portal_del_referido(uuid)
  from public, anon, authenticated;
grant execute on function public.referido_intento(text), public.referido_entrada_buena(text),
  public.referido_nuevo_enlace(uuid, text, text, integer), public.referido_usar_enlace(text, text),
  public.referido_de_la_sesion(text), public.portal_del_referido(uuid)
  to service_role;

commit;
