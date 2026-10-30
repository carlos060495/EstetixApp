import { apiFetch } from "./client";

export type SalonPlan = "basico" | "pro" | "elite";

export type SalonProfile = {
  nombre_comercial: string;
  cif: string | null;
  direccion: string | null;
  telefono: string | null;
  email: string | null;
  plan: SalonPlan;
  /** Puede faltar si el backend aún no expone la columna (deploy parcial). */
  accent_color?: string | null;
  /** Zona horaria IANA del centro (0060). Puede faltar con un backend viejo. */
  zona_horaria?: string;
  /** Signed URL fresca (TTL ~1 h) o null si el salón no tiene logo. */
  logo_url?: string | null;
  /**
   * Qué le pasa al teléfono que YA está guardado, si le pasa algo.
   *
   * `null` = está bien. Existe porque los números guardados antes de que esto
   * se validara siguen ahí: uno tenía un dígito de más y su botón «Llamar al
   * salón» llevaba muerto en todos los recordatorios. El texto viene escrito
   * del backend para leerse tal cual.
   */
  telefono_aviso?: string | null;
  /**
   * El mismo número, escrito para leerlo: «+34 944 12 34 56».
   *
   * En la base se guarda en E.164 —es la única forma que se marca desde
   * cualquier país— pero quien teclea «944 12 34 56» y recarga no tiene por qué
   * encontrarse una cadena pegada. `null` si no hay número o si el guardado no
   * es válido: eso NO se maquilla, se enseña tal cual.
   */
  telefono_legible?: string | null;
};

export type SalonProfilePayload = {
  nombre_comercial?: string;
  cif?: string | null;
  direccion?: string | null;
  telefono?: string | null;
  email?: string | null;
  accent_color?: string;
  zona_horaria?: string;
};

export function getSalonProfile(): Promise<SalonProfile> {
  return apiFetch<SalonProfile>("/salon/profile");
}

export function updateSalonProfile(payload: SalonProfilePayload): Promise<SalonProfile> {
  return apiFetch<SalonProfile>("/salon/profile", { method: "PATCH", body: payload });
}

export type SalonLogo = { logo_url: string };

/** Sube (o reemplaza) el logo. La validación real vive en el backend. */
export function uploadSalonLogo(file: File): Promise<SalonLogo> {
  const form = new FormData();
  form.append("file", file);
  return apiFetch<SalonLogo>("/salon/logo", { method: "POST", body: form });
}

export function deleteSalonLogo(): Promise<void> {
  return apiFetch<void>("/salon/logo", { method: "DELETE" });
}
