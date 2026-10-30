"use client";

import { Suspense, useMemo, useState, useSyncExternalStore, type FormEvent } from "react";
import Image from "next/image";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { CampoContrasena } from "@/components/forms/campo-contrasena";
import type { SalonPlan } from "@/lib/api/salon";
import { useMoneda } from "@/lib/usar-moneda";
import { PRECIOS } from "@/lib/pricing";
import { AvisoCamposObligatorios, CampoLabel } from "@/components/forms/campos-obligatorios";
import { PASSWORD_ERROR_TEXT, PASSWORD_RULES_TEXT, isValidPassword } from "@/lib/password";
import { LARGO_MAXIMO, codigoParaElAlta } from "@/lib/referido";
import { comprobarCodigo } from "@/lib/api/referidos";
import { fetchConPlazo } from "@/lib/api/plazo";
import { mensajeSinDetalle } from "@/lib/api/mensaje-sin-detalle";

type SignupBody = {
  salon_nombre: string;
  /** EL PRIMER CENTRO NO ES UNA EXCEPCIÓN. Se pedían solo al abrir el segundo,
   *  y el primero es el que se crea con calma — o sea el que más fácil se
   *  rellena. Sin ellos su página pública nace sin saber decirle a una clienta
   *  ni adónde ir ni por dónde llamar. */
  direccion: string;
  telefono: string;
  admin_nombre: string;
  email: string;
  password: string;
  plan: SalonPlan;
  /** La entidad que factura es la CUENTA, no el centro: se guarda en
   *  `accounts.cif`. Obligatorio: sin él no se puede emitir su factura. */
  cif: string;
  /** El código de quien trajo este salón (0070): el del enlace —de la cookie si
   *  hay una (gana el primero) o de la dirección, ver `lib/referido.ts`—, o el
   *  que escriba a mano (0077). Tal como esté en el campo: lo compara la base. */
  codigo_referido?: string;
};

const PLAN_ORDER: SalonPlan[] = ["basico", "pro", "elite"];

const PLAN_LABEL: Record<SalonPlan, string> = {
  basico: "Básico",
  pro: "Pro",
  elite: "Elite",
};

const API_URL = process.env.NEXT_PUBLIC_API_URL;

export default function SignupPage() {
  return (
    <Suspense fallback={null}>
      <SignupForm />
    </Suspense>
  );
}

const sinSuscripcion = () => () => {};
const leerCookies = () => document.cookie;
const sinCookies = () => null;

function parsePlan(raw: string | null): SalonPlan {
  return raw === "pro" || raw === "elite" || raw === "basico" ? raw : "basico";
}

function formatEuro(value: number): string {
  return value.toLocaleString("es-ES", { style: "currency", currency: "EUR" });
}

