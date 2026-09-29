/** Shell story fixtures: projects the person is in, with their entities. */
import type { ProjectNode } from "./ProjectsRail";

export const PROJECTS: ProjectNode[] = [
  {
    slug: "storefront",
    name: "storefront",
    entities: [
      { kind: "app", key: "checkout", name: "checkout", href: "/apps/checkout", status: "ok" },
      {
        kind: "app",
        key: "billing-api",
        name: "billing-api",
        href: "/apps/billing-api",
        status: "error",
      },
      {
        kind: "agent",
        key: "support-bot",
        name: "support-bot",
        href: "/agents/support-bot",
        status: "pending",
      },
      {
        kind: "workflow",
        key: "nightly-sync",
        name: "nightly-sync",
        href: "/workflows/nightly-sync",
        status: "ok",
      },
    ],
  },
  {
    slug: "data-platform",
    name: "data-platform",
    entities: [
      {
        kind: "agent",
        key: "triage-agent",
        name: "triage-agent",
        href: "/agents/triage-agent",
        status: "ok",
      },
      { kind: "app", key: "ingest", name: "ingest", href: "/apps/ingest", status: "warn" },
    ],
  },
  {
    slug: "internal-tools",
    name: "internal-tools",
    entities: [
      {
        kind: "app",
        key: "status-board",
        name: "status-board",
        href: "/apps/status-board",
        status: "muted",
      },
    ],
  },
];
