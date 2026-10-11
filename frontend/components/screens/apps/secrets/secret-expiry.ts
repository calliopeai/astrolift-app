const DAY_MS = 1000 * 60 * 60 * 24;

export type SecretExpiry =
  | { kind: "expired"; days: number }
  | { kind: "expiredToday" }
  | { kind: "remaining"; days: number };

/**
 * Whole days since expiry, or days remaining rounded up: a key that lapsed
 * hours ago expired today, and one expiring within 24 hours has 1 day left.
 */
export function secretExpiry(expiresAt: string, now: number): SecretExpiry | null {
  const ms = new Date(expiresAt).getTime() - now;
  if (!Number.isFinite(ms)) return null;
  if (ms < 0) {
    const days = Math.floor(-ms / DAY_MS);
    return days === 0 ? { kind: "expiredToday" } : { kind: "expired", days };
  }
  return { kind: "remaining", days: Math.ceil(ms / DAY_MS) };
}
