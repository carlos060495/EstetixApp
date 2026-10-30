"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, MessageCircle } from "lucide-react";

import { conectarWhatsapp, getDatosDeMeta, type EstadoAsistente } from "@/lib/api/bot";
import {
  abrirDialogoDeMeta,
  CanceladoPorElSalon,
  prepararMeta,
  type DialogoDeMeta,
  type FB,
} from "@/lib/meta-embedded-signup";

/**
 * Cuánto tiempo seguido puede estar la persona DE VUELTA en esta página sin que
 * Meta haya devuelto nada antes de soltar el botón. Mientras está dentro de la
 * ventana de Meta, esta página no tiene el foco y el reloj no corre: no se le
 * corta a nadie a mitad del diálogo.
 */
export const SIN_RESPUESTA_MS = 4000;

export const MENSAJE_SIN_RESPUESTA =
  "Meta no ha conectado ningún número. Vuelve a pulsar el botón y sigue los pasos hasta elegir tu número " +
  "(si Meta te pregunta si quieres continuar con tu configuración anterior, elige «Editar configuración»). " +
  "Si la ventana no llega a abrirse, permite las ventanas emergentes de este sitio. " +
  "Si Meta te dice que no puede conectar tu cuenta, escríbenos y lo hacemos contigo.";

const NO_DISPONIBLE = "La conexión desde aquí no está disponible todavía. Escríbenos y lo hacemos contigo.";

/**
 * El botón que abre el diálogo de Meta. Al terminar, el backend canjea el
 * permiso, comprueba el número con Meta, suscribe los avisos, lo registra y
 * crea la fila: nadie teclea nada.
 *
 * TODO SE PREPARA ANTES DEL CLIC. Antes el clic pedía los datos al backend y
 * cargaba el SDK, y después abría Meta: con esas esperas por medio el navegador
 * podía descartar la ventana sin avisar, y el botón se quedaba girando para
 * siempre (13-10-2026, en producción). Ahora el clic llama a Meta directamente.
 *
 * NADIE SE QUEDA MIRANDO UN BOTÓN QUE GIRA. Meta puede cerrar su ventana sin
 * devolver nada: a quien ya autorizó la aplicación le ofrece «continuar con la
 * configuración anterior» y, si la acepta, cierra sin pasar por la elección de
 * número (13-10-2026, en producción). Tampoco avisa si la ventana no llega a
 * abrirse. Las dos cosas se ven igual desde aquí: la persona está de vuelta en
 * esta página y no ha llegado nada. Tras SIN_RESPUESTA_MS así, el botón se
 * suelta y se dice qué hacer. El intento sigue escuchando, por si algo llega
 * tarde.
 */
export function BotonConectar({
  texto,
  puede,
  alCambiar,
}: {
  texto: string;
  puede: boolean;
  alCambiar: (e: EstadoAsistente) => void;
}) {
  const [listo, setListo] = useState<{ fb: FB; configId: string } | null>(null);
  const [preparando, setPreparando] = useState(true);
  const [paso, setPaso] = useState<"quieto" | "abriendo" | "guardando">("quieto");
  const [error, setError] = useState<string | null>(null);
  const intento = useRef<DialogoDeMeta | null>(null);

  const preparar = useCallback(async () => {
    try {
      const meta = await getDatosDeMeta();
      if (!meta.disponible || !meta.app_id || !meta.config_id) {
        setError(NO_DISPONIBLE);
        return;
      }
      setListo({ fb: await prepararMeta(meta.app_id, meta.version), configId: meta.config_id });
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "No hemos podido preparar la conexión. Inténtalo otra vez.");
    } finally {
      setPreparando(false);
    }
  }, []);

  useEffect(() => {
    if (puede) void preparar();
    return () => intento.current?.abandonar();
  }, [puede, preparar]);

  function conectar() {
    if (!listo) {
      // Falló la preparación: el clic la reintenta (no abre Meta, así que no importa esperar).
      setPreparando(true);
      void preparar();
      return;
    }
    intento.current?.abandonar();
    setError(null);
    setPaso("abriendo");
    const este = abrirDialogoDeMeta(listo.fb, listo.configId); // FB.login, aquí mismo, dentro del clic
    intento.current = este;
    let enLaPaginaDesde: number | null = null;
    const reloj = window.setInterval(() => {
      if (intento.current !== este) return window.clearInterval(reloj);
      if (!document.hasFocus()) {
        enLaPaginaDesde = null; // está en la ventana de Meta
        return;
      }
      enLaPaginaDesde ??= Date.now();
      if (Date.now() - enLaPaginaDesde < SIN_RESPUESTA_MS) return;
      window.clearInterval(reloj);
      setPaso("quieto");
      setError(MENSAJE_SIN_RESPUESTA);
    }, 500);

    este.resultado
      .then(async (resultado) => {
        if (intento.current !== este) return;
        window.clearInterval(reloj);
        setError(null);
        setPaso("guardando");
        alCambiar(await conectarWhatsapp(resultado));
      })
      .catch((e: unknown) => {
        if (intento.current !== este) return;
        window.clearInterval(reloj);
        setError(
          e instanceof CanceladoPorElSalon
            ? "No se ha conectado nada: has cerrado el diálogo de Meta antes de terminar."
            : e instanceof Error
              ? e.message
              : "No hemos podido conectarlo. Inténtalo otra vez.",
        );
      })
      .finally(() => {
        if (intento.current !== este) return;
        intento.current = null;
        setPaso("quieto");
      });
  }

  if (!puede) return null;
  const ocupado = preparando || paso !== "quieto";
  return (
    <div className="mt-5">
      <button
        type="button"
        disabled={ocupado}
        aria-busy={ocupado}
        onClick={conectar}
        className="inline-flex h-11 w-full items-center justify-center gap-2 rounded-xl bg-gold px-4 text-[13.5px] font-semibold text-[#1a1610] transition-opacity hover:opacity-90 disabled:cursor-progress disabled:opacity-80 sm:w-auto"
      >
        {ocupado ? <Loader2 size={16} className="animate-spin" /> : <MessageCircle size={16} />}
        {preparando
          ? "Preparando…"
          : paso === "abriendo"
            ? "Abriendo Meta…"
            : paso === "guardando"
              ? "Conectando tu número…"
              : texto}
      </button>
      {error && (
        <p className="m-0 mt-2.5 text-[12.5px] leading-relaxed text-danger" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
