"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { onConsentChange, hasConsent } from "@/lib/cookie-consent";
import {
  codigoDelEnlace,
  codigoGuardado,
  conElCodigo,
  guardarElCodigo,
  olvidarElCodigo,
} from "@/lib/referido";

function codigoDeLaDireccion(): string | null {
  return codigoDelEnlace(new URLSearchParams(window.location.search));
}

/**
 * EL CÓDIGO DEL REFERIDO, en la dirección y —con permiso— en la cookie (0070).
 * Ver `lib/referido.ts`. No pinta nada: va una vez, en el layout raíz.
 *
 *   · ARRASTRARLO: mientras la dirección lleve `?ref=`, cada enlace interno
 *     que se pulse lo lleva también. Se hace al pulsar, sobre CUALQUIER enlace,
 *     para no depender de que cada página se acuerde de añadirlo. Un `Link` de
 *     Next navega por su `href` y no por el del DOM: por eso se cancela su
 *     clic y se navega aquí con el código (Next respeta `defaultPrevented`).
 *     Para una pestaña nueva (Ctrl, botón central) basta con reescribir el
 *     `href` del DOM, que es el que usa el navegador.
 *
 *   · LA COOKIE, EN SU ORDEN. Al llegar, solo si ya había consentimiento de
 *     marketing. Y si lo da DESPUÉS —llega con `?ref=`, ve el banner y
 *     acepta—, en ese momento, con el código que trae la dirección. Si lo
 *     retira, o el consentimiento caduca, la cookie se va.
 */
export function ArrastraElReferido() {
  const router = useRouter();

  useEffect(() => {
    const codigo = codigoDeLaDireccion();
    if (codigo) guardarElCodigo(codigo);
    if (!hasConsent("marketing") && codigoGuardado(document.cookie)) olvidarElCodigo();

    const dejar = onConsentChange((estado) => {
      if (!estado.marketing) {
        olvidarElCodigo();
        return;
      }
      const ahora = codigoDeLaDireccion();
      if (ahora) guardarElCodigo(ahora);
    });

    function alPulsar(e: MouseEvent) {
      const codigoAhora = codigoDeLaDireccion();
      if (!codigoAhora) return;
      const enlace = (e.target as Element | null)?.closest?.("a[href]") as HTMLAnchorElement | null;
      if (!enlace || enlace.hasAttribute("download")) return;
      const destino = conElCodigo(enlace.getAttribute("href") ?? "", window.location.href, codigoAhora);
      if (!destino) return;
      const nuevaPestana =
        e.type === "auxclick" || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey ||
        (enlace.target && enlace.target !== "_self");
      if (nuevaPestana) {
        enlace.setAttribute("href", destino);
        return;
      }
      e.preventDefault();
      router.push(destino);
    }

    document.addEventListener("click", alPulsar, true);
    document.addEventListener("auxclick", alPulsar, true);
    return () => {
      dejar();
      document.removeEventListener("click", alPulsar, true);
      document.removeEventListener("auxclick", alPulsar, true);
    };
  }, [router]);

  return null;
}
