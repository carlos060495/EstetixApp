"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  Check,
  Hash,
  Info,
  Mail,
  MapPin,
  Phone,
  Store,
  Trash2,
  UploadCloud,
} from "lucide-react";
import { CampoQueCrece } from "@/components/forms/campo-que-crece";
import { CampoLabel } from "@/components/forms/campos-obligatorios";
import { PieDeGuardado } from "@/components/forms/pie-de-guardado";
import { ACCENT_PRESETS, DEFAULT_ACCENT, accentError, applyAccent, normalizeAccent } from "@/lib/accent";
import { ApiError } from "@/lib/api/client";
import {
  deleteSalonLogo,
  getSalonProfile,
  updateSalonProfile,
  uploadSalonLogo,
  type SalonProfile,
} from "@/lib/api/salon";
import { useSession } from "@/lib/mock-session";
import { ZONA_POR_DEFECTO, ZONAS } from "@/lib/horas";
import { olvidarZonaDelCentro } from "@/lib/zona-del-centro";
import { ErrorDeCarga } from "@/components/error-de-carga";

const EMPTY_PROFILE: SalonProfile = {
  nombre_comercial: "",
  cif: null,
  direccion: null,
  telefono: null,
  email: null,
  plan: "basico",
  accent_color: DEFAULT_ACCENT,
  logo_url: null,
  zona_horaria: ZONA_POR_DEFECTO,
};

// Pre-validación solo por UX: la validación real (magic bytes, re-encode,
// tamaño) vive en el backend.
const LOGO_TYPES = ["image/png", "image/jpeg", "image/webp"];
const MAX_LOGO_BYTES = 2 * 1024 * 1024;

