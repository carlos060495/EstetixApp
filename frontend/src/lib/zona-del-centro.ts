"use client";

import { useEffect, useState } from "react";
import { getMe } from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";
import { ZONA_POR_DEFECTO, ZONAS, type ZonaHoraria } from "@/lib/horas";

/**
 * La zona horaria del centro activo (0060), leída UNA vez por carga de página.
 *
 * Cambiar de centro recarga la página (`barra-centro.tsx`), así que la caché
 * del módulo no puede quedarse con la zona del centro anterior.
 *
 * Si el backend todavía no la manda (despliegue a medias: el frontend nuevo
 * con el backend viejo), se usa la península, que es la de todos los centros
 * de hoy (01-10-2026) y la que usaba el backend viejo.
 */
let promesa: Promise<ZonaHoraria> | null = null;

export function cargarZonaDelCentro(): Promise<ZonaHoraria> {
  if (!promesa) {
    promesa = getMe()
      .then((me) => (me.zona_horaria && me.zona_horaria in ZONAS ? (me.zona_horaria as ZonaHoraria) : ZONA_POR_DEFECTO))
      .catch((e) => {
        promesa = null;
        throw e;
      });
  }
  return promesa;
}

/** Para los tests y para cuando se cambia la zona en Ajustes. */
export function olvidarZonaDelCentro(): void {
  promesa = null;
}

/**
 * La zona del centro, o `null` mientras llega: hasta entonces no se pinta ni se
 * guarda ninguna hora, porque cualquiera sería una suposición. Si no se puede
 * saber (la sesión ha fallado o no ha contestado a tiempo), `fallo` es `true`,
 * `mensaje` dice qué pasó y `reintentar` lo vuelve a pedir: nunca una pantalla
 * girando (29-10-2026).
 */
export function useZonaDelCentro(): {
  zona: ZonaHoraria | null;
  fallo: boolean;
  mensaje: string | null;
  reintentar: () => void;
} {
  const [estado, setEstado] = useState<{ zona: ZonaHoraria | null; mensaje: string | null }>({
    zona: null,
    mensaje: null,
  });
  const [intento, setIntento] = useState(0);
  useEffect(() => {
    let vivo = true;
    cargarZonaDelCentro()
      .then((zona) => {
        if (vivo) setEstado({ zona, mensaje: null });
      })
      .catch((e: unknown) => {
        if (vivo)
          setEstado({
            zona: null,
            mensaje: e instanceof ApiError ? e.message : "No se ha podido saber la zona horaria del centro.",
          });
      });
    return () => {
      vivo = false;
    };
  }, [intento]);
  return {
    zona: estado.zona,
    fallo: estado.mensaje !== null,
    mensaje: estado.mensaje,
    reintentar: () => {
      setEstado({ zona: null, mensaje: null });
      setIntento((n) => n + 1);
    },
  };
}
