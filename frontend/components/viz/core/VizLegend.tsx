import { cn } from "@/lib/utils";

/**
 * What a view's motion and colour mean, drawn under every viz (spec 44 viz
 * addendum: motion must mean something, and the page says what).
 */

export type LegendGlyph = "dot" | "glow" | "ring" | "spark" | "flicker" | "flow" | "bar" | "signal";

export interface LegendItem {
  glyph: LegendGlyph;
  /** A CSS colour, normally one of HEALTH_COLOR. */
  color?: string;
  label: string;
}

export interface VizLegendProps {
  items: LegendItem[];
  motion: "full" | "reduced";
  className?: string;
}

function Glyph({ glyph, color = "var(--foreground)" }: { glyph: LegendGlyph; color?: string }) {
  return (
    <svg width="18" height="12" viewBox="0 0 18 12" aria-hidden className="shrink-0">
      {glyph === "dot" && <circle cx="9" cy="6" r="3" fill={color} />}
      {glyph === "glow" && (
        <>
          <circle cx="9" cy="6" r="5.5" fill={color} opacity="0.25" />
          <circle cx="9" cy="6" r="3" fill={color} />
        </>
      )}
      {glyph === "ring" && (
        <circle cx="9" cy="6" r="4.5" fill="none" stroke={color} strokeWidth="1.5" />
      )}
      {glyph === "spark" && (
        <path d="M9 11 L9 3 M6 5 L9 1 L12 5" fill="none" stroke={color} strokeWidth="1.5" />
      )}
      {glyph === "flicker" && (
        <>
          <circle cx="5" cy="6" r="2.5" fill={color} />
          <circle cx="13" cy="6" r="2.5" fill={color} opacity="0.35" />
        </>
      )}
      {glyph === "flow" && (
        <path d="M1 6 H17" stroke={color} strokeWidth="2" strokeDasharray="2 3" />
      )}
      {glyph === "bar" && (
        <>
          <rect x="3" y="7" width="3" height="4" fill={color} />
          <rect x="8" y="4" width="3" height="7" fill={color} />
          <rect x="13" y="1" width="3" height="10" fill={color} />
        </>
      )}
      {glyph === "signal" && (
        <>
          <rect x="6" y="1" width="6" height="10" rx="1" fill="none" stroke="var(--border)" />
          <circle cx="9" cy="6" r="2" fill={color} />
        </>
      )}
    </svg>
  );
}

export function VizLegend({ items, motion, className }: VizLegendProps) {
  return (
    <div
      className={cn(
        "text-muted-foreground flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs",
        className
      )}
      aria-label="Legend"
    >
      {items.map((item) => (
        <span key={item.label} className="inline-flex items-center gap-1.5">
          <Glyph glyph={item.glyph} color={item.color} />
          {item.label}
        </span>
      ))}
      {motion === "reduced" && <span className="italic">Reduced motion: showing still frames</span>}
    </div>
  );
}
