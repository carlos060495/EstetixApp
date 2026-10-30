import type { Metadata } from "next";
import { Cormorant_Garamond, Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { CookieConsentProvider } from "@/components/legal/cookie-consent-provider";
import { CortinaGestion } from "@/components/cortina-gestion/cortina-gestion";
import { ArrastraElReferido } from "@/components/referido/arrastra-el-referido";

const inter = Inter({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-sans",
});
const cormorant = Cormorant_Garamond({
  subsets: ["latin"],
  weight: ["500", "600", "700"],
  variable: "--font-display",
});
const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-mono",
});

export const metadata: Metadata = {
  title: "EstetixApp",
  description: "Gestión integral para barberías, peluquerías y centros de estética",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="es">
      <body
        className={`${inter.variable} ${cormorant.variable} ${jetbrainsMono.variable} font-sans antialiased`}
      >
        <CookieConsentProvider>
          {/* El código del referido (0070): lo arrastra por la dirección y,
              solo con consentimiento de marketing, lo guarda. No pinta nada. */}
          <ArrastraElReferido />
          {/* El PIN de gestión: una CORTINA sobre todo lo que no es Agenda ni
              Fichaje, no un permiso. Va aquí para bloquear la RUTA —venga de
              donde venga— antes de que la página se monte y pida sus datos. */}
          <CortinaGestion>{children}</CortinaGestion>
        </CookieConsentProvider>
      </body>
    </html>
  );
}
