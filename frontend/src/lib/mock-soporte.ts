// Temas previstos para la Academia EstetixApp. Aún no hay vídeos publicados
// (la sección se muestra como placeholder "Próximamente"), por eso
// no existen duraciones, contadores de vistas ni enlaces.
export type TemaAcademia = {
  titulo: string;
  categoria: string;
  color: string;
};

export const TEMAS_ACADEMIA: TemaAcademia[] = [
  { titulo: "Crea tu primera cita en la Agenda", categoria: "Primeros pasos", color: "var(--accent)" },
  { titulo: "Configura servicios, duraciones y precios", categoria: "Catálogo", color: "#9B7BC9" },
  { titulo: "Cobrar y cerrar la caja del día", categoria: "Caja", color: "#4FA86B" },
  { titulo: "Da de alta a tu equipo y sus horarios", categoria: "Equipo", color: "#5B8DB8" },
  { titulo: "Activa la reserva online en 1 minuto", categoria: "Reservas", color: "#5B8DB8" },
  { titulo: "Controla el stock y avisa al proveedor", categoria: "Stock", color: "#D9A441" },
];
