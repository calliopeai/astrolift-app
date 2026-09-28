import type { WorkflowLine, WorkflowTrain } from "../core/workflow-model";

/**
 * Geometry for the transit map: each workflow is a horizontal metro line in
 * its own row, with 45-degree jogs like a printed transit diagram. Pure, so
 * the renderer only draws and the positions are tested.
 */

export interface Pt {
  x: number;
  y: number;
}

export interface LineGeom {
  id: string;
  /** Station centres, in order. */
  stations: Pt[];
  /** Polyline for the segment from station `i` to `i + 1`. */
  segments: Pt[][];
}

export interface TransitLayout {
  width: number;
  height: number;
  lines: LineGeom[];
}

export const TRANSIT = {
  labelW: 132,
  dx: 116,
  rowH: 76,
  jog: 20,
  padTop: 34,
  padRight: 56,
  padBottom: 20,
} as const;

/** Rows alternate where their jog sits, so the stack reads as a diagram, not a grid. */
function jogOffset(line: number, station: number): number {
  const at = 1 + (line % 2);
  return station >= at && station < at + 2 ? TRANSIT.jog : 0;
}

/** A straight run, or horizontal, 45-degree diagonal, horizontal. */
export function segmentPath(a: Pt, b: Pt): Pt[] {
  if (a.y === b.y) return [a, b];
  const dy = Math.abs(b.y - a.y);
  const xm = (a.x + b.x) / 2;
  return [a, { x: xm - dy / 2, y: a.y }, { x: xm + dy / 2, y: b.y }, b];
}

export function layoutTransit(lines: WorkflowLine[]): TransitLayout {
  const maxStations = Math.max(2, ...lines.map((l) => l.stations.length));
  const width = TRANSIT.labelW + (maxStations - 1) * TRANSIT.dx + TRANSIT.padRight;
  const height = TRANSIT.padTop + lines.length * TRANSIT.rowH + TRANSIT.padBottom;
  return {
    width,
    height,
    lines: lines.map((l, i) => {
      const y0 = TRANSIT.padTop + i * TRANSIT.rowH + TRANSIT.rowH / 2 - TRANSIT.jog / 2;
      const stations = l.stations.map((_, j) => ({
        x: TRANSIT.labelW + j * TRANSIT.dx,
        y: y0 + jogOffset(i, j),
      }));
      const segments = stations.slice(0, -1).map((p, j) => segmentPath(p, stations[j + 1]));
      return { id: l.id, stations, segments };
    }),
  };
}

function length(poly: Pt[]): number {
  let sum = 0;
  for (let i = 1; i < poly.length; i++)
    sum += Math.hypot(poly[i].x - poly[i - 1].x, poly[i].y - poly[i - 1].y);
  return sum;
}

/** The point `f` (0..1) of the way along a polyline, and its heading in degrees. */
export function pointAlong(poly: Pt[], f: number): Pt & { angle: number } {
  let remaining = Math.max(0, Math.min(1, f)) * length(poly);
  for (let i = 1; i < poly.length; i++) {
    const a = poly[i - 1];
    const b = poly[i];
    const d = Math.hypot(b.x - a.x, b.y - a.y);
    const angle = (Math.atan2(b.y - a.y, b.x - a.x) * 180) / Math.PI;
    if (remaining <= d || i === poly.length - 1) {
      const k = d === 0 ? 0 : Math.min(1, remaining / d);
      return { x: a.x + (b.x - a.x) * k, y: a.y + (b.y - a.y) * k, angle };
    }
    remaining -= d;
  }
  return { ...poly[0], angle: 0 };
}

/** How far a held train stops short of its gate: it waits at the red signal. */
export const HOLD_SHORT = 0.2;

/**
 * A train's position in station units: `at + progress`, except a held train
 * waits just short of its gate and a finished one sits at the terminus.
 */
export function trainT(train: WorkflowTrain, stationCount: number): number {
  const last = stationCount - 1;
  if (train.state === "done") return last;
  if (train.state === "held") return Math.max(0, train.at - HOLD_SHORT);
  return Math.max(0, Math.min(last, train.at + train.progress));
}

/** Where station-unit position `t` lands on a laid-out line. */
export function positionOnLine(line: LineGeom, t: number): Pt & { angle: number } {
  const last = line.stations.length - 1;
  if (last <= 0) return { ...line.stations[0], angle: 0 };
  const clamped = Math.max(0, Math.min(last, t));
  const j = Math.min(last - 1, Math.floor(clamped));
  return pointAlong(line.segments[j], clamped - j);
}

/** Shorten a name for a fixed slot; the full name goes in a <title>. */
export function truncate(s: string, max: number): string {
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}

/**
 * Particle spacing along a segment for a 0..1 rate. Every period divides 24,
 * the viz-flow keyframe's dash offset, so the loop has no seam.
 */
export function particlePeriod(rate: number): number {
  if (rate > 0.75) return 6;
  if (rate > 0.5) return 8;
  if (rate > 0.25) return 12;
  return 24;
}
