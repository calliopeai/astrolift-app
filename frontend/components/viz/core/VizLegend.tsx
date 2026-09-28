import { cn } from "@/lib/utils";

import { HEALTH_COLOR } from "./semantics";

/**
 * What a view's motion and colour mean, drawn under every viz (spec 44 viz
 * addendum: motion must mean something, and the page says what).
 */

export type LegendGlyph =
  | "dot"
  | "glow"
  | "ring"
  | "spark"
  | "flicker"
  | "flow"
  | "bar"
  | "signal"
  | "return"
  | "round"
  | "siding"
  | "join"
  | "swarm"
  | "nested";

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
      {glyph === "return" && (
        <>
          <path d="M2 9 H16" stroke="var(--border)" strokeWidth="1.5" />
          <path d="M14 8 C14 1 4 1 4 7" fill="none" stroke={color} strokeWidth="1.5" />
          <path d="M2 5 L4 8 L6 5" fill="none" stroke={color} strokeWidth="1.5" />
        </>
      )}
      {glyph === "round" && (
        <>
          <rect x="2" y="1.5" width="14" height="9" rx="4.5" fill="none" stroke={color} />
          <text x="9" y="8.5" textAnchor="middle" fontSize="7" fill={color} className="font-mono">
            2
          </text>
        </>
      )}
      {glyph === "siding" && (
        <>
          <path d="M1 9 H17" stroke="var(--border)" strokeWidth="1.5" />
          <path d="M5 9 C5 3 13 3 13 9" fill="none" stroke={color} strokeWidth="1.5" />
          <circle cx="9" cy="9" r="1.75" fill={color} />
        </>
      )}
      {glyph === "join" && (
        <>
          <path d="M1 2 L11 6 M1 6 H11 M1 10 L11 6" stroke={color} strokeWidth="1" />
          <rect x="11" y="3" width="6" height="6" rx="1" fill={color} />
        </>
      )}
      {glyph === "swarm" && (
        <>
          <circle cx="9" cy="6" r="2.5" fill={color} />
          <circle cx="3" cy="3" r="1.25" fill={color} opacity="0.7" />
          <circle cx="15" cy="4" r="1.25" fill={color} opacity="0.7" />
          <circle cx="4" cy="10" r="1.25" fill={color} opacity="0.7" />
          <circle cx="14" cy="10" r="1.25" fill={color} opacity="0.35" />
        </>
      )}
      {glyph === "nested" && (
        <>
          <rect x="5" y="1" width="11" height="7" rx="1" fill="none" stroke="var(--border)" />
          <rect x="2" y="4" width="11" height="7" rx="1" fill="var(--card)" stroke={color} />
        </>
      )}
    </svg>
  );
}

/**
 * The workflow shape encodings (loops, rounds, fanouts, supervisors, nested
 * workflows), for the views that draw them to include in their legend.
 */
export const WORKFLOW_SHAPE_LEGEND = {
  returnTrack: {
    glyph: "return",
    color: HEALTH_COLOR.degraded,
    label: "Return track: work sent back to an earlier stage, labelled with its round bound",
  },
  roundBadge: {
    glyph: "round",
    color: HEALTH_COLOR.ok,
    label: "Round badge: which round a run is on",
  },
  roundNearBound: {
    glyph: "round",
    color: HEALTH_COLOR.degraded,
    label: "Round badge amber: one round from the bound; past it the run fails",
  },
  siding: {
    glyph: "siding",
    color: HEALTH_COLOR.degraded,
    label: "Siding: a retry of the same stage, labelled with its attempt bound",
  },
  join: {
    glyph: "join",
    color: HEALTH_COLOR.ok,
    label: "Join: parallel branches merge once every branch has settled",
  },
  supervisorSwarm: {
    glyph: "swarm",
    color: HEALTH_COLOR.ok,
    label: "Supervisor swarm: its workers, lit while busy on a sub-task",
  },
  nestedStack: {
    glyph: "nested",
    color: HEALTH_COLOR.ok,
    label: "Nested stack: a stage that runs a whole child workflow",
  },
} satisfies Record<string, LegendItem>;

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
