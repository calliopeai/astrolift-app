/**
 * The one status colour map (#2126, spec 35 §A.1).
 *
 * `StatusDot`, `DeploymentStatusPill` and `RunStatusBadge` each carried their
 * own, and two of them coloured "ok" with `--brand-primary`, which is the
 * selectable accent: with the copper or ice accent a healthy deployment turned
 * copper or ice. Status is semantic and never follows the accent (the design
 * amendment of 2026-08-21), so every tone here is a status token.
 */

export type StatusTone = "ok" | "warn" | "error" | "info" | "muted";

/** A dot: the one place, with focus rings, the brand permits a glow. */
export const DOT_TONE: Record<StatusTone, string> = {
  ok: "bg-success shadow-success/50",
  warn: "bg-warning shadow-warning/50",
  error: "bg-danger shadow-danger/50",
  info: "bg-info shadow-info/50",
  muted: "bg-muted-foreground shadow-muted-foreground/40",
};

/** A pill: tinted ground, readable text, hairline border. */
export const PILL_TONE: Record<StatusTone, string> = {
  ok: "bg-success/15 text-success-fg border-success-border",
  warn: "bg-warning/15 text-warning-fg border-warning-border",
  error: "bg-danger/15 text-danger-fg border-danger-border",
  info: "bg-info/15 text-info-fg border-info-border",
  muted: "bg-foreground/5 text-muted-foreground border-border",
};

/** In-flight states pulse; nothing else moves. */
export const IN_FLIGHT = "animate-pulse motion-reduce:animate-none";
