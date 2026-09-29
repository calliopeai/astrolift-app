import { ARN200, LONG_URL, SHA64 } from "@/components/screens/agents/skills/agent-skills.fixtures";
import { workload } from "@/components/screens/apps/workloads/app-workloads.fixtures";
import type { AstroliftWorkload } from "@/graphql/registry/registry.types";

/** Hand-typed fixtures for Agents › Workloads and Functions. */

export const AREA: AstroliftWorkload[] = [
  workload({
    id: "wl-support",
    slug: "support-bot",
    name: "Support bot",
    kind: "agent",
    registeredAppSlug: "support",
    replicas: 1,
  }),
  workload({
    id: "wl-nightly",
    slug: "nightly-sync",
    name: "Nightly sync",
    kind: "workflow",
    registeredAppSlug: "data-platform",
    replicas: 0,
    schedule: "0 3 * * *",
  }),
  workload({
    id: "wl-thumb",
    slug: "thumbnailer",
    name: "Thumbnailer",
    kind: "function",
    registeredAppSlug: "media",
    replicas: 0,
    hpaMinReplicas: 0,
    hpaMaxReplicas: 10,
  }),
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

/** An app's own workload: the hook drops it, so it never reaches the list. */
export const APP_WORKLOAD = workload({ id: "wl-api", kind: "deployment" });

export const LONG_WORKLOADS: AstroliftWorkload[] = [
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
export const MANY_WORKLOADS: AstroliftWorkload[] = Array.from({ length: 60 }, (_, i) =>
  workload({
    ...AREA[i % 4],
    id: `wl-many-${i}`,
    slug: `workload-${i + 1}`,
    name: `Workload ${String(i + 1).padStart(2, "0")}`,
  })
);
