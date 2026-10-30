import type { Expense, Income, ServicioIngreso } from "@/lib/api/finances";

/**
 * LOS MOVIMIENTOS DEL PERIODO, PARA ABRIR EN EXCEL (29-10-2026).
 *
 * Sustituye a los tres botones de exportar que nunca se podían pulsar. PDF y
 * Email eran un trabajo entero cada uno (maquetar un informe; mandarlo desde
 * el servidor con su adjunto y su rastro); esto son los mismos datos que ya
 * están en la pantalla, en el formato que abre Excel tal cual:
 *
 *  · separador `;` y coma decimal, que es lo que Excel en español espera;
 *  · BOM al principio, para que las tildes no salgan rotas;
 *  · lo anulado SALE, marcado, y con «Cuenta: No»: así una suma por esa
 *    columna da lo mismo que la pantalla, y nada desaparece del archivo.
 */

const CABECERA = [
  "Fecha", "Movimiento", "Tipo", "Concepto", "Proveedor", "Estado", "Importe (€)", "Cuenta", "Motivo de la anulación",
];

function celda(v: string): string {
  return /[";\n\r]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v;
}

function euros(n: number): string {
  return n.toFixed(2).replace(".", ",");
}

export function movimientosCsv(gastos: Expense[], ingresos: Income[], servicios: ServicioIngreso[]): string {
  const filas: { fecha: string; c: string[] }[] = [
    ...gastos.map((g) => ({
      fecha: g.fecha,
      c: [
        g.fecha, "Gasto", g.tipo === "fijo" ? "Fijo" : "Variable", g.categoria, g.providers?.nombre ?? "",
        g.estado === "pendiente" ? "Pendiente" : "Pagado", euros(-g.importe), g.anulado_at ? "No" : "Sí",
        g.anulado_motivo ?? "",
      ],
    })),
    ...ingresos.map((i) => ({
      fecha: i.fecha,
      c: [
        i.fecha, "Ingreso", i.tipo === "producto" ? "Producto" : "Otro",
        i.cantidad > 1 ? `${i.concepto} ×${i.cantidad}` : i.concepto, "", "", euros(i.importe),
        i.anulado_at ? "No" : "Sí", i.anulado_motivo ?? "",
      ],
    })),
    ...servicios.map((s) => ({
      fecha: s.fecha,
      c: [s.fecha, "Ingreso", "Servicio", s.concepto, "", "", euros(s.importe), "Sí", ""],
    })),
  ];
  filas.sort((a, b) => a.fecha.localeCompare(b.fecha));
  const lineas = [CABECERA, ...filas.map((f) => f.c)].map((l) => l.map(celda).join(";"));
  return "﻿" + lineas.join("\r\n") + "\r\n";
}
