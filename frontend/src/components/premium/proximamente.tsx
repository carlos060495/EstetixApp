import type { LucideIcon } from "lucide-react";

type Props = {
  icon: LucideIcon;
  title: string;
  description: string;
};

/**
 * Placeholder honesto para features cuyo plan ya está incluido pero la pantalla
 * aún no existe. Mismo patrón que otros «Próximamente» del proyecto
 * (Widget online, acceso remoto): no simula nada que aún no funciona.
 */
export function Proximamente({ icon: Icon, title, description }: Props) {
  return (
    <div className="flex flex-1 items-center justify-center px-6 py-10">
      <div className="max-w-[440px] text-center">
        <div className="mx-auto mb-5 flex h-16 w-16 items-center justify-center rounded-2xl bg-gold-tenue text-gold">
          <Icon size={30} strokeWidth={1.7} />
        </div>
        <span className="inline-flex items-center rounded-full border border-gold/35 bg-gold-tenue px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[.1em] text-gold">
          Próximamente
        </span>
        <h2 className="mt-3.5 font-display text-[22px] font-semibold text-text">{title}</h2>
        <p className="mx-auto mt-2 max-w-[380px] text-[13.5px] leading-relaxed text-mute">
          {description}
        </p>
      </div>
    </div>
  );
}
