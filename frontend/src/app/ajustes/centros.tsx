"use client";

import { useEffect, useState, type FormEvent } from "react";
import { Check, Loader2, Lock, MapPin, Plus } from "lucide-react";
import { ApiError } from "@/lib/api/client";
import {
  crearCentro,
  getCentros,
  getCitasEnPausa,
  type Centro,
  type CitasEnPausa,
} from "@/lib/api/centros";
import { CENTROS_POR_PLAN, TIER_LABEL } from "@/lib/mock-plan";
import { useSession } from "@/lib/mock-session";
import { ElCentroQueSigue } from "./el-centro-que-sigue";
import { formatear, ZONA_POR_DEFECTO } from "@/lib/horas";
import { ErrorDeCarga } from "@/components/error-de-carga";

/**
 * Los centros de la cuenta, y cómo se abre el segundo.
 *
 * CON CANDADO EN BÁSICO Y PRO, NO ESCONDIDA. Es el mismo criterio que ya usan
 * Asistente IA e Insights IA en el menú: la función existe de verdad, así que
 * enseñarla es honesto — no se está anunciando humo. Esconderla haría que
 * nadie supiera que Elite sirve para esto.
 *
 * AL LLEGAR AL TOPE EL BOTÓN NO FALLA: desaparece y en su sitio queda escrito
 * por qué. Un botón que revienta al pulsarlo es peor que no tenerlo, porque
 * enseña a desconfiar del resto.
 *
 * NOTA DE DEUDA, anotada y no construida: esta pestaña es de la CUENTA, como
 * Suscripción; Perfil, Horario, reservas y recordatorios son de ESTE CENTRO.
 * Ajustes está mezclando los dos niveles y con dos centros se va a notar. Se
 * separa cuando llegue la vista comparada; hoy solo se añade la pestaña.
 */
/** «un centro» / «2 centros». Con la cifra en el uno queda «los 1 centros»,
 *  que no es castellano — y salió en una captura. */
function cuantosCentros(n: number): string {
  return n === 1 ? "un centro" : `${n} centros`;
}

/** «22 sept, 10:30». Corta a propósito: en una lista de citas lo que se busca
 *  es el día y la hora, y el año sobra en algo que por definición está por
 *  venir. Nunca lanza: una fecha ilegible no puede tumbar la lista entera. */
