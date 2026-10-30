"use client";

import { useEffect, useId, useState } from "react";
import Link from "next/link";
import { ArrowUpRight, Dices, UserMinus, X } from "lucide-react";
import {
  AvisoCamposObligatorios,
  CampoLabel,
  bordeCampo,
  useCamposTocados,
} from "@/components/forms/campos-obligatorios";
import {
  createEmployee,
  deleteEmployee,
  listVinculables,
  updateEmployee,
  type Employee,
  type EmployeePayload,
  type EmployeeVinculable,
} from "@/lib/api/employees";
import type { Puesto } from "@/lib/api/puestos";
import type { Service } from "@/lib/api/services";
import { ApiError } from "@/lib/api/client";
import { useAvisoCitasAfectadas } from "@/components/citas-afectadas/aviso-citas-afectadas";
import { guardarViendoLasCitas } from "@/lib/citas-afectadas";
import { DarDeBajaDialogo } from "./dar-de-baja-dialogo";

type Props = {
  /** null = alta; con valor = edición de ese empleado. */
  empleado: Employee | null;
  /** Lista editable del salón. Ya no hay una lista fija en el código: era la
   *  que ofrecía «Admin» y hacía creer que este campo daba accesos. */
  puestos: Puesto[];
  servicios: Service[];
  onClose: () => void;
  onSaved: () => void;
};

function pinAleatorio(): string {
  return Array.from({ length: 4 }, () => Math.floor(Math.random() * 10)).join("");
}

