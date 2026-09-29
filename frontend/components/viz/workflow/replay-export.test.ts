import { describe, expect, it, vi } from "vitest";

import { makeFeatureTimeline } from "../core/workflow-model";

import { makeFanoutTimeline, placeRun } from "./replay";
import { drawFrame, exportHeight, frameTimes, type ExportPalette } from "./replay-export";

describe("frameTimes", () => {
  it("spans the run from start to finish at the requested rate", () => {
    const t = frameTimes(60_000, 2, 5);
    expect(t).toHaveLength(10);
    expect(t[0]).toBe(0);
    expect(t[t.length - 1]).toBe(60_000);
    expect(t.every((v, i) => i === 0 || v > t[i - 1])).toBe(true);
  });

  it("always has a first and a last frame", () => {
    expect(frameTimes(1000, 0, 12)).toEqual([0, 1000]);
  });
});

describe("exportHeight", () => {
  it("makes room for a fan-out's parallel bars and nothing else", () => {
    expect(exportHeight(placeRun(makeFeatureTimeline()), 180)).toBe(180);
    expect(exportHeight(placeRun(makeFanoutTimeline()), 180)).toBe(180 + 6 + 6 * 10);
  });
});

describe("drawFrame", () => {
  it("draws the round band, the loop marks and the branch bars", () => {
    const texts: string[] = [];
    const ctx = new Proxy(
      { canvas: { width: 720, height: 246 } } as Record<string | symbol, unknown>,
      {
        get(target, key) {
          if (key in target) return target[key];
          if (key === "fillText") return (t: string) => texts.push(t);
          if (key === "measureText") return () => ({ width: 40 });
          return vi.fn();
        },
        set(target, key, value) {
          target[key] = value;
          return true;
        },
      }
    ) as unknown as CanvasRenderingContext2D;
    const colors = Object.fromEntries(
      ["bg", "fg", "muted", "border", "ok", "degraded", "failing", "idle"].map((k) => [k, k])
    ) as ExportPalette;
    const rounds = placeRun(makeFeatureTimeline());
    drawFrame(ctx, rounds, rounds.total, colors, "Feature");
    expect(texts).toEqual(expect.arrayContaining(["Round 1", "Round 4"]));
    expect(texts.at(-1)).toBe("Round 4 · Deploy · succeeded");
    const fan = placeRun(makeFanoutTimeline());
    texts.length = 0;
    const rects: number[] = [];
    (ctx as unknown as Record<string, unknown>).fillRect = (_x: number, y: number) => rects.push(y);
    drawFrame(ctx, fan, fan.total / 2, colors, "Research");
    // Six branch bars under the 64px track at y 70.
    expect(rects.filter((y) => y >= 140)).toHaveLength(6);
  });
});