function fechaCorta(iso: string, zona: string): string {
  if (Number.isNaN(new Date(iso).getTime())) return iso;
  return formatear(iso, zona, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

export function Centros() {
  const { session } = useSession();
  // Las citas pendientes son de ESTE centro: su hora es la de este centro.
  const zona = session?.zona ?? ZONA_POR_DEFECTO;
  const plan = session?.tier ?? "basico";
  const tope = CENTROS_POR_PLAN[plan] ?? 1;
  const conCandado = tope < 2;

  const [centros, setCentros] = useState<Centro[] | null>(null);
  const [activo, setActivo] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pendientes, setPendientes] = useState<CitasEnPausa | null>(null);
  const [sigueElegido, setSigueElegido] = useState(true);
  const [cambioDesde, setCambioDesde] = useState<string | null>(null);

  const [abriendo, setAbriendo] = useState(false);
  const [nombre, setNombre] = useState("");
  const [direccion, setDireccion] = useState("");
  const [telefono, setTelefono] = useState("");
  const [copiar, setCopiar] = useState(true);
  const [guardando, setGuardando] = useState(false);

  // LA LISTA SE PIDE SIEMPRE, también con el plan por debajo de Elite, y eso
  // es lo que cambia en B3a. Antes esta pestaña ni preguntaba si el plan tenía
  // candado: enseñaba «Incluido en Elite» y ya. Con una cuenta en Pro que tiene
  // dos centros —el caso que existe desde que las bajadas se programan— eso
  // dejaba a la titular sin NINGÚN sitio donde ver que su otro centro sigue
  // ahí, después de haberle prometido por escrito que no se borra nada.
  // Si no se puede cargar, se dice y se deja volver a pedirlo (19-10-2026).
  const [errorCarga, setErrorCarga] = useState<string | null>(null);
  const [intento, setIntento] = useState(0);
  useEffect(() => {
    let vivo = true;
    void (async () => {
      try {
        const r = await getCentros();
        if (!vivo) return;
        setCentros(r.centros);
        setActivo(r.activo);
        setSigueElegido(r.sigue_elegido !== false);
        setCambioDesde(r.cambio_de_centro_desde ?? null);
        if (!r.centros.some((c) => c.en_pausa)) return;
        // Solo si hay algo en pausa: son tres consultas más y casi ninguna
        // cuenta las necesita.
        const p = await getCitasEnPausa();
        if (vivo) setPendientes(p);
      } catch (e) {
        if (vivo) setErrorCarga(e instanceof ApiError ? e.message : "No se pudieron leer tus centros.");
      }
    })();
    return () => {
      vivo = false;
    };
  }, [intento]);

  const enPausa = (centros ?? []).filter((c) => c.en_pausa);

  // QUÉ LE FALTA AL FORMULARIO, dicho con nombres. Un botón gris sin motivo es
  // una puerta cerrada en la cara: quien la ve no sabe si le falta algo, si la
  // aplicación está rota o si su plan no llega — y acaba llamando a soporte.
  const faltan = [
    !nombre.trim() && "el nombre",
    !direccion.trim() && "la dirección",
    !telefono.trim() && "el teléfono",
  ].filter((x): x is string => Boolean(x));

  async function handleCrear(event: FormEvent) {
    event.preventDefault();
    if (!nombre.trim() || !direccion.trim() || !telefono.trim() || guardando) return;
    setError(null);
    setGuardando(true);
    try {
      const desde = copiar ? (activo ?? centros?.[0]?.id) : undefined;
      const creado = await crearCentro(
        nombre.trim(),
        direccion.trim(),
        telefono.trim(),
        desde,
      );
      setCentros((previos) => [...(previos ?? []), creado]);
      setNombre("");
      setDireccion("");
      setTelefono("");
      setAbriendo(false);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "No se pudo crear el centro.");
    } finally {
      setGuardando(false);
    }
  }

  // ── Básico y Pro SIN centros de sobra: se ve, con candado y con el porqué.
  //
  // CON centros de sobra NO se enseña el candado, se enseña la lista: en esa
  // cuenta la función no es una promesa de venta, es algo que YA tiene y que se
  // le ha quedado en pausa. Contarle lo bonito que es Elite cuando lo que
  // necesita es ver sus citas pendientes sería una broma pesada.
  if (conCandado && enPausa.length === 0 && !errorCarga) {
    return (
      <section className="rounded-2xl border border-border bg-card p-5">
        <div className="mb-2 inline-flex items-center gap-2 rounded-full border border-border bg-card-alt px-3 py-1 text-[11px] font-semibold uppercase tracking-[.08em] text-faint">
          <Lock size={12} />
          Incluido en Elite
        </div>
        <h2 className="m-0 text-[17px] font-semibold text-text">Un segundo centro</h2>
        <p className="mt-2 max-w-[62ch] text-[13.5px] leading-relaxed text-mute">
          Con Elite puedes abrir un segundo local en la misma cuenta: una sola
          suscripción, un selector arriba para cambiar de uno a otro, y cada centro
          con su agenda, su equipo, su catálogo y sus precios.
        </p>
        <a
          href="/ajustes?tab=suscripcion"
          className="mt-4 inline-flex h-11 min-h-[44px] items-center rounded-xl bg-gold px-4 text-[14px] font-semibold text-bg transition-colors hover:bg-gold-hover"
        >
          Ver planes
        </a>
      </section>
    );
  }

  const alTope = (centros?.length ?? 0) >= tope;

  return (
    <section className="rounded-2xl border border-border bg-card p-5">
      <h2 className="m-0 text-[17px] font-semibold text-text">Centros</h2>
      <p className="mt-1.5 text-[13.5px] text-mute">
        Tu plan {TIER_LABEL[plan]} incluye {cuantosCentros(tope)} en la misma cuenta y con una
        sola suscripción.
      </p>

      {errorCarga && (
        <ErrorDeCarga
          mensaje={errorCarga}
          onReintentar={() => { setErrorCarga(null); setIntento((n) => n + 1); }}
        />
      )}
      {error && (
        <p className="mt-4 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-[13px] text-danger">
          {error}
        </p>
      )}

      <ul className="mt-4 flex list-none flex-col gap-2 p-0">
        {(centros ?? []).map((c) => (
          <li
            key={c.id}
            className="flex min-h-[56px] items-center gap-3 rounded-xl border border-border bg-card-alt px-3.5 py-2.5"
          >
            <MapPin size={16} className="flex-shrink-0 text-gold" />
            <div className="min-w-0 flex-1">
              <div className="truncate text-[14px] font-semibold text-text">
                {c.nombre_comercial}
              </div>
              {c.slug && (
                <div className="truncate text-[12px] text-faint">/{c.slug}</div>
              )}
            </div>
            {c.en_pausa ? (
              <span className="flex-shrink-0 rounded-md border border-border px-2 py-1 text-[11.5px] font-semibold text-faint">
                En pausa
              </span>
            ) : c.id === activo ? (
              <span className="flex-shrink-0 rounded-md bg-gold-tenue px-2 py-1 text-[11.5px] font-semibold text-gold">
                Estás aquí
              </span>
            ) : null}
          </li>
        ))}
        {centros === null && (
          <li className="flex h-14 items-center gap-2 text-[13px] text-faint">
            <Loader2 size={15} className="animate-spin" />
            Cargando tus centros…
          </li>
        )}
      </ul>

      {/* LO QUE LA PAUSA NO PUEDE ESCONDER: las citas que ese centro ya tenía
          cogidas. Si no se pueden ver, la clienta del día 22 se presenta y no
          la espera nadie — y eso no es una función de menos, es un compromiso
          roto con alguien que no ha hecho nada.

          Esto es SOLO LECTURA a propósito: no es «entrar» en el centro, es no
          ocultarlo. Para tocarlas hay que reactivar el centro. */}
      {enPausa.length > 0 && (
        <div className="mt-4 rounded-xl border border-border bg-card-alt p-4">
          <h3 className="m-0 text-[14px] font-semibold text-text">
            {enPausa.length === 1
              ? `${enPausa[0].nombre_comercial} está en pausa`
              : `${enPausa.length} centros están en pausa`}
          </h3>
          <p className="mt-1.5 text-[12.5px] leading-[1.55] text-mute">
            Tu plan {TIER_LABEL[plan]} incluye {cuantosCentros(tope)}. No se ha borrado nada: sus
            datos, sus citas y su equipo siguen ahí, y vuelven en cuanto recuperes un plan que
            los incluya.
          </p>

          <ElCentroQueSigue
            centros={centros ?? []}
            sigueElegido={sigueElegido}
            cambioDesde={cambioDesde}
            zona={zona}
            onCambiado={(nuevo) => {
              setSigueElegido(true);
              setActivo(nuevo);
              setCentros((previos) => (previos ?? []).map((c) => ({ ...c, en_pausa: c.id !== nuevo })));
            }}
          />

          {pendientes === null ? (
            <p className="mt-3 flex items-center gap-2 text-[12.5px] text-faint">
              <Loader2 size={14} className="animate-spin" />
              Comprobando si hay citas pendientes…
            </p>
          ) : pendientes.total === 0 ? (
            <p className="mt-3 text-[12.5px] text-mute">
              No hay ninguna cita pendiente en {enPausa.length === 1 ? "ese centro" : "esos centros"}.
            </p>
          ) : (
            <>
              <p className="mt-3 rounded-lg border border-gold/30 bg-gold-tenue px-3 py-2.5 text-[12.5px] leading-[1.55] text-text">
                <strong className="font-semibold">
                  {pendientes.total === 1 ? "Hay 1 cita" : `Hay ${pendientes.total} citas`} ya
                  reservadas
                </strong>{" "}
                {pendientes.ultima && <>y la última es el {fechaCorta(pendientes.ultima, zona)}. </>}
                Nadie va a poder atenderlas desde la agenda mientras el centro siga en pausa:
                avisa a esas clientas o reactiva el centro.
              </p>
              <ul className="mt-3 flex list-none flex-col gap-1.5 p-0">
                {pendientes.citas.map((cita) => (
                  <li
                    key={cita.id}
                    className="flex min-h-[44px] flex-wrap items-center gap-x-2 gap-y-0.5 rounded-lg border border-border bg-side px-3 py-2 text-[12.5px]"
                  >
                    <span className="font-semibold tabular-nums text-text">
                      {fechaCorta(cita.inicio, zona)}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-mute">
                      {cita.nombre_reserva ?? cita.clienta ?? "Sin nombre"}
                      {cita.servicio && ` · ${cita.servicio}`}
                    </span>
                  </li>
                ))}
              </ul>
              {pendientes.total > pendientes.citas.length && (
                <p className="mt-2 text-[12px] text-faint">
                  Y {pendientes.total - pendientes.citas.length} más. Se enseñan las{" "}
                  {pendientes.se_pintan_como_mucho} más próximas, que son a las que hay que llamar
                  antes.
                </p>
              )}
            </>
          )}
        </div>
      )}

      {/* AL TOPE NO HAY BOTÓN, hay explicación. Pulsar algo que falla enseña a
          desconfiar de lo demás. */}
      {alTope ? (
        <p className="mt-4 rounded-xl border border-border bg-card-alt px-3.5 py-3 text-[13px] text-mute">
          {enPausa.length > 0 ? (
            <>
              Tienes más centros de los que incluye tu plan. Para volver a usarlos todos,{" "}
              <a href="/ajustes?tab=suscripcion" className="font-semibold text-gold hover:underline">
                cambia de plan
              </a>
              .
            </>
          ) : (
            <>Ya tienes {cuantosCentros(tope)}, que es lo que incluye tu plan. Para abrir otro, escríbenos y lo vemos.</>
          )}
        </p>
      ) : abriendo ? (
        <form onSubmit={handleCrear} className="mt-4 rounded-xl border border-border bg-card-alt p-4">
          <label className="mb-2 block text-[12.5px] font-semibold text-mute">
            Nombre del nuevo centro
          </label>
          <input
            autoFocus
            value={nombre}
            onChange={(e) => setNombre(e.target.value)}
            placeholder="Por ejemplo: Centro Norte"
            className="mb-3 h-[46px] w-full rounded-xl border border-border bg-side px-3.5 text-sm text-text outline-none transition-colors focus:border-gold"
          />

          {/* DIRECCIÓN Y TELÉFONO, OBLIGATORIOS, y con el porqué escrito donde
              se rellenan. San Sebastián se creó solo con nombre: su página
              pública le decía a una clienta «escríbenos por otro medio o pásate
              por el salón» sin decir por qué medio ni adónde. Quien abre un
              local los tiene a mano; pedírselos después es pedírselos nunca.

              El servidor los exige igual (`CrearCentroIn`): esto es la cortesía
              de decirlo antes, no la comprobación. */}
          <label className="mb-2 block text-[12.5px] font-semibold text-mute">
            Dirección
          </label>
          <input
            value={direccion}
            onChange={(e) => setDireccion(e.target.value)}
            placeholder="Calle Mayor 1, Donostia"
            className="mb-3 h-[46px] w-full rounded-xl border border-border bg-side px-3.5 text-sm text-text outline-none transition-colors focus:border-gold"
          />

          <label className="mb-2 block text-[12.5px] font-semibold text-mute">
            Teléfono
          </label>
          <input
            value={telefono}
            onChange={(e) => setTelefono(e.target.value)}
            inputMode="tel"
            placeholder="600 11 22 33"
            className="mb-1.5 h-[46px] w-full rounded-xl border border-border bg-side px-3.5 text-sm text-text outline-none transition-colors focus:border-gold"
          />
          <p className="mb-3 text-[12px] leading-relaxed text-faint">
            Los ve la clienta en la página de reservas, en el recordatorio y cuando el asistente no
            puede ayudarla. Sin ellos, ese centro no sabe decirle adónde ir ni por dónde llamar.
          </p>

          {/* LA OPCIÓN DE COPIAR, BIEN VISIBLE Y DICHA EN UNA FRASE. Es la
              decisión que más ahorra al abrir un local y la que más confunde si
              no se explica: la gente asume que quedan sincronizados. */}
          <button
            type="button"
            onClick={() => setCopiar((v) => !v)}
            aria-pressed={copiar}
            className={`flex w-full min-h-[44px] items-start gap-3 rounded-xl border px-3.5 py-3 text-left transition-colors ${
              copiar ? "border-gold/50 bg-gold-tenue" : "border-border bg-side hover:border-gold/30"
            }`}
          >
            <span
              className={`mt-0.5 flex h-[18px] w-[18px] flex-shrink-0 items-center justify-center rounded border ${
                copiar ? "border-gold bg-gold text-bg" : "border-border"
              }`}
            >
              {copiar && <Check size={13} strokeWidth={3} />}
            </span>
            <span className="min-w-0">
              <span className="block text-[13.5px] font-semibold text-text">
                Copiar los servicios de este centro
              </span>
              <span className="mt-0.5 block text-[12.5px] leading-relaxed text-mute">
                Se copian una vez para empezar. A partir de ahí cada centro va por
                su lado: cambiar un precio aquí no lo cambia allí.
              </span>
            </span>
          </button>

          {faltan.length > 0 && (
            <p
              role="status"
              className="mt-4 rounded-lg border border-border bg-side px-3 py-2 text-[12.5px] leading-relaxed text-mute"
            >
              Para crear el centro falta{faltan.length > 1 ? "n" : ""}{" "}
              <strong className="font-semibold text-text">
                {faltan.length > 1
                  ? `${faltan.slice(0, -1).join(", ")} y ${faltan.at(-1)}`
                  : faltan[0]}
              </strong>
              .
            </p>
          )}

          <div className="mt-4 flex flex-col gap-2 sm:flex-row">
            <button
              type="submit"
              disabled={guardando || faltan.length > 0}
              className="flex h-11 min-h-[44px] flex-1 items-center justify-center gap-2 rounded-xl bg-gold text-[14px] font-semibold text-bg transition-colors hover:bg-gold-hover disabled:opacity-60"
            >
              {guardando && <Loader2 size={15} className="animate-spin" />}
              {guardando ? "Creando…" : "Crear centro"}
            </button>
            <button
              type="button"
              onClick={() => setAbriendo(false)}
              className="flex h-11 min-h-[44px] items-center justify-center rounded-xl border border-border px-4 text-[14px] font-medium text-mute transition-colors hover:text-text"
            >
              Cancelar
            </button>
          </div>
        </form>
      ) : (
        <button
          type="button"
          onClick={() => setAbriendo(true)}
          disabled={centros === null}
          className="mt-4 flex h-11 min-h-[44px] items-center justify-center gap-2 rounded-xl bg-gold px-4 text-[14px] font-semibold text-bg transition-colors hover:bg-gold-hover disabled:opacity-60"
        >
          <Plus size={16} />
          Añadir centro
        </button>
      )}
    </section>
  );
}
