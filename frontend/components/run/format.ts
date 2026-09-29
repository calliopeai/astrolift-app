/** Durations and log timestamps for the run view (spec 44 §5.5). */

/** A step's duration on the timeline: `0:12`, `1:40`, `1:02:03`. */
export function formatClock(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = String(total % 60).padStart(2, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${m}:${s}`;
}

/** A run's elapsed time in the header: `850ms`, `42s`, `4m 12s`, `2h 5m`. */
export function formatElapsed(ms: number): string {
  if (ms < 1000) return `${Math.max(0, Math.round(ms))}ms`;
  const total = Math.floor(ms / 1000);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

/** A log line's time of day in UTC, `12:01:04`; the full instant goes in the title. */
export function formatLogTime(ts: string | number): { short: string; full: string } {
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return { short: String(ts), full: String(ts) };
  const full = d.toISOString();
  return { short: full.slice(11, 19), full };
}
