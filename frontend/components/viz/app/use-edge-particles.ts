"use client";

import * as React from "react";

import { decodePts, pointAlong, polylineLength } from "./app-render-layout";

/**
 * Move every `[data-particle]` circle along its edge in one rAF loop, writing
 * cx/cy straight to the DOM (no React state per frame). Each particle carries
 * its path (`data-pts`), speed in px/s (`data-speed`) and phase (`data-phase`).
 * Parsed paths are cached per string so a model step does not re-measure.
 */
export function useEdgeParticles(svg: React.RefObject<SVGSVGElement | null>, active: boolean) {
  React.useEffect(() => {
    if (!active || typeof window.requestAnimationFrame !== "function") return;
    const cache = new Map<string, { pts: ReturnType<typeof decodePts>; len: number }>();
    let frame = 0;
    const start = performance.now();
    const tick = (now: number) => {
      const t = (now - start) / 1000;
      svg.current?.querySelectorAll<SVGCircleElement>("[data-particle]").forEach((el) => {
        const d = el.dataset;
        const key = d.pts ?? "";
        let path = cache.get(key);
        if (!path) {
          const pts = decodePts(key);
          path = { pts, len: Math.max(1, polylineLength(pts)) };
          cache.set(key, path);
        }
        const f = (Number(d.phase) + (t * Number(d.speed)) / path.len) % 1;
        const [x, y] = pointAlong(path.pts, f);
        el.setAttribute("cx", x.toFixed(1));
        el.setAttribute("cy", y.toFixed(1));
      });
      if (cache.size > 200) cache.clear();
      frame = window.requestAnimationFrame(tick);
    };
    frame = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(frame);
  }, [svg, active]);
}
