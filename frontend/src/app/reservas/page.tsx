"use client";

import { useEffect, useState } from "react";
import { AppShell } from "@/components/shell/app-shell";
import { Header, HuecoAccionesFlotantes } from "@/components/shell/header";
import { NuevaCitaModal } from "@/app/agenda/nueva-cita-modal";
import { listEmployees, type Employee } from "@/lib/api/employees";
import { listExcepciones, type Excepcion } from "@/lib/api/excepciones";
import { getHorario, type HorarioSemana } from "@/lib/api/horario";
import { listServices, type Service } from "@/lib/api/services";
import { ReservasEntrantes } from "./reservas-entrantes";
import { WidgetOnline } from "./widget-online";
import { diaComoFecha, hoyEn } from "@/lib/horas";
import { useZonaDelCentro } from "@/lib/zona-del-centro";
import { ErrorDeCarga } from "@/components/error-de-carga";

type Tab = "entrantes" | "widget";

export default function ReservasPage() {
  const [tab, setTab] = useState<Tab>("entrantes");
  const [modalAbierto, setModalAbierto] = useState(false);
  // La cita nueva se crea en la hora DEL CENTRO (0060), así que sin zona no se abre.
  const { zona, mensaje: mensajeZona, reintentar: reintentarZona } = useZonaDelCentro();
  const [empleados, setEmpleados] = useState<Employee[]>([]);
  const [servicios, setServicios] = useState<Service[]>([]);
  const [refreshKey, setRefreshKey] = useState(0);
  // El mismo modal que la agenda: también tiene que avisar si la hora elegida
  // cae fuera del horario del salón.
  const [horario, setHorario] = useState<HorarioSemana | null>(null);
  const [excepciones, setExcepciones] = useState<Excepcion[] | null>(null);

  // Catálogo para "Nueva reserva" (MISMO modal que la Agenda, con selector de
  // canal y fecha). Si falla, la propia tabla ya enseña el error de conexión.
  useEffect(() => {
    Promise.all([listEmployees(), listServices()])
      .then(([emps, srvs]) => {
        setEmpleados(emps.filter((e) => e.estado === "activo"));
        setServicios(srvs);
      })
      .catch(() => {});
    getHorario()
      .then(setHorario)
      .catch(() => setHorario(null));
    listExcepciones()
      .then(setExcepciones)
      .catch(() => setExcepciones(null));
  }, []);

  return (
    <AppShell>
      <Header
        title="Reservas"
        subtitle="Reserva online y citas entrantes por canal"
        actionLabel="Nueva reserva"
        // SOLO EN LA PESTAÑA DE LA LISTA. «Nueva reserva» es la acción de las
        // reservas entrantes; en «Widget online» no hay lista que ampliar y sí
        // hay un botón propio —«Activar mi página»— que el flotante tapaba: a
        // 360 px el dedo caía en «Nueva reserva» al pulsarlo. Medido, no
        // supuesto.
        onAction={tab === "entrantes" ? () => setModalAbierto(true) : undefined}
      />

      <div className="flex h-[52px] flex-shrink-0 items-end gap-7 border-b border-[#1B1B1E] px-4 md:px-[30px]">
        <button
          onClick={() => setTab("entrantes")}
          className={`-mx-1 flex min-h-11 items-end px-1 pb-4 text-sm transition-colors ${
            tab === "entrantes" ? "font-semibold text-text shadow-[inset_0_-2px_0_var(--accent)]" : "font-medium text-mute"
          }`}
        >
          Reservas entrantes
        </button>
        <button
          onClick={() => setTab("widget")}
          className={`-mx-1 flex min-h-11 items-end px-1 pb-4 text-sm transition-colors ${
            tab === "widget" ? "font-semibold text-text shadow-[inset_0_-2px_0_var(--accent)]" : "font-medium text-mute"
          }`}
        >
          Widget online
        </button>
      </div>

      <div className="flex-1 overflow-y-auto">
        <div
          className="mx-auto max-w-[1080px] px-4 py-[26px] pb-14 md:px-[30px]"
        >
          {tab === "entrantes" ? (
            zona ? (
              <ReservasEntrantes refreshKey={refreshKey} zona={zona} />
            ) : mensajeZona ? (
              <ErrorDeCarga mensaje={mensajeZona} onReintentar={reintentarZona} />
            ) : null
          ) : (
            <WidgetOnline />
          )}
        </div>
        <HuecoAccionesFlotantes />
      </div>

      {modalAbierto && zona && (
        <NuevaCitaModal
          zona={zona}
          horario={horario}
          excepciones={excepciones}
          cita={null}
          preset={null}
          express={false}
          fecha={diaComoFecha(hoyEn(zona))}
          empleados={empleados}
          servicios={servicios}
          citasDelDia={[]}
          conCanal
          conFecha
          onClose={() => setModalAbierto(false)}
          onSaved={() => setRefreshKey((k) => k + 1)}
        />
      )}
    </AppShell>
  );
}
