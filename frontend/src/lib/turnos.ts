import { useEffect, useState } from "react";

/**
 * UNA RESPUESTA QUE YA NO CORRESPONDE A LO QUE LA PERSONA ESTÁ MIRANDO NO PUEDE
 * ESCRIBIR EN LA PANTALLA.
 *
 * Es todo lo que hace este módulo, y existe porque se midieron tres veces la
 * misma avería con tres caras:
 *
 *   · El widget público enseñaba «No quedan huecos» con el día elegido marcado:
 *     la clienta tocaba un día y luego otro, y la disponibilidad del primero
 *     llegaba tarde y pisaba la del segundo. Se iba pensando que el salón
 *     estaba lleno, sin error, sin aviso y sin forma de que el salón se
 *     enterase de la reserva que no llegó.
 *   · El primer cambio de centro tras entrar no ocurría en 6 de 8 intentos: una
 *     lectura de la carga inicial seguía en vuelo, el clic guardaba el centro
 *     nuevo y la respuesta vieja llegaba después y lo reescribía.
 *
 * EL MECANISMO: TURNOS. Cada lectura abre un turno, y abrir uno deja caducados
 * todos los anteriores. Al volver, antes de escribir nada, la lectura pregunta
 * si su turno sigue vigente; si no, se calla. Una acción de la persona que deja
 * sin sentido lo que está en vuelo —cambiar de centro— lo caduca sin abrir otro.
 *
 * POR QUÉ TURNOS Y NO CANCELAR LA PETICIÓN. Cancelar exigiría pasar una señal
 * por `apiFetch` y por cada función de `lib/api`, y aun así no bastaría: una
 * respuesta que ya ha llegado cuando se cancela sigue ejecutando lo que viene
 * después del `await`. La pregunta «¿sigue siendo mío?» hace falta igual; con
 * turnos es la ÚNICA pieza, y no toca la capa de API.
 *
 * LA FORMA DE USARLO, siempre la misma:
 *
 *     const turno = turnos.abrir();
 *     const datos = await leerAlgo();
 *     if (!turno.vigente()) return;   // ← antes de CUALQUIER set, y de
 *     setDatos(datos);                //   cualquier otra escritura
 *
 * Y NADA MÁS. Sin limpieza en el efecto: la lectura siguiente ya caduca la
 * anterior al abrir la suya, y desmontar el componente lo caduca todo. Cada
 * pieza de más sería una que ningún test puede distinguir de no tenerla.
 */
export type Turno = {
  /** ¿Sigue siendo esta la última lectura, y no se ha caducado nada después? */
  vigente(): boolean;
};

export type Turnos = {
  /** Abre un turno nuevo y deja caducados todos los anteriores. */
  abrir(): Turno;
  /** Caduca todo lo que haya en vuelo, sin abrir uno nuevo. */
  caducar(): void;
};

/** El núcleo, sin React: se prueba en Node y no depende de ningún render. */
export function crearTurnos(): Turnos {
  let actual = 0;
  return {
    abrir() {
      const mio = ++actual;
      return { vigente: () => mio === actual };
    },
    caducar() {
      actual += 1;
    },
  };
}

/**
 * Un juego de turnos por componente, estable entre renders, que se caduca solo
 * al desmontar: lo que llegue después ya no tiene pantalla a la que escribir.
 *
 * `useState` con inicializador y no un `ref`: el objeto se crea una vez y se
 * lee en el render sin tocar `ref.current`, que es lo que las reglas de hooks
 * de React 19 no dejan.
 */
export function useTurnos(): Turnos {
  const [turnos] = useState(crearTurnos);
  useEffect(() => () => turnos.caducar(), [turnos]);
  return turnos;
}
