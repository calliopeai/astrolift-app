import type { LegendItem } from "@/components/viz/core/VizLegend";
import type { WorkflowLine, WorkflowStation } from "@/components/viz/core/workflow-model";
import { lineShape } from "@/components/workflows/definition-line";
import { cn } from "@/lib/utils";

/**
 * A workflow's stage shape as one small still picture, for a card: its
 * stages in order on a line, with a fan-out's branches, the join that merges
 * them, a supervisor's workers, a nested workflow and a retry loop each drawn
 * the way the workflow views draw them. Nothing moves: a definition has no
 * state to show. Its accessible name says the shape in words. Pure.
 */

const INK = "var(--foreground)";
const MUTED = "var(--muted-foreground)";
const CELL = 16;
const MID = 12;
/** Past this many stages the rest read "+n". */
export const GLYPH_MAX_STATIONS = 7;

/** What the glyph's marks mean, for the legend under a card grid. */
export const WORKFLOW_GLYPH_LEGEND: LegendItem[] = [
  { glyph: "dot", color: INK, label: "Stage" },
  { glyph: "signal", color: INK, label: "Human gate" },
  { glyph: "join", color: INK, label: "Fan-out, and the join that merges it" },
  { glyph: "swarm", color: INK, label: "Supervisor and its workers" },
  { glyph: "nested", color: INK, label: "Nested workflow" },
  { glyph: "siding", color: MUTED, label: "Retried on failure" },
];

function Station({ station, cx }: { station: WorkflowStation; cx: number }) {
  switch (station.kind) {
    case "gate":
      return (
        <>
          <rect
            x={cx - 3.5}
            y={MID - 4.5}
            width="7"
            height="9"
            rx="1"
            fill="var(--card)"
            stroke={INK}
          />
          <circle cx={cx} cy={MID} r="1.5" fill={INK} />
        </>
      );
    case "fanout":
      return (
        <>
          <circle cx={cx - 4} cy={MID} r="2.5" fill={INK} />
          <path
            d={`M${cx - 4} ${MID} L${cx + 5} ${MID - 6} M${cx - 4} ${MID} H${cx + 5} M${cx - 4} ${MID} L${cx + 5} ${MID + 6}`}
            stroke={INK}
            strokeWidth="1"
          />
          <circle cx={cx + 5} cy={MID - 6} r="1.25" fill={INK} />
          <circle cx={cx + 5} cy={MID + 6} r="1.25" fill={INK} />
        </>
      );
    case "join":
      return (
        <>
          <path
            d={`M${cx - 6} ${MID - 6} L${cx} ${MID} M${cx - 6} ${MID + 6} L${cx} ${MID}`}
            stroke={INK}
            strokeWidth="1"
          />
          <rect x={cx - 2.5} y={MID - 3} width="6" height="6" rx="1" fill={INK} />
        </>
      );
    case "supervisor":
      return (
        <>
          <circle cx={cx} cy={MID} r="3" fill={INK} />
          <circle cx={cx - 5} cy={MID - 6} r="1.25" fill={INK} opacity="0.7" />
          <circle cx={cx + 5} cy={MID - 6} r="1.25" fill={INK} opacity="0.7" />
          <circle cx={cx - 5} cy={MID + 6} r="1.25" fill={INK} opacity="0.7" />
          <circle cx={cx + 5} cy={MID + 6} r="1.25" fill={INK} opacity="0.7" />
        </>
      );
    case "workflow":
      return (
        <>
          <rect
            x={cx - 3}
            y={MID - 6}
            width="9"
            height="7"
            rx="1"
            fill="none"
            stroke="var(--border)"
          />
          <rect
            x={cx - 5.5}
            y={MID - 3}
            width="9"
            height="7"
            rx="1"
            fill="var(--card)"
            stroke={INK}
          />
        </>
      );
    default:
      return <circle cx={cx} cy={MID} r="3" fill={INK} />;
  }
}

export interface WorkflowGlyphProps {
  line: WorkflowLine;
  className?: string;
}

export function WorkflowGlyph({ line, className }: WorkflowGlyphProps) {
  const shown = line.stations.slice(0, GLYPH_MAX_STATIONS);
  const more = line.stations.length - shown.length;
  const label = lineShape(line);
  const width = Math.max(1, shown.length) * CELL + (more > 0 ? 18 : 0);
  const x = (i: number) => i * CELL + CELL / 2;
  return (
    <svg
      role="img"
      aria-label={label}
      width={width}
      height="24"
      viewBox={`0 0 ${width} 24`}
      className={cn("text-muted-foreground max-w-full shrink-0", className)}
    >
      <title>{label}</title>
      {shown.length === 0 ? (
        <circle cx={CELL / 2} cy={MID} r="3" fill="none" stroke={MUTED} strokeDasharray="2 2" />
      ) : (
        <>
          {shown.length > 1 && (
            <path d={`M${x(0)} ${MID} H${x(shown.length - 1)}`} stroke={MUTED} strokeWidth="1" />
          )}
          {(line.loops ?? [])
            .filter((l) => l.from < shown.length)
            .map((l) => (
              <path
                key={l.id}
                d={`M${x(l.from) - 4} ${MID - 4} C${x(l.from) - 4} 1 ${x(l.from) + 4} 1 ${x(l.from) + 4} ${MID - 4}`}
                fill="none"
                stroke={MUTED}
                strokeWidth="1.25"
              />
            ))}
          {shown.map((s, i) => (
            <Station key={s.id} station={s} cx={x(i)} />
          ))}
          {more > 0 && (
            <text
              x={shown.length * CELL + 2}
              y={MID + 3.5}
              fontSize="9"
              fill={MUTED}
              className="font-mono"
            >
              +{more}
            </text>
          )}
        </>
      )}
    </svg>
  );
}
