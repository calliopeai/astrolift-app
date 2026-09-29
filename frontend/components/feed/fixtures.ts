import { LONG_ARN, LONG_SHA, LONG_URL } from "@/components/run/fixtures";

export { LONG_ARN, LONG_SHA, LONG_URL };

/** One feed line, as a story or test draws it. */
export interface FeedEvent {
  id: string;
  at: string;
  actor: string;
  action: string;
  target: string;
  round: number;
}

const ACTIONS = ["deployed", "scaled", "rotated a secret on", "approved", "rolled back", "synced"];
const TARGETS = ["checkout", "billing-api", "support-bot", "nightly-sync", "storefront"];
const ACTORS = ["leo@example.com", "astrolift-bot", "eric@example.com"];

/** Fixed clock so stories and tests read the same. */
export const FEED_NOW = Date.UTC(2026, 8, 28, 15, 0);

/**
 * `count` events, newest first, 47 minutes apart from `offset` on, so a page
 * of 20 spans most of a day and grouping by day shows several headings.
 */
export function makeEvents(count: number, offset = 0, idPrefix = "e"): FeedEvent[] {
  return Array.from({ length: count }, (_, i) => {
    const n = offset + i;
    return {
      id: `${idPrefix}${n}`,
      at: new Date(FEED_NOW - n * 47 * 60_000).toISOString(),
      actor: ACTORS[n % ACTORS.length]!,
      action: ACTIONS[n % ACTIONS.length]!,
      target: TARGETS[n % TARGETS.length]!,
      round: Math.floor(n / 4) + 1,
    };
  });
}

export const LONG_EVENTS: FeedEvent[] = [
  { ...makeEvents(1)[0]!, id: "l1", target: `commit ${LONG_SHA}` },
  { ...makeEvents(1, 1)[0]!, id: "l2", target: LONG_ARN },
  { ...makeEvents(1, 2)[0]!, id: "l3", target: LONG_URL },
];
