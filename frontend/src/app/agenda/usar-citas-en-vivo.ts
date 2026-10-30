"use client";

import { useCallback, useEffect, useRef } from "react";
import { listAppointments, type Appointment } from "@/lib/api/appointments";
import { fechaDelDia, instanteDe, sumarDias } from "@/lib/horas";

/**
 * La agenda, al día sola.
 *
 * QUÉ RESUELVE: cuando el bot de WhatsApp agenda una cita, o la recepcionista
 * la mueve desde otro dispositivo, quien tiene la agenda abierta no se entera
 * hasta que recarga. En un mostrador eso es una hora que dos personas creen
 * libre.
 *
 * POR QUÉ SONDEO Y NO REALTIME: el frontend no habla nunca con PostgREST ni con
 * Supabase; todo pasa por FastAPI, que es donde se revalida el token y se aplica
 * la RLS. Abrir un WebSocket contra Supabase desde el navegador rompería esa
 * regla para ganar unos segundos. Se reutiliza `GET /appointments`, el mismo
 * endpoint que ya carga la agenda, con el mismo rango del día visible.
 *
 * TRES REGLAS QUE EXPLICAN TODO EL FICHERO:
 *
 *   1. NO INTERRUMPIR. Con un modal abierto no se consulta nada. Ni se pierde
 *      lo que el usuario está escribiendo, ni se le mueve la rejilla bajo la
 *      mano, ni cambian las citas del día que el propio modal está usando para
 *      avisar de solapes. En cuanto cierra, se pone al día de inmediato.
 *   2. NO REPINTAR SIN MOTIVO. Si la respuesta es idéntica a lo que ya hay, NO
 *      se llama a `onActualizar`: sin `setState` no hay render, y sin render no
 *      hay salto. Y cuando sí hay cambios, las citas que no cambiaron se
 *      devuelven con la MISMA referencia, así que solo se repinta lo que de
 *      verdad se movió.
 *   3. BARATO. Solo el día visible, y nada mientras la pestaña está en segundo
 *      plano —una agenda abierta toda la noche no vale una consulta por
 *      minuto—. Al volver a ella se consulta enseguida, que es justo cuando
 *      hace falta.
 */

/**
 * Cada cuánto se consulta el día visible.
 *
 * UN MINUTO, el mismo ritmo que la bandeja de conversaciones. En un mostrador
 * la diferencia con medio minuto no se nota —entre clienta y clienta pasa mucho
 * más que eso— y cuesta la mitad. Lo que hace que el minuto no se sienta es el
 * aviso: cuando una cita llega sola, se señala, así que nadie tiene que estar
 * mirando la pantalla para enterarse.
 */
export const INTERVALO_MS = 60_000;

/**
 * Lo que hace que dos citas se consideren «la misma sin cambios».
 *
 * Se serializa el objeto ENTERO y no una lista de campos elegidos a mano: una
 * lista se queda corta el día que el backend añada un campo, y entonces un
 * cambio real pasaría por «sin cambios», que es el peor fallo posible aquí —la
 * agenda diría que está al día enseñando algo viejo—. El orden de las claves lo
 * fija el serializador de FastAPI y es estable dentro de un mismo despliegue; si
 * cambiara, el único efecto sería un repintado de más una vez.
 */
function firma(cita: Appointment): string {
  return JSON.stringify(cita);
}

type Diferencias = {
  /** Las citas nuevas, reutilizando la referencia de las que no cambiaron. */
  citas: Appointment[];
  /** Ids que aparecen o cambian. Sirve para señalarlas en el tablero. */
  cambiados: string[];
  eliminados: string[];
};

/** Qué ha cambiado entre lo que se está enseñando y lo que acaba de llegar. */
export function diferencias(previas: Appointment[], nuevas: Appointment[]): Diferencias | null {
  const antes = new Map(previas.map((c) => [c.id, c]));
  const cambiados: string[] = [];

  const citas = nuevas.map((n) => {
    const p = antes.get(n.id);
    if (p && firma(p) === firma(n)) return p; // misma referencia: no se repinta
    cambiados.push(n.id);
    return n;
  });

  const vivos = new Set(nuevas.map((c) => c.id));
  const eliminados = previas.filter((c) => !vivos.has(c.id)).map((c) => c.id);

  // `null` = no ha pasado nada. El llamador no toca el estado, así que no hay
  // render: es la diferencia entre una agenda tranquila y una que parpadea cada
  // minuto.
  if (cambiados.length === 0 && eliminados.length === 0) return null;
  return { citas, cambiados, eliminados };
}

