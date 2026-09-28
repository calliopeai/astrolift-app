"use client";

import { HEALTH_COLOR } from "../core/semantics";

import { STAGE_HEALTH, formatClock, formatDuration, stageIndexAt, type PlacedRun } from "./replay";

/**
 * Export a run replay as a GIF or a WebM video. The on-screen replay is HTML,
 * so frames are painted here from the same placed run onto an offscreen
 * canvas: the export shows exactly the replay's states (stages lighting as the
 * playhead enters, settling to their outcome), time-lapsed to `seconds`.
 */

export interface ExportOptions {
  label: string;
  width?: number;
  height?: number;
  /** Length of the exported time-lapse. */
  seconds?: number;
  fps?: number;
}

/** Playhead positions for each frame, ending on the finished run. */
export function frameTimes(total: number, seconds: number, fps: number): number[] {
  const n = Math.max(2, Math.round(seconds * fps));
  return Array.from({ length: n }, (_, i) => (total * i) / (n - 1));
}

export type ExportPalette = Record<
  "bg" | "fg" | "muted" | "border" | "ok" | "degraded" | "failing" | "idle",
  string
>;

/** Resolve theme tokens to colours a canvas understands, from where the replay is mounted. */
export function readPalette(el: Element): ExportPalette {
  const probe = document.createElement("span");
  el.appendChild(probe);
  const resolve = (css: string) => {
    probe.style.color = css;
    return getComputedStyle(probe).color;
  };
  const palette: ExportPalette = {
    bg: resolve("var(--card)"),
    fg: resolve("var(--foreground)"),
    muted: resolve("var(--muted-foreground)"),
    border: resolve("var(--border)"),
    ok: resolve(HEALTH_COLOR.ok),
    degraded: resolve(HEALTH_COLOR.degraded),
    failing: resolve(HEALTH_COLOR.failing),
    idle: resolve(HEALTH_COLOR.idle),
  };
  probe.remove();
  return palette;
}

const MONO = 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace';

/** Paint one replay frame at playhead `t`. */
export function drawFrame(
  ctx: CanvasRenderingContext2D,
  run: PlacedRun,
  t: number,
  colors: ExportPalette,
  label: string
) {
  const { width: w, height: h } = ctx.canvas;
  const pad = 24;
  const trackY = 70;
  const trackH = 64;
  const trackW = w - pad * 2;
  const x = (ms: number) => pad + (run.total > 0 ? (ms / run.total) * trackW : 0);

  ctx.fillStyle = colors.bg;
  ctx.fillRect(0, 0, w, h);

  ctx.fillStyle = colors.fg;
  ctx.font = `600 16px system-ui, sans-serif`;
  ctx.textBaseline = "alphabetic";
  ctx.fillText(label, pad, 34);
  ctx.font = `13px ${MONO}`;
  ctx.fillStyle = colors.muted;
  const clock = `${formatClock(t)} / ${formatClock(run.total)}`;
  ctx.fillText(clock, w - pad - ctx.measureText(clock).width, 34);

  const idx = stageIndexAt(run, t);
  run.stages.forEach((p, i) => {
    const x0 = x(p.start);
    const width = Math.max(2, x(p.end) - x0);
    const settled = i < idx || (i === idx && t >= p.end);
    const active = i === idx && !settled;
    const skipped = p.stage.status === "skipped";
    const outcome = colors[STAGE_HEALTH[p.stage.status]];
    const live = p.stage.kind === "gate" ? colors.degraded : colors.ok;
    const color = active ? live : settled ? outcome : colors.muted;

    ctx.globalAlpha = skipped ? 0 : active ? 0.2 : settled ? 0.28 : 0.08;
    ctx.fillStyle = color;
    ctx.fillRect(x0, trackY, width, trackH);
    ctx.globalAlpha = 1;
    ctx.strokeStyle = active || settled ? color : colors.border;
    ctx.setLineDash(skipped ? [4, 3] : []);
    ctx.lineWidth = 1;
    ctx.strokeRect(x0 + 0.5, trackY + 0.5, width - 1, trackH - 1);
    ctx.setLineDash([]);

    if (width > 40) {
      ctx.save();
      ctx.beginPath();
      ctx.rect(x0 + 4, trackY, width - 8, trackH);
      ctx.clip();
      ctx.fillStyle = active || settled ? colors.fg : colors.muted;
      ctx.font = `${p.stage.kind === "gate" ? 600 : 400} 12px system-ui, sans-serif`;
      ctx.fillText(p.stage.name, x0 + 6, trackY + 26);
      if (active || settled) {
        ctx.fillStyle = colors.muted;
        ctx.font = `11px ${MONO}`;
        ctx.fillText(formatDuration(Math.min(t, p.end) - p.start), x0 + 6, trackY + 44);
      }
      ctx.restore();
    }
  });

  // The playhead: the one moving thing, because replay time is passing.
  const hx = Math.round(x(t)) + 0.5;
  ctx.strokeStyle = colors.fg;
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(hx, trackY - 6);
  ctx.lineTo(hx, trackY + trackH + 6);
  ctx.stroke();
  ctx.fillStyle = colors.fg;
  ctx.beginPath();
  ctx.arc(hx, trackY - 6, 4, 0, Math.PI * 2);
  ctx.fill();

  const active = idx >= 0 ? run.stages[idx] : null;
  ctx.font = `12px system-ui, sans-serif`;
  ctx.fillStyle = colors.muted;
  ctx.fillText(
    active
      ? `${active.stage.name} · ${t >= active.end ? active.stage.status : "running"}`
      : "Queued",
    pad,
    h - 20
  );
}

