-- Demo data for local development. Mirrors CREDENCIALES_DEMO.md.
-- These are throwaway local credentials, never production secrets.
--
-- Includes a second tenant ("Salón Norte") purely so the isolation tests in
-- supabase/tests/ have a real "salon B" to prove salon A can't reach.

-- ---------------------------------------------------------------------------
-- Salons
--
-- Los dos centros de prueba. Su identificación fiscal va en el `update` a
-- `accounts` de más abajo, porque es de la cuenta (0040). Es texto libre, sin
-- reglas de ningún país: los números de aquí son solo de relleno. El país
-- (`cif_pais`) ya no se pide en pantalla, pero la columna sigue: la facturación
-- lo necesitará para el tipo de identificador de Stripe.
-- ---------------------------------------------------------------------------
insert into public.salons (id, nombre_comercial, direccion, telefono, email, plan, created_at)
values
  ('00000000-0000-0000-0000-000000000001', 'Salón Demo', 'Calle Mayor 1, Madrid', '+34600000001', 'info@salondemo.es', 'pro', now()),
  ('00000000-0000-0000-0000-000000000002', 'Salón Norte', 'Avenida Norte 9, Bilbao', '+34600000002', 'info@salonnorte.es', 'basico', now());

-- EL CIF VA A LA CUENTA, que es la que factura (0040), y `salons.cif` ya no
-- existe (0051). El trigger `salon_entra_en_una_cuenta` le crea su cuenta a
-- cada centro al insertarlo; aquí se le pone el número a esa cuenta.
update public.accounts a
   set cif = v.cif, cif_pais = 'ES'
  from (values
    ('00000000-0000-0000-0000-000000000001'::uuid, 'B12345674'),
    ('00000000-0000-0000-0000-000000000002'::uuid, 'B87654323')
  ) as v(salon_id, cif)
 where a.id = (select s.account_id from public.salons s where s.id = v.salon_id);

-- LAS DOS CUENTAS PAGAN (0086). Sin una suscripción viva, la base corta la
-- cuenta: ni escribe ni lee nada fuera de su agenda. El seed representa dos
-- salones que trabajan, así que cada uno tiene la suya, activa. Va aquí, antes
-- que cualquier fichaje: el fichaje de una cuenta cortada lo rechaza un trigger.
insert into public.subscriptions (account_id, salon_id, estado, plan, intervalo)
select s.account_id, s.id, 'active', s.plan, 'mensual'
from public.salons s
where s.id in ('00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000002');

-- ---------------------------------------------------------------------------
-- auth.users (Supabase Auth) for the salon admin login
-- ---------------------------------------------------------------------------
insert into auth.users (
  instance_id, id, aud, role, email, encrypted_password,
  email_confirmed_at, raw_app_meta_data, raw_user_meta_data,
  created_at, updated_at, confirmation_token, recovery_token,
  email_change_token_new, email_change
) values (
  '00000000-0000-0000-0000-000000000000',
  '10000000-0000-0000-0000-000000000001',
  'authenticated', 'authenticated',
  'carlos@salondemo.es',
  crypt('estetix2026', gen_salt('bf')),
  now(), '{"provider":"email","providers":["email"]}', '{}',
  now(), now(), '', '', '', ''
);

insert into auth.identities (
  id, provider_id, user_id, identity_data, provider, last_sign_in_at, created_at, updated_at
) values (
  gen_random_uuid(), '10000000-0000-0000-0000-000000000001', '10000000-0000-0000-0000-000000000001',
  '{"sub":"10000000-0000-0000-0000-000000000001","email":"carlos@salondemo.es"}', 'email', now(), now(), now()
);

