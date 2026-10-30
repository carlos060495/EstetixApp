"use client";

import { AlertOctagon, Archive, Cloud, Trash2 } from "lucide-react";
import type { FilaSimulacro, Simulacro } from "@/lib/api/admin-plataforma";
import { formatearDia } from "@/lib/horas";

/**
 * EL SIMULACRO DEL BORRADO, EN PANTALLA. SOLO LECTURA.
 *
 * Lo que borraría el borrado a 90 días de una cuenta, tabla por tabla, puesto al
 * lado de lo decidido que se va y que se queda. Arriba, en rojo, lo
 * que contradice lo decidido o haría fallar el borrado: para eso existe.
 *
 * No hay ningún botón. El borrado existe y está APAGADO: se ejecuta a mano, con
 * el procedimiento de la cabecera de la migración 0068 (Stripe → Storage → la
 * función). Por eso lo de fuera de la base enseña sus ids y sus rutas: es lo
 * que hay que borrar ANTES.
 */

const NOMBRES: Record<string, string> = {
  "public.salons": "Centros",
  "public.users": "Usuarios de la app",
  "public.clients": "Clientas",
  "public.consents": "Consentimientos",
  "public.appointments": "Citas",
  "public.bot_sessions": "Conversaciones de WhatsApp",
  "public.bot_messages": "Mensajes de WhatsApp",
  "public.employees": "Equipo",
  "public.employee_services": "Servicios de cada empleada",
  "public.employee_schedule": "Horario de cada empleada",
  "public.employee_schedule_exceptions": "Ausencias del equipo",
  "public.time_entries": "Fichajes (registro de jornada)",
  "public.correcciones_de_fichaje": "Correcciones de fichaje (registro de jornada)",
  "public.pin_attempt_locks": "Bloqueos de PIN del quiosco",
  "public.puestos": "Puestos",
  "public.services": "Servicios",
  "public.service_categories": "Categorías de servicio",
  "public.products": "Productos",
  "public.providers": "Proveedores",
  "public.incomes": "Ventas",
  "public.expenses": "Gastos",
  "public.automations": "Automatizaciones",
  "public.salon_schedule": "Horario del salón",
  "public.salon_schedule_exceptions": "Cierres del salón",
  "public.salon_whatsapp_config": "Configuración de WhatsApp",
  "public.user_salons": "Centros de cada usuario",
  "public.pin_gestion": "PIN de gestión",
  "public.accounts": "La cuenta",
  "public.subscriptions": "La suscripción",
  "public.decisiones_de_dinero": "Registro de decisiones de dinero",
  "public.descargas_de_datos": "Registro de descargas de datos",
  "public.impersonation_sessions": "Visitas de soporte",
  "public.support_access_codes": "Códigos de acceso de soporte",
  "public.admin_logs": "Registro del panel",
  "auth.users": "Usuario de acceso (Supabase Auth)",
  "auth.audit_log_entries": "Registro de accesos (Supabase Auth)",
  "auth.identities": "Identidades de acceso (Supabase Auth)",
  "auth.sessions": "Sesiones abiertas (Supabase Auth)",
  "auth.refresh_tokens": "Tokens de sesión (Supabase Auth)",
  "storage.objects (salon-assets)": "Logo (Storage)",
  "stripe (clientes)": "Cliente de Stripe",
};

function nombre(tabla: string): string {
  return NOMBRES[tabla] ?? tabla.replace(/^public\./, "");
}

const SITUACION: Record<Simulacro["situacion"], string> = {
  fuera_de_plazo: "Fuera de plazo",
  en_plazo: "Dentro del plazo",
  sin_reloj: "Sin reloj: cuenta viva",
};

