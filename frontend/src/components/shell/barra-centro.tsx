"use client";

import { useEffect, useState } from "react";
import { Check, Loader2, MapPin, PauseCircle } from "lucide-react";
import { getCentros, recordarCentro, type Centro } from "@/lib/api/centros";
import {
  guardarCentroActivo,
  leerCentroActivo,
  olvidarCentroActivo,
} from "@/lib/centro-activo";
import { useSession } from "@/lib/mock-session";
import { enModoMostrador } from "@/lib/modo-mostrador";
import { useTurnos } from "@/lib/turnos";

/**
 * EN QUÉ CENTRO ESTÁS TRABAJANDO. Una barra, no un menú.
 *
 * EL PEOR FALLO DE UN SELECTOR no es que no funcione: es que alguien agende en
 * el centro equivocado sin darse cuenta y una clienta se presente donde no la
 * esperan. De ahí las tres decisiones de esta pieza:
 *
 *   1. NO SE ESCONDE. Es una barra a todo lo ancho, encima de la cabecera,
 *      en móvil y en escritorio. Un desplegable en la esquina se deja de ver a
 *      los dos días; una barra que ocupa una línea entera, no.
 *   2. DICE EL NOMBRE, no un icono. «Centro Norte» se lee de reojo; un pin de
 *      colores hay que interpretarlo.
 *   3. CON UN SOLO CENTRO NO EXISTE. Ni barra, ni hueco, ni un píxel. Es la
 *      equivalencia: hoy todas las cuentas tienen un centro y
 *      para todas ellas la aplicación no cambia en nada.
 *
 * Y NO ES LA ÚNICA DEFENSA. La chrome se deja de ver: por eso el centro se
 * nombra TAMBIÉN dentro de la acción que puede salir cara —el diálogo de nueva
 * cita—, que es donde de verdad importa. Esto es el contexto; aquello es el
 * recordatorio en el momento.
 */
