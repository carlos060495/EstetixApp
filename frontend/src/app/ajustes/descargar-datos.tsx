"use client";

import { useEffect, useRef, useState } from "react";
import { AlertTriangle, Download, FolderDown, Loader2, X } from "lucide-react";
import { ApiError } from "@/lib/api/client";
import { descargarMisDatos, getDatosPreview, nombreDelZip, type DatosPreview } from "@/lib/api/datos";
import { saveBlob } from "@/lib/api/rgpd";
import { CampoSecreto } from "@/components/forms/campo-secreto";
import { SecretoDeMuestra } from "@/components/demo/secreto-de-muestra";

/**
 * LLEVARSE LOS DATOS: el botón y su confirmación.
 *
 * Es un derecho (portabilidad, RGPD), así que está en todos los planes y en
 * el muro de una cuenta suspendida. Pide SIEMPRE la contraseña de inicio de
 * sesión (01-10-2026): mirar las fichas en pantalla se queda dentro,
 * un fichero con todas sale y no vuelve. No el PIN de gestión, que puede no
 * estar puesto. La comprueba el backend contra el servicio de autenticación;
 * aquí solo vive en el campo mientras se escribe.
 *
 * Si la vista previa no responde (una empleada recibe 403), no se pinta nada:
 * un botón que no puede funcionar es peor que ninguno.
 */
export function DescargarDatos({ enElMuro = false }: { enElMuro?: boolean }) {
  const [preview, setPreview] = useState<DatosPreview | null>(null);
  const [abierto, setAbierto] = useState(false);
  const [hecho, setHecho] = useState(false);

  useEffect(() => {
    let vivo = true;
    getDatosPreview()
      .then((p) => vivo && setPreview(p))
      .catch(() => vivo && setPreview(null));
    return () => {
      vivo = false;
    };
  }, []);

  if (!preview) return null;

  const boton = (
    <button
      type="button"
      onClick={() => {
        setHecho(false);
        setAbierto(true);
      }}
      className={
        enElMuro
          ? "flex h-11 w-full items-center justify-center gap-2 rounded-[10px] border border-border px-5 text-[13.5px] font-medium text-text transition-colors hover:bg-card-alt"
          : "flex h-11 w-full items-center justify-center gap-2 rounded-[11px] bg-gold px-5 text-[13.5px] font-semibold text-bg transition-colors hover:bg-gold-hover sm:w-auto"
      }
    >
      <Download size={15} strokeWidth={1.8} />
      Descargar mis datos
    </button>
  );

  const dialogo = abierto && (
    <ConfirmarDescarga
      onCerrar={() => setAbierto(false)}
      onHecho={() => {
        setAbierto(false);
        setHecho(true);
      }}
    />
  );

  const listo = hecho && (
    <p role="status" className="m-0 text-[12.5px] leading-[1.5] text-success">
      Descargado. Lo encontrarás en tu carpeta de descargas como {nombreDelZip()}.
    </p>
  );

  if (enElMuro) {
    return (
      <div className="mt-4 flex flex-col gap-2">
        {boton}
        {listo}
        {dialogo}
      </div>
    );
  }

  return (
    <section
      aria-labelledby="mis-datos-titulo"
      className="mb-[22px] flex flex-col gap-4 rounded-2xl border border-border bg-card px-5 py-5 md:px-6"
    >
      <div className="flex items-start gap-3.5">
        <div className="flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-xl bg-card-alt text-gold">
          <FolderDown size={20} strokeWidth={1.7} />
        </div>
        <div className="min-w-0 flex-1">
          <h3 id="mis-datos-titulo" className="m-0 text-[15px] font-semibold leading-[1.25] text-text">
            Tus datos
          </h3>
          <p className="m-0 mt-1 text-[12.5px] leading-[1.55] text-mute">
            Descarga todo lo que tienes en EstetixApp: clientas, citas, servicios, equipo, horarios, fichajes,
            ventas, gastos, stock, quién no quiere recordatorios y conversaciones
            {preview.centros > 1 ? `, de tus ${preview.centros} centros` : ""}. Es un ZIP con una tabla por
            cada cosa que se abre en Excel.
          </p>
        </div>
      </div>
      {boton}
      {listo}
      {dialogo}
    </section>
  );
}

function mensajeDeError(e: unknown): string {
  if (!(e instanceof ApiError)) return "No se pudo preparar la descarga. Inténtalo de nuevo.";
  if (e.codigo === "contrasena_incorrecta") {
    const quedan = Number(e.datos?.intentos_restantes);
    if (quedan === 1) return "La contraseña no es correcta. Te queda 1 intento antes de tener que esperar.";
    if (quedan > 1) return `La contraseña no es correcta. Te quedan ${quedan} intentos antes de tener que esperar.`;
  }
  return e.message;
}

