// Minimal standard 5-field cron support (minute hour day-of-month month
// day-of-week). Enough to compute the next fire time for the schedules
// astrolift cronjob workloads use — no external dependency. cronstrue handles
// the human-readable description; this handles "when next".

type FieldMatcher = (v: number) => boolean;

const RANGES: [number, number][] = [
  [0, 59], // minute
  [0, 23], // hour
  [1, 31], // day of month
  [1, 12], // month
  [0, 6], // day of week (0 = Sunday)
];

function parseField(spec: string, min: number, max: number): FieldMatcher {
  if (spec === "*" || spec === "?") return () => true;
  const allowed = new Set<number>();
  for (const part of spec.split(",")) {
    const [rangePart, stepPart] = part.split("/");
    const step = stepPart ? parseInt(stepPart, 10) : 1;
    let lo = min;
    let hi = max;
    if (rangePart !== "*" && rangePart !== "") {
      const [a, b] = rangePart.split("-");
      lo = parseInt(a, 10);
      hi = b !== undefined ? parseInt(b, 10) : lo;
    }
    if (Number.isNaN(lo) || Number.isNaN(hi) || Number.isNaN(step) || step < 1) continue;
    for (let v = lo; v <= hi; v += step) allowed.add(v);
  }
  return (v: number) => allowed.has(v);
}

/** Parse a 5-field cron into a per-minute matcher, or null if malformed. */
export function parseCron(expr: string): ((d: Date) => boolean) | null {
  const fields = expr.trim().split(/\s+/);
  if (fields.length !== 5) return null;
  const [min, hour, dom, month, dow] = fields.map((f, i) =>
    parseField(f, RANGES[i][0], RANGES[i][1])
  );
  const domRestricted = fields[2] !== "*" && fields[2] !== "?";
  const dowRestricted = fields[4] !== "*" && fields[4] !== "?";
  return (d: Date) => {
    if (!min(d.getMinutes()) || !hour(d.getHours()) || !month(d.getMonth() + 1)) return false;
    // Standard cron: when both DOM and DOW are restricted, either may match.
    const domOk = dom(d.getDate());
    const dowOk = dow(d.getDay());
    if (domRestricted && dowRestricted) return domOk || dowOk;
    if (domRestricted) return domOk;
    if (dowRestricted) return dowOk;
    return true;
  };
}

/**
 * Next fire time at or after `from` (default now), scanning minute-by-minute up
 * to ~366 days. Returns null for a malformed expr or if nothing matches within
 * the horizon. Cannot use Date.now in workflow scripts, but this is browser code.
 */
export function nextCronRun(expr: string, from: Date = new Date()): Date | null {
  const match = parseCron(expr);
  if (!match) return null;
  const d = new Date(from.getTime());
  d.setSeconds(0, 0);
  d.setMinutes(d.getMinutes() + 1); // strictly after `from`
  const horizon = 366 * 24 * 60;
  for (let i = 0; i < horizon; i++) {
    if (match(d)) return new Date(d.getTime());
    d.setMinutes(d.getMinutes() + 1);
  }
  return null;
}
