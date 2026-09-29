"use client";

import * as React from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { HEALTH_COLOR, MOTION_CLASS } from "../core/semantics";
import type { LegendItem } from "../core/VizLegend";
import type { GateState } from "../core/workflow-model";

/**
 * An approval gate drawn as an airlock (spec 44 viz addendum). Waiting: the
 * doors are shut and the gauge breathes, filling with how long the run has
 * been held. Approved: the doors slide open under a lit success light.
 * Denied: the doors seal under a danger light and the booth shakes once, at
 * the moment of the decision. Reduced motion: the same doors, gauge and
 * lights, drawn still; the shake and the slide are skipped.
 */

export interface AirlockGateProps {
  state: GateState;
  /** When the run arrived at the gate (epoch ms). */
  waitingSince?: number;
  /** Who approved or denied it. */
  decidedBy?: string;
  onApprove?: () => void;
  onDeny?: () => void;
  /** The clock the wait is measured against; defaults to a ticking Date.now(). */
  now?: number;
  motion?: "full" | "reduced";
  className?: string;
}

export const AIRLOCK_LEGEND: LegendItem[] = [
  {
    glyph: "signal",
    color: HEALTH_COLOR.degraded,
    label: "Waiting for approval: gauge breathes and fills with time held",
  },
  { glyph: "glow", color: HEALTH_COLOR.ok, label: "Approved: doors open" },
  { glyph: "flicker", color: HEALTH_COLOR.failing, label: "Denied: sealed, shakes once" },
];

/** The gauge reads full once a run has been held this long. */
const PRESSURE_FULL_MS = 15 * 60_000;

export function formatWait(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${String(s % 60).padStart(2, "0")}s`;
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`;
}