export function NuevoEmpleadoModal({ empleado, puestos, servicios, onClose, onSaved }: Props) {
  const [nombre, setNombre] = useState(empleado?.nombre ?? "");
  const [movil, setMovil] = useState(empleado?.telefono ?? "");
  const [email, setEmail] = useState(empleado?.email ?? "");
  const [puestoId, setPuestoId] = useState(empleado?.puesto_id ?? "");
  // Una ficha de baja no llega aquí (no se edita), así que el formulario solo
  // conoce los dos estados que se eligen a mano.
  const [estado, setEstado] = useState<"activo" | "pausa">(empleado?.estado === "pausa" ? "pausa" : "activo");
  // En edición el PIN empieza vacío = "no cambiar" (el actual nunca viaja al
  // frontend, solo tiene_pin); en alta se propone uno aleatorio.
  const [pin, setPin] = useState(empleado ? "" : pinAleatorio);

  const [tipoComision, setTipoComision] = useState<"porcentaje" | "fijo">(
    empleado?.comision_tipo ?? "porcentaje"
  );
  const [comisionServicios, setComisionServicios] = useState(
    empleado ? String(empleado.comision_servicios ?? 0) : "15"
  );
  const [comisionProductos, setComisionProductos] = useState(
    empleado ? String(empleado.comision_productos ?? 0) : "5"
  );
  const [serviceIds, setServiceIds] = useState<string[]>(empleado?.service_ids ?? []);
  // «Esta persona ya trabaja en otro centro». Solo en el ALTA: enlazar una
  // ficha que ya existe es otra operación y otra pantalla.
  const [vinculables, setVinculables] = useState<EmployeeVinculable[]>([]);
  const [vincularCon, setVincularCon] = useState("");
  // Qué campos tienen AHORA MISMO un valor traído de la otra ficha. Sirve para
  // dos cosas a la vez, y por eso es un solo mecanismo: marcarlos en pantalla
  // («esto vino de allí») y saber qué limpiar si se desmarca la casilla. En
  // cuanto alguien edita un campo, sale del conjunto: deja de ser una copia.
  const [copiados, setCopiados] = useState<Set<string>>(new Set());
  // El PIN de una persona que ya tiene uno no se toca salvo que se pida.
  const [cambiarPin, setCambiarPin] = useState(false);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [dialogoBaja, setDialogoBaja] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [preguntarCitas, avisoCitas] = useAvisoCitasAfectadas("Ponerla en pausa igualmente");
  // DAR DE BAJA, LA CUARTA PUERTA (0062). Primero el diálogo que dice la
  // verdad —qué se conserva, qué deja de pasar, y la pausa como primera
  // opción—; después, si tiene citas por venir, el aviso de siempre con ellas.
  const [sugerirPausa, setSugerirPausa] = useState(false);
  const [preguntarBorrar, avisoBorrar] = useAvisoCitasAfectadas({
    titulo: (n) => `${empleado?.nombre.split(" ")[0] ?? "Tiene"} tiene ${n === 1 ? "1 cita" : `${n} citas`} por venir`,
  });
  // El backend responde 409 al superar el cupo de empleados del plan: el aviso
  // se muestra con un CTA a Suscripción en vez de como error genérico.
  const [limitReached, setLimitReached] = useState(false);
  const tocados = useCamposTocados<"nombre" | "movil">();
  const avisoId = useId();

  // CON UN SOLO CENTRO ESTO DEVUELVE UNA LISTA VACÍA y no se pinta nada: la
  // pantalla de siempre, sin una casilla más que explicar. La equivalencia se
  // decide con datos, igual que en el selector de centro.
  useEffect(() => {
    if (empleado) return;
    let vivo = true;
    listVinculables()
      .then((v) => vivo && setVinculables(v))
      .catch(() => {
        // Que no se pueda ofrecer el enlace no puede impedir dar de alta a
        // nadie: es un extra, y el alta es lo que la persona vino a hacer.
      });
    return () => {
      vivo = false;
    };
  }, [empleado]);

  const comServ = parseFloat(comisionServicios.replace(",", ".")) || 0;
  const comProd = parseFloat(comisionProductos.replace(",", ".")) || 0;
  const comisionesOk =
    comServ >= 0 && comProd >= 0 && (tipoComision !== "porcentaje" || (comServ <= 100 && comProd <= 100));
  const pinOk = pin === "" || /^\d{4}$/.test(pin);
  const valido = nombre.trim().length > 0 && movil.trim().length > 0 && comisionesOk && pinOk;
  const busy = saving || deleting;


  const vinculada = vinculables.find((v) => v.id === vincularCon) ?? null;
  // Ya tiene PIN: entonces aquí NO se genera nada. El PIN es de la persona, y
  // el trigger de la base se lo impone a la ficha nueva se mande lo que se
  // mande; proponer uno aleatorio era contradecir por escrito lo que el sistema
  // hace de verdad.
  const heredaPin = Boolean(vinculada?.tiene_pin);

  /** Los servicios de la otra ficha que este centro NO tiene con ese nombre. */
  const servMap = new Map(servicios.map((x) => [x.nombre, x.id]));
  const servsSinEquivalente = (vinculada?.servicios ?? []).filter((n) => !servMap.has(n));

  /** Deja de marcarse como copiado en cuanto alguien lo edita. */
  function tocado(campo: string) {
    setCopiados((prev) => {
      if (!prev.has(campo)) return prev;
      const s = new Set(prev);
      s.delete(campo);
      return s;
    });
  }

  /**
   * Copiar UNA VEZ para empezar. No es sincronizar: a partir de aquí cada
   * centro edita lo suyo, y el recuadro lo dice.
   *
   * El puesto y los servicios se casan POR NOMBRE porque sus ids son de cada
   * centro; lo que no exista aquí simplemente no se marca, y se avisa de cuál.
   * La comisión no se copia: el dinero se decide a propósito en cada centro.
   */
  function copiarDe(v: EmployeeVinculable) {
    const marcados = new Set<string>();
    setNombre(v.nombre);
    marcados.add("nombre");
    if (v.telefono) {
      setMovil(v.telefono);
      marcados.add("movil");
    }
    if (v.email) {
      setEmail(v.email);
      marcados.add("email");
    }
    const idPuesto = v.puesto ? puestos.find((p) => p.nombre === v.puesto)?.id : undefined;
    if (idPuesto) {
      setPuestoId(idPuesto);
      marcados.add("puesto");
    }
    const ids = v.servicios.map((n) => servMap.get(n)).filter((x): x is string => Boolean(x));
    if (ids.length) {
      setServiceIds(ids);
      marcados.add("servicios");
    }
    setCopiados(marcados);
    setCambiarPin(false);
    // Si hereda el PIN, no se manda ninguno: se queda el suyo.
    if (v.tiene_pin) setPin("");
    else if (!pin) setPin(pinAleatorio());
  }

  /** Al desmarcar, se van SOLO los valores que siguen siendo una copia. */
  function dejarDeVincular() {
    setVincularCon("");
    if (copiados.has("nombre")) setNombre("");
    if (copiados.has("movil")) setMovil("");
    if (copiados.has("email")) setEmail("");
    if (copiados.has("puesto")) setPuestoId("");
    if (copiados.has("servicios")) setServiceIds([]);
    setCopiados(new Set());
    setCambiarPin(false);
    if (!pin) setPin(pinAleatorio());
  }

  /**
   * El sello «esto vino de la otra ficha», al lado de la etiqueta.
   *
   * Devuelve JSX y NO es un componente —se llama `{sello("nombre")}`, no
   * `<Sello …/>`— porque declarar un componente dentro del render crea un tipo
   * nuevo en cada pasada y React desmonta y vuelve a montar el subárbol. Con un
   * `<span>` no se nota; con un `<input>` dentro se perdería el foco al
   * teclear. Se hace bien desde el principio.
   */
  function sello(campo: string) {
    if (!copiados.has(campo)) return null;
    return (
      <span className="ml-1.5 rounded-full bg-gold-tenue px-1.5 py-px text-[10px] font-semibold uppercase tracking-wide text-gold-hover">
        copiado
      </span>
    );
  }

  function toggleServicio(id: string) {
    setServiceIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
    tocado("servicios");
  }

  async function handleGuardar() {
    if (!valido || busy) return;
    setSaving(true);
    setError(null);
    setLimitReached(false);
    const payload: EmployeePayload = {
      nombre: nombre.trim(),
      telefono: movil.trim(),
      estado,
      comision_tipo: tipoComision,
      comision_servicios: comServ,
      comision_productos: comProd,
      service_ids: serviceIds,
    };
    // Se manda SIEMPRE, también vacío: así «Sin puesto» de verdad lo quita.
    payload.puesto_id = puestoId || null;
    if (email.trim()) payload.email = email.trim();
    if (pin) payload.pin_fichaje = pin; // vacío = conservar el PIN actual
    if (!empleado && vincularCon) payload.vincular_con = vincularCon;
    try {
      if (empleado) {
        // Ponerla en pausa con citas por venir: el servidor no guarda hasta que
        // se hayan visto (`lib/citas-afectadas`). Volver deja el modal abierto.
        const hecho = await guardarViendoLasCitas(
          (citas_vistas) => updateEmployee(empleado.id, { ...payload, citas_vistas }),
          preguntarCitas,
        );
        if (!hecho) {
          setSaving(false);
          return;
        }
      } else {
        await createEmployee(payload as EmployeePayload & { nombre: string; telefono: string });
      }
      onSaved();
      onClose();
    } catch (e) {
      // 409 = cupo de empleados del plan alcanzado (enforcement del backend).
      setLimitReached(e instanceof ApiError && e.status === 409);
      setError(e instanceof ApiError ? e.message : "No se ha podido guardar el empleado.");
      setSaving(false);
    }
  }

  async function handleDarDeBaja() {
    if (!empleado || busy) return;
    setDeleting(true);
    setError(null);
    try {
      const hecho = await guardarViendoLasCitas(
        (vistas) => deleteEmployee(empleado.id, vistas).then(() => true),
        preguntarBorrar,
      );
      if (!hecho) {
        // Volvió sin dar de baja: el diálogo sigue abierto.
        setDeleting(false);
        return;
      }
      onSaved();
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "No se ha podido dar de baja.");
      setDeleting(false);
      setDialogoBaja(false);
    }
  }

  const suffix = tipoComision === "porcentaje" ? "%" : "€";
  // El borde lo decide bordeCampo(): no dejes `border-border` fijo aquí o el
  // resaltado de obligatorio vacío no se aplicará de forma predecible.
  // 44px con el dedo (el mínimo táctil) y los 42px de siempre en escritorio,
  // donde apunta un ratón. Suben todos los campos a la vez para que no se
  // descuadren entre sí, igual que en el modal de cita.
  const inputClass = (falta = false) =>
    `h-11 md:h-[42px] w-full rounded-[10px] border ${bordeCampo(falta)} bg-card-alt px-3.5 text-[13.5px] text-text outline-none focus:border-gold`;

  const faltaNombre = tocados.falta("nombre", nombre.trim() === "");
  const faltaMovil = tocados.falta("movil", movil.trim() === "");

  return (
    <div
      onClick={onClose}
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/70 p-4 backdrop-blur-[2px] md:px-6 md:py-10"
    >
      {/* El formulario mide 943 px de alto y la pantalla 780: «Crear empleado»
          nacía fuera de la vista. El pie ya era `sticky bottom-0`, pero eso no
          servía de nada porque quien scrolleaba era el velo, no el panel: lo
          pegajoso se anclaba a la caja del panel, más alta que la pantalla.
          Acotando el alto del panel y dejando que scrollee su CUERPO, el pie se
          queda fijo abajo y los botones están siempre a la vista. Desde `md` se
          quita el tope y todo vuelve a comportarse como antes. */}
      <div
        onClick={(e) => e.stopPropagation()}
        className="my-auto flex max-h-[calc(100dvh-2rem)] w-full max-w-[520px] flex-col overflow-hidden rounded-2xl border border-border bg-card shadow-[0_24px_70px_rgba(0,0,0,.6)] md:max-h-none"
      >
        <div className="flex flex-shrink-0 items-center justify-between border-b border-border-subtle px-[22px] py-5">
          <h3 className="m-0 text-base font-semibold text-text">
            {empleado ? "Editar empleado" : "Nuevo empleado"}
          </h3>
          <button
            onClick={onClose}
            aria-label="Cerrar"
            className="-mr-2 flex h-11 w-11 items-center justify-center rounded-lg text-mute transition-colors hover:bg-[#1B1B1E] hover:text-text md:-mr-0 md:h-[30px] md:w-[30px]"
          >
            <X size={18} />
          </button>
        </div>

        <div className="flex min-h-0 flex-1 flex-col gap-[22px] overflow-y-auto px-[22px] py-[22px] md:overflow-visible">
          {/* ARRIBA DEL TODO, y no escondido en un desplegable al final: si se
              marca después de haber rellenado la ficha, el nombre que se
              autocompleta pisa lo que se acaba de escribir. Aquí se ve antes
              de empezar, que es cuando la decisión es gratis. */}
          {vinculables.length > 0 && (
            <div className="rounded-xl border border-border bg-card-alt p-3.5">
              {/* `min-h-11` = 44 px. Sin él la fila mide lo que mida su texto: a 390 px
                  el título cabe en una línea y el objetivo táctil se quedaba en 30 px
                  —medido—, mientras que a 360 px, al partirse en dos, llegaba a 44 por
                  casualidad. Un objetivo táctil no puede depender de dónde parta una
                  línea de texto. */}
              <label className="flex min-h-11 cursor-pointer items-center gap-2.5 py-1">
                <input
                  type="checkbox"
                  checked={vincularCon !== ""}
                  onChange={(e) => {
                    if (!e.target.checked) return dejarDeVincular();
                    setVincularCon(vinculables[0].id);
                    copiarDe(vinculables[0]);
                  }}
                  className="mt-0.5 h-5 w-5 flex-shrink-0 accent-gold"
                />
                <span className="text-[13px] leading-snug text-text">
                  Esta persona <strong className="font-semibold">ya trabaja en otro centro</strong>
                </span>
              </label>
              {vinculada && (
                <div className="mt-3 pl-[30px]">
                  <label className="mb-1.5 block text-xs font-semibold text-mute">¿Quién es?</label>
                  <select
                    value={vincularCon}
                    onChange={(e) => {
                      setVincularCon(e.target.value);
                      const elegida = vinculables.find((v) => v.id === e.target.value);
                      if (elegida) copiarDe(elegida);
                    }}
                    className={inputClass()}
                  >
                    {vinculables.map((v) => (
                      <option key={v.id} value={v.id}>
                        {v.nombre} · {v.centro}
                      </option>
                    ))}
                  </select>
                  {/* QUÉ SE HA COPIADO Y QUÉ NO, dicho aquí y no en la ayuda:
                      es un formulario que cambia la agenda de dos locales. Lo
                      de «una copia para empezar» es la frase importante: si
                      alguien lo lee como «se sincronizan», editará el móvil en
                      un centro creyendo que cambia en los dos. */}
                  <p className="m-0 mt-2.5 text-[11.5px] leading-relaxed text-faint">
                    Se ha copiado su <strong className="font-medium text-mute">móvil, email,
                    puesto y servicios</strong> de {vinculada.centro}, como punto de partida:
                    revísalo y cámbialo si aquí trabaja de otra forma. Es una copia, no se
                    sincroniza. <strong className="font-medium text-mute">La comisión no se
                    copia</strong>: se decide en este centro.
                  </p>
                  {servsSinEquivalente.length > 0 && (
                    <p className="m-0 mt-1.5 text-[11.5px] leading-relaxed text-faint">
                      En este centro no existe{servsSinEquivalente.length > 1 ? "n" : ""}{" "}
                      <strong className="font-medium text-mute">
                        {servsSinEquivalente.join(", ")}
                      </strong>
                      , así que no se ha{servsSinEquivalente.length > 1 ? "n" : ""} marcado.
                    </p>
                  )}
                  <p className="m-0 mt-1.5 text-[11.5px] leading-relaxed text-faint">
                    Y no se le podrá dar cita en los dos centros a la vez; sus horas se podrán
                    ver sumadas.
                  </p>
                </div>
              )}
            </div>
          )}

          <div>
            <div className="mb-[13px] text-[11px] font-semibold uppercase tracking-[.1em] text-faint">Datos básicos</div>
            <div className="flex flex-col gap-3.5">
              <div className="grid grid-cols-2 gap-3.5">
                <div>
                  <CampoLabel obligatorio className="mb-1.5 block text-xs font-semibold text-mute">
                    Nombre
                    {sello("nombre")}
                  </CampoLabel>
                  <input
                    autoFocus
                    value={nombre}
                    onChange={(e) => {
                      setNombre(e.target.value);
                      tocado("nombre");
                    }}
                    onBlur={() => tocados.marcar("nombre")}
                    placeholder="Introduce el nombre"
                    className={inputClass(faltaNombre)}
                  />
                </div>
                <div>
                  <CampoLabel obligatorio className="mb-1.5 block text-xs font-semibold text-mute">
                    Móvil
                    {sello("movil")}
                  </CampoLabel>
                  <input
                    value={movil}
                    onChange={(e) => {
                      setMovil(e.target.value);
                      tocado("movil");
                    }}
                    onBlur={() => tocados.marcar("movil")}
                    placeholder="Introduce el móvil"
                    className={inputClass(faltaMovil)}
                  />
                </div>
              </div>
              <div className="grid grid-cols-[1.3fr_1fr] gap-3.5">
                <div>
                  <label className="mb-1.5 block text-xs font-semibold text-mute">
                    Email
                    {sello("email")}
                  </label>
                  <input
                    value={email}
                    onChange={(e) => {
                      setEmail(e.target.value);
                      tocado("email");
                    }}
                    placeholder="tu@email.com"
                    className={inputClass()}
                  />
                </div>
                <div>
                  <label className="mb-1.5 block text-xs font-semibold text-mute">
                    Puesto
                    {sello("puesto")}
                  </label>
                  <select
                    value={puestoId}
                    onChange={(e) => {
                      setPuestoId(e.target.value);
                      tocado("puesto");
                    }}
                    disabled={puestos.length === 0}
                    className={inputClass()}
                  >
                    <option value="">
                      {puestos.length === 0 ? "Sin puestos" : "Sin puesto"}
                    </option>
                    {puestos.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.nombre}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
              <div>
                <label className="mb-1.5 block text-xs font-semibold text-mute">Estado</label>
                <div className="grid grid-cols-2 gap-2">
                  {(
                    [
                      { value: "activo", label: "Activo" },
                      { value: "pausa", label: "En pausa · vuelve" },
                    ] as const
                  ).map((opt) => (
                    <button
                      key={opt.value}
                      type="button"
                      onClick={() => setEstado(opt.value)}
                      className={`h-10 rounded-[10px] border text-[13px] font-medium transition-colors ${
                        estado === opt.value
                          ? "border-gold bg-gold-tenue text-gold-hover"
                          : "border-border bg-card-alt text-mute hover:border-[#3a3a40]"
                      }`}
                    >
                      {opt.label}
                    </button>
                  ))}
                </div>
                {/* PAUSA ES «VUELVE». Dicho aquí, donde se elige, y no en un manual. */}
                {estado === "pausa" && !sugerirPausa && (
                  <p className="m-0 mt-2 text-[12.5px] leading-snug text-mute">
                    Para quien va a volver. No sale en la agenda, el quiosco ni la reserva online, y conserva
                    su ficha y su PIN. Si se fue, usa «Dar de baja».
                  </p>
                )}
                {/* Viene del diálogo de la baja: no se guarda sola, que no se lo pidió nadie. */}
                {sugerirPausa && estado === "pausa" && (
                  <p role="status" className="m-0 mt-2 text-[12.5px] leading-snug text-gold">
                    Marcada «En pausa». Pulsa «Guardar cambios» para pausarla en lugar de darla de baja.
                  </p>
                )}
              </div>
            </div>
          </div>

          <div>
            <div className="mb-[13px] text-[11px] font-semibold uppercase tracking-[.1em] text-faint">PIN de fichaje</div>
            {/* EL PIN ES DE LA PERSONA. Si ya trabaja en otro centro y ya tiene
                uno, aquí no se genera nada: seguirá siendo el mismo, porque es
                lo que hace la base. Proponer un PIN nuevo era escribir en el
                formulario lo contrario de lo que el sistema iba a hacer, y el
                número propuesto no llegaba a existir en ninguna parte. */}
            {heredaPin && !cambiarPin ? (
              <>
                <p className="m-0 text-[13px] leading-relaxed text-text">
                  Ya tiene PIN y <strong className="font-semibold">seguirá siendo el mismo</strong>:
                  el de {vinculada?.nombre} es único y le sirve para fichar en los dos centros.
                </p>
                <button
                  type="button"
                  onClick={() => {
                    setCambiarPin(true);
                    setPin(pinAleatorio());
                  }}
                  className="mt-2.5 flex h-11 items-center gap-2 rounded-[10px] border border-gold/35 px-3.5 text-[13px] font-semibold text-gold-hover transition-colors hover:bg-gold-tenue md:h-[42px]"
                >
                  <Dices size={16} />
                  Cambiar el PIN
                </button>
              </>
            ) : (
              <>
                <div className="flex items-center gap-3">
                  <input
                    value={pin}
                    onChange={(e) => setPin(e.target.value.replace(/\D/g, "").slice(0, 4))}
                    placeholder={empleado?.tiene_pin ? "••••" : "----"}
                    inputMode="numeric"
                    aria-label="PIN de fichaje"
                    className="h-[52px] w-[132px] rounded-[11px] border border-border bg-card-alt text-center font-mono text-2xl tracking-[.35em] text-gold outline-none focus:border-gold"
                  />
                  <button
                    type="button"
                    onClick={() => setPin(pinAleatorio())}
                    className="flex h-11 items-center gap-2 rounded-[10px] border border-gold/35 px-3.5 text-[13px] font-semibold text-gold-hover transition-colors hover:bg-gold-tenue md:h-[42px]"
                  >
                    <Dices size={16} />
                    Generar
                  </button>
                </div>
                <p className="mt-2.5 text-xs leading-relaxed text-faint">
                  4 dígitos para fichar desde el quiosco. Se guarda cifrado y no se puede consultar.
                  {empleado?.tiene_pin && " Déjalo vacío para conservar el PIN actual."}
                  {/* El aviso va EN LA MISMA FRASE en que se ofrece cambiarlo,
                      no en una nota aparte: quien cambia un PIN aquí tiene que
                      leer en ese mismo momento que lo cambia en los dos sitios. */}
                  {cambiarPin && (
                    <>
                      {" "}
                      <strong className="font-semibold text-mute">
                        Al cambiarlo aquí, cambia también en {vinculada?.centro}
                      </strong>
                      : es la misma persona y tiene un solo PIN.
                    </>
                  )}
                  {vinculada && !heredaPin && (
                    <> Este PIN le servirá para fichar en los dos centros.</>
                  )}
                </p>
                {cambiarPin && (
                  <button
                    type="button"
                    onClick={() => {
                      setCambiarPin(false);
                      setPin("");
                    }}
                    className="mt-2 h-11 text-[12.5px] font-semibold text-mute underline underline-offset-2 md:h-auto"
                  >
                    Dejarlo como está
                  </button>
                )}
              </>
            )}
            {!pinOk && <p className="mt-1.5 text-xs text-danger">El PIN debe tener exactamente 4 dígitos.</p>}
          </div>

          <div>
            <div className="mb-[13px] text-[11px] font-semibold uppercase tracking-[.1em] text-faint">Comisiones</div>
            <div className="mb-3.5 flex gap-2">
              <button
                type="button"
                onClick={() => setTipoComision("porcentaje")}
                className={`h-10 flex-1 rounded-[10px] text-[13px] font-semibold transition-colors ${
                  tipoComision === "porcentaje"
                    ? "border border-gold/35 bg-gold-tenue text-gold-hover"
                    : "border border-border bg-card-alt font-medium text-mute"
                }`}
              >
                Porcentaje (%)
              </button>
              <button
                type="button"
                onClick={() => setTipoComision("fijo")}
                className={`h-10 flex-1 rounded-[10px] text-[13px] font-semibold transition-colors ${
                  tipoComision === "fijo"
                    ? "border border-gold/35 bg-gold-tenue text-gold-hover"
                    : "border border-border bg-card-alt font-medium text-mute"
                }`}
              >
                Fijo (€)
              </button>
            </div>
            <div className="grid grid-cols-2 gap-3.5">
              <div>
                <label className="mb-1.5 block text-xs font-semibold text-mute">Sobre servicios</label>
                <div className="flex h-[42px] items-center justify-between rounded-[10px] border border-border bg-card-alt px-3.5">
                  <input
                    value={comisionServicios}
                    onChange={(e) => setComisionServicios(e.target.value)}
                    inputMode="decimal"
                    className="w-14 bg-transparent font-mono text-[13.5px] text-text outline-none"
                  />
                  <span className="text-faint">{suffix}</span>
                </div>
              </div>
              <div>
                <label className="mb-1.5 block text-xs font-semibold text-mute">Sobre productos</label>
                <div className="flex h-[42px] items-center justify-between rounded-[10px] border border-border bg-card-alt px-3.5">
                  <input
                    value={comisionProductos}
                    onChange={(e) => setComisionProductos(e.target.value)}
                    inputMode="decimal"
                    className="w-14 bg-transparent font-mono text-[13.5px] text-text outline-none"
                  />
                  <span className="text-faint">{suffix}</span>
                </div>
              </div>
            </div>
            {!comisionesOk && (
              <p className="mt-2 text-xs text-danger">
                Las comisiones deben ser ≥ 0{tipoComision === "porcentaje" && " y ≤ 100%"}.
              </p>
            )}
          </div>

          <div>
            <div className="mb-[13px] text-[11px] font-semibold uppercase tracking-[.1em] text-faint">
              Servicios que puede realizar
              {sello("servicios")}
            </div>
            <div className="grid grid-cols-2 gap-x-4 gap-y-2.5">
              {servicios.map((s) => {
                const checked = serviceIds.includes(s.id);
                return (
                  <label key={s.id} className="flex cursor-pointer items-center gap-2.5 text-[13px] text-text">
                    <button
                      type="button"
                      onClick={() => toggleServicio(s.id)}
                      className={`flex h-[18px] w-[18px] flex-shrink-0 items-center justify-center rounded-[5px] border text-bg ${
                        checked ? "border-gold bg-gold" : "border-border bg-transparent"
                      }`}
                    >
                      {checked && (
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
                          <path d="M20 6 9 17l-5-5" />
                        </svg>
                      )}
                    </button>
                    {s.nombre}
                  </label>
                );
              })}
            </div>
            {servicios.length === 0 && (
              <p className="m-0 text-[13px] text-mute">
                No hay servicios en el catálogo todavía. Créalos en la pantalla de Servicios.
              </p>
            )}
          </div>

          {error &&
            (limitReached ? (
              <div className="rounded-lg border border-gold/35 bg-gold-tenue px-3.5 py-3">
                <p className="m-0 text-[13px] font-medium text-gold-hover">{error}</p>
                <Link
                  href="/ajustes?tab=suscripcion"
                  className="mt-2.5 inline-flex h-9 items-center gap-1.5 rounded-lg bg-gold px-3.5 text-[12.5px] font-semibold text-bg transition-colors hover:bg-gold-hover"
                >
                  Mejorar plan
                  <ArrowUpRight size={15} strokeWidth={2.2} />
                </Link>
              </div>
            ) : (
              <p className="m-0 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-[13px] text-danger">
                {error}
              </p>
            ))}
        </div>

        {/* El aviso ocupa su propia línea sobre los botones: entre «Dar de baja»
            y Cancelar se amontonaba y había que ocultarlo en móvil. */}
        <div className="sticky bottom-0 flex-shrink-0 border-t border-border-subtle bg-card px-[22px] py-[18px]">
          <AvisoCamposObligatorios id={avisoId} className="mb-3" />
          <div
            className={`flex flex-wrap items-center gap-2.5 ${
              empleado ? "justify-between" : "justify-end"
            }`}
          >
            {empleado && (
              <button
                onClick={() => setDialogoBaja(true)}
                disabled={busy}
                className="flex min-h-11 items-center gap-2 rounded-[10px] border border-border px-[16px] text-[13.5px] font-medium text-danger transition-colors hover:border-danger/50 hover:bg-danger/10 disabled:opacity-40"
              >
                <UserMinus size={16} />
                Dar de baja
              </button>
            )}
            <div className="flex flex-wrap items-center justify-end gap-2.5">
              <button
                onClick={onClose}
                disabled={busy}
                className="h-10 rounded-[10px] border border-border px-[18px] text-[13.5px] font-medium text-text transition-colors hover:border-[#3a3a40] hover:bg-[#1B1B1E] disabled:opacity-40"
              >
                Cancelar
              </button>
              <button
                disabled={!valido || busy}
                aria-describedby={avisoId}
                onClick={handleGuardar}
                className="h-10 rounded-[10px] bg-gold px-5 text-[13.5px] font-semibold text-bg transition-colors hover:bg-gold-hover disabled:cursor-not-allowed disabled:opacity-40"
              >
                {saving ? "Guardando…" : empleado ? "Guardar cambios" : "Crear empleado"}
              </button>
            </div>
          </div>
        </div>
      </div>
      {avisoCitas}
      {empleado && dialogoBaja && (
        <DarDeBajaDialogo
          empleado={empleado}
          ocupado={deleting}
          onPausar={
            estado === "activo"
              ? () => {
                  setEstado("pausa");
                  setSugerirPausa(true);
                  setDialogoBaja(false);
                }
              : null
          }
          onCancelar={() => setDialogoBaja(false)}
          onConfirmar={handleDarDeBaja}
        />
      )}
      {avisoBorrar}
    </div>
  );
}
