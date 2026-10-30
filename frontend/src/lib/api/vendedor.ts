import { apiFetch } from "./client";

/** El salón asigna el código de su vendedor después del alta (0078). */

export type EstadoVendedor = {
  /** Sale el campo: sin atribución y dentro de los 30 días del alta. */
  puede: boolean;
  /** Hasta cuándo (solo si puede). */
  hasta: string | null;
  /** «Se le empieza a pagar desde el siguiente recibo; el que ya se cobró no cuenta.» */
  frase: string;
};

export type Vendedor = {
  /** Quien cobra: con el código de un trabajador, su agencia. Vuelve en la confirmación. */
  referido_id: string;
  nombre: string;
  tipo: "individual" | "agencia" | string;
  trabajador_id: string | null;
  trabajador_nombre: string | null;
  frase: string;
};

export type Asignado = {
  nombre: string;
  trabajador_nombre: string | null;
  devenga_desde: string;
  frase: string;
};

export function getEstadoVendedor(): Promise<EstadoVendedor> {
  return apiFetch<EstadoVendedor>("/billing/vendedor");
}

/** De quién es el código, para enseñar su nombre ANTES de confirmar. */
export function comprobarVendedor(codigo: string): Promise<Vendedor> {
  return apiFetch<Vendedor>("/billing/vendedor/comprobar", { method: "POST", body: { codigo } });
}

/** Confirmar, con la persona que se le enseñó. Una sola vez. */
export function asignarVendedor(codigo: string, v: Vendedor): Promise<Asignado> {
  return apiFetch<Asignado>("/billing/vendedor", {
    method: "POST",
    body: { codigo, referido_id: v.referido_id, trabajador_id: v.trabajador_id, confirmado: true },
  });
}
