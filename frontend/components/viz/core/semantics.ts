/**
 * What colour and motion mean, once, for every viz style (spec 44 viz
 * addendum). A renderer never picks its own status colour: it reads these, so
 * "failing" looks failing in the orbit, the hive and the transit map alike,
 * whatever the theme accent is.
 */

export type Health = "ok" | "degraded" | "failing" | "idle";

export const HEALTH_COLOR: Record<Health, string> = {
  ok: "var(--success)",
  degraded: "var(--warning)",
  failing: "var(--danger)",
  idle: "var(--muted-foreground)",
};

export const HEALTH_LABEL: Record<Health, string> = {
  ok: "Healthy",
  degraded: "Degraded",
  failing: "Failing",
  idle: "Idle",
};

/** Glow is reserved for state worth noticing; idle things stay dark. */
export const HEALTH_GLOWS: Record<Health, boolean> = {
  ok: true,
  degraded: true,
  failing: true,
  idle: false,
};

/** The CSS classes in app/globals.css (`viz-*`); each is off under reduced motion. */
export const MOTION_CLASS = {
  /** Failing: an uneven flicker. */
  flicker: "viz-flicker",
  /** A run started: a spark rising and fading once. */
  rise: "viz-rise",
  /** A dispatch: a ring expanding once from its source. */
  ripple: "viz-ripple",
  /** An event: a dot lit at full and fading over several seconds. */
  phosphor: "viz-phosphor",
  /** Work moving along an edge; speed is set per edge with --viz-flow-duration. */
  flow: "viz-flow",
  /** An event on a card or row: its outline lit, then fading once. */
  flash: "viz-flash",
  /** Waiting: a slow breathing pulse (gates pressurizing, queued runs). */
  breathe: "viz-breathe",
} as const;

/** Map a 0..1 rate onto a flow duration: busier edges move faster. */
export function flowDuration(rate: number): string {
  const clamped = Math.max(0, Math.min(1, rate));
  return `${(2.4 - clamped * 1.9).toFixed(2)}s`;
}

/** Deterministic PRNG, so simulations and stories replay identically. */
export function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