export function BarraCentro() {
  const { session } = useSession();
  const [centros, setCentros] = useState<Centro[] | null>(null);
  const [activo, setActivo] = useState<string | null>(null);
  const [abierto, setAbierto] = useState(false);
  const [cambiando, setCambiando] = useState<string | null>(null);
  const [aviso, setAviso] = useState<string | null>(null);
  // Con centros en pausa y SIN elección de la titular (0088): el plan bajó
  // desde fuera y se quedó el más antiguo. Se le dice cuál y cómo cambiarlo.
  const [sinElegir, setSinElegir] = useState(false);
  // UNA RESPUESTA QUE YA NO CORRESPONDE NO ESCRIBE. Aquí no solo pinta: guarda
  // el centro. Sin esto el primer cambio de centro tras entrar no ocurría en 6
  // de 8 intentos: la carga inicial seguía en vuelo, el clic guardaba el centro
  // nuevo y la respuesta vieja llegaba después y lo reescribía con el anterior.
  // Ver `lib/turnos.ts`.
  const turnos = useTurnos();

  useEffect(() => {
    const turno = turnos.abrir();
    void (async () => {
      try {
        const r = await getCentros();
        if (!turno.vigente()) return;
        setCentros(r.centros);
        setActivo(r.activo);
        setSinElegir(r.centros.some((c) => c.en_pausa) && r.sigue_elegido === false);

        // EL CENTRO GUARDADO YA NO VALE. El backend ha vuelto al propio y lo
        // ha marcado; aquí se borra lo guardado —si no, cada petición volvería
        // a pedir el mismo centro inválido para siempre— y se dice.
        //
        // Esto es la salida del callejón: pasó que cambiar de centro dejaba la
        // sesión sin forma de volver y hubo que arreglarlo con un UPDATE en
        // producción. Ningún estado puede ser de ida y no de vuelta.
        if (r.centro_rechazado) {
          olvidarCentroActivo();
          setAviso(
            "El centro que tenías abierto ya no está disponible. Te hemos devuelto al tuyo.",
          );
        } else if (r.centro_en_pausa) {
          // OTRO MOTIVO Y OTRO TEXTO. Aquí el centro SIGUE SIENDO SUYO: lo que
          // pasa es que el plan ya no lo incluye. «Ya no está disponible» haría
          // pensar que se ha perdido algo, y no se ha perdido nada.
          olvidarCentroActivo();
          setAviso(
            "Ese centro está en pausa porque tu plan incluye menos centros. No se ha borrado nada.",
          );
        } else if (
          // NO SE GUARDA SIN SABER DE QUIÉN ES. Este efecto puede correr antes
          // de que la sesión cargue, y guardar entonces dejaba la preferencia
          // sin dueño: en un navegador compartido —el del mostrador— se la
          // habría quedado la siguiente persona.
          session?.usuarioId &&
          leerCentroActivo(session.usuarioId) !== r.activo
        ) {
          // El backend manda: si lo guardado no coincide con lo que la
          // petición ha usado de verdad, gana lo que ha usado.
          guardarCentroActivo(session.usuarioId, r.activo);
        }
      } catch {
        // Sin la lista no se enseña selector. Preferible a enseñar uno que no
        // se sabe si es correcto.
        if (turno.vigente()) setCentros([]);
      }
    })();
  }, [session?.usuarioId, turnos]);

  // LA EQUIVALENCIA, aquí y no en una bandera de configuración: con un centro
  // este componente no devuelve nada.
  if (!centros || centros.length < 2) return null;

  const elActivo = centros.find((c) => c.id === activo) ?? centros[0];
  // UN MOSTRADOR ESTÁ FIJADO AL CENTRO DONDE SE ACTIVÓ (0090): el backend
  // ignora la elección. En vez de una lista que no hace nada, se dice.
  const fijado = enModoMostrador();

  async function cambiar(centro: Centro) {
    if (centro.id === activo) {
      setAbierto(false);
      return;
    }
    // Lo que estuviera en vuelo es de ANTES de elegir: ya no puede escribir.
    // Tiene que ir antes de guardar, no después.
    turnos.caducar();
    setCambiando(centro.id);
    guardarCentroActivo(session?.usuarioId, centro.id);
    if (centro.en_pausa) {
      // UN CENTRO EN PAUSA SE ABRE EN SU AGENDA, en solo lectura (0088). No
      // se recuerda en el servidor: recordar es escribir, y en él no se escribe.
      window.location.assign("/agenda");
      return;
    }
    try {
      await recordarCentro();
    } catch {
      // Que no se pueda recordar en el servidor no impide trabajar: la
      // elección ya está puesta para las siguientes peticiones.
    }
    // Se recarga en vez de refrescar por partes: cada pantalla abierta tiene
    // datos del centro anterior, y refrescar «casi todo» deja justo el resto
    // mintiendo. Es el único sitio de la aplicación donde una recarga entera
    // es lo correcto.
    window.location.reload();
  }

  return (
    <div className="w-full pt-3">
      <button
        type="button"
        onClick={() => setAbierto((v) => !v)}
        aria-expanded={abierto}
        aria-label={
          fijado
            ? `Centro del mostrador: ${elActivo.nombre_comercial}. Pulsa para ver cómo cambiarlo.`
            : `Centro activo: ${elActivo.nombre_comercial}. Pulsa para cambiar.`
        }
        className="flex h-11 w-full min-h-[44px] items-center gap-2 rounded-lg border border-gold/40 bg-gold-tenue px-3 text-left transition-colors hover:border-gold/70"
      >
        <MapPin size={16} className="flex-shrink-0 text-gold" />
        <span className="text-[11px] font-semibold uppercase tracking-[.08em] text-gold/80">
          Centro
        </span>
        <span className="min-w-0 flex-1 truncate text-[13.5px] font-semibold text-text">
          {elActivo.nombre_comercial}
        </span>
        <span className="flex-shrink-0 text-[12px] font-semibold text-gold">{fijado ? "Fijado" : "Cambiar"}</span>
      </button>

      {abierto && fijado && (
        <p
          role="status"
          className="mt-1.5 rounded-lg border border-border bg-side px-3 py-2 text-[12.5px] leading-snug text-mute"
        >
          Este mostrador está fijado a {elActivo.nombre_comercial}. Para usarlo en otro centro, quítale el modo
          mostrador en Ajustes › PIN de gestión y vuelve a activarlo desde ese centro.
        </p>
      )}

      {aviso && (
        <p
          role="status"
          className="mt-1.5 rounded-lg border border-[#6A5B2B] bg-[rgba(201,168,76,.12)] px-3 py-2 text-[12.5px] leading-snug text-[#E4CC85]"
        >
          {aviso}
        </p>
      )}

      {sinElegir && (
        <p
          role="status"
          className="mt-1.5 flex flex-col gap-1 rounded-lg border border-[#6A5B2B] bg-[rgba(201,168,76,.12)] px-3 py-2 text-[12.5px] leading-snug text-[#E4CC85] sm:flex-row sm:items-center sm:justify-between sm:gap-3"
        >
          <span>
            Tu plan incluye menos centros y se ha quedado activo {elActivo.nombre_comercial}, el más
            antiguo. Nadie lo eligió: puedes cambiarlo.
          </span>
          <a
            href="/ajustes?tab=centros"
            className="inline-flex min-h-[44px] flex-shrink-0 items-center font-semibold text-gold hover:underline"
          >
            Elegir centro
          </a>
        </p>
      )}

      {abierto && !fijado && (
        <div className="mt-1.5 overflow-hidden rounded-lg border border-border bg-side">
          {centros.map((c) => {
            const esElActivo = c.id === elActivo.id;
            // EL CENTRO EN PAUSA NO SE ESCONDE. Se queda en la lista, marcado,
            // y abre SU AGENDA en solo lectura (0088): ver lo que tiene cogido
            // es lo único que se puede hacer en él.
            const pausado = Boolean(c.en_pausa);
            return (
              <button
                key={c.id}
                type="button"
                onClick={() => void cambiar(c)}
                disabled={cambiando !== null}
                aria-label={
                  pausado
                    ? `${c.nombre_comercial}. En pausa: abre su agenda en solo lectura.`
                    : undefined
                }
                className={`flex h-12 min-h-[44px] w-full items-center gap-2.5 px-3 text-left text-[14px] transition-colors ${
                  pausado
                    ? "text-faint hover:bg-card-alt hover:text-mute"
                    : esElActivo
                      ? "bg-gold-tenue text-text"
                      : "text-mute hover:bg-card-alt hover:text-text"
                }`}
              >
                {cambiando === c.id ? (
                  <Loader2 size={15} className="flex-shrink-0 animate-spin text-gold" />
                ) : pausado ? (
                  <PauseCircle size={15} className="flex-shrink-0 text-faint" />
                ) : (
                  <Check
                    size={15}
                    className={`flex-shrink-0 ${esElActivo ? "text-gold" : "opacity-0"}`}
                  />
                )}
                <span className="min-w-0 flex-1 truncate">{c.nombre_comercial}</span>
                {pausado && (
                  <span className="flex-shrink-0 rounded-full border border-border px-2 py-0.5 text-[10.5px] font-semibold uppercase tracking-[.06em] text-faint">
                    En pausa · ver agenda
                  </span>
                )}
              </button>
            );
          })}
          {centros.some((c) => c.en_pausa) && (
            /* Y qué hacer con eso. Una etiqueta sin salida solo informa de que
               algo va mal; el camino de vuelta es media respuesta. */
            <a
              href="/ajustes?tab=centros"
              className="flex min-h-[44px] items-center gap-2 border-t border-border px-3 text-[12.5px] text-gold hover:underline"
            >
              Ver los centros en pausa y sus citas
            </a>
          )}
        </div>
      )}
    </div>
  );
}
