import { adminFetch } from "./admin-http";

export type AdminMe = {
  id: string;
  email: string;
  nombre: string;
  rol: "owner" | "support" | "viewer";
  ultimo_acceso: string | null;
};

export async function adminLogin(payload: {
  email: string;
  password: string;
  totp_code: string;
}): Promise<void> {
  await adminFetch("/admin/login", { method: "POST", body: JSON.stringify(payload) });
}

export async function adminMe(): Promise<AdminMe> {
  const response = await adminFetch("/admin/me");
  return (await response.json()) as AdminMe;
}

export async function adminLogout(): Promise<void> {
  await adminFetch("/admin/logout", { method: "POST" });
}
