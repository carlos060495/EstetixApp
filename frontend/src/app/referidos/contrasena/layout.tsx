import type { Metadata } from "next";
import type { ReactNode } from "react";

// El token de un solo uso viaja en la dirección de esta página: que no salga en
// la cabecera Referer hacia ningún sitio. La página además lo quita de la barra
// en cuanto lo lee.
export const metadata: Metadata = { referrer: "no-referrer" };

export default function Layout({ children }: { children: ReactNode }) {
  return children;
}
