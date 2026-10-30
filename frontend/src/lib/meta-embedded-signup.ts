/**
 * El diálogo de Meta para conectar el WhatsApp del salón (Embedded Signup, 0085).
 *
 * CÓMO FUNCIONA. Se carga el SDK de Facebook y se abre `FB.login` con la
 * «configuración» de Embedded Signup que se crea en el panel de Meta. Mientras
 * el salón elige su cuenta y su número dentro del diálogo, Meta avisa a esta
 * ventana con `postMessage` (tipo `WA_EMBEDDED_SIGNUP`): al terminar manda el
 * `phone_number_id` y el `waba_id`. Y el callback de `FB.login` trae un `code`
 * de un solo uso. Las dos cosas llegan por caminos distintos y en cualquier
 * orden: se espera a tenerlas las dos.
 *
 * El navegador NUNCA ve un token: el `code` se canjea en el backend con el App
 * Secret, que solo vive allí.
 */

import type { ResultadoDeMeta } from "@/lib/api/bot";

type FbLoginRespuesta = { authResponse?: { code?: string } | null };
export type FB = {
  init: (opciones: Record<string, unknown>) => void;
  login: (cb: (r: FbLoginRespuesta) => void, opciones: Record<string, unknown>) => void;
};

declare global {
  interface Window {
    FB?: FB;
    fbAsyncInit?: () => void;
  }
}

/** Lo que Meta cuenta por `postMessage`, ya leído. `null` si no es suyo. */
export type AvisoDeMeta =
  | { tipo: "fin"; phone_number_id: string; waba_id: string; coexistencia: boolean }
  | { tipo: "cancelado"; paso: string | null }
  | { tipo: "error"; mensaje: string };

/**
 * Lee un `postMessage`. Solo cuenta lo que viene de un origen de Facebook y
 * tiene la forma de Embedded Signup: cualquier otra ventana podría escribirle a
 * esta, y un número inventado aquí no debe llegar ni al backend (que además lo
 * comprueba con Meta).
 */
export function leerAvisoDeMeta(origen: string, datos: unknown): AvisoDeMeta | null {
  let host: string;
  try {
    host = new URL(origen).hostname;
  } catch {
    return null;
  }
  if (!(host === "facebook.com" || host.endsWith(".facebook.com"))) return null;
  let d: unknown = datos;
  if (typeof d === "string") {
    try {
      d = JSON.parse(d);
    } catch {
      return null;
    }
  }
  if (!d || typeof d !== "object") return null;
  const m = d as { type?: string; event?: string; data?: Record<string, unknown> };
  if (m.type !== "WA_EMBEDDED_SIGNUP") return null;
  const data = m.data ?? {};
  if (m.event === "FINISH" || m.event === "FINISH_ONLY_WABA" || m.event === "FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING") {
    const phone = String(data.phone_number_id ?? "");
    const waba = String(data.waba_id ?? "");
    if (!/^\d+$/.test(phone) || !/^\d+$/.test(waba)) {
      return { tipo: "error", mensaje: "Meta no ha devuelto el número. Vuelve a empezar y elige un número." };
    }
    return {
      tipo: "fin",
      phone_number_id: phone,
      waba_id: waba,
      coexistencia: m.event === "FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING",
    };
  }
  if (m.event === "CANCEL") {
    return { tipo: "cancelado", paso: typeof data.current_step === "string" ? data.current_step : null };
  }
  if (m.event === "ERROR") {
    return { tipo: "error", mensaje: String(data.error_message ?? "Meta ha devuelto un error.") };
  }
  return null;
}

let cargando: Promise<FB> | null = null;

/**
 * Carga e inicia el SDK, una sola vez por página. Se llama al PINTAR el botón,
 * nunca dentro del clic: ver `abrirDialogoDeMeta`.
 */
export function prepararMeta(appId: string, version: string): Promise<FB> {
  if (window.FB) return Promise.resolve(window.FB);
  if (cargando) return cargando;
  cargando = new Promise<FB>((resolver, rechazar) => {
    window.fbAsyncInit = () => {
      window.FB!.init({ appId, autoLogAppEvents: true, xfbml: false, version });
      resolver(window.FB!);
    };
    const s = document.createElement("script");
    s.src = "https://connect.facebook.net/es_ES/sdk.js";
    s.async = true;
    s.defer = true;
    s.crossOrigin = "anonymous";
    s.onerror = () => {
      cargando = null;
      rechazar(new Error("No se ha podido cargar el diálogo de Meta. Revisa tu conexión y vuelve a intentarlo."));
    };
    document.body.appendChild(s);
  });
  return cargando;
}

export class CanceladoPorElSalon extends Error {}

export type DialogoDeMeta = {
  /** Lo que hace falta para conectar, cuando Meta lo haya dado todo. */
  resultado: Promise<ResultadoDeMeta>;
  /** Deja de escuchar este intento (otro clic, o el botón se va). */
  abandonar: () => void;
};

/**
 * Abre el diálogo de Meta. TIENE QUE LLAMARSE DIRECTAMENTE DESDE EL CLIC, con el
 * SDK ya cargado (`prepararMeta`) y sin ningún `await` por delante: el navegador
 * solo deja abrir una ventana si la pide la persona, y una espera de por medio
 * puede hacer que la descarte. Por eso esto no es `async`: `FB.login` corre
 * antes de devolver.
 *
 * SI LA VENTANA NO SE ABRE, META NO AVISA. Con `window.open` devolviendo `null`
 * el SDK solo lo anota en su registro interno y no llama al callback: la promesa
 * no se resuelve nunca. El reloj para no dejar a nadie esperando lo pone quien
 * llama (`boton-conectar.tsx`).
 */
export function abrirDialogoDeMeta(fb: FB, configId: string): DialogoDeMeta {
  let abandonar = () => {};
  const resultado = new Promise<ResultadoDeMeta>((resolver, rechazar) => {
    let code: string | null = null;
    let fin: Extract<AvisoDeMeta, { tipo: "fin" }> | null = null;
    let cerrado = false;

    const terminar = (error?: Error) => {
      if (cerrado) return;
      if (!error && !(code && fin)) return;
      cerrado = true;
      window.removeEventListener("message", alAvisar);
      if (error) rechazar(error);
      else resolver({ code: code!, waba_id: fin!.waba_id, phone_number_id: fin!.phone_number_id, coexistencia: fin!.coexistencia });
    };
    abandonar = () => {
      cerrado = true;
      window.removeEventListener("message", alAvisar);
    };

    function alAvisar(e: MessageEvent) {
      const aviso = leerAvisoDeMeta(e.origin, e.data);
      if (!aviso) return;
      if (aviso.tipo === "fin") {
        fin = aviso;
        terminar();
      } else if (aviso.tipo === "cancelado") {
        terminar(new CanceladoPorElSalon("Has cerrado el diálogo de Meta antes de terminar."));
      } else {
        terminar(new Error(aviso.mensaje));
      }
    }
    window.addEventListener("message", alAvisar);

    fb.login(
      (r) => {
        const c = r?.authResponse?.code;
        if (!c) {
          terminar(new CanceladoPorElSalon("Has cerrado el diálogo de Meta antes de terminar."));
          return;
        }
        code = c;
        terminar();
      },
      {
        config_id: configId,
        response_type: "code",
        override_default_response_type: true,
        extras: { setup: {}, sessionInfoVersion: "3" },
      },
    );
  });
  return { resultado, abandonar };
}
