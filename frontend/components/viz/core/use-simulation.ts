"use client";

import * as React from "react";

import { mulberry32 } from "./semantics";

/**
 * Drive a viz model forward on a timer, for stories and the settings preview.
 * Deterministic for a given seed. Paused, it returns `initial` unchanged, so
 * a story can show one frame of a live view.
 */
export function useSimulation<T>(
  initial: T,
  step: (prev: T, rng: () => number, dtMs: number) => T,
  {
    intervalMs = 1200,
    seed = 1,
    paused = false,
  }: { intervalMs?: number; seed?: number; paused?: boolean } = {}
): T {
  const [state, setState] = React.useState(initial);
  const stepRef = React.useRef(step);
  React.useEffect(() => {
    stepRef.current = step;
  });
  React.useEffect(() => {
    if (paused) return;
    const rng = mulberry32(seed);
    const id = window.setInterval(
      () => setState((prev) => stepRef.current(prev, rng, intervalMs)),
      intervalMs
    );
    return () => window.clearInterval(id);
  }, [intervalMs, seed, paused]);
  return paused ? initial : state;
}
