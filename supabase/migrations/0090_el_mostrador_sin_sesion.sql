-- 0090 · EL MOSTRADOR NO GUARDA SESIÓN DE LA BASE (Carlos, 20-10-2026).
--
-- ═══ EL AGUJERO ═══
--
-- La tablet del mostrador llevaba la sesión de la administradora: un token de
-- Supabase al alcance del JavaScript, con el que se habla con PostgREST y con
-- GoTrue (cambiar la contraseña, por ejemplo) sin pasar por nuestro backend. El
-- PIN de gestión solo escondía pantallas.
--
-- ═══ LO QUE CAMBIA ═══
--
-- Un dispositivo marcado como mostrador no tiene token de la base. Tiene una
-- cookie httpOnly del backend cuyo valor es un secreto aleatorio; aquí se guarda
-- solo su huella (sha256). El token del salón lo acuña y lo guarda el backend,
-- como en el modo soporte. Con esa cookie el backend contesta a las rutas de la
-- agenda y el fichaje (`app/core/mostrador.py`, una lista) y a nada más, salvo
-- con la GESTIÓN ABIERTA en ese dispositivo: `gestion_hasta`, que el PIN pone a
-- diez minutos y cada uso renueva.
--
--   · SIN PERMISOS PARA LA SESIÓN. Ni `anon` ni `authenticated` leen ni tocan
--     esta tabla: si la sesión pudiera escribir `gestion_hasta` se abriría la
--     gestión sola. Solo `service_role`, desde el backend.
--   · UNA SOLA CLAVE AJENA, a `users`. NO a `salons`: dos claves ajenas harían
--     de esta tabla un puente entre `users` y `salons`, y PostgREST dejaría de
--     saber qué camino tomar en los embeds que hoy van sin nombre. `salon_id` es
--     el centro del mostrador y lo valida el backend en cada petición; si el
--     centro deja de ser de su cuenta, el dispositivo deja de valer.
--   · SE VA CON LA FICHA de quien lo activó (`on delete cascade`): borrar la
--     cuenta se lo lleva, y no hay registro que conservar.
--   · RETIRAR NO BORRA: `retirado_el`. Salir del modo mostrador lo pone.
--
-- LAS REGLAS DE LA BASE NO CAMBIAN. Ninguna política, ninguna función.
--
-- ═══ ORDEN DE DESPLIEGUE: 0090 → backend → frontend ═══
--
-- Con la 0090 y el backend viejo no pasa nada: nadie escribe la tabla. Con el
-- backend nuevo y el frontend viejo tampoco: nadie activa un mostrador, y sin
-- cookie todo va como hoy.
--
-- Para deshacer (primero frontend y backend anteriores): drop table.

create table public.dispositivos_mostrador (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.users(id) on delete cascade,
  salon_id uuid not null,
  token_hash text not null unique check (token_hash ~ '^[0-9a-f]{64}$'),
  creado_el timestamptz not null default now(),
  gestion_hasta timestamptz,
  retirado_el timestamptz
);

create index dispositivos_mostrador_user_id on public.dispositivos_mostrador (user_id);

comment on table public.dispositivos_mostrador is
  'Dispositivos en modo mostrador (0090): sin token de la base. token_hash = sha256 del valor de la cookie '
  'httpOnly. gestion_hasta = la gestión abierta EN ESE dispositivo (PIN, 10 min sin usarla). Solo service_role.';

alter table public.dispositivos_mostrador enable row level security;
revoke all on public.dispositivos_mostrador from public, anon, authenticated;
grant select, insert, update on public.dispositivos_mostrador to service_role;