function useClock(active: boolean, fixed: number | undefined): number {
  const [now, setNow] = React.useState(() => fixed ?? Date.now());
  React.useEffect(() => {
    if (!active || fixed !== undefined) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [active, fixed]);
  return fixed ?? now;
}

function prefersReduced(el: Element | null): boolean {
  if (!el) return true;
  if (el.closest('[data-motion="reduced"]')) return true;
  return typeof window.matchMedia === "function"
    ? window.matchMedia("(prefers-reduced-motion: reduce)").matches
    : false;
}

const DOOR_FILL = "color-mix(in oklab, var(--muted-foreground) 28%, var(--card))";

export function AirlockGate({
  state,
  waitingSince,
  decidedBy,
  onApprove,
  onDeny,
  now: fixedNow,
  motion = "full",
  className,
}: AirlockGateProps) {
  const clipId = React.useId();
  const booth = React.useRef<SVGGElement>(null);
  const prev = React.useRef(state);
  const now = useClock(state === "waiting" && waitingSince !== undefined, fixedNow);

  // The shake is the denial event itself: once, when the state turns denied.
  React.useEffect(() => {
    const was = prev.current;
    prev.current = state;
    const el = booth.current;
    if (state !== "denied" || was === "denied" || motion === "reduced" || !el) return;
    if (prefersReduced(el) || typeof el.animate !== "function") return;
    el.animate(
      [
        { transform: "translateX(0)" },
        { transform: "translateX(-4px)" },
        { transform: "translateX(4px)" },
        { transform: "translateX(-2px)" },
        { transform: "translateX(0)" },
      ],
      { duration: 360, easing: "ease-out" }
    );
  }, [state, motion]);

  const waited = waitingSince !== undefined ? now - waitingSince : undefined;
  const pressure =
    state === "approved"
      ? 1
      : state === "denied"
        ? 0
        : waited !== undefined
          ? Math.min(1, Math.max(0.04, waited / PRESSURE_FULL_MS))
          : 0.5;
  const color =
    state === "approved"
      ? HEALTH_COLOR.ok
      : state === "denied"
        ? HEALTH_COLOR.failing
        : HEALTH_COLOR.degraded;
  const open = state === "approved";
  // The slide is the approval event; any reduced ancestor (or the OS) stills it.
  const slide =
    "transition-transform duration-700 ease-out in-data-[motion=reduced]:transition-none motion-reduce:transition-none";

  const caption =
    state === "waiting"
      ? waited !== undefined
        ? `WAIT ${formatWait(waited)}`
        : "WAITING"
      : `${state === "approved" ? "OPEN" : "DENIED"}${decidedBy ? ` · ${decidedBy}` : ""}`;
  const shortCaption = caption.length > 24 ? `${caption.slice(0, 23)}…` : caption;
  const label =
    state === "waiting"
      ? `Gate waiting for approval${waited !== undefined ? `, held ${formatWait(waited)}` : ""}`
      : `Gate ${state}${decidedBy ? ` by ${decidedBy}` : ""}`;
  const showActions = state === "waiting" && (onApprove || onDeny);

  return (
    <div
      data-motion={motion}
      role="group"
      aria-label={label}
      className={cn("inline-flex w-40 flex-col items-center gap-1.5", className)}
    >
      <svg viewBox="0 0 160 120" role="img" aria-label={label} className="h-auto w-full">
        <g ref={booth}>
          {/* Pressure gauge: breathes while waiting, fill = time held. */}
          <path
            d="M68 22 A12 12 0 0 1 92 22"
            fill="none"
            stroke="var(--border)"
            strokeWidth="4"
            strokeLinecap="round"
          />
          <path
            d="M68 22 A12 12 0 0 1 92 22"
            pathLength={1}
            fill="none"
            stroke={color}
            strokeWidth="4"
            strokeLinecap="round"
            strokeDasharray={`${pressure.toFixed(3)} 1`}
            className={state === "waiting" ? MOTION_CLASS.breathe : undefined}
          />
          <rect
            x="20"
            y="28"
            width="120"
            height="72"
            rx="2"
            fill="var(--card)"
            stroke="var(--border)"
          />
          <clipPath id={clipId}>
            <rect x="34" y="40" width="92" height="52" />
          </clipPath>
          <rect
            x="34"
            y="40"
            width="92"
            height="52"
            fill={open ? `color-mix(in oklab, ${HEALTH_COLOR.ok} 14%, var(--card))` : "var(--card)"}
          />
          <g clipPath={`url(#${clipId})`}>
            <rect
              x="34"
              y="40"
              width="46"
              height="52"
              fill={DOOR_FILL}
              stroke="var(--border)"
              style={{ transform: open ? "translateX(-42px)" : "none" }}
              className={slide}
            />
            <rect
              x="80"
              y="40"
              width="46"
              height="52"
              fill={DOOR_FILL}
              stroke="var(--border)"
              style={{ transform: open ? "translateX(42px)" : "none" }}
              className={slide}
            />
            {state === "denied" && (
              <>
                <rect x="34" y="52" width="92" height="4" fill={HEALTH_COLOR.failing} />
                <rect x="34" y="76" width="92" height="4" fill={HEALTH_COLOR.failing} />
              </>
            )}
          </g>
          <rect x="34" y="40" width="92" height="52" fill="none" stroke="var(--border)" />
          {/* Status light: glows only once decided; dim while waiting. */}
          {state !== "waiting" && <circle cx="130" cy="34" r="5" fill={color} opacity="0.3" />}
          <circle
            cx="130"
            cy="34"
            r="2.5"
            fill={color}
            className={state === "waiting" ? MOTION_CLASS.breathe : undefined}
          />
          <text
            x="80"
            y="114"
            textAnchor="middle"
            className="fill-muted-foreground font-mono"
            fontSize="10"
          >
            <title>{caption}</title>
            {shortCaption}
          </text>
        </g>
      </svg>
      {showActions && (
        <div className="flex gap-1.5">
          {onApprove && (
            <Button type="button" size="xs" variant="outline" onClick={onApprove}>
              Approve
            </Button>
          )}
          {onDeny && (
            <Button type="button" size="xs" variant="destructive" onClick={onDeny}>
              Deny
            </Button>
          )}
        </div>
      )}
    </div>
  );
}
