import { describe, expect, it } from "vitest";
import type { Expense, Income } from "@/lib/api/finances";
import { movimientosCsv } from "./movimientos-csv";

const gasto = (o: Partial<Expense>): Expense => ({
  id: "g", fecha: "2026-10-02", categoria: "Luz", provider_id: null, tipo: "fijo", estado: "pagado", importe: 90.5,
  product_id: null, cantidad: null, anulado_at: null, anulado_motivo: null, providers: null, products: null, ...o,
});
const ingreso = (o: Partial<Income>): Income => ({
  id: "i", fecha: "2026-10-01", concepto: "Vale", tipo: "otro", importe: 12.5, cantidad: 1, product_id: null,
  anulado_at: null, anulado_motivo: null, products: null, ...o,
});

describe("los movimientos para Excel", () => {
  it("separa con ; y escribe la coma decimal, con el BOM delante y en orden de fecha", () => {
    const csv = movimientosCsv([gasto({})], [ingreso({})], [{ id: "c", fecha: "2026-10-03", concepto: "Corte", importe: 28 }]);
    expect(csv.startsWith("﻿Fecha;Movimiento;")).toBe(true);
    const lineas = csv.trim().split("\r\n");
    expect(lineas[1]).toBe("2026-10-01;Ingreso;Otro;Vale;;;12,50;Sí;");
    expect(lineas[2]).toBe("2026-10-02;Gasto;Fijo;Luz;;Pagado;-90,50;Sí;");
    expect(lineas[3]).toBe("2026-10-03;Ingreso;Servicio;Corte;;;28,00;Sí;");
  });

  it("lo anulado sale, marcado como que no cuenta, con su motivo entre comillas si hace falta", () => {
    const csv = movimientosCsv([gasto({ anulado_at: "2026-10-05T10:00:00Z", anulado_motivo: 'Duplicado; ver "ticket"' })], [], []);
    expect(csv).toContain(';-90,50;No;"Duplicado; ver ""ticket"""');
  });
});