export function PerfilSalon() {
  const { refresh } = useSession();
  const [profile, setProfile] = useState<SalonProfile>(EMPTY_PROFILE);
  const [loading, setLoading] = useState(true);
  // DOS GUARDADOS, DOS ESTADOS. Antes había uno solo, y por eso un permiso
  // que faltaba sobre `accounts` impedía rellenar la dirección, que vive en
  // `salons`. Cada sección falla —o acierta— por su cuenta.
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savingFiscal, setSavingFiscal] = useState(false);
  const [savedFiscal, setSavedFiscal] = useState(false);
  const [errorFiscal, setErrorFiscal] = useState<string | null>(null);
  // El motivo por el que el teléfono no vale, para ponerlo DEBAJO DEL CAMPO.
  // El aviso general vive en la esquina, junto al botón de guardar: en un móvil
  // eso queda lejos de lo que hay que corregir, y este mensaje dice qué le pasa
  // al número — «le sobra un dígito» no se lee igual a 300 px del campo.
  const [telefonoError, setTelefonoError] = useState<string | null>(null);
  const [savingColor, setSavingColor] = useState(false);
  const [colorError, setColorError] = useState<string | null>(null);
  const [hexDraft, setHexDraft] = useState("");
  const [logoBusy, setLogoBusy] = useState(false);
  const [logoError, setLogoError] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  // Si no se puede cargar, se dice y se deja volver a pedirlo (19-10-2026).
  const [errorCarga, setErrorCarga] = useState<string | null>(null);
  const [intento, setIntento] = useState(0);
  useEffect(() => {
    let alive = true;
    setLoading(true);
    setErrorCarga(null);
    void getSalonProfile()
      .then((data) => {
        if (alive) {
          // SE PINTA EL LEGIBLE, SE GUARDA EL CANÓNICO. El backend devuelve las
          // dos formas; el campo enseña la que se lee y al guardar vuelve a
          // pasar por `phonenumbers`, que se come los espacios sin rechistar.
          setProfile({ ...data, telefono: data.telefono_legible ?? data.telefono });
          setHexDraft(normalizeAccent(data.accent_color));
        }
      })
      .catch((e: unknown) => {
        if (!alive) return;
        setErrorCarga(e instanceof ApiError ? e.message : "No se pudo cargar el perfil del salón.");
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [intento]);

  const initials = useMemo(() => {
    const value = profile.nombre_comercial.trim();
    if (!value) return "S";
    return value
      .split(/\s+/)
      .slice(0, 2)
      .map((part) => part[0]?.toUpperCase() ?? "")
      .join("") || "S";
  }, [profile.nombre_comercial]);

  function updateField<K extends keyof SalonProfile>(key: K, value: SalonProfile[K]) {
    if (key === "cif") {
      setSavedFiscal(false);
      setErrorFiscal(null);
      setProfile((prev) => ({ ...prev, [key]: value }));
      return;
    }
    setSaved(false);
    if (key === "telefono") {
      // Los dos avisos hablan del número GUARDADO. En cuanto se toca el campo
      // dejan de ser verdad: se quitan hasta que se vuelva a guardar.
      setTelefonoError(null);
      setProfile((prev) => ({ ...prev, telefono_aviso: null, [key]: value }));
      return;
    }
    setProfile((prev) => ({ ...prev, [key]: value }));
  }

  // Primero lo que se acaba de rechazar; si no, lo que ya estaba guardado mal.
  const avisoTelefono = telefonoError ?? profile.telefono_aviso ?? null;

  async function handleSave() {
    if (saving) return;
    setSaving(true);
    setError(null);
    setTelefonoError(null);
    setSaved(false);
    try {
      // SIN EL CIF, a propósito: es de la cuenta y tiene su propio botón. Lo
      // que se manda es lo de ESTE local, así que un dato fiscal heredado que
      // no pase la validación ya no puede impedir guardar una dirección.
      const payload = {
        nombre_comercial: profile.nombre_comercial.trim(),
        direccion: profile.direccion?.trim() || null,
        telefono: profile.telefono?.trim() || null,
        email: profile.email?.trim() || null,
        zona_horaria: profile.zona_horaria ?? ZONA_POR_DEFECTO,
      };
      const updated = await updateSalonProfile(payload);
      // La agenda y las demás pantallas vuelven a preguntar la zona.
      olvidarZonaDelCentro();
      setProfile({ ...updated, telefono: updated.telefono_legible ?? updated.telefono });
      setSaved(true);
      await refresh();
    } catch (e) {
      // El backend manda un código con el motivo del teléfono; así el mensaje
      // va al lado del campo y no al aviso general de la esquina.
      if (e instanceof ApiError && e.codigo === "telefono_no_valido") {
        setTelefonoError(e.message);
      } else {
        setError(e instanceof ApiError ? e.message : "No se ha podido guardar el perfil del salón.");
      }
    } finally {
      setSaving(false);
    }
  }

  async function guardarDatosFiscales() {
    if (savingFiscal) return;
    setErrorFiscal(null);
    setSavedFiscal(false);
    // OBLIGATORIA AL GUARDAR ESTA TARJETA, Y SOLO ESTA. La del local va por
    // `handleSave`, que no la manda: quien no la tiene puede seguir guardando
    // su dirección y su teléfono. Solo que haya algo escrito, sin reglas.
    const cif = profile.cif?.trim() ?? "";
    if (!cif) {
      setErrorFiscal("Escribe tu identificación fiscal: la necesitamos para emitir tus facturas.");
      return;
    }
    setSavingFiscal(true);
    try {
      const updated = await updateSalonProfile({ cif });
      setProfile((prev) => ({ ...prev, cif: updated.cif }));
      setSavedFiscal(true);
      await refresh();
    } catch (e) {
      setErrorFiscal(
        e instanceof ApiError ? e.message : "No se han podido guardar los datos fiscales.",
      );
    } finally {
      setSavingFiscal(false);
    }
  }

  async function aplicarColor(hex: string) {
    if (savingColor) return;
    const anterior = normalizeAccent(profile.accent_color);
    setColorError(null);
    setSavingColor(true);
    // Preview inmediato en toda la app; si el guardado falla, se revierte.
    applyAccent(hex);
    setProfile((prev) => ({ ...prev, accent_color: hex }));
    setHexDraft(hex);
    try {
      const updated = await updateSalonProfile({ accent_color: hex });
      setProfile(updated);
      await refresh();
    } catch (e) {
      applyAccent(anterior);
      setProfile((prev) => ({ ...prev, accent_color: anterior }));
      setColorError(e instanceof ApiError ? e.message : "No se ha podido guardar el color de marca.");
    } finally {
      setSavingColor(false);
    }
  }

  async function subirLogo(file: File) {
    if (logoBusy) return;
    setLogoError(null);
    if (!LOGO_TYPES.includes(file.type)) {
      setLogoError("Formato no soportado. Usa una imagen PNG, JPG o WebP.");
      return;
    }
    if (file.size > MAX_LOGO_BYTES) {
      setLogoError("El archivo supera el máximo de 2 MB.");
      return;
    }
    setLogoBusy(true);
    try {
      const res = await uploadSalonLogo(file);
      setProfile((prev) => ({ ...prev, logo_url: res.logo_url }));
      await refresh(); // el sidebar repinta el logo desde /auth/me
    } catch (e) {
      setLogoError(e instanceof ApiError ? e.message : "No se ha podido subir el logo.");
    } finally {
      setLogoBusy(false);
    }
  }

  async function eliminarLogo() {
    if (logoBusy) return;
    setLogoError(null);
    setLogoBusy(true);
    try {
      await deleteSalonLogo();
      setProfile((prev) => ({ ...prev, logo_url: null }));
      await refresh();
    } catch (e) {
      setLogoError(e instanceof ApiError ? e.message : "No se ha podido eliminar el logo.");
    } finally {
      setLogoBusy(false);
    }
  }

  function aplicarColorLibre() {
    const hex = hexDraft.trim();
    const invalido = accentError(hex);
    if (invalido) {
      setColorError(invalido);
      return;
    }
    void aplicarColor(hex);
  }

  return (
    // La primera columna era de 300 px FIJOS, así que en 360 px de pantalla la
    // tarjeta «Datos del negocio» empezaba en x=350: fuera de la vista. Solo se
    // llegaba a ella arrastrando en horizontal, y ese arrastre ni siquiera
    // estaba declarado —el contenedor de la página solo tiene `overflow-y-auto`
    // y el navegador computaba `overflow-x: auto` de rebote—, así que no había
    // barra ni pista de que hubiera nada más a la derecha. Apilado en móvil.
    //
    // Y APILADO TAMBIÉN EN TABLETA: las dos columnas empiezan en `lg`, no en
    // `md`. A 768 px la barra lateral ya se lleva 250, y con los 300 fijos
    // del logotipo a «Datos del local» le quedaban unos 100 px de campo.
    <div className="grid grid-cols-1 items-start gap-5 lg:grid-cols-[300px_1fr]">
      <div className="rounded-2xl border border-border bg-card p-[22px]">
        <div className="mb-4 text-[11px] font-semibold uppercase tracking-[.1em] text-faint">Logotipo</div>
        <input
          ref={fileInputRef}
          type="file"
          accept="image/png,image/jpeg,image/webp"
          className="hidden"
          disabled={logoBusy}
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void subirLogo(file);
            e.target.value = "";
          }}
        />
        <div
          role="button"
          aria-label="Subir logo"
          onClick={() => {
            if (!logoBusy) fileInputRef.current?.click();
          }}
          onDragOver={(e) => {
            e.preventDefault();
            if (!logoBusy) setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragOver(false);
            if (logoBusy) return;
            const file = e.dataTransfer.files?.[0];
            if (file) void subirLogo(file);
          }}
          className={`flex flex-col items-center gap-3 rounded-[14px] border border-dashed px-[18px] py-[26px] transition-colors ${
            dragOver ? "border-gold bg-gold/10" : "border-[#3A3A40] bg-card-alt"
          } cursor-pointer`}
        >
          {profile.logo_url ? (
            /* eslint-disable-next-line @next/next/no-img-element -- signed URL efímera de Storage, sin optimizador */
            <img
              src={profile.logo_url}
              alt={`Logo de ${profile.nombre_comercial || "salón"}`}
              className="h-[74px] w-[74px] rounded-[18px] border border-border object-cover"
            />
          ) : (
            <div
              className="flex h-[74px] w-[74px] items-center justify-center rounded-[18px] border font-display text-[38px] font-bold"
              style={{ borderColor: "var(--accent)", background: "linear-gradient(145deg,color-mix(in srgb, var(--accent) 10%, #101012),#161618)", color: "var(--accent)" }}
            >
              {initials}
            </div>
          )}
          <div className="inline-flex items-center gap-2 text-[12.5px] font-semibold text-gold">
            <UploadCloud size={15} strokeWidth={2} />
            {logoBusy ? "Procesando…" : profile.logo_url ? "Cambiar logo" : "Subir logo"}
          </div>
          <div className="text-center text-[11.5px] leading-[1.5] text-faint">
            Arrastra una imagen o haz clic.
            <br />
            PNG, JPG o WebP · máx. 2 MB.
          </div>
        </div>
        {profile.logo_url && (
          <button
            type="button"
            onClick={() => void eliminarLogo()}
            disabled={logoBusy}
            className="mt-2.5 inline-flex h-8 items-center gap-1.5 rounded-[9px] border border-border px-3 text-[12px] font-semibold text-mute transition-colors hover:border-danger hover:text-danger disabled:cursor-not-allowed disabled:opacity-55"
          >
            <Trash2 size={13} strokeWidth={1.8} />
            Eliminar logo
          </button>
        )}
        {logoError && <p className="mt-2 text-[11.5px] leading-[1.4] text-danger">{logoError}</p>}
        <div className="mt-3.5 text-[11.5px] leading-[1.5] text-faint">
          Aparecerá en el menú lateral, documentos y comunicaciones del salón.
        </div>

        <div className="mt-5 border-t border-border-subtle pt-5">
          <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-[.1em] text-faint">Color de marca</div>
          <p className="mb-3.5 text-[11.5px] leading-[1.5] text-faint">
            El acento se aplica en toda la app. La base oscura no cambia.
          </p>
          <div className="grid grid-cols-4 gap-2.5">
            {ACCENT_PRESETS.map((preset) => {
              const active = normalizeAccent(profile.accent_color).toLowerCase() === preset.hex.toLowerCase();
              return (
                <button
                  key={preset.hex}
                  type="button"
                  title={preset.nombre}
                  aria-label={`Color ${preset.nombre}`}
                  onClick={() => void aplicarColor(preset.hex)}
                  disabled={savingColor}
                  className="flex h-11 items-center justify-center rounded-[10px] border-2 transition-transform hover:scale-[1.05] disabled:cursor-not-allowed disabled:opacity-55 md:h-[34px]"
                  style={{ backgroundColor: preset.hex, borderColor: active ? "#F5F0E8" : "transparent" }}
                >
                  {active && <Check size={15} strokeWidth={3} className="text-[#0A0A0B]" />}
                </button>
              );
            })}
          </div>
          <div className="mt-3 flex items-center gap-2">
            <input
              value={hexDraft}
              onChange={(e) => {
                setHexDraft(e.target.value);
                setColorError(null);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") aplicarColorLibre();
              }}
              placeholder="#RRGGBB"
              maxLength={7}
              disabled={savingColor}
              className="h-9 min-w-0 flex-1 rounded-[9px] border border-border bg-card-alt px-3 font-mono text-[12.5px] text-text outline-none focus:border-[var(--accent)] disabled:opacity-60"
            />
            <button
              type="button"
              onClick={aplicarColorLibre}
              disabled={savingColor || !hexDraft.trim()}
              className="h-9 flex-shrink-0 rounded-[9px] border border-border bg-card-alt px-3 text-[12px] font-semibold text-mute transition-colors hover:text-text disabled:cursor-not-allowed disabled:opacity-55"
            >
              {savingColor ? "Guardando…" : "Aplicar"}
            </button>
          </div>
          {colorError && <p className="mt-2 text-[11.5px] leading-[1.4] text-danger">{colorError}</p>}
        </div>
      </div>

      <div className="rounded-2xl border border-border bg-card px-[26px] py-6">
        <div className="mb-5 flex items-center justify-between gap-4">
          <div className="text-[11px] font-semibold uppercase tracking-[.1em] text-faint">Datos del local</div>
          <span className="inline-flex items-center gap-1.5 text-[11.5px] text-faint">
            <Info size={13} strokeWidth={1.8} />
            Plan: {profile.plan}
          </span>
        </div>

        {loading ? (
          <div className="rounded-[14px] border border-border bg-card-alt px-4 py-10 text-center text-[13.5px] text-mute">
            Cargando perfil del salón…
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-x-[18px] gap-y-4">
            <div className="col-span-2">
              <label htmlFor="nombre_comercial" className="mb-1.5 block text-xs font-medium text-mute">
                Nombre comercial
              </label>
              <CampoQueCrece
                id="nombre_comercial"
                tipo="texto"
                autoComplete="organization"
                value={profile.nombre_comercial}
                onChange={(v) => updateField("nombre_comercial", v)}
                icono={<Store size={16} strokeWidth={1.7} />}
                maxLength={200}
              />
            </div>

            {/* CON AVISO, A TODO LO ANCHO. El teléfono comparte fila con el
                CIF, así que a 360 px el mensaje cae en media columna y se
                estira a seis líneas — visto en la captura. Solo se ensancha
                cuando hay algo que leer; sin aviso, la fila queda como estaba. */}
            {/* A TODO LO ANCHO, SIEMPRE. Dos motivos, los dos vistos en la
                captura a 360 px: el aviso caía en media columna y se estiraba a
                seis líneas, y el número escrito para leerlo —«+34 658 72 31 49»—
                es cinco caracteres más largo que el canónico y se salía del
                campo. Un teléfono que no se puede leer entero en Ajustes es el
                mismo problema que veníamos a arreglar. */}
            <div className="col-span-2">
              <label htmlFor="telefono" className="mb-1.5 block text-xs font-medium text-mute">
                Teléfono
              </label>
              <CampoQueCrece
                id="telefono"
                tipo="tel"
                autoComplete="tel"
                value={profile.telefono ?? ""}
                onChange={(v) => updateField("telefono", v)}
                icono={<Phone size={16} strokeWidth={1.7} />}
                invalido={Boolean(avisoTelefono)}
                describedBy={avisoTelefono ? "telefono-aviso" : undefined}
                maxLength={30}
              />
              {avisoTelefono && (
                // POR QUÉ EXISTE: el teléfono de un salón tenía un dígito de más
                // y se guardó sin que nada chistara. El botón «Llamar al salón»
                // de todos sus recordatorios llevaba muerto y nadie se enteró.
                // Enterarse aquí es barato; por un cliente que no pudo llamar, no.
                <p
                  id="telefono-aviso"
                  className="mt-1.5 flex items-start gap-1.5 text-[12px] leading-[1.45] text-danger"
                >
                  <AlertTriangle size={13} strokeWidth={2} className="mt-[2px] flex-shrink-0" />
                  <span>{avisoTelefono}</span>
                </p>
              )}
            </div>
            <div className="col-span-2">
              <label htmlFor="direccion" className="mb-1.5 block text-xs font-medium text-mute">
                Dirección
              </label>
              <CampoQueCrece
                id="direccion"
                tipo="texto"
                autoComplete="street-address"
                value={profile.direccion ?? ""}
                onChange={(v) => updateField("direccion", v)}
                icono={<MapPin size={16} strokeWidth={1.7} />}
                maxLength={250}
              />
            </div>
            <div className="col-span-2">
              <label htmlFor="email" className="mb-1.5 block text-xs font-medium text-mute">
                Email
              </label>
              <CampoQueCrece
                id="email"
                tipo="email"
                autoComplete="email"
                value={profile.email ?? ""}
                onChange={(v) => updateField("email", v)}
                icono={<Mail size={16} strokeWidth={1.7} />}
                maxLength={254}
              />
            </div>
            {/* LA ZONA HORARIA DEL CENTRO (0060). Con ella se guardan y se dicen
                todas las horas: la agenda, los recordatorios, el bot, el
                fichaje. Las Palmas va una hora por detrás de Madrid todo el año. */}
            <div className="col-span-2">
              <label htmlFor="zona_horaria" className="mb-1.5 block text-xs font-medium text-mute">
                Zona horaria
              </label>
              <select
                id="zona_horaria"
                value={profile.zona_horaria ?? ZONA_POR_DEFECTO}
                onChange={(e) => setProfile((prev) => ({ ...prev, zona_horaria: e.target.value }))}
                className="h-11 w-full rounded-[10px] border border-border bg-card-alt px-3 text-[14px] text-text outline-none focus:border-gold/60 disabled:opacity-60"
              >
                {Object.entries(ZONAS).map(([valor, texto]) => (
                  <option key={valor} value={valor}>
                    {texto}
                  </option>
                ))}
              </select>
              <p className="m-0 mt-1.5 text-[12px] leading-[1.45] text-faint">
                Las horas de la agenda, los recordatorios, el asistente y el fichaje van en esta zona.
              </p>
            </div>
          </div>
        )}

        {/* EL ERROR DE LA TARJETA, AL FINAL DE SUS CAMPOS, no en el pie. El
            del teléfono ya va bajo el teléfono; este es el que no es de un
            campo concreto (se cayó la red, falló el servidor). Vivía en el pie
            junto al botón, que es donde rompía la maquetación. */}
        {errorCarga && (
          <ErrorDeCarga
            mensaje={errorCarga}
            onReintentar={() => { setErrorCarga(null); setIntento((n) => n + 1); }}
          />
        )}
        {error && (
          <p
            role="alert"
            className="mt-4 flex items-start gap-1.5 text-[12px] leading-[1.45] text-danger"
          >
            <AlertTriangle size={13} strokeWidth={2} className="mt-[2px] flex-shrink-0" />
            <span>{error}</span>
          </p>
        )}

        <PieDeGuardado
          ayuda="Puedes editar estos datos desde la sesión de administrador."
          etiqueta="Guardar cambios"
          onGuardar={handleSave}
          guardando={saving}
          deshabilitado={loading}
          guardado={saved}
        />
      </div>

      {/* ═══ LOS DATOS FISCALES SON DE LA EMPRESA, NO DE ESTE LOCAL ═══
          Tarjeta aparte y guardado aparte, y no es una decisión de diseño: el
          CIF vive en `accounts` y la dirección en `salons`. Mientras los dos
          iban en el mismo PATCH, un permiso que faltaba en una tabla impedía
          rellenar un dato de la otra — y un CIF heredado que no pasa la
          validación bloqueaba una dirección que no tiene nada que ver con él.
          Separados, cada uno falla solo. */}
      <div className="rounded-2xl border border-border bg-card px-[26px] py-6">
        <div className="mb-4 text-[11px] font-semibold uppercase tracking-[.1em] text-faint">
          Datos fiscales de la empresa
        </div>

        {/* EL AVISO VA ARRIBA Y SE VE. Quien tiene dos locales entra por el que
            está usando; si cambia el CIF desde el segundo sin saber que cambia
            también el del primero, la siguiente factura sale mal.
            Y NO DICE «CIF»: el selector ofrece doce países y el aviso se ve
            con todos. Con Francia elegida, la etiqueta dice «Numéro SIRET /
            TVA» y el aviso no puede contradecirla. */}
        <p className="mb-5 flex items-start gap-2 rounded-[11px] border border-[#6A5B2B] bg-[rgba(201,168,76,.10)] px-3.5 py-2.5 text-[12.5px] leading-[1.5] text-[#E4CC85]">
          <Info size={14} strokeWidth={1.9} className="mt-[2px] flex-shrink-0" />
          <span>
            <strong className="font-semibold">Son de la empresa, no de este centro.</strong>{" "}
            Tu identificación fiscal es la misma para todos tus centros y es la
            que sale en tus documentos: si la cambias aquí, cambia en todos.
          </span>
        </p>

        {loading ? (
          <div className="rounded-[14px] border border-border bg-card-alt px-4 py-10 text-center text-[13.5px] text-mute">
            Cargando datos fiscales…
          </div>
        ) : (
          <>
            {/* UN SOLO CAMPO, TEXTO LIBRE. Hubo un selector de doce países y una
                regla española: se quitaron (ver `core/identificacion_fiscal.py`
                en el backend). Una lista cerrada deja fuera al primer salón de
                Andorra, y la regla solo decía si el número estaba bien
                tecleado, no si la empresa existe. Se queda lo que no es
                validar: una línea, sin recortar, y que quepa. Y obligatoria
                al guardar esta tarjeta (la factura la exige); la del local no
                la toca. */}
            <CampoLabel htmlFor="cif" obligatorio className="mb-1.5 block text-xs font-medium text-mute">
              Identificación fiscal
            </CampoLabel>
            <CampoQueCrece
              id="cif"
              tipo="texto"
              autoComplete="off"
              value={profile.cif ?? ""}
              onChange={(v) => updateField("cif", v)}
              icono={<Hash size={16} strokeWidth={1.7} />}
              invalido={Boolean(errorFiscal)}
              describedBy={errorFiscal ? "cif-error cif-ayuda" : "cif-ayuda"}
              maxLength={40}
            />
            {/* EL ERROR, BAJO SU CAMPO, como el del teléfono. Vivía en el pie:
                lejos del problema, y a 1280 px se estrujaba en seis líneas y
                empujaba el botón fuera de la tarjeta. */}
            {errorFiscal && (
              <p
                id="cif-error"
                role="alert"
                className="mt-1.5 flex items-start gap-1.5 text-[12px] leading-[1.45] text-danger"
              >
                <AlertTriangle size={13} strokeWidth={2} className="mt-[2px] flex-shrink-0" />
                <span>{errorFiscal}</span>
              </p>
            )}
            <p id="cif-ayuda" className="mt-1.5 text-[12px] leading-[1.45] text-faint">
              Es el número que aparece en tus facturas.
            </p>
            {/* EL AVISO SE QUEDA para las cuentas que ya existen sin el dato.
                Obligatoria al guardar no es echar a nadie: quien entró sin ella
                sigue usando la app y guardando su local; solo esta tarjeta no
                se guarda vacía. */}
            {!profile.cif && (
              <p className="mt-2 flex items-start gap-1.5 text-[12px] leading-[1.45] text-mute">
                <Info size={13} strokeWidth={1.9} className="mt-[2px] flex-shrink-0" />
                <span>
                  Todavía no has puesto tu identificación fiscal. La necesitas para tus
                  facturas y para los documentos que descargas desde aquí.
                </span>
              </p>
            )}
          </>
        )}

        <PieDeGuardado
          ayuda="Vale para toda la cuenta."
          etiqueta="Guardar datos fiscales"
          onGuardar={guardarDatosFiscales}
          guardando={savingFiscal}
          deshabilitado={loading}
          guardado={savedFiscal}
        />
      </div>
    </div>
  );
}