function ConfirmarDescarga({ onCerrar, onHecho }: { onCerrar: () => void; onHecho: () => void }) {
  const [contrasena, setContrasena] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Un cerrojo síncrono: dos toques en el mismo instante leen `enviando` a
  // false los dos. El backend además deja una a la vez por cuenta.
  const enVuelo = useRef(false);

  async function descargar() {
    if (enVuelo.current) return;
    if (!contrasena) {
      setError("Escribe tu contraseña.");
      return;
    }
    enVuelo.current = true;
    setEnviando(true);
    setError(null);
    try {
      const zip = await descargarMisDatos(contrasena);
      setContrasena("");
      saveBlob(zip, nombreDelZip());
      onHecho();
    } catch (e) {
      // Se vacía el campo: la contraseña no se queda escrita en pantalla.
      setContrasena("");
      setError(mensajeDeError(e));
      enVuelo.current = false;
      setEnviando(false);
    }
  }

  return (
    <div
      onClick={enviando ? undefined : onCerrar}
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/70 p-4 backdrop-blur-[2px] md:px-6 md:py-10"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="descarga-titulo"
        onClick={(e) => e.stopPropagation()}
        className="my-auto flex w-full max-w-[440px] flex-col overflow-hidden rounded-2xl border border-border bg-card shadow-[0_24px_70px_rgba(0,0,0,.6)]"
      >
        <div className="flex items-center justify-between border-b border-border-subtle px-[22px] py-5">
          <h3 id="descarga-titulo" className="m-0 text-base font-semibold text-text">
            Descargar tus datos
          </h3>
          <button
            onClick={onCerrar}
            disabled={enviando}
            aria-label="Cerrar"
            className="-mr-2 flex h-11 w-11 items-center justify-center rounded-lg text-mute transition-colors hover:bg-[#1B1B1E] hover:text-text disabled:opacity-50"
          >
            <X size={18} />
          </button>
        </div>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            void descargar();
          }}
          className="flex flex-col"
        >
          <div className="flex flex-col gap-4 px-[22px] py-[22px]">
            <ul className="m-0 flex list-none flex-col gap-2.5 p-0 text-[13.5px] leading-[1.5] text-text">
              <li>Un ZIP con una tabla por cada cosa y un fichero LEEME que explica cada una.</li>
              <li>Lleva datos personales de tus clientas y de tu equipo. Guárdalo en un sitio seguro.</li>
            </ul>

            <div>
              <label htmlFor="descarga-contrasena" className="mb-1.5 block text-xs font-medium text-mute">
                Tu contraseña de inicio de sesión
              </label>
              <CampoSecreto
                id="descarga-contrasena"
                autoComplete="current-password"
                value={contrasena}
                autoFocus
                onChange={(e) => setContrasena(e.target.value)}
                aria-invalid={error ? true : undefined}
                aria-describedby={error ? "descarga-error" : "descarga-ayuda"}
                className="h-[46px] w-full rounded-[11px] border border-border bg-card-alt px-3.5 text-[15px] text-text outline-none focus:border-[var(--accent)]"
              />
              <div className="mt-2">
                <SecretoDeMuestra que="contrasena" />
              </div>
              <p id="descarga-ayuda" className="m-0 mt-1.5 text-[12px] leading-[1.45] text-faint">
                La misma con la que entras en EstetixApp. Te la pedimos porque el fichero sale de la app.
              </p>
            </div>

            {error && (
              <p
                id="descarga-error"
                role="alert"
                className="m-0 flex items-start gap-2 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2.5 text-[12.5px] leading-[1.45] text-danger"
              >
                <AlertTriangle size={14} className="mt-[2px] flex-shrink-0" />
                <span>{error}</span>
              </p>
            )}
          </div>

          <div className="flex flex-col-reverse gap-2.5 border-t border-border-subtle px-[22px] py-4 sm:flex-row sm:justify-end">
            <button
              type="button"
              onClick={onCerrar}
              disabled={enviando}
              className="flex h-11 items-center justify-center rounded-[10px] border border-border px-5 text-[13.5px] font-semibold text-text transition-colors hover:bg-[#1B1B1E] disabled:opacity-50"
            >
              Volver
            </button>
            <button
              type="submit"
              disabled={enviando}
              className="flex h-11 items-center justify-center gap-2 whitespace-nowrap rounded-[10px] bg-gold px-5 text-[13.5px] font-semibold text-bg transition-colors hover:bg-gold-hover disabled:cursor-not-allowed disabled:opacity-60"
            >
              {enviando ? <Loader2 size={15} className="animate-spin" /> : <Download size={15} />}
              {enviando ? "Comprobando…" : "Descargar"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
