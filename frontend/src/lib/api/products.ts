import { apiFetch } from "./client";
import type { Expense } from "./finances";

/** Espejo de ProductOut (backend/app/schemas/products.py). */
export type Product = {
  id: string;
  nombre: string;
  marca: string | null;
  codigo_barras: string | null;
  category_id: string | null;
  /** Precio de venta; null = producto sin precio (p. ej. solo consumo interno). */
  precio: number | null;
  /** Coste de adquisición unitario; null = desconocido. Base del margen. */
  precio_coste: number | null;
  stock: number;
  stock_min: number;
  venta_publico: boolean;
  consumo_interno: boolean;
};

/** Campos que el frontend puede enviar (ProductCreate/Update); salon_id sale del JWT. */
export type ProductPayload = {
  nombre?: string;
  marca?: string;
  codigo_barras?: string;
  category_id?: string;
  precio?: number;
  precio_coste?: number | null;
  stock?: number;
  stock_min?: number;
  venta_publico?: boolean;
  consumo_interno?: boolean;
};

/** Cuerpo de POST /products/{id}/reposicion. */
export type ReposicionPayload = {
  cantidad: number;
  coste_unitario: number;
  provider_id?: string;
};

/** Motivos de salida de stock (sin impacto financiero). */
export type StockSalidaMotivo = "consumo_interno" | "merma_caducado" | "correccion";

/** Cuerpo de POST /products/{id}/salida. */
export type SalidaPayload = {
  cantidad: number;
  motivo: StockSalidaMotivo;
};

/** Espejo de ReposicionOut: producto ya actualizado + gasto registrado. */
export type Reposicion = {
  producto: Product;
  gasto: Expense;
};

/** Espejo de ProviderOut (backend/app/schemas/providers.py). */
export type Provider = {
  id: string;
  nombre: string;
  categoria: string | null;
  telefono: string | null;
  email: string | null;
  notas: string | null;
  created_at: string;
};

export type ProviderPayload = {
  nombre?: string;
  categoria?: string;
  telefono?: string;
  email?: string;
  notas?: string;
};

export function listProducts(
  params: { limit?: number; offset?: number; q?: string } = {}
): Promise<Product[]> {
  return apiFetch<Product[]>("/products", {
    query: { limit: params.limit, offset: params.offset, q: params.q },
  });
}

export function createProduct(payload: ProductPayload & { nombre: string }): Promise<Product> {
  return apiFetch<Product>("/products", { method: "POST", body: payload });
}

export function updateProduct(id: string, payload: ProductPayload): Promise<Product> {
  return apiFetch<Product>(`/products/${id}`, { method: "PATCH", body: payload });
}

export function deleteProduct(id: string): Promise<void> {
  return apiFetch<void>(`/products/${id}`, { method: "DELETE" });
}

/**
 * Compra/reposición: incrementa stock y registra el gasto en Finanzas de forma
 * atómica (si el gasto falla, el backend revierte el stock).
 */
export function registrarReposicion(
  id: string,
  payload: ReposicionPayload
): Promise<Reposicion> {
  return apiFetch<Reposicion>(`/products/${id}/reposicion`, { method: "POST", body: payload });
}

/**
 * Salida de stock por consumo / merma / corrección: baja el stock, SIN crear
 * ingreso ni gasto (el coste ya se contabilizó al comprar).
 */
export function registrarSalida(id: string, payload: SalidaPayload): Promise<Product> {
  return apiFetch<Product>(`/products/${id}/salida`, { method: "POST", body: payload });
}

export function listProviders(): Promise<Provider[]> {
  return apiFetch<Provider[]>("/providers");
}

export function createProvider(payload: ProviderPayload & { nombre: string }): Promise<Provider> {
  return apiFetch<Provider>("/providers", { method: "POST", body: payload });
}

export function updateProvider(id: string, payload: ProviderPayload): Promise<Provider> {
  return apiFetch<Provider>(`/providers/${id}`, { method: "PATCH", body: payload });
}

export function deleteProvider(id: string): Promise<void> {
  return apiFetch<void>(`/providers/${id}`, { method: "DELETE" });
}