type Opciones = {
  /** El día que se está viendo. Se consulta ese, no «hoy». */
  fecha: Date;
  /** La zona del centro: el día va de su medianoche a su medianoche. */
  zona: string;
  /** Lo que hay ahora en pantalla, para comparar. */
  citas: Appointment[];
  /**
   * El usuario está a mitad de algo (un modal abierto, la carga inicial, la
   * pantalla de error). Mientras sea `true` no se consulta nada; al pasar a
   * `false` se consulta enseguida si se saltó algún turno.
   */
  pausado: boolean;
  onActualizar: (citas: Appointment[], cambiados: string[]) => void;
};

/** De la medianoche a la medianoche DEL CENTRO. */
function rangoDelDia(fecha: Date, zona: string): { desde: string; hasta: string } {
  const d = fechaDelDia(fecha);
  return { desde: instanteDe(d, "00:00", zona), hasta: instanteDe(sumarDias(d, 1), "00:00", zona) };
}

export function useCitasEnVivo({ fecha, zona, citas, pausado, onActualizar }: Opciones): void {
  // Refs y no dependencias: el intervalo se monta UNA vez por día visible. Si
  // dependiera de `citas`, cada cambio lo desmontaría y volvería a montarlo, y
  // el reloj no llegaría nunca al minuto en una agenda con movimiento.
  const citasRef = useRef(citas);
  const onActualizarRef = useRef(onActualizar);
  const pausadoRef = useRef(pausado);

  // La sincronización va en un efecto y no en el cuerpo del componente: escribir
  // una ref durante el render rompe la regla de React (y el compilador lo marca
  // como error). Sin lista de dependencias, para que corra tras CADA render y
  // las refs nunca se queden atrás. El intervalo dispara mucho después, así que
  // no hay ventana en la que lea un valor viejo.
  useEffect(() => {
    citasRef.current = citas;
    onActualizarRef.current = onActualizar;
    pausadoRef.current = pausado;
  });

  /** Se saltó un turno por estar ocupado: hay que ponerse al día al quedar libre. */
  const pendiente = useRef(false);
  /**
   * Qué día pidió cada consulta. Si el usuario cambia de día mientras una está
   * en vuelo, la respuesta que llega tarde es de OTRO día y pintarla sería
   * enseñar las citas del martes en el miércoles.
   */
  const generacion = useRef(0);

  const consultar = useCallback(async () => {
    if (pausadoRef.current) {
      pendiente.current = true;
      return;
    }
    const mia = generacion.current;
    try {
      const nuevas = await listAppointments(rangoDelDia(fecha, zona));
      if (mia !== generacion.current) return; // se cambió de día por el camino
      // Y si mientras tanto se ha abierto un modal, esto se guarda para luego:
      // aplicarlo ahora movería la rejilla bajo la mano del usuario.
      if (pausadoRef.current) {
        pendiente.current = true;
        return;
      }
      const cambio = diferencias(citasRef.current, nuevas);
      if (cambio) onActualizarRef.current(cambio.citas, cambio.cambiados);
    } catch {
      // En silencio A PROPÓSITO. Esto corre solo, de fondo: un corte de red de
      // dos segundos no puede vaciarle la agenda a nadie ni pintarle un error
      // que no ha provocado. Lo que ya está en pantalla sigue estando, y el
      // siguiente turno lo intenta otra vez.
      pendiente.current = true;
    }
  }, [fecha, zona]);

  // El reloj, más la pestaña. Se remonta al cambiar de día porque el rango de
  // la consulta cambia con él.
  useEffect(() => {
    generacion.current += 1;
    pendiente.current = false;

    const t = setInterval(() => void consultar(), INTERVALO_MS);

    const alVolver = () => {
      // Volver a la pestaña es el momento en que más falta hace estar al día:
      // se consulta ya, sin esperar al siguiente turno.
      if (document.visibilityState === "visible") void consultar();
    };
    document.addEventListener("visibilitychange", alVolver);

    return () => {
      clearInterval(t);
      document.removeEventListener("visibilitychange", alVolver);
    };
  }, [consultar]);

  // Al quedar libre —el usuario cierra el modal, termina la carga inicial— se
  // recupera el turno que se saltó. Es lo que hace que «no interrumpir» no
  // signifique «quedarse desactualizado».
  useEffect(() => {
    if (!pausado && pendiente.current) {
      pendiente.current = false;
      void consultar();
    }
  }, [pausado, consultar]);
}
