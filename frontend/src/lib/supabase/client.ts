import { createBrowserClient } from "@supabase/ssr";

import { fetchConPlazo } from "@/lib/api/plazo";
import { authConPlazo } from "./auth-con-plazo";
import { cookiesDelNavegador } from "./sesion";

export function createClient() {
  return authConPlazo(createBrowserClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY!,
    // Supabase también es «de fuera»: sin esto, iniciar sesión o refrescarla
    // espera para siempre si Supabase no contesta (ver lib/api/plazo.ts).
    {
      global: { fetch: (entrada, opciones) => fetchConPlazo(entrada, opciones) },
      // 12 horas desde el último uso (ver `sesion.ts`).
      cookies: cookiesDelNavegador,
    }
  ));
}
