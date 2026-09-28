"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

import type { AppNode } from "../../core/app-model";
import { HEALTH_COLOR, HEALTH_GLOWS, HEALTH_LABEL, MOTION_CLASS } from "../../core/semantics";

import { EVENT_WINDOW_MS, FLASH_MS, recency } from "./layouts-b";

/**
 * Small pieces the layouts-b dashboards share. Each draws one piece of model
 * state and nothing decorative.
 */

export function tint(color: string, pct: number): string {
  return `color-mix(in oklab, ${color} ${pct}%, transparent)`;
}

/** Health as a dot; failing flickers, idle is dark. */
export function HealthDot({
  health,
  className,
}: {
  health: AppNode["health"];
  className?: string;
}) {
  const color = HEALTH_COLOR[health];
  return (
    <span
      aria-hidden
      className={cn(
        "inline-block size-2 shrink-0 rounded-full",
        health === "failing" && MOTION_CLASS.flicker,
        className
      )}
      style={{
        background: color,
        boxShadow: HEALTH_GLOWS[health] && health !== "ok" ? `0 0 6px ${color}` : undefined,
      }}
    />
  );
}

/** Utilisation 0..1 as a filled bar in the node's health colour. */
export function LoadBar({ node, label = "load" }: { node: AppNode; label?: string }) {
  const pct = Math.round(node.load * 100);
  return (
    <span
      className="bg-muted/60 relative block h-1 w-full overflow-hidden rounded-sm"
      title={`${label} ${pct}%`}
    >
      <span
        className="absolute inset-y-0 left-0 rounded-sm"
        style={{ width: `${pct}%`, background: HEALTH_COLOR[node.health] }}
      />
    </span>
  );
}

/** Enter or Space on a focusable tile selects it, as a click does. */
export function selectProps(id: string, onSelect?: (id: string) => void) {
  return {
    tabIndex: 0,
    role: "button" as const,
    onClick: () => onSelect?.(id),
    onKeyDown: (e: React.KeyboardEvent) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        onSelect?.(id);
      }
    },
  };
}

export const FOCUS_RING =
  "outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background";

/**
 * A ring that expands once when `eventId` changes: the node just got a call
 * or an invocation. Remounting by key replays the CSS animation. Under
 * reduced motion it is a still ring whose opacity is the event's recency.
 */
export function Flash({
  eventId,
  age,
  motion,
  color,
}: {
  eventId?: string;
  age: number;
  motion: "full" | "reduced";
  color: string;
}) {
  if (!eventId) return null;
  if (motion === "full" && age > FLASH_MS) return null;
  if (motion === "reduced" && age > EVENT_WINDOW_MS) return null;
  return (
    <span
      key={eventId}
      aria-hidden
      className={cn(
        "pointer-events-none absolute inset-0 rounded-sm border-2",
        motion === "full" && MOTION_CLASS.flash
      )}
      style={{
        borderColor: color,
        opacity: motion === "reduced" ? recency(age) : undefined,
      }}
    />
  );
}

/**
 * A dot that fades like phosphor over the event window. The CSS fade starts
 * once per event with a negative delay equal to its age at mount; the inline
 * opacity is the still frame used under reduced motion.
 */
export const PhosphorDot = React.memo(function PhosphorDot({
  age,
  color,
}: {
  age: number;
  color: string;
}) {
  const [delay] = React.useState(() => -age);
  return (
    <span
      aria-hidden
      className={cn("inline-block size-2 shrink-0 rounded-full", MOTION_CLASS.phosphor)}
      style={{
        background: color,
        boxShadow: `0 0 6px ${color}`,
        opacity: recency(age),
        animationDelay: `${delay}ms`,
      }}
    />
  );
});

export function healthLabel(node: AppNode): string {
  return HEALTH_LABEL[node.health];
}

export function fmtRps(rps?: number): string {
  if (rps === undefined) return "n/a";
  return rps >= 1000 ? `${(rps / 1000).toFixed(1)}k/s` : `${rps}/s`;
}
