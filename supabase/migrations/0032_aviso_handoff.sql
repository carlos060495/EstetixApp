-- Agente de WhatsApp · Entrega (b) del handoff — el aviso por correo.
--
-- Una sola columna: cuándo se avisó por última vez a este salón de que alguien
-- está esperando en WhatsApp. Es el freno que impide que cinco derivaciones
-- seguidas se conviertan en cinco correos.
--
-- No crea tablas ni toca políticas: una columna nueva hereda el grant de su
-- tabla (`salon_whatsapp_config`, migración 0024) y la RLS sigue filtrando por
-- `salon_id`. El bot la escribe con la identidad del ADMIN del salón —así entra
-- el webhook, ver bot_tenant_token.py—, que es la que exige la política de
-- escritura de esa tabla.

-- ---------------------------------------------------------------------------
-- Cuándo se avisó por última vez
-- ---------------------------------------------------------------------------
-- POR QUÉ EN LA BASE DE DATOS Y NO EN MEMORIA DEL PROCESO: con varias réplicas
-- un freno en memoria deja pasar tantos correos como réplicas haya, que es
-- justo el escenario del que protege. Es el mismo razonamiento que el tope
-- anti-spam del webhook, que también se cuenta contra la BD.
--
-- Y POR QUÉ AQUÍ Y NO EN `bot_sessions`: el freno es POR SALÓN, no por
-- conversación. Cinco clientes distintos pidiendo persona a la vez son
-- exactamente el caso que hay que frenar —el salón ya está avisado y verá los
-- cinco en la bandeja—, y una marca por conversación no lo frenaría.
--
-- NULL = nunca se ha avisado a este salón. Es el valor de arranque y hace que
-- el primer aviso salga siempre.
--
-- NOTA sobre salones con dos números: la 0024 permite a propósito varias filas
-- por salón (dos sedes, dos números). El freno se reclama filtrando por
-- `salon_id`, así que las filas de un mismo salón se sellan juntas y el freno
-- sigue siendo por salón, que es lo que se quiere: al dueño se le avisa una
-- vez, no una por sede.
alter table public.salon_whatsapp_config
  add column ultimo_aviso_handoff_at timestamptz;

-- Sin índice nuevo: la reclamación busca por `salon_id` y ese índice ya lo creó
-- la 0024 (`salon_whatsapp_config_salon_id_idx`). Añadir otro sobre la columna
-- del reloj no ayudaría —son una o dos filas por salón— y habría que mantenerlo
-- en cada aviso.
