"use client";

import { useEffect, useState } from "react";
import { Loader2, Sparkles } from "lucide-react";
import { abrirSalonDeMuestra } from "@/lib/api/demo";
import { createClient } from "@/lib/supabase/client";
import { TiempoAgotado } from "@/lib/api/plazo";

/**
 * EL BOTÓN DE LA PORTADA (0080). Abre el salón de muestra de ESTE navegador:
 * el que ya tiene vivo, o uno nuevo. Pasado un tope, dice que no hay sitio y
 * que se intente en un rato; nunca un error feo.
 */
export function BotonSalonDeMuestra({ className = "" }: { className?: string }) {
  const [abriendo, setAbriendo] = useState(false);
  const [aviso, setAviso] = useState<string | null>(null);

  // Vuelve de una demo: que ha terminado, o de la que acaba de salir (0081).
  useEffect(() => {
    const motivo = new URLSearchParams(window.location.search).get("demo");
    if (motivo === "terminada") {
      setAviso("Tu salón de muestra ha terminado y se ha borrado. Puedes abrir otro.");
    } else if (motivo === "salir") {
      setAviso(
        "Tu salón de muestra sigue ahí: con este botón vuelves al mismo durante sus 24 horas. " +
          "Para usarlo de verdad, crea tu cuenta.",
      );
    }
  }, []);

  async function abrir() {
    if (abriendo) return;
    setAbriendo(true);
    setAviso(null);
    try {
      const r = await abrirSalonDeMuestra();
      if (r.estado === "sin_sitio") {
        setAviso(r.mensaje);
        setAbriendo(false);
        return;
      }
      const { error } = await createClient().auth.setSession({
        access_token: r.access_token,
        refresh_token: r.refresh_token,
      });
      if (error) throw new Error("No se ha podido abrir el salón de muestra. Inténtalo otra vez.");
      window.location.href = "/agenda";
    } catch (e) {
      setAviso(
        e instanceof TiempoAgotado
          ? "No hemos podido abrir el salón de muestra: no ha contestado a tiempo. Inténtalo otra vez."
          : e instanceof Error
            ? e.message
            : "No se ha podido abrir el salón de muestra.",
      );
      setAbriendo(false);
    }
  }

  return (
    <div className="flex flex-col items-center gap-2">
      <button
        type="button"
        onClick={() => void abrir()}
        disabled={abriendo}
        className={`flex h-[50px] items-center gap-2 rounded-[11px] border border-border px-6 text-[15px] font-medium text-text transition-colors hover:bg-card-alt disabled:opacity-60 ${className}`}
      >
        {abriendo ? <Loader2 size={17} className="animate-spin" aria-hidden /> : <Sparkles size={17} aria-hidden />}
        {abriendo ? "Preparando tu salón…" : "Probar un salón de muestra"}
      </button>
      {aviso && (
        <p role="status" className="m-0 max-w-[34ch] text-center text-[13px] leading-[1.5] text-gold">
          {aviso}
        </p>
      )}
    </div>
  );
}
