"use client";

import * as React from "react";

import { detectPlatform, type PlatformAsset } from "./platforms";

/**
 * The visitor's platform, detected once on mount. `detectionDone` stays
 * false through SSR and first paint so the screen can hold a neutral
 * placeholder instead of jumping.
 */
export function useDetectedPlatform() {
  const [detected, setDetected] = React.useState<PlatformAsset | null>(null);
  const [detectionDone, setDetectionDone] = React.useState(false);

  React.useEffect(() => {
    let cancelled = false;
    void detectPlatform().then((p) => {
      if (cancelled) return;
      setDetected(p);
      setDetectionDone(true);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  return { detected, detectionDone };
}
