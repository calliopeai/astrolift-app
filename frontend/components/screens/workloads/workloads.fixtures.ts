import type { SortState } from "@/components/data-table";
import { ARN200, LONG_URL, SHA64 } from "@/components/screens/agents/skills/agent-skills.fixtures";
import { lower, selectPage } from "@/components/screens/agents/skills/catalog";
import { workload } from "@/components/screens/apps/workloads/app-workloads.fixtures";
import type { AstroliftWorkload } from "@/graphql/registry/registry.types";

/** Hand-typed fixtures for Agents › Workloads and Functions. */

/** A workload as the list fixtures carry it: whether the viewer owns it, for Mine. */
export type AreaWorkload = AstroliftWorkload & { ownedByMe?: boolean };

export const AREA: AreaWorkload[] = [
  // The viewer created the support bot and the thumbnailer (Mine).
  {
    ...workload({
      id: "wl-support",
      slug: "support-bot",
      name: "Support bot",
      kind: "agent",
      registeredAppSlug: "support",
      replicas: 1,
    }),
    ownedByMe: true,
  },
  workload({
    id: "wl-nightly",
    slug: "nightly-sync",
    name: "Nightly sync",
    kind: "workflow",
    registeredAppSlug: "data-platform",
    replicas: 0,
    schedule: "0 3 * * *",
  }),
  {
    ...workload({
      id: "wl-thumb",
      slug: "thumbnailer",
      name: "Thumbnailer",
      kind: "function",
      registeredAppSlug: "media",
      replicas: 0,
      hpaMinReplicas: 0,
      hpaMaxReplicas: 10,
    }),
    ownedByMe: true,
  },
  workload({
    id: "wl-webhook",
    slug: "webhook-relay",
    name: "Webhook relay",
    kind: "function",
    registeredAppSlug: "integrations",
    replicas: 1,
    hpaMinReplicas: 1,
    hpaMaxReplicas: 4,
  }),
];

/** An app's own workload: the server's `kinds` leaves it out, so it never reaches the list. */
export const APP_WORKLOAD: AreaWorkload = workload({ id: "wl-api", kind: "deployment" });

export const LONG_WORKLOADS: AreaWorkload[] = [
  workload({
    id: "wl-sha",
    slug: SHA64,
    name: SHA64,
    kind: "agent",
    registeredAppSlug: ARN200.replace(/[^a-z0-9-]/g, "-"),
  }),
  workload({
    id: "wl-url",
    slug: "url-function",
    name: LONG_URL,
    kind: "function",
    registeredAppSlug: "media",
    hpaMinReplicas: 0,
    hpaMaxReplicas: 100,
  }),
];

/** Sixty across the three kinds: numbered pages. */
export const MANY_WORKLOADS: AreaWorkload[] = Array.from({ length: 60 }, (_, i) => ({
  ...workload({
    ...AREA[i % 4],
    id: `wl-many-${i}`,
    slug: `workload-${i + 1}`,
    name: `Workload ${String(i + 1).padStart(2, "0")}`,
  }),
  ownedByMe: AREA[i % 4].ownedByMe,
}));

const AREA_KINDS = ["agent", "workflow", "function"];

/**
 * A stand-in for `astroliftWorkloadsPage` in stories: the fixture workloads
 * held to `kinds`, filtered, searched, sorted and sliced the way the server
 * answers the list state.
 */
export function serveWorkloads(
  workloads: AreaWorkload[],
  q: {
    filters: Record<string, string>;
    q: string;
    sort: SortState[];
    page: number;
    pageSize: number;
  },
  kinds: string[] = AREA_KINDS
) {
  return selectPage(
    workloads,
    {
      matches: (w, f) =>
        kinds.includes(w.kind) &&
        (!f.kind || w.kind === f.kind) &&
        (!f.owner || Boolean(w.ownedByMe)) &&
        (!f.app || lower(w.registeredAppSlug) === lower(f.app)),
      text: (w) => [w.name, w.slug, w.registeredAppSlug],
      sortValue: {
        name: (w) => lower(w.name),
        kind: (w) => w.kind,
        app: (w) => lower(w.registeredAppSlug),
      },
      id: (w) => w.id,
    },
    q
  );
}
