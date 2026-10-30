"use client";

import { useEffect, useState } from "react";
import { MapPin } from "lucide-react";
import { getCentros, type Centro } from "@/lib/api/centros";

/**
 * A ESTE CENTRO LE FALTA EL TELÉFONO O LA DIRECCIÓN. Una barra, no un muro.
 *
 * EL CASO REAL, del aterrizaje del 20-09-2026: San Sebastián se creó con un
 * nombre y nada más. Su página pública le decía a una clienta «escríbenos por
 * otro medio o pásate por el salón» sin decir por qué medio ni adónde, y su bot
 * tampoco tenía nada que darle. Desde hoy no se puede crear un centro así; los
 * que ya existen hay que poder arreglarlos, y para eso primero hay que enterarse.
 *
 * NO BLOQUEA NADA, y es deliberado: un salón que no puede entrar a media mañana
 * porque le exigimos un dato NUESTRO es peor problema que el que arreglamos. Es
 * el mismo criterio que `BarraPagoPendiente` con `past_due`, y por el mismo
 * motivo. Persistente sí: no se puede cerrar, porque se arregla en un minuto y
 * mientras tanto cuesta clientas.
 *
 * UNA LÍNEA, Y CORTA. Explicaba lo mismo que el 409 de Reservas —qué falta, por
 * qué importa, adónde ir— y entre las dos se comían dos tercios de una pantalla
 * de 360 px para un solo problema. Se reparten el trabajo: el 409 aparece en el
 * momento de la acción y explica; esta barra está para RECORDARLO cuando nadie
 * está pensando en ello, y para eso basta el titular y el enlace.
 *
 * UNA LÍNEA DE VERDAD, medida a 360: el hueco del texto son ~187 px (328 de
 * barra, menos sus márgenes, el icono y el enlace), o sea unos 29 caracteres a
 * 13 px. «A este centro le falta la dirección o el teléfono» son 50 y partía en
 * dos renglones — una línea en el código no es una línea en la pantalla.
 *
 * LA PIDE QUIEN PUEDE ARREGLARLA. El perfil del salón es de administración, así
 * que a quien no puede editarlo se le ahorra un aviso que no puede atender.
 *
 * DOS CASOS, DOS TEXTOS. Si el que está incompleto es el centro ABIERTO, el
 * botón lleva derecho al formulario. Si es otro de la cuenta, se dice cuál y que
 * hay que cambiarse a él — porque el perfil edita el centro activo, y mandarla a
 * una pantalla que guarda en otro sitio sería peor que no mandarla.
 */
export function BarraCentroIncompleto() {
  const [centros, setCentros] = useState<Centro[] | null>(null);
  const [activo, setActivo] = useState<string | null>(null);

  useEffect(() => {
    let vivo = true;
    void (async () => {
      try {
        const r = await getCentros();
        if (!vivo) return;
        setCentros(r.centros);
        setActivo(r.activo);
      } catch {
        // Sin la lista no se avisa. Preferible a un aviso que no se sabe si es
        // cierto: esta barra existe para que alguien actúe, y actuar sobre algo
        // que no pasa gasta la confianza del siguiente aviso.
      }
    })();
    return () => {
      vivo = false;
    };
  }, []);

  const incompletos = (centros ?? []).filter((c) => c.falta_contacto);
  if (incompletos.length === 0) return null;

  const esElAbierto = incompletos.some((c) => c.id === activo);
  const otros = incompletos.filter((c) => c.id !== activo);

  return (
    <div
      role="status"
      // UNA FILA TAMBIÉN A 360: si se apila, vuelven a ser dos renglones y la
      // barra deja de ser el recordatorio corto que se quería.
      className="mt-3 flex w-full items-center justify-between gap-2 rounded-lg border border-[#6A5B2B] bg-[rgba(201,168,76,.12)] px-3 py-2"
    >
      <div className="flex min-w-0 items-center gap-2">
        <MapPin size={15} className="flex-shrink-0 text-gold" />
        {/* `truncate` es el seguro: un nombre de centro largo puede estirar la
            frase, y la barra tiene que seguir siendo un renglón. */}
        <p className="m-0 min-w-0 truncate text-[13px] leading-snug text-[#E4CC85]">
          {esElAbierto
            ? "Faltan dirección y teléfono"
            : `En ${otros.map((c) => c.nombre_comercial).join(" y ")}: faltan dirección y teléfono`}
        </p>
      </div>
      {esElAbierto && (
        <a
          href="/ajustes?tab=perfil"
          className="inline-flex h-11 min-h-[44px] flex-shrink-0 items-center justify-center rounded-xl px-3 text-[13px] font-semibold text-gold underline decoration-dotted underline-offset-4 transition-colors hover:text-[#E4CC85]"
        >
          Completar
        </a>
      )}
    </div>
  );
}
