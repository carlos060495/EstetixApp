"use client";

import { useState, useSyncExternalStore } from "react";
import { Loader2, Lock } from "lucide-react";

import { cerrarGestion } from "@/lib/api/mostrador";
import { cerrarCortina, cortinaAbierta, suscribirCortina } from "@/lib/cortina-gestion";
import { enModoMostrador } from "@/lib/modo-mostrador";

// Fichero aparte de la cortina a propósito: la cabecera sale también en la
// Agenda, y lo que importa la Agenda es lo que la guarda de las pantallas libres
// cuenta como «lo que pide» (`test_lo_que_viaja_es_lo_que_se_ensena.py`).

const sinCambios = () => () => undefined;

/** En un mostrador con la gestión abierta: cerrarla YA, sin esperar a los diez
 *  minutos. Va en la cabecera de cada pantalla (`Header`): fija abajo se montaba
 *  encima de los botones flotantes a 360 px. Fuera del mostrador, o con la
 *  gestión cerrada, no pinta nada. */
export function CerrarGestion() {
  const esMostrador = useSyncExternalStore(sinCambios, enModoMostrador, () => false);
  const abierta = useSyncExternalStore(suscribirCortina, cortinaAbierta, () => false);
  const [cerrando, setCerrando] = useState(false);
  if (!esMostrador || !abierta) return null;
  async function cerrar() {
    setCerrando(true);
    await cerrarGestion().catch(() => undefined);
    cerrarCortina();
    window.location.assign("/agenda");
  }
  return (
    <button
      type="button"
      onClick={() => void cerrar()}
      disabled={cerrando}
      className="flex h-11 flex-shrink-0 items-center gap-2 rounded-lg border border-gold/40 bg-card-alt px-3 text-[13px] font-semibold text-text transition-colors hover:bg-[#1B1B1E] disabled:opacity-60"
    >
      {cerrando ? <Loader2 size={16} className="animate-spin" /> : <Lock size={16} className="text-gold" />}
      Cerrar gestión
    </button>
  );
}
