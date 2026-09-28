/**
 * Isometric projection helpers for the isometric fleet, workflow and app
 * views. World units: x runs down-right, y down-left, z up. Pure SVG, no 3D.
 */

const COS = Math.cos(Math.PI / 6);
const SIN = 0.5;

export type Point = [number, number];

/** Project a world point to screen space at `unit` pixels per world unit. */
export function iso(x: number, y: number, z: number, unit: number): Point {
  return [(x - y) * COS * unit, (x + y) * SIN * unit - z * unit];
}

export function points(pts: Point[]): string {
  return pts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
}

export interface IsoBoxFaces {
  top: string;
  /** The face toward the viewer's left (y = y1). */
  left: string;
  /** The face toward the viewer's right (x = x1). */
  right: string;
}

/** The three visible faces of a box, as SVG polygon point lists. */
export function isoBox(
  x: number,
  y: number,
  w: number,
  d: number,
  h: number,
  unit: number,
  z = 0
): IsoBoxFaces {
  const p = (px: number, py: number, pz: number) => iso(px, py, pz, unit);
  const x1 = x + w;
  const y1 = y + d;
  const top = z + h;
  return {
    top: points([p(x, y, top), p(x1, y, top), p(x1, y1, top), p(x, y1, top)]),
    left: points([p(x, y1, z), p(x1, y1, z), p(x1, y1, top), p(x, y1, top)]),
    right: points([p(x1, y, z), p(x1, y1, z), p(x1, y1, top), p(x1, y, top)]),
  };
}

/** Painter's order: draw far things first. */
export function depth(x: number, y: number, z = 0): number {
  return x + y + z * 0.01;
}

/** Side faces are the top colour darkened, so shape reads without new hues. */
export function shade(color: string, amount: number): string {
  return `color-mix(in oklab, ${color} ${Math.round((1 - amount) * 100)}%, black)`;
}
