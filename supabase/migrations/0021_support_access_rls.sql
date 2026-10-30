-- Fase 2 (endurecimiento): el registro de consentimiento deja de ser
-- escribible por el propio salón.
--
-- POR QUÉ. `support_access_codes` e `impersonation_sessions` no son datos del
-- salón como lo son sus clientes o sus citas: son la PRUEBA de que el acceso de
-- soporte fue consentido, por quién, cuándo se usó y cuándo terminó. Es lo que
-- se mira el día que alguien pregunta "¿quién entró en mi salón y con permiso de
-- quién?".
--
-- La 0020 le dio al admin del salón una política `for all` sobre los códigos y
-- otra de UPDATE sobre las sesiones. A nivel de fila eso es correcto —son sus
-- filas—, pero convierte al sujeto del registro en su editor: con su propio JWT,
-- vía PostgREST, podría borrar un `used_at` (negar que el código llegó a
-- usarse), limpiar un `revoked_at`, estirar un `expires_at` para revivir un
-- código muerto, o cambiar el `ended_reason` de una sesión para que su final
-- cuente otra historia. Un registro que su sujeto puede reescribir no prueba
-- nada.
--
-- QUÉ. Lo que el salón necesita de verdad es leer (pintar "sesión de soporte
-- activa" en /soporte, ver si tiene código vivo) y crear un código nuevo. Eso se
-- le deja. Todo lo demás —las tres revocaciones: código anterior, código vivo y
-- cierre de sesión— pasa a ser operación de backend con service role
-- (app/routers/soporte.py), que sí puede escribir aquí y cuyo WHERE repite el
-- salon_id porque el service role no pasa por RLS.
--
-- La condición de acceso no cambia (mismo salón + rol admin): lo único que
-- cambia es el VERBO permitido.

-- support_access_codes: SELECT + INSERT. Sin UPDATE, sin DELETE.
drop policy "support_codes_admin_own" on public.support_access_codes;

create policy "support_codes_admin_select_own" on public.support_access_codes
  for select using (salon_id = public.current_salon_id() and public.is_admin_rol());

create policy "support_codes_admin_insert_own" on public.support_access_codes
  for insert with check (salon_id = public.current_salon_id() and public.is_admin_rol());

-- impersonation_sessions: SELECT y nada más. El cierre de una sesión (ended_at,
-- ended_reason) lo escriben siempre el panel o el backend con service role, que
-- es lo que hace creíble el motivo por el que terminó.
-- `impersonation_sessions_select_own` (0020) se queda tal cual.
drop policy "impersonation_sessions_update_own" on public.impersonation_sessions;

-- Segunda barrera, a nivel de GRANT. Mismo patrón que `admin_logs` en la 0004:
-- si la RLS fuera lo único, bastaría con que alguien añadiera luego una política
-- permisiva —o hiciera `disable row level security`— para reabrir el agujero
-- sin que nadie lo notase. El revoke sobrevive a las dos cosas.
--
-- Efecto secundario deseado: sin el privilegio, PostgreSQL lanza 42501
-- (permission denied) en vez de dejar pasar el UPDATE y afectar a 0 filas por
-- RLS. Un intento de manipular la prueba deja de ser un no-op silencioso y pasa
-- a ser un 403 de PostgREST que se ve en los logs.
--
-- `service_role` conserva sus privilegios (el revoke no le toca): es quien hace
-- las revocaciones legítimas desde app/routers/soporte.py y desde el panel.
--
-- OJO en local: `supabase db reset` deja al rol `authenticated` sin DML en el
-- CLI nuevo, y el atajo habitual para arreglarlo —un `grant ... on all tables in
-- schema public to authenticated`— DESHACE estas dos líneas. Después de ese
-- grant hay que volver a ejecutarlas, o la pila local no reproduce producción.
revoke update, delete on public.support_access_codes from public, anon, authenticated;

-- En las sesiones el salón no escribe NADA: ni las abre (eso es el panel con
-- service role) ni las cierra. Por eso aquí el revoke incluye también el insert.
revoke insert, update, delete on public.impersonation_sessions from public, anon, authenticated;
