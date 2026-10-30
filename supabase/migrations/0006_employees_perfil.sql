-- Fase 1 · Paso 6 — Empleados: campos de perfil para la pantalla de Empleados.
--
-- El PIN de fichaje del quiosco pasa a vivir en employees.pin_fichaje_hash
-- (Argon2id, nunca en claro): la pantalla de Fichaje verificará contra esta
-- columna. users.pin_fichaje_hash queda como legado de los usuarios con login
-- propio hasta que Fichaje se integre.

create type empleado_estado as enum ('activo', 'pausa');

alter table public.employees
  add column estado empleado_estado not null default 'activo',
  add column telefono text,
  add column email text,
  add column pin_fichaje_hash text;
