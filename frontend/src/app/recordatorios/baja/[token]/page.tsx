"use client";

import { use, useEffect, useState } from "react";
import { Check, Loader2 } from "lucide-react";
import { fetchConPlazo } from "@/lib/api/plazo";

/**
 * Darse de baja de los recordatorios. Sin login, desde el pie de un correo.
 *
 * POR QUÉ ES OBLIGATORIA: a estos correos se les escribe a gente que NO ha dado
 * consentimiento para recibir publicidad — se les escribe porque tienen una
 * cita que ellos mismos pidieron. Eso lo convierte en un mensaje de servicio y
 * es lo que lo hace legítimo, pero también obliga a que salirse sea trivial:
 * quien no lo quiera tiene que poder decirlo sin llamar a nadie.
 *
 * SE EJECUTA AL ABRIR, sin botón de confirmar. Quien llega aquí ya ha decidido
 * —ha pulsado «no quiero recibir recordatorios» en su correo— y pedirle que lo
 * confirme otra vez es la clase de fricción que se pone cuando no se quiere que
 * la gente se dé de baja.
 *
 * Y SE DICE QUÉ SE APAGA Y QUÉ NO: su correo sigue en la ficha del salón, que
 * es como le escriben para todo lo demás. Sin esa frase, «date de baja» suena a
 * «desaparece», y quien solo quería menos correos acaba sin ninguno.
 */
export default function BajaRecordatoriosPage({
  params,
}: {
  params: Promise<{ token: string }>;
}) {
  const { token } = use(params);
  const [estado, setEstado] = useState<"cargando" | "hecho" | "error">("cargando");

  useEffect(() => {
    let vivo = true;
    void (async () => {
      const base = process.env.NEXT_PUBLIC_API_URL;
      try {
        const r = await fetchConPlazo(
          `${(base ?? "").replace(/\/$/, "")}/recordatorios/baja/${encodeURIComponent(token)}`,
          // Sin credenciales: quien abre esto no tiene sesión, y el token es lo
          // único que lo identifica.
          { method: "POST", credentials: "omit" }
        );
        if (!vivo) return;
        setEstado(r.ok ? "hecho" : "error");
      } catch {
        if (vivo) setEstado("error");
      }
    })();
    return () => {
      vivo = false;
    };
  }, [token]);

  return (
    <div className="flex min-h-dvh items-center justify-center bg-bg px-6 text-text">
      <div className="w-full max-w-[380px] text-center">
        {estado === "cargando" && <Loader2 size={22} className="mx-auto animate-spin text-gold" />}

        {estado === "hecho" && (
          <>
            <div
              className="mx-auto mb-4 flex h-[56px] w-[56px] items-center justify-center rounded-full border"
              style={{ borderColor: "#4FA86B66", background: "#4FA86B1f" }}
            >
              <Check size={26} className="text-[#4FA86B]" />
            </div>
            <h1 className="font-display text-[22px] font-semibold">Hecho</h1>
            <p className="mt-2.5 text-[13.5px] leading-relaxed text-mute">
              No volveremos a enviarte recordatorios de tus citas.
            </p>
            <p className="mt-3 text-[12.5px] leading-relaxed text-faint">
              {/* Lo que NO se ha hecho, dicho antes de que nadie se lo pregunte. */}
              Tu correo sigue en la ficha de tu salón: podrán escribirte si hay
              algún cambio con una cita. Si quieres que lo borren del todo,
              pídeselo a ellos.
            </p>
          </>
        )}

        {estado === "error" && (
          <>
            <h1 className="font-display text-[22px] font-semibold">No hemos podido hacerlo</h1>
            <p className="mt-2.5 text-[13.5px] leading-relaxed text-mute">
              Vuelve a abrir el enlace en unos minutos. Si sigue sin funcionar,
              díselo a tu salón y ellos lo apagan.
            </p>
          </>
        )}
      </div>
    </div>
  );
}