function canvasFor(width: number, height: number): CanvasRenderingContext2D {
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("This browser cannot draw the export (no 2D canvas).");
  return ctx;
}

export async function exportGif(
  run: PlacedRun,
  colors: ExportPalette,
  { label, width = 720, height = 180, seconds = 6, fps = 12 }: ExportOptions
): Promise<Blob> {
  const { GIFEncoder, quantize, applyPalette } = await import("gifenc");
  const ctx = canvasFor(width, height);
  const gif = GIFEncoder();
  const times = frameTimes(run.total, seconds, fps);
  times.forEach((t, i) => {
    drawFrame(ctx, run, t, colors, label);
    const { data } = ctx.getImageData(0, 0, width, height);
    const palette = quantize(data, 256);
    // Hold the finished picture for a beat before the loop restarts.
    const delay = i === times.length - 1 ? 1500 : Math.round(1000 / fps);
    gif.writeFrame(applyPalette(data, palette), width, height, { palette, delay });
  });
  gif.finish();
  return new Blob([gif.bytes() as BlobPart], { type: "image/gif" });
}

export function canExportVideo(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.MediaRecorder === "function" &&
    typeof HTMLCanvasElement.prototype.captureStream === "function"
  );
}

export async function exportVideo(
  run: PlacedRun,
  colors: ExportPalette,
  { label, width = 1280, height = 320, seconds = 6, fps = 30 }: ExportOptions
): Promise<Blob> {
  if (!canExportVideo()) throw new Error("This browser cannot record video.");
  const ctx = canvasFor(width, height);
  const mime = ["video/webm;codecs=vp9", "video/webm;codecs=vp8", "video/webm"].find((m) =>
    MediaRecorder.isTypeSupported(m)
  );
  const recorder = new MediaRecorder(
    ctx.canvas.captureStream(fps),
    mime ? { mimeType: mime } : undefined
  );
  const chunks: Blob[] = [];
  recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);
  const stopped = new Promise<void>((resolve) => (recorder.onstop = () => resolve()));
  const times = frameTimes(run.total, seconds, fps);
  drawFrame(ctx, run, 0, colors, label);
  recorder.start();
  // Recording is real time: paint one frame per tick, then hold the end.
  await new Promise<void>((resolve) => {
    let i = 0;
    const id = window.setInterval(() => {
      drawFrame(ctx, run, times[Math.min(i, times.length - 1)], colors, label);
      i += 1;
      if (i >= times.length + fps) {
        window.clearInterval(id);
        resolve();
      }
    }, 1000 / fps);
  });
  recorder.stop();
  await stopped;
  return new Blob(chunks, { type: mime ?? "video/webm" });
}

export function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
