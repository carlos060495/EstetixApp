import type { Metadata } from "next";
import Link from "next/link";
import { AvisoBorrador } from "@/components/legal/aviso-borrador";
import { CookieSettingsLink } from "@/components/legal/cookie-settings-link";

export const metadata: Metadata = {
  title: "Política de cookies · EstetixApp",
  description: "Qué cookies utiliza EstetixApp, con qué finalidad y cómo gestionarlas.",
};

const ACTUALIZADO = "5 de octubre de 2026";

type FilaCookie = {
  categoria: string;
  cookies: string;
  finalidad: string;
  duracion: string;
  titular: string;
};

const FILAS: FilaCookie[] = [
  {
    categoria: "Necesarias",
    cookies: "sb-<ref>-auth-token (puede aparecer fragmentada en .0, .1)",
    finalidad: "Mantener iniciada la sesión del usuario en la aplicación (Supabase Auth).",
    duracion: "Sesión / hasta cierre de sesión",
    titular: "EstetixApp (primera parte)",
  },
  {
    categoria: "Necesarias",
    cookies: "estetix_cookie_consent",
    finalidad: "Recordar tu elección sobre las cookies para no volver a preguntarte.",
    duracion: "12 meses",
    titular: "EstetixApp (primera parte)",
  },
  // Las cuatro que pone el backend (`core/cookies_de_sesion.py`). Si
  // cambia su nombre o su duración, cambia esto.
  {
    categoria: "Necesarias",
    cookies: "__Host-ref-sesion",
    finalidad:
      "Mantener iniciada tu sesión en el portal de referidos, si recomiendas EstetixApp. Solo la lee nuestro servidor.",
    duracion: "12 horas",
    titular: "EstetixApp (primera parte)",
  },
  {
    categoria: "Necesarias",
    cookies: "__Host-sa-access-token",
    finalidad:
      "Sesión del panel de administración interno de EstetixApp. Solo la lee nuestro servidor.",
    duracion: "12 horas",
    titular: "EstetixApp (primera parte)",
  },
  {
    categoria: "Necesarias",
    cookies: "__Host-sa-impersonation",
    finalidad:
      "Cuando nuestro equipo de soporte entra a ayudarte con tu permiso, mantiene esa visita abierta. Solo la tiene el navegador de quien da soporte.",
    duracion: "30 minutos",
    titular: "EstetixApp (primera parte)",
  },
  {
    categoria: "Necesarias",
    cookies: "__Host-demo",
    finalidad:
      "Si abres un salón de muestra desde la portada, recuerda cuál es el tuyo para que vuelvas al mismo. Solo la lee nuestro servidor.",
    duracion: "24 horas (lo que vive el salón de muestra)",
    titular: "EstetixApp (primera parte)",
  },
  {
    categoria: "Analíticas",
    cookies: "Ninguna actualmente",
    finalidad: "Medir de forma agregada el uso de la aplicación para mejorarla.",
    duracion: "—",
    titular: "—",
  },
  {
    // 0070. Si cambia lo que hace `estetix_ref` (lib/referido.ts), cambia esto.
    categoria: "Marketing",
    cookies: "estetix_ref",
    finalidad:
      "Si llegas por el enlace de alguien que nos recomienda, guarda su código para que, si te das de alta más adelante, sepamos quién te trajo y podamos pagarle su comisión. Solo se guarda si aceptas las cookies de marketing. Si no, el código te acompaña en la dirección de la página mientras navegas en esa visita, sin guardarse en tu dispositivo.",
    duracion: "30 días",
    titular: "EstetixApp (primera parte)",
  },
];

