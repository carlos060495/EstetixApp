-- 0060 · La zona horaria de cada centro
--
-- Hasta aquí la zona era una constante del backend (`Europe/Madrid`, escrita en
-- tres sitios) y, en las pantallas, la del dispositivo de quien miraba. Un
-- salón de Las Palmas va una hora por detrás TODO EL AÑO, y la agenda guardaba
-- las citas con la zona del portátil desde el que se creaban: un dato mal
-- escrito, no una pantalla mal pintada.
--
-- ═══ QUÉ GUARDA ═══
--
-- El NOMBRE IANA de la zona, nunca un desplazamiento. Con el nombre, el cambio
-- de hora va incluido (el último domingo de octubre y el último de marzo); con
-- un «+1» fijo, medio año estaría mal. Lista cerrada, decidida por Carlos:
--
--   · Europe/Madrid    — Península, Baleares, Ceuta y Melilla (mismas reglas).
--   · Atlantic/Canary  — Canarias.
--
-- Los centros que ya existen nacen en Península: son cinco, todos de prueba, y
-- ninguno canario de verdad (Carlos, 01-10-2026). Un centro nuevo, también, y
-- se cambia desde Ajustes → Perfil del salón.
--
-- ═══ LA REGLA DE LA COLUMNA OBLIGATORIA (supabase/README.md) ═══
--
-- Es `not null` CON valor por defecto: el backend que esté desplegado al
-- correr esto inserta salones sin saber de la columna, y la base la rellena.
--
-- ═══ ORDEN DE DESPLIEGUE: 0060 → BACKEND → FRONTEND ═══
--
-- El backend nuevo lee la columna; el viejo la ignora. El frontend nuevo la
-- recibe del backend nuevo.

begin;

alter table public.salons
  add column zona_horaria text not null default 'Europe/Madrid'
  constraint salons_zona_horaria_conocida check (zona_horaria in ('Europe/Madrid', 'Atlantic/Canary'));

comment on column public.salons.zona_horaria is
  'Zona horaria IANA del centro (0060): Europe/Madrid o Atlantic/Canary. '
  'Todas las horas del centro se convierten con ella, nunca sumando horas.';

commit;
