"use client";

import { useEffect, useState } from "react";
import { FlaskConical } from "lucide-react";
import { getMe } from "@/lib/api/auth";

/** Lo que una demo enseña para poder probar lo que pide un secreto (0081). */
export type DemoDeLaSesion = {
  contrasena: string | null;
  pines: { nombre: string; pin: string }[];
} | null;

type Que =
  /** La descarga de datos: la contraseña de muestra. */
  | { que: "contrasena" }
  /** El quiosco: el PIN de fichaje de esta empleada, si es una de las sembradas. */
  | { que: "pin_de_empleada"; nombre: string }
  /** El PIN de gestión: lo pone quien prueba; olvidado, se quita. */
  | { que: "pin_de_gestion" }
  /** Lo que no se puede desde una demo (darse de baja). */
  | { que: "no_se_puede"; accion: string };

let cargando: Promise<DemoDeLaSesion> | null = null;

function demoDeLaSesion(): Promise<DemoDeLaSesion> {
  // Sin respuesta no se pinta nada: la pista es una ayuda, nunca un error.
  cargando ??= (async () => {
    try {
      const me = await getMe();
      return me.demo ? { contrasena: me.demo.contrasena ?? null, pines: me.demo.pines ?? [] } : null;
    } catch {
      return null;
    }
  })();
  return cargando;
}

/**
 * DENTRO DE UN SALÓN DE MUESTRA, lo que pide una contraseña o un PIN dice cómo
 * probarlo (0081): o el secreto sembrado, o que eso no se puede desde una demo.
 * Fuera de una demo no pinta nada.
 *
 * `demo` lo pasa quien ya tiene la sesión a mano; sin él (una pantalla fuera
 * de la aplicación, como el quiosco o la cuenta suspendida) lo pregunta una
 * vez al backend. `tests/test_secretos_en_la_demo.py` exige este componente en
 * toda pantalla que llama a una acción con secreto.
 */
export function SecretoDeMuestra(props: Que & { demo?: DemoDeLaSesion }) {
  const [cargada, setCargada] = useState<DemoDeLaSesion>(null);
  const dada = props.demo !== undefined;

  useEffect(() => {
    if (dada) return;
    let vivo = true;
    void demoDeLaSesion().then((d) => vivo && setCargada(d));
    return () => {
      vivo = false;
    };
  }, [dada]);

  const demo = dada ? props.demo : cargada;
  if (!demo) return null;

  let texto: React.ReactNode;
  if (props.que === "contrasena") {
    texto = demo.contrasena ? (
      <>
        Salón de muestra: la contraseña es{" "}
        <strong className="select-all font-mono text-text">{demo.contrasena}</strong>
      </>
    ) : (
      "Esta descarga no se puede desde este salón de muestra: es de antes de que la contraseña se enseñara aquí."
    );
  } else if (props.que === "pin_de_empleada") {
    const pin = demo.pines.find((p) => p.nombre === props.nombre)?.pin;
    texto = pin ? (
      <>
        Salón de muestra: el PIN de {props.nombre} es <strong className="font-mono text-text">{pin}</strong>
      </>
    ) : (
      "Salón de muestra: el PIN de esta ficha es el que le pusiste en Equipo."
    );
  } else if (props.que === "pin_de_gestion") {
    texto = "Salón de muestra: el PIN de gestión es el que pongas tú. Si se te olvida, «Lo he olvidado» lo quita.";
  } else {
    texto = `${props.accion} no se puede desde un salón de muestra.`;
  }

  return (
    <p
      role="note"
      className="m-0 flex items-start gap-2 rounded-lg border border-gold/40 bg-gold-tenue px-3 py-2 text-left text-[12.5px] leading-[1.45] text-mute"
    >
      <FlaskConical size={14} className="mt-[2px] flex-shrink-0 text-gold" aria-hidden />
      <span>{texto}</span>
    </p>
  );
}
