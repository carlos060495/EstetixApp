import type { Metadata } from "next";
import Link from "next/link";
import { AvisoBorrador } from "@/components/legal/aviso-borrador";
import { LO_QUE_SE_BORRA } from "@/lib/lo-que-se-borra";

export const metadata: Metadata = {
  title: "Política de privacidad · EstetixApp",
  description: "Cómo trata EstetixApp los datos personales de sus usuarios y clientes.",
};

const ACTUALIZADO = "31 de julio de 2026";

export default function PoliticaPrivacidadPage() {
  return (
    <article>
      <AvisoBorrador />

      <h1 className="mb-2 font-display text-[36px] font-semibold leading-[1.08] text-text sm:text-[42px]">
        Política de privacidad
      </h1>
      <p className="mb-10 text-[13px] text-faint">Última actualización: {ACTUALIZADO}</p>

      <div className="flex flex-col gap-8 text-[14.5px] leading-[1.7] text-mute">
        <section>
          <h2 className="mb-3 text-[19px] font-semibold text-text">
            1. Responsable del tratamiento
          </h2>
          <p>
            <strong className="text-text">[PENDIENTE: identidad del responsable]</strong>, con
            domicilio en <strong className="text-text">[PENDIENTE: dirección]</strong>, NIF{" "}
            <strong className="text-text">[PENDIENTE: NIF]</strong>. Contacto:{" "}
            <strong className="text-text">[PENDIENTE: email de contacto]</strong>. Delegado de
            Protección de Datos: <strong className="text-text">[PENDIENTE: contacto DPD]</strong>.
          </p>
        </section>

        <section>
          <h2 className="mb-3 text-[19px] font-semibold text-text">2. Qué datos tratamos</h2>
          <p className="mb-3">
            <strong className="text-text">Del titular de la cuenta:</strong> nombre, correo
            electrónico, teléfono y datos de facturación del salón.
          </p>
          <p className="mb-3">
            <strong className="text-text">De los clientes del salón:</strong> los datos que el
            propio salón introduce en la aplicación (nombre, contacto, historial de citas y
            notas de servicio). En este tratamiento EstetixApp actúa como{" "}
            <strong className="text-text">encargado del tratamiento</strong>, y el salón como
            responsable.{" "}
            <strong className="text-text">
              [PENDIENTE: referencia al contrato de encargo de tratamiento]
            </strong>
          </p>
          <p>
            <strong className="text-text">De uso:</strong> datos técnicos mínimos necesarios
            para prestar el servicio y mantener su seguridad.
          </p>
        </section>

        <section>
          <h2 className="mb-3 text-[19px] font-semibold text-text">3. Finalidad y base legal</h2>
          <p>
            Tratamos estos datos para prestar el servicio contratado (art. 6.1.b RGPD),
            cumplir nuestras obligaciones legales y fiscales (art. 6.1.c RGPD) y, cuando nos
            lo autorizas expresamente, enviarte comunicaciones o medir el uso de la
            aplicación (art. 6.1.a RGPD).{" "}
            <strong className="text-text">[PENDIENTE: revisar y completar por el gestor]</strong>
          </p>
        </section>

        <section>
          <h2 className="mb-3 text-[19px] font-semibold text-text">4. Conservación</h2>
          <p>
            Conservamos los datos mientras la cuenta esté activa. Cuando la suscripción
            termina: {LO_QUE_SE_BORRA}
          </p>
        </section>

        <section>
          <h2 className="mb-3 text-[19px] font-semibold text-text">5. Destinatarios</h2>
          <p>
            Compartimos datos únicamente con los proveedores necesarios para prestar el
            servicio, todos ellos sujetos a contrato de encargo de tratamiento:{" "}
            <strong className="text-text">
              [PENDIENTE: listado de encargados —alojamiento, base de datos, pasarela de pago,
              mensajería—]
            </strong>
            . Los datos se alojan en la Unión Europea.
          </p>
        </section>

        <section>
          <h2 className="mb-3 text-[19px] font-semibold text-text">6. Tus derechos</h2>
          <p>
            Puedes ejercer los derechos de acceso, rectificación, supresión, oposición,
            limitación y portabilidad escribiendo a{" "}
            <strong className="text-text">[PENDIENTE: email de ejercicio de derechos]</strong>. Si
            consideras que no hemos atendido tu solicitud correctamente, puedes reclamar ante
            la Agencia Española de Protección de Datos (
            <a
              href="https://www.aepd.es"
              target="_blank"
              rel="noopener noreferrer"
              className="text-gold underline underline-offset-2 hover:text-gold-hover"
            >
              aepd.es
            </a>
            ).
          </p>
        </section>

        <section>
          <h2 className="mb-3 text-[19px] font-semibold text-text">7. Cookies</h2>
          <p>
            El uso de cookies se detalla en nuestra{" "}
            <Link
              href="/cookies"
              className="text-gold underline underline-offset-2 hover:text-gold-hover"
            >
              Política de cookies
            </Link>
            .
          </p>
        </section>
      </div>
    </article>
  );
}