export function SimulacroVista({ s }: { s: Simulacro }) {
  const borra = s.filas.filter((f) => f.que_haria === "borra" && !esFuera(f) && !f.problema);
  const queda = s.filas.filter((f) => f.que_haria === "queda" && !f.problema);
  const fuera = s.filas.filter((f) => esFuera(f) && !f.problema);
  const problemas = s.filas.filter((f) => f.problema);

  return (
    <article className="rounded-[14px] border border-border bg-card p-4 md:p-5" aria-label={`Simulacro de ${s.cuenta ?? "una cuenta"}`}>
      <header className="mb-3">
        <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
          <h2 className="m-0 min-w-0 truncate text-[16px] font-semibold text-text">{s.cuenta ?? "Cuenta sin nombre"}</h2>
          <span className="rounded-md border border-border px-2 py-0.5 text-[11.5px] font-semibold text-mute">
            {SITUACION[s.situacion]}
          </span>
        </div>
        <p className="m-0 mt-1 text-[12.5px] leading-snug text-mute">
          {s.se_borra_el
            ? `Se borraría el ${formatearDia(s.se_borra_el, { day: "numeric", month: "long", year: "numeric" })}${
                s.dias_restantes !== null && s.dias_restantes < 0 ? ` (hace ${-s.dias_restantes} días)` : ""
              }. `
            : "No tiene reloj: esto es lo que se borraría si lo tuviera. "}
          {s.filas_a_borrar} filas en la base, más lo de fuera.{" "}
          <strong className="text-text">Simulacro: no se ha borrado nada.</strong>
        </p>
      </header>

      {problemas.length > 0 && (
        <section aria-label="Problemas" className="mb-4 rounded-[11px] border border-danger/40 bg-danger/10 px-3 py-2.5">
          <h3 className="m-0 mb-2 flex items-center gap-2 text-[13px] font-semibold text-danger">
            <AlertOctagon size={15} /> {problemas.length === 1 ? "1 problema" : `${problemas.length} problemas`} antes de
            poder borrar
          </h3>
          <ul className="m-0 flex list-none flex-col gap-2 p-0">
            {problemas.map((f) => (
              <li key={`${f.tabla}-${f.que_haria}`} className="text-[12.5px] leading-snug text-text">
                <span className="font-semibold">{nombre(f.tabla)}</span>
                {f.filas !== null && <span className="text-mute"> · {f.filas} filas</span>}
                <span className="block text-mute">{f.problema}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <Grupo icono={<Trash2 size={14} />} titulo="Se borraría" filas={borra} vacio="Nada." />
      <Grupo icono={<Cloud size={14} />} titulo="Fuera de la base: se borra antes que la base" filas={fuera} vacio="Nada." conDetalle />
      <Grupo icono={<Archive size={14} />} titulo="Se quedaría" filas={queda} vacio="Nada." />
    </article>
  );
}

// Lo que no cabe en la transacción del borrado. Auth sí cabe (0068): está en
// «Se borraría».
function esFuera(f: FilaSimulacro): boolean {
  return f.tabla.startsWith("storage.") || f.tabla.startsWith("stripe");
}

function Grupo({
  icono, titulo, filas, vacio, conDetalle = false,
}: { icono: React.ReactNode; titulo: string; filas: FilaSimulacro[]; vacio: string; conDetalle?: boolean }) {
  return (
    <section className="mb-3 last:mb-0">
      <h3 className="m-0 mb-1.5 flex items-center gap-2 text-[11.5px] font-semibold uppercase tracking-[.08em] text-faint">
        {icono}
        {titulo}
      </h3>
      {filas.length === 0 ? (
        <p className="m-0 text-[12.5px] text-faint">{vacio}</p>
      ) : (
        <ul className="m-0 flex list-none flex-col p-0">
          {filas.map((f) => (
            <li key={`${f.tabla}-${f.que_haria}`} className="flex items-baseline justify-between gap-3 border-b border-border-subtle py-1.5 last:border-b-0">
              <span className="min-w-0">
                <span className="block truncate text-[13px] text-text">{nombre(f.tabla)}</span>
                <span className="block truncate font-mono text-[10.5px] text-faint">{f.tabla}</span>
                {/* Los ids de Stripe y las rutas de Storage, enteros y seleccionables:
                    son lo que hay que copiar para borrarlo antes. */}
                {f.en_la_baja ? (
                  <span className="mt-0.5 block text-[11.5px] leading-snug text-mute">{f.en_la_baja}</span>
                ) : null}
                {conDetalle && f.filas ? (
                  <span className="mt-0.5 block select-all break-all font-mono text-[11px] leading-snug text-mute">{f.detalle}</span>
                ) : null}
              </span>
              <span className={`flex-shrink-0 font-mono text-[13px] ${f.filas ? "text-text" : "text-faint"}`}>
                {f.filas ?? "—"}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