function readErrorMessage(data: unknown, status: number): string {
  if (data && typeof data === "object" && "detail" in data) {
    const detail = (data as { detail?: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return mensajeSinDetalle(status, "completar el registro");
}

function SignupForm() {
  const searchParams = useSearchParams();
  // EL PARÁMETRO SOLO PRESELECCIONA. Antes fijaba el plan y no había forma de
  // cambiarlo sin volver atrás: quien llegaba sin `?plan=` se quedaba en Básico
  // sin enterarse de que había otra cosa, y quien se equivocaba al pulsar
  // perdía el formulario.
  const [plan, setPlan] = useState<SalonPlan>(() => parsePlan(searchParams.get("plan")));
  const [salonNombre, setSalonNombre] = useState("");
  const [direccion, setDireccion] = useState("");
  const [telefono, setTelefono] = useState("");
  const [adminNombre, setAdminNombre] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [cif, setCif] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  // EL CÓDIGO DE QUIEN LO TRAJO (0077). Un comercial que entra en persona no
  // manda ningún enlace: dice su código. Si se llegó por un enlace, el campo
  // aparece relleno, a la vista y editable. Se comprueba antes de enviar y se
  // avisa si no existe; si aun así se envía, EL ALTA SIGUE, sin código: un
  // alta perdida cuesta más que una atribución perdida.
  // Las cookies, solo en el navegador (en el servidor no hay): sin efecto que
  // copie nada a un estado. Lo escrito a mano gana a lo del enlace.
  const cookies = useSyncExternalStore(sinSuscripcion, leerCookies, sinCookies);
  const delVisitante = cookies === null ? null : codigoParaElAlta(cookies, searchParams);
  const [escrito, setEscrito] = useState<string | null>(null);
  const codigo = escrito ?? delVisitante ?? "";
  const delEnlace = escrito === null && delVisitante !== null;
  // null: sin comprobar (o no se pudo). El código comprobado va al lado.
  const [codigoVale, setCodigoVale] = useState<{ codigo: string; vale: boolean | null } | null>(null);
  const [avisadoSinCodigo, setAvisadoSinCodigo] = useState(false);

  async function comprobar(valor: string): Promise<boolean | null> {
    if (!valor.trim()) return null;
    if (codigoVale && codigoVale.codigo === valor) return codigoVale.vale;
    const vale = await comprobarCodigo(valor);
    setCodigoVale({ codigo: valor, vale });
    return vale;
  }

  const [moneda] = useMoneda();
  const price = useMemo(() => PRECIOS[moneda][plan], [moneda, plan]);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);

    if (!API_URL) {
      setError("Falta NEXT_PUBLIC_API_URL en la configuración del frontend.");
      return;
    }
    if (!salonNombre.trim() || !adminNombre.trim()) {
      setError("Rellena el nombre del salón y el nombre del administrador.");
      return;
    }
    if (!direccion.trim() || !telefono.trim()) {
      setError("Rellena la dirección y el teléfono del salón.");
      return;
    }
    // SOLO QUE HAYA ALGO ESCRITO. Ninguna regla de formato de ningún país: el
    // número lo verificará Stripe contra VIES cuando se facture.
    if (!cif.trim()) {
      setError("Escribe tu identificación fiscal: la necesitamos para emitir tus facturas.");
      return;
    }
    if (!isValidPassword(password)) {
      setError(PASSWORD_ERROR_TEXT);
      return;
    }
    // Un código que no existe se avisa UNA vez; la segunda, se crea igual.
    if (codigo.trim() && !avisadoSinCodigo) {
      const vale = await comprobar(codigo);
      if (vale === false) {
        setAvisadoSinCodigo(true);
        return;
      }
    }
    const payload: SignupBody = {
      salon_nombre: salonNombre.trim(),
      direccion: direccion.trim(),
      telefono: telefono.trim(),
      admin_nombre: adminNombre.trim(),
      email: email.trim().toLowerCase(),
      password,
      plan,
      cif: cif.trim(),
    };
    // QUIÉN LO TRAJO. Lo mira la base al crear la cuenta; si no vale, el alta
    // sigue igual y queda apuntado.
    if (codigo.trim()) payload.codigo_referido = codigo;

    setLoading(true);
    try {
      const response = await fetchConPlazo(`${API_URL.replace(/\/$/, "")}/auth/signup`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (!response.ok) {
        let data: unknown = null;
        try {
          data = await response.json();
        } catch {
          data = null;
        }
        setError(readErrorMessage(data, response.status));
        return;
      }

      setDone(true);
    } catch {
      setError("No se ha podido conectar con el servidor. Reintenta en unos segundos.");
    } finally {
      setLoading(false);
    }
  }

  if (done) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-bg px-6 py-12">
        <div className="w-full max-w-[520px] rounded-3xl border border-border bg-side px-7 py-9 text-center shadow-[0_24px_70px_rgba(0,0,0,.45)]">
          <Image src="/brand/estetix_logo.png" alt="ESTETIX APP" width={192} height={30} className="mx-auto mb-7" />
          <span className="inline-flex rounded-full border border-[#2e4a3a] bg-[#102218] px-3 py-1 text-[11px] font-semibold uppercase tracking-[.12em] text-[#9BE7B8]">
            Cuenta creada
          </span>
          <h1 className="mt-4 font-display text-[32px] font-semibold leading-tight text-text">Revisa tu email para confirmar</h1>
          <p className="mt-3 text-[14px] leading-relaxed text-mute">
            Te enviamos un enlace de verificación. Cuando confirmes el correo, entra con tu cuenta y arrancaremos tu prueba con tarjeta en Stripe.
          </p>
          <div className="mt-7 flex flex-col gap-2.5 sm:flex-row sm:justify-center">
            <Link
              href="/signup/checkout"
              className="flex h-11 items-center justify-center rounded-xl bg-gold px-5 text-[14px] font-semibold text-bg transition-colors hover:bg-gold-hover"
            >
              Ya confirmé mi email
            </Link>
            <Link
              href="/login?redirectTo=%2Fsignup%2Fcheckout"
              className="flex h-11 items-center justify-center rounded-xl border border-border bg-card-alt px-5 text-[14px] font-semibold text-text transition-colors hover:bg-[#1B1B1E]"
            >
              Iniciar sesión
            </Link>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen bg-bg">
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="px-7 py-7 sm:px-[8%]">
          <Image
            src="/brand/estetix_logo.png"
            alt="ESTETIX APP"
            width={168}
            height={26}
            style={{ height: 26, width: "auto" }}
          />
        </div>

        <div className="flex flex-1 items-center justify-center px-6 pb-[60px] pt-5">
          <form onSubmit={handleSubmit} className="w-full max-w-[430px]">
            <h1 className="m-0 mb-2 font-display text-[38px] font-semibold leading-tight text-text">
              Crea tu cuenta
            </h1>
            <p className="m-0 mb-7 text-sm text-mute">Empieza ahora y activa tu prueba gratis.</p>

            {/* TRES BOTONES EN VEZ DE UNA ETIQUETA. Ocupa lo mismo que la
                tarjeta que había —un poco menos, de hecho— y deja cambiar de
                plan sin salir de la pantalla. Mismo patrón que el muro de
                activación, para que quien pase por los dos reconozca el sitio. */}
            <fieldset className="mb-5 border-0 p-0">
              <legend className="mb-2 block text-[12.5px] font-semibold text-mute">
                Elige tu plan
              </legend>
              <div className="grid grid-cols-3 gap-2">
                {PLAN_ORDER.map((opcion) => {
                  const activo = opcion === plan;
                  const importe = PRECIOS[moneda][opcion];
                  return (
                    <button
                      key={opcion}
                      type="button"
                      aria-pressed={activo}
                      onClick={() => setPlan(opcion)}
                      className={`min-h-[56px] rounded-xl border px-2 py-2.5 text-left transition-colors ${
                        activo
                          ? "border-gold bg-gold-tenue"
                          : "border-border bg-side hover:border-gold/35"
                      }`}
                    >
                      <span className="block text-[13px] font-semibold text-text">
                        {PLAN_LABEL[opcion]}
                      </span>
                      <span className="mt-0.5 block text-[12px] text-mute">
                        {moneda === "usd" ? `$${importe}` : formatEuro(importe)}
                      </span>
                      <span className="sr-only">al mes</span>
                    </button>
                  );
                })}
              </div>
              <p className="mt-1.5 text-[12px] text-faint">
                Puedes cambiarlo después desde Ajustes. No se cobra nada hasta que
                termine la prueba.
              </p>
            </fieldset>

            <CampoLabel obligatorio className="mb-2 block text-[12.5px] font-semibold text-mute">
              Nombre del salón
            </CampoLabel>
            <input
              type="text"
              value={salonNombre}
              onChange={(e) => setSalonNombre(e.target.value)}
              placeholder="Introduce el nombre del salón"
              className="mb-4 h-[46px] w-full rounded-xl border border-border bg-side px-3.5 text-sm text-text outline-none transition-colors focus:border-gold"
            />

            {/* DIRECCIÓN Y TELÉFONO, con el porqué escrito donde se rellenan.
                Es lo que la clienta ve en la página de reservas, en el
                recordatorio y cuando el asistente no puede ayudarla. */}
            <CampoLabel obligatorio className="mb-2 block text-[12.5px] font-semibold text-mute">
              Dirección del salón
            </CampoLabel>
            <input
              type="text"
              value={direccion}
              onChange={(e) => setDireccion(e.target.value)}
              placeholder="Calle Mayor 1, Donostia"
              className="mb-4 h-[46px] w-full rounded-xl border border-border bg-side px-3.5 text-sm text-text outline-none transition-colors focus:border-gold"
            />

            <CampoLabel obligatorio className="mb-2 block text-[12.5px] font-semibold text-mute">
              Teléfono del salón
            </CampoLabel>
            <input
              type="tel"
              inputMode="tel"
              value={telefono}
              onChange={(e) => setTelefono(e.target.value)}
              placeholder="600 11 22 33"
              className="mb-1.5 h-[46px] w-full rounded-xl border border-border bg-side px-3.5 text-sm text-text outline-none transition-colors focus:border-gold"
            />
            <p className="mb-4 text-[12px] leading-relaxed text-faint">
              Los ven tus clientas en tu página de reservas y en los recordatorios. Sin ellos no
              saben adónde ir ni por dónde llamar.
            </p>

            {/* UN SOLO CAMPO, TEXTO LIBRE. Hubo un selector de doce países y
                una regla española: se quitaron (ver la cabecera de
                `core/identificacion_fiscal.py` en el backend). Una lista cerrada
                deja fuera al primer salón de Andorra, y la regla solo decía si
                el número estaba bien tecleado, no si la empresa existe.
                Sin `uppercase`: lo que se ve es lo que se guarda.
                Y OBLIGATORIO: la factura sin IVA por inversión del sujeto
                pasivo exige el número fiscal del salón. Solo se comprueba que
                haya algo escrito. `lib/cif-obligatorio.test.ts` lo vigila. */}
            <CampoLabel htmlFor="cif" obligatorio className="mb-2 block text-[12.5px] font-semibold text-mute">
              Identificación fiscal
            </CampoLabel>
            <input
              id="cif"
              type="text"
              inputMode="text"
              autoComplete="off"
              autoCapitalize="characters"
              maxLength={40}
              value={cif}
              onChange={(e) => setCif(e.target.value)}
              aria-describedby="cif-ayuda"
              className="mb-1.5 h-[46px] w-full min-w-0 rounded-xl border border-border bg-side px-3.5 text-sm text-text outline-none transition-colors focus:border-gold"
            />
            <p id="cif-ayuda" className="mb-4 text-[12px] text-faint">
              Es el número que aparece en tus facturas.
            </p>

            <CampoLabel obligatorio className="mb-2 block text-[12.5px] font-semibold text-mute">
              Tu nombre (admin)
            </CampoLabel>
            <input
              type="text"
              value={adminNombre}
              onChange={(e) => setAdminNombre(e.target.value)}
              placeholder="Introduce tu nombre"
              className="mb-4 h-[46px] w-full rounded-xl border border-border bg-side px-3.5 text-sm text-text outline-none transition-colors focus:border-gold"
            />

            <CampoLabel obligatorio className="mb-2 block text-[12.5px] font-semibold text-mute">
              Email
            </CampoLabel>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="Introduce tu email"
              className="mb-4 h-[46px] w-full rounded-xl border border-border bg-side px-3.5 text-sm tracking-normal text-text outline-none transition-colors focus:border-gold"
            />

            <CampoLabel obligatorio className="mb-2 block text-[12.5px] font-semibold text-mute">
              Contraseña
            </CampoLabel>
            <CampoContrasena
              value={password}
              onChange={setPassword}
              className="mb-2 w-full"
              conIcono={false}
            />
            <p className="mb-2 text-[12px] text-faint">{PASSWORD_RULES_TEXT}</p>

            {/* EL CÓDIGO DE QUIEN TE RECOMENDÓ (0077). Opcional, a la vista,
                editable. Mayúsculas y espacios dan igual (lo compara la base). */}
            <CampoLabel htmlFor="codigo-referido" className="mb-2 block text-[12.5px] font-semibold text-mute">
              Código de quien te recomendó (opcional)
            </CampoLabel>
            <input
              id="codigo-referido"
              type="text"
              autoComplete="off"
              autoCapitalize="characters"
              spellCheck={false}
              maxLength={LARGO_MAXIMO}
              value={codigo}
              onChange={(e) => {
                setEscrito(e.target.value);
                setAvisadoSinCodigo(false);
              }}
              onBlur={() => void comprobar(codigo)}
              aria-describedby="codigo-referido-ayuda"
              aria-invalid={codigoVale?.codigo === codigo && codigoVale.vale === false}
              placeholder="Por ejemplo, K7M3XQ"
              className="mb-1.5 h-[46px] w-full min-w-0 rounded-xl border border-border bg-side px-3.5 font-mono text-sm uppercase tracking-[.08em] text-text outline-none transition-colors placeholder:font-sans placeholder:normal-case placeholder:tracking-normal focus:border-gold"
            />
            <p id="codigo-referido-ayuda" className="mb-4 text-[12px] leading-relaxed text-faint" aria-live="polite">
              {codigo.trim() && codigoVale?.codigo === codigo && codigoVale.vale === false ? (
                <span className="text-warning">
                  No encontramos ese código. Revísalo con quien te lo dio
                  {avisadoSinCodigo && ". Si creas la cuenta así, se crea igual, pero sin código"}.
                </span>
              ) : codigo.trim() && codigoVale?.codigo === codigo && codigoVale.vale ? (
                <span className="text-success">Código correcto.</span>
              ) : delEnlace ? (
                "Viene del enlace con el que has llegado. Puedes cambiarlo."
              ) : (
                "Si un comercial o una agencia te dio un código, escríbelo aquí."
              )}
            </p>

            <AvisoCamposObligatorios className="mb-5" />

            {error && (
              <p className="mb-3 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-[13px] text-danger">
                {error}
              </p>
            )}

            <button
              type="submit"
              disabled={loading}
              className="flex h-12 w-full items-center justify-center rounded-xl bg-gold text-[14.5px] font-semibold text-bg shadow-[0_8px_26px_rgb(var(--accent-rgb)/0.24)] transition-colors hover:bg-gold-hover disabled:opacity-60"
            >
              {loading
                ? "Creando cuenta…"
                : avisadoSinCodigo
                  ? "Crear la cuenta sin código"
                  : "Crear cuenta y confirmar email"}
            </button>

            <p className="mt-7 text-center text-[13px] text-mute">
              ¿Ya tienes cuenta?{" "}
              <Link href="/login" className="font-semibold text-gold hover:text-gold-hover">
                Iniciar sesión
              </Link>
            </p>
          </form>
        </div>
      </div>

      <div className="relative hidden flex-1 items-center justify-center overflow-hidden border-l border-[#1B1B1E] bg-[#0D0D0F] p-12 lg:flex">
        <div
          className="pointer-events-none absolute left-1/2 top-1/2 h-[600px] w-[600px] -translate-x-1/2 -translate-y-1/2"
          style={{
            background:
              "radial-gradient(circle at center, color-mix(in srgb, var(--accent) 13%, transparent), transparent 60%)",
          }}
        />
        <div className="relative max-w-[430px] text-center">
          <Image
            src="/brand/estetix_logo.png"
            alt="ESTETIX APP"
            width={220}
            height={34}
            className="mx-auto mb-8"
            style={{ height: 34, width: "auto", maxWidth: "80%" }}
          />
          <blockquote className="m-0 mb-6 font-display text-[28px] font-medium leading-snug text-text">
            &ldquo;Confirmas el email, añades la tarjeta y en minutos estás facturando con agenda y caja al día.&rdquo;
          </blockquote>
          <div className="rounded-2xl border border-[#3A3526] bg-[#161310] px-5 py-4 text-left">
            <div className="mb-2 text-[12px] uppercase tracking-[.08em] text-[#D9BC6A]">Flujo seguro</div>
            <ul className="space-y-2 text-[13px] text-[#d7d0bf]">
              <li>1. Creamos tu salón y tu usuario admin.</li>
              <li>2. Confirmas email para activar la cuenta.</li>
              <li>3. Stripe inicia tu prueba gratis con tarjeta.</li>
            </ul>
          </div>
        </div>
      </div>
    </div>
  );
}
