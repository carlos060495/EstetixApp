import { adminFetch } from "./admin-http";

/** Los salones de muestra en el panel (0080). Las cifras salen de la base. */
export type PanelDemos = {
  estado: {
    vivas: number;
    ultima_hora: number;
    caducadas: number;
    vivas_max: number;
    por_hora_max: number;
    por_ip_hora_max: number;
    barrido_encendido: boolean;
    tope_por_pasada: number;
    /** De la última hora (0082): cuántas veces no se pudo abrir una, y por qué. */
    rechazos_vivas: number;
    rechazos_hora: number;
    rechazos_ip: number;
    fallos_al_crear: number;
  };
  /** Lo que borraría la próxima pasada del barrido, sin borrar nada. */
  ensayo: { account_id: string; centro: string | null; creada_at: string; caduca_at: string; entra_en_esta_pasada: boolean }[];
  cambios: { tope: TopeDeDemos; antes: number; despues: number; motivo: string; hecho_por_email: string; hecho_at: string }[];
};

export type TopeDeDemos = "vivas_max" | "por_hora_max" | "por_ip_hora_max";

export async function cargarPanelDemos(): Promise<PanelDemos> {
  const response = await adminFetch("/admin/demos");
  return (await response.json()) as PanelDemos;
}

/** Solo el owner, con motivo; queda registrado (como el mínimo de pago). */
export async function cambiarTopeDeDemos(tope: TopeDeDemos, valor: number, motivo: string): Promise<PanelDemos> {
  const response = await adminFetch(`/admin/demos/topes/${tope}`, {
    method: "PUT",
    body: JSON.stringify({ valor, motivo: motivo.trim() }),
  });
  return (await response.json()) as PanelDemos;
}
