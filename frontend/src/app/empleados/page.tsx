"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Briefcase, Loader2 } from "lucide-react";
import { AppShell } from "@/components/shell/app-shell";
import { Header } from "@/components/shell/header";
import { EmpleadosBoard } from "./empleados-board";
import { EmpleadoDetail } from "./empleado-detail";
import { NuevoEmpleadoModal } from "./nuevo-empleado-modal";
import { PuestosModal } from "./puestos-modal";
import { listEmployees, type Employee } from "@/lib/api/employees";
import { listPuestos, type Puesto } from "@/lib/api/puestos";
import { listServices, type Service } from "@/lib/api/services";
import { ApiError } from "@/lib/api/client";
import { useSession } from "@/lib/mock-session";
import { EMPLOYEE_LIMITS } from "@/lib/mock-plan";
import { useTurnos } from "@/lib/turnos";

// useSession vive dentro del SessionProvider (montado por AppShell): por eso el
// contenido —que lee el plan para el contador X/Y— va en este hijo, nunca en la
// página que renderiza el propio AppShell.
function EmpleadosContent() {
  const { session } = useSession();
  const [empleados, setEmpleados] = useState<Employee[]>([]);
  const [servicios, setServicios] = useState<Service[]>([]);
  const [puestos, setPuestos] = useState<Puesto[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // ENLACE DIRECTO A UN MES DE FICHAJES: `/empleados?ficha=…&mes=AAAA-MM` abre
  // la ficha en «Jornada» y en ese mes. Llega desde Estadísticas, desde el aviso
  // de días sin cerrar: el sitio donde se corrige tiene que estar a un toque.
  // Con `useSearchParams`, no con `window.location`: al llegar desde otra
  // pantalla sin recargar, la ficha se monta antes de que cambie la dirección
  // del navegador, y leerla de ahí no abría nada.
  const params = useSearchParams();
  const [abrirEn, setAbrirEn] = useState(() => leerEnlace(params));
  const [selectedId, setSelectedId] = useState<string | null>(() => abrirEn?.ficha ?? null);
  // null = modal cerrado; "new" = alta; objeto = edición.
  const [modal, setModal] = useState<"new" | Employee | null>(null);
  const [puestosAbierto, setPuestosAbierto] = useState(false);

  // UNA RESPUESTA QUE YA NO CORRESPONDE NO ESCRIBE. Dos cambios seguidos lanzan
  // dos recargas; si la primera llegaba la última, dejaba la lista de antes del
  // segundo cambio: medido con dos puestos creados seguidos, el segundo faltaba
  // en el desplegable «Puesto». La recarga más reciente manda. Ver `lib/turnos.ts`.
  const turnos = useTurnos();

  const cargar = useCallback(async () => {
    const turno = turnos.abrir();
    setLoading(true);
    setError(null);
    try {
      // Los servicios alimentan los checkboxes del modal y los chips de la
      // ficha; los puestos, el desplegable de «Puesto».
      // CON LAS BAJAS: esta es la pantalla donde se enseñan, aparte, para que
      // su historial se pueda seguir abriendo.
      const [emps, srvs, pstos] = await Promise.all([
        listEmployees({ conBajas: true }),
        listServices(),
        listPuestos(),
      ]);
      if (!turno.vigente()) return;
      setEmpleados(emps);
      setServicios(srvs);
      setPuestos(pstos);
    } catch (e) {
      if (!turno.vigente()) return;
      setError(e instanceof ApiError ? e.message : "No se han podido cargar los empleados.");
    } finally {
      if (turno.vigente()) setLoading(false);
    }
  }, [turnos]);

  useEffect(() => {
    void cargar();
  }, [cargar]);

  const refrescar = () => void cargar();

  // La ficha se deriva de la lista: tras refrescar muestra datos al día, y si
  // el empleado se eliminó, se vuelve al grid automáticamente.
  const seleccionado = empleados.find((e) => e.id === selectedId) ?? null;
  const activos = empleados.filter((e) => e.estado === "activo").length;
  // «En total» es el equipo: quien está de baja ya no es del equipo.
  const enElEquipo = empleados.filter((e) => e.estado !== "baja").length;
  // Cupo de empleados activos del plan (el límite real lo hace cumplir el backend).
  const limite = EMPLOYEE_LIMITS[session?.tier ?? "basico"];
  // POR ENCIMA DEL TOPE, y no es un error: cuando los límites bajaron, a nadie
  // se le expulsó del equipo. Un salón puede tener 10 activos con un tope de 8
  // hasta que él mismo dé de baja a alguien; lo que no puede es añadir más. Se
  // dice tal cual —«10 / 8»— en vez de esconderlo: quien lo ve necesita
  // entender por qué el botón de añadir le va a dar un error.
  const porEncima = activos > limite;

  const subtitle = loading
    ? "Cargando empleados…"
    : error
      ? "No se han podido cargar los empleados"
      : porEncima
        ? // CORTO A PROPÓSITO: la cabecera trunca a una línea, y a 360 y 390 se
          // cortaba justo la parte que explicaba qué pasa. La explicación entera
          // va en el aviso de debajo, que sí ocupa las líneas que necesite.
          `${activos} activos · tu plan admite ${limite}`
        : `${activos}/${limite} activos · ${enElEquipo} en total`;

  return (
    <>
      <Header
        title="Empleados"
        subtitle={subtitle}
        actionLabel="Nuevo empleado"
        onAction={() => setModal("new")}
        secondaryAction={{
          label: "Puestos",
          icon: Briefcase,
          onClick: () => setPuestosAbierto(true),
        }}
        // En la ficha, «Nuevo empleado» y «Puestos» flotando tapaban el
        // contenido —y el «Guardar horario» de la pestaña Jornada—.
        sinFlotantes={seleccionado !== null}
      />

      {loading && (
        <div className="flex flex-1 items-center justify-center gap-2.5 text-sm text-mute">
          <Loader2 size={20} className="animate-spin text-gold" />
          Cargando empleados…
        </div>
      )}

      {!loading && error && (
        <div className="flex flex-1 flex-col items-center justify-center gap-4 px-6 text-center">
          <p className="m-0 max-w-[420px] rounded-xl border border-danger/30 bg-danger/10 px-4 py-3 text-[13.5px] text-danger">
            {error}
          </p>
          <button
            onClick={refrescar}
            className="h-10 rounded-[10px] border border-border px-[18px] text-[13.5px] font-medium text-text transition-colors hover:border-[#3a3a40] hover:bg-card-alt"
          >
            Reintentar
          </button>
        </div>
      )}

      {!loading && !error && porEncima && !seleccionado && (
        <div className="flex-shrink-0 px-4 pt-3 md:px-[30px]">
          <p
            role="status"
            className="m-0 rounded-lg border border-[#6A5B2B] bg-[rgba(201,168,76,.12)] px-3 py-2.5 text-[13px] leading-snug text-[#E4CC85]"
          >
            Tu plan admite {limite} personas activas en este centro. Nadie se ha desactivado:
            las {activos} siguen trabajando y en la agenda, pero no podrás añadir ni reactivar a
            nadie hasta que haya menos de {limite}.
          </p>
        </div>
      )}

      {!loading &&
        !error &&
        (seleccionado ? (
          <EmpleadoDetail
            empleado={seleccionado}
            servicios={servicios}
            onBack={() => {
              setSelectedId(null);
              setAbrirEn(null);
            }}
            // A quien está de baja no se le edita: su ficha se conserva tal cual.
            onEdit={seleccionado.estado === "baja" ? undefined : () => setModal(seleccionado)}
            onReload={refrescar}
            {...(abrirEn?.ficha === seleccionado.id ? { tabInicial: 1, mesInicial: abrirEn.mes ?? undefined } : {})}
          />
        ) : (
          <EmpleadosBoard empleados={empleados} onSelect={setSelectedId} onEdit={(e) => setModal(e)} />
        ))}

      {/* Renombrar un puesto cambia lo que se lee en las fichas, así que al
          cerrar se recarga todo si hubo cambios. */}
      {puestosAbierto && (
        <PuestosModal onClose={() => setPuestosAbierto(false)} onCambios={refrescar} />
      )}

      {modal !== null && (
        <NuevoEmpleadoModal
          empleado={modal === "new" ? null : modal}
          puestos={puestos}
          servicios={servicios}
          onClose={() => setModal(null)}
          onSaved={refrescar}
        />
      )}
    </>
  );
}

export default function EmpleadosPage() {
  return (
    <AppShell>
      {/* `useSearchParams` (el enlace a un mes de fichajes) pide su Suspense. */}
      <Suspense fallback={null}>
        <EmpleadosContent />
      </Suspense>
    </AppShell>
  );
}

function leerEnlace(q: { get(nombre: string): string | null }): { ficha: string; mes: string | null } | null {
  const ficha = q.get("ficha");
  if (!ficha) return null;
  const mes = q.get("mes");
  return { ficha, mes: mes && /^\d{4}-\d{2}$/.test(mes) ? mes : null };
}