export default function PoliticaCookiesPage() {
  return (
    <article>
      <AvisoBorrador />

      <h1 className="mb-2 font-display text-[36px] font-semibold leading-[1.08] text-text sm:text-[42px]">
        Política de cookies
      </h1>
      <p className="mb-10 text-[13px] text-faint">Última actualización: {ACTUALIZADO}</p>

      <div className="flex flex-col gap-8 text-[14.5px] leading-[1.7] text-mute">
        <section>
          <h2 className="mb-3 text-[19px] font-semibold text-text">1. Qué son las cookies</h2>
          <p>
            Una cookie es un pequeño archivo de texto que un sitio web guarda en tu
            navegador. Sirve, entre otras cosas, para recordar que has iniciado sesión o
            qué preferencias has elegido. Algunas son imprescindibles para que el servicio
            funcione; otras solo se instalan si tú lo autorizas.
          </p>
        </section>

        <section>
          <h2 className="mb-3 text-[19px] font-semibold text-text">2. Responsable</h2>
          <p>
            Responsable del tratamiento:{" "}
            <strong className="text-text">[PENDIENTE: identidad del responsable]</strong>, con
            domicilio en <strong className="text-text">[PENDIENTE: dirección]</strong> y NIF{" "}
            <strong className="text-text">[PENDIENTE: NIF]</strong>. Contacto en materia de
            protección de datos: <strong className="text-text">[PENDIENTE: contacto DPD]</strong>.
          </p>
        </section>

        <section>
          <h2 className="mb-3 text-[19px] font-semibold text-text">3. Cookies que utilizamos</h2>
          <p className="mb-4">
            Las cookies necesarias se instalan siempre, porque sin ellas no podríamos
            prestarte el servicio. Las analíticas y las de marketing solo se instalan si
            las aceptas. <strong className="text-text">Ahora mismo no usamos ninguna analítica</strong>, y
            de marketing solo una: la que recuerda quién te recomendó EstetixApp.
          </p>
          <div className="overflow-x-auto rounded-xl border border-border-subtle">
            <table className="w-full min-w-[720px] border-collapse text-left text-[13px]">
              <thead>
                <tr className="border-b border-border-subtle bg-side">
                  <th className="px-4 py-3 font-semibold text-text">Categoría</th>
                  <th className="px-4 py-3 font-semibold text-text">Cookies</th>
                  <th className="px-4 py-3 font-semibold text-text">Finalidad</th>
                  <th className="px-4 py-3 font-semibold text-text">Duración</th>
                  <th className="px-4 py-3 font-semibold text-text">Titular</th>
                </tr>
              </thead>
              <tbody>
                {FILAS.map((fila) => (
                  <tr
                    key={`${fila.categoria}-${fila.cookies}`}
                    className="border-b border-border-subtle last:border-b-0"
                  >
                    <td className="px-4 py-3 align-top font-medium text-gold">{fila.categoria}</td>
                    <td className="px-4 py-3 align-top font-mono text-[12px] text-[#cfc7b6]">
                      {fila.cookies}
                    </td>
                    <td className="px-4 py-3 align-top">{fila.finalidad}</td>
                    <td className="whitespace-nowrap px-4 py-3 align-top">{fila.duracion}</td>
                    <td className="px-4 py-3 align-top">{fila.titular}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section>
          <h2 className="mb-3 text-[19px] font-semibold text-text">4. Cookies de terceros</h2>
          <p>
            El pago de las suscripciones se procesa mediante{" "}
            <strong className="text-text">Stripe</strong>. Al pulsar el botón de pago te
            redirigimos a un dominio de Stripe (
            <span className="font-mono text-[13px]">checkout.stripe.com</span>), donde Stripe
            puede instalar sus propias cookies conforme a su política. Esas cookies{" "}
            <strong className="text-text">no se instalan en estetixapp.es</strong> y no están
            bajo nuestro control.
          </p>
        </section>

        <section>
          <h2 className="mb-3 text-[19px] font-semibold text-text">5. Base legal</h2>
          <p>
            Las cookies necesarias se amparan en el interés legítimo de prestar un servicio
            expresamente solicitado por ti (art. 22.2 LSSI). Las cookies analíticas y de
            marketing se instalan únicamente con tu consentimiento previo, libre, específico
            e informado (art. 6.1.a RGPD), que puedes retirar en cualquier momento.
          </p>
        </section>

        <section>
          <h2 className="mb-3 text-[19px] font-semibold text-text">
            6. Cómo retirar o cambiar tu consentimiento
          </h2>
          <p className="mb-4">
            Puedes cambiar tu elección cuando quieras, con la misma facilidad con la que la
            diste. Encontrarás el enlace <em>Configuración de cookies</em> en el pie de la web
            y, si has iniciado sesión, al final del menú lateral de la aplicación.
          </p>
          <CookieSettingsLink className="inline-flex h-11 items-center justify-center rounded-xl border border-gold px-5 text-[14px] font-semibold text-gold transition-colors hover:bg-gold-tenue" />
        </section>

        <section>
          <h2 className="mb-3 text-[19px] font-semibold text-text">
            7. Cómo borrar las cookies desde tu navegador
          </h2>
          <p>
            Además, puedes eliminar o bloquear las cookies desde la configuración de tu
            navegador. Ten en cuenta que si bloqueas las necesarias, la aplicación puede
            dejar de funcionar correctamente. Instrucciones oficiales:{" "}
            <a
              href="https://support.google.com/chrome/answer/95647"
              target="_blank"
              rel="noopener noreferrer"
              className="text-gold underline underline-offset-2 hover:text-gold-hover"
            >
              Chrome
            </a>
            ,{" "}
            <a
              href="https://support.mozilla.org/es/kb/Borrar%20cookies"
              target="_blank"
              rel="noopener noreferrer"
              className="text-gold underline underline-offset-2 hover:text-gold-hover"
            >
              Firefox
            </a>
            ,{" "}
            <a
              href="https://support.apple.com/es-es/guide/safari/sfri11471/mac"
              target="_blank"
              rel="noopener noreferrer"
              className="text-gold underline underline-offset-2 hover:text-gold-hover"
            >
              Safari
            </a>{" "}
            y{" "}
            <a
              href="https://support.microsoft.com/es-es/microsoft-edge/eliminar-las-cookies-en-microsoft-edge-63947406-40ac-c3b8-57b9-2a946a29ae09"
              target="_blank"
              rel="noopener noreferrer"
              className="text-gold underline underline-offset-2 hover:text-gold-hover"
            >
              Edge
            </a>
            .
          </p>
        </section>

        <section>
          <h2 className="mb-3 text-[19px] font-semibold text-text">8. Cambios en esta política</h2>
          <p>
            Si incorporamos nuevas cookies o cambiamos su finalidad, actualizaremos esta
            política y te volveremos a pedir el consentimiento. Consulta siempre la fecha de
            última actualización que figura arriba.
          </p>
        </section>

        <section>
          <h2 className="mb-3 text-[19px] font-semibold text-text">9. Más información</h2>
          <p>
            Para saber cómo tratamos tus datos personales, consulta nuestra{" "}
            <Link
              href="/privacidad"
              className="text-gold underline underline-offset-2 hover:text-gold-hover"
            >
              Política de privacidad
            </Link>
            .
          </p>
        </section>
      </div>
    </article>
  );
}