-- ---------------------------------------------------------------------------
-- public.users — salon_id + rol injected into the JWT by the auth hook
-- ---------------------------------------------------------------------------
-- Solo la administradora tiene usuario (0012, 0063, 0069). Su PIN de quiosco,
-- como el de todo el equipo, va en su FICHA de `employees`, no aquí.
insert into public.users (id, salon_id, nombre, email, rol, auth_user_id)
values (
  '20000000-0000-0000-0000-000000000001',
  '00000000-0000-0000-0000-000000000001',
  'Carlos Muñoz', 'carlos@salondemo.es', 'admin',
  '10000000-0000-0000-0000-000000000001'
);

-- ---------------------------------------------------------------------------
-- employees — PIN hashes precomputed with Argon2id (see CREDENCIALES_DEMO.md)
-- ---------------------------------------------------------------------------
-- estado/telefono/email/pin_fichaje_hash añadidos en 0006_employees_perfil.
-- PINs del quiosco (Argon2id), en la ficha de cada uno:
-- Luis 4528 · Juan 1100 · Marta 7777 · Carlos 2024 · José 3030 · Ana 9090
-- EL PUESTO YA NO ES UN TEXTO EN `employees`. La migración 0027 lo sacó a su
-- propia tabla y dejó aquí una FK, porque un texto libre llamado `rol` hacía
-- creer que ese campo daba permisos —y un puesto no da ni permisos ni acceso:
-- solo la administradora tiene cuenta—. Este seed se quedó atrás, y por eso `supabase db reset` llevaba
-- desde entonces fallando justo aquí: sin entorno local sembrado, nadie volvió
-- a abrir el quiosco de fichaje en local.
insert into public.puestos (id, salon_id, nombre)
values
  ('31000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'Estilista sénior'),
  ('31000000-0000-0000-0000-000000000002', '00000000-0000-0000-0000-000000000001', 'Barbero'),
  ('31000000-0000-0000-0000-000000000003', '00000000-0000-0000-0000-000000000001', 'Manicurista'),
  ('31000000-0000-0000-0000-000000000004', '00000000-0000-0000-0000-000000000001', 'Encargado'),
  ('31000000-0000-0000-0000-000000000005', '00000000-0000-0000-0000-000000000001', 'Estilista'),
  ('31000000-0000-0000-0000-000000000006', '00000000-0000-0000-0000-000000000001', 'Recepción');

insert into public.employees (id, salon_id, user_id, nombre, puesto_id, comision_tipo, comision_servicios, comision_productos, estado, telefono, email, pin_fichaje_hash)
values
  ('30000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', null, 'Luis Ramírez', '31000000-0000-0000-0000-000000000001', 'porcentaje', 15, 5, 'activo', '600 45 67 89', 'luis@salondemo.es', '$argon2id$v=19$m=65536,t=3,p=4$0rHn4zDvPOYCvIYwa8a7hQ$WDvBnw40c4/ir/oXaJQidzkywGfMXKBJw2wpDapDzL8'),
  ('30000000-0000-0000-0000-000000000002', '00000000-0000-0000-0000-000000000001', null, 'Juan Pérez', '31000000-0000-0000-0000-000000000002', 'porcentaje', 12, 5, 'activo', '611 22 33 44', 'juan@salondemo.es', '$argon2id$v=19$m=65536,t=3,p=4$oDnPQvnOoQ9QxKO+eT0qDg$DmYoxFWn3bYmGxCRrTbB7TgErbeT2xyuzx9eJ2gT2os'),
  ('30000000-0000-0000-0000-000000000003', '00000000-0000-0000-0000-000000000001', null, 'Marta Soler', '31000000-0000-0000-0000-000000000003', 'porcentaje', 10, 5, 'activo', '622 33 44 55', 'marta@salondemo.es', '$argon2id$v=19$m=65536,t=3,p=4$XizTLqZy+VtyKtufptfIvA$aKvG9WHwmzYbXPEtBz9MdTvLf0FFC7Vo8I35LRlW6UU'),
  ('30000000-0000-0000-0000-000000000004', '00000000-0000-0000-0000-000000000001', '20000000-0000-0000-0000-000000000001', 'Carlos Muñoz', '31000000-0000-0000-0000-000000000004', 'porcentaje', 20, 8, 'activo', '600 12 34 56', 'carlos@salondemo.es', '$argon2id$v=19$m=65536,t=3,p=4$qE/iIH5aJxoa1Nerl1ubeA$eKX24hHU2ppKw1YX4FMZxXDnucqDl7Q1u3KlFaBVkHk'),
  ('30000000-0000-0000-0000-000000000005', '00000000-0000-0000-0000-000000000001', null, 'José Díaz', '31000000-0000-0000-0000-000000000005', 'porcentaje', 12, 5, 'pausa', '633 44 55 66', 'jose@salondemo.es', '$argon2id$v=19$m=65536,t=3,p=4$CWqmdkvA7oiLwI5NvAJF2g$uGeMF/LjSMScuV3sGjHvVncdmIdpJaGMkWgnMx56POA'),
  ('30000000-0000-0000-0000-000000000006', '00000000-0000-0000-0000-000000000001', null, 'Ana López', '31000000-0000-0000-0000-000000000006', 'fijo', 0, 0, 'activo', '644 55 66 77', 'ana@salondemo.es', '$argon2id$v=19$m=65536,t=3,p=4$eqPowiJjRngraj0FT16P7A$kYjvk0y2Idg+5M5h8QSLjZy26BxKfyB2OxPr66mToGI');

-- El equipo NO tiene usuario: ficha en el quiosco con el PIN de su ficha. Hasta
-- la 0069 aquí se creaban estilistas y una recepcionista con el PIN en `users`,
-- restos del primer diseño.

-- ---------------------------------------------------------------------------
-- Catalog
-- ---------------------------------------------------------------------------
insert into public.service_categories (id, salon_id, nombre, tipo, color) values
  ('40000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'Corte', 'servicio', '#C9A84C'),
  ('40000000-0000-0000-0000-000000000002', '00000000-0000-0000-0000-000000000001', 'Color', 'servicio', '#C5524A'),
  ('40000000-0000-0000-0000-000000000003', '00000000-0000-0000-0000-000000000001', 'Manicura', 'servicio', '#4FA86B'),
  ('40000000-0000-0000-0000-000000000004', '00000000-0000-0000-0000-000000000001', 'Cuidado capilar', 'producto', '#D9A441');

insert into public.services (salon_id, category_id, nombre, precio, duracion_min, visible_online) values
  ('00000000-0000-0000-0000-000000000001', '40000000-0000-0000-0000-000000000001', 'Corte caballero', 18.00, 30, true),
  ('00000000-0000-0000-0000-000000000001', '40000000-0000-0000-0000-000000000001', 'Corte señora', 25.00, 45, true),
  ('00000000-0000-0000-0000-000000000001', '40000000-0000-0000-0000-000000000002', 'Coloración completa', 55.00, 90, true),
  ('00000000-0000-0000-0000-000000000001', '40000000-0000-0000-0000-000000000003', 'Manicura semipermanente', 22.00, 45, true);

-- Servicios que puede realizar cada empleado (subselect: los servicios del
-- seed no tienen id fijo).
insert into public.employee_services (employee_id, service_id)
select '30000000-0000-0000-0000-000000000001'::uuid, id from public.services
  where salon_id = '00000000-0000-0000-0000-000000000001' and nombre in ('Corte caballero', 'Corte señora', 'Coloración completa')
union all
select '30000000-0000-0000-0000-000000000002'::uuid, id from public.services
  where salon_id = '00000000-0000-0000-0000-000000000001' and nombre = 'Corte caballero'
union all
select '30000000-0000-0000-0000-000000000003'::uuid, id from public.services
  where salon_id = '00000000-0000-0000-0000-000000000001' and nombre = 'Manicura semipermanente'
union all
select '30000000-0000-0000-0000-000000000004'::uuid, id from public.services
  where salon_id = '00000000-0000-0000-0000-000000000001' and nombre in ('Corte caballero', 'Corte señora');

-- ---------------------------------------------------------------------------
-- Clients — one with an allergy flagged, one VIP. Sin visitas ni gastado:
-- salen de sus citas completadas (vista 0058); las columnas se borran en la 0059.
-- ---------------------------------------------------------------------------
insert into public.clients (id, salon_id, nombre, telefono, email, cumpleanos, alergias, vip) values
  ('50000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'Lucía Fernández', '+34611111111', 'lucia@example.com', '1990-03-12', 'Alergia al tinte (amoniaco)', true),
  ('50000000-0000-0000-0000-000000000002', '00000000-0000-0000-0000-000000000001', 'Pablo García', '+34622222222', 'pablo@example.com', '1985-07-04', null, false),
  ('50000000-0000-0000-0000-000000000003', '00000000-0000-0000-0000-000000000001', 'Marina Ruiz', '+34633333333', 'marina@example.com', '1998-11-23', null, true);

-- ---------------------------------------------------------------------------
-- Appointments — today's board
-- ---------------------------------------------------------------------------
insert into public.appointments (salon_id, client_id, employee_id, service_id, inicio, fin, estado, canal)
select
  '00000000-0000-0000-0000-000000000001',
  '50000000-0000-0000-0000-000000000001',
  '30000000-0000-0000-0000-000000000001',
  s.id,
  date_trunc('day', now()) + interval '10 hours',
  date_trunc('day', now()) + interval '10 hours 30 minutes',
  'confirmada', 'manual'
from public.services s where s.nombre = 'Corte caballero' and s.salon_id = '00000000-0000-0000-0000-000000000001';

insert into public.appointments (salon_id, client_id, employee_id, service_id, inicio, fin, estado, canal)
select
  '00000000-0000-0000-0000-000000000001',
  '50000000-0000-0000-0000-000000000002',
  '30000000-0000-0000-0000-000000000002',
  s.id,
  date_trunc('day', now()) + interval '11 hours',
  date_trunc('day', now()) + interval '11 hours 45 minutes',
  'pendiente', 'whatsapp'
from public.services s where s.nombre = 'Corte señora' and s.salon_id = '00000000-0000-0000-0000-000000000001';

-- ---------------------------------------------------------------------------
-- Salon B ("Salón Norte") — minimal data, used only to prove tenant isolation
-- ---------------------------------------------------------------------------
insert into auth.users (
  instance_id, id, aud, role, email, encrypted_password,
  email_confirmed_at, raw_app_meta_data, raw_user_meta_data,
  created_at, updated_at, confirmation_token, recovery_token,
  email_change_token_new, email_change
) values (
  '00000000-0000-0000-0000-000000000000',
  '10000000-0000-0000-0000-000000000002',
  'authenticated', 'authenticated',
  'admin@salonnorte.es',
  crypt('salonnorte2026', gen_salt('bf')),
  now(), '{"provider":"email","providers":["email"]}', '{}',
  now(), now(), '', '', '', ''
);

insert into auth.identities (
  id, provider_id, user_id, identity_data, provider, last_sign_in_at, created_at, updated_at
) values (
  gen_random_uuid(), '10000000-0000-0000-0000-000000000002', '10000000-0000-0000-0000-000000000002',
  '{"sub":"10000000-0000-0000-0000-000000000002","email":"admin@salonnorte.es"}', 'email', now(), now(), now()
);

insert into public.users (id, salon_id, nombre, email, rol, auth_user_id)
values (
  '20000000-0000-0000-0000-000000000002',
  '00000000-0000-0000-0000-000000000002',
  'Admin Norte', 'admin@salonnorte.es', 'admin',
  '10000000-0000-0000-0000-000000000002'
);

insert into public.clients (salon_id, nombre, telefono, vip)
values ('00000000-0000-0000-0000-000000000002', 'Cliente Secreto Norte', '+34699999999', false);
