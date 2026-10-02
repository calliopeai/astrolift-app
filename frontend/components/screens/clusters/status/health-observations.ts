/** Counts and readiness are observations, not a fallback health diagnosis. */
export function observedHealthCount(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

export function observedReadiness(ready: unknown, desired: unknown): boolean {
  return observedHealthCount(ready) && observedHealthCount(desired) && ready <= desired;
}

export function observedHealthDate(value: unknown): Date | null {
  if (typeof value !== "string" || !value) return null;
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? date : null;
}
