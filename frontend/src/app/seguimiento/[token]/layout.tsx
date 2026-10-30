import type { Metadata } from "next";
import type { ReactNode } from "react";

/**
 * EL ENLACE DE SEGUIMIENTO (0077): no se indexa, y el enlace no viaja en el
 * `Referer` de nada que se abra desde aquí. El backend dice lo mismo en sus
 * cabeceras (`routers/seguimiento.py`).
 */
export const metadata: Metadata = {
  title: "Tus salones · EstetixApp",
  robots: {
    index: false,
    follow: false,
    nocache: true,
    googleBot: { index: false, follow: false },
  },
  referrer: "no-referrer",
};

export default function LayoutSeguimiento({
  children,
}: {
  children: ReactNode;
}) {
  return children;
}
