/** Hand-typed fixtures for Admin › App access (#2534), against the screen's props. */
import { LONG_URL } from "@/components/list/fixtures";

import type { AppAccessOverviewScreenProps } from "./AppAccessOverviewScreen";
import { accessEditorHref, type EdgeAccessRow, type PersonSummary } from "./edge-access-list";

const noop = () => {};

const row = (appSlug: string, patch: Partial<EdgeAccessRow> = {}): EdgeAccessRow => ({
  id: `${appSlug}:${patch.environment ?? "prod"}`,
  appSlug,
  appName: appSlug,
  environment: "prod",
  clusterSlug: "conflict-astrolift",
  hosts: [`${appSlug}.apps.conflict.cloud`],
  restricted: false,
  groups: [],
  users: [],
  source: "",
  setBy: null,
  setAt: null,
  status: "open",
  edgeAppliedAt: "2026-10-26T14:02:11Z",
  href: accessEditorHref(appSlug),
  ...patch,
});

export const EDGE_ACCESS_ROWS: EdgeAccessRow[] = [
  row("conflict-bdr", {
    restricted: true,
    groups: ["conflict-sales"],
    users: ["eric@calliope.ai", "keith@calliope.ai"],
    source: "ui",
    setBy: "leo@calliope.ai",
    setAt: "2026-10-26T13:58:40Z",
    status: "enforced",
  }),
  row("fossilrepos-brain", {
    restricted: true,
    groups: ["fossil"],
    source: "manifest",
    status: "applying",
  }),
  row("storybook-zentinelle", {
    restricted: true,
    users: ["edge-access-test@weareconflict.com"],
    source: "ui",
    setBy: "leo@calliope.ai",
    setAt: "2026-10-26T14:05:02Z",
    status: "applying",
  }),
  row("storybook-zentinelle", {
    id: "storybook-zentinelle:staging",
    environment: "staging",
    hosts: [],
    restricted: true,
    users: ["edge-access-test@weareconflict.com"],
    source: "ui",
    setBy: "leo@calliope.ai",
    setAt: "2026-10-26T14:05:02Z",
    status: "pending_deploy",
  }),
  row("veruus-demo", {
    restricted: true,
    groups: ["veruus"],
    source: "ui",
    setBy: null,
    status: "drift",
  }),
  row("quake-dash", { status: "open" }),
  row("legacy-portal", {
    clusterSlug: "conflict-nginx",
    hosts: [],
    status: "not_on_edge",
    edgeAppliedAt: null,
  }),
  row("ops-console", {
    restricted: true,
    groups: ["platform"],
    source: "ui",
    setBy: "leo@calliope.ai",
    setAt: "2026-09-27T09:12:00Z",
    status: "unverified",
    edgeAppliedAt: null,
  }),
];

/** The same rows, read for one person: how each lets them in. */
export const PERSON_ROWS: EdgeAccessRow[] = [
  {
    ...EDGE_ACCESS_ROWS[0]!,
    entry: { enters: true, via: "group", groups: ["conflict-sales"] },
  },
  { ...EDGE_ACCESS_ROWS[5]!, entry: { enters: true, via: "open", groups: [] } },
  { ...EDGE_ACCESS_ROWS[2]!, entry: { enters: true, via: "user", groups: [] } },
];

export const PERSON: PersonSummary = {
  email: "eric@calliope.ai",
  logins: [
    {
      clusterSlug: "conflict-astrolift",
      resolved: true,
      reason: "",
      hasLogin: true,
      enabled: true,
      groups: ["conflict-sales", "bdr"],
    },
  ],
  refusedCount: 4,
};

/** A provider Astrolift cannot list: group rules read as unknown. */
export const PERSON_UNRESOLVED: PersonSummary = {
  email: "contractor@example.com",
  logins: [
    {
      clusterSlug: "client-eks",
      resolved: false,
      reason:
        "This cluster's central auth is an external identity provider Astrolift does not manage.",
      hasLogin: null,
      enabled: null,
      groups: [],
    },
    {
      clusterSlug: "conflict-astrolift",
      resolved: true,
      reason: "",
      hasLogin: false,
      enabled: null,
      groups: [],
    },
  ],
  refusedCount: 6,
};

export const PERSON_UNRESOLVED_ROWS: EdgeAccessRow[] = [
  {
    ...EDGE_ACCESS_ROWS[0]!,
    clusterSlug: "client-eks",
    entry: { enters: null, via: "unknown", groups: [] },
  },
  {
    ...EDGE_ACCESS_ROWS[5]!,
    clusterSlug: "client-eks",
    entry: { enters: null, via: "open", groups: [] },
  },
];

/** A 200-char host, an unbroken group name and a long email never widen the page. */
export const EDGE_ACCESS_LONG: EdgeAccessRow[] = [
  row("a-very-long-application-slug-that-keeps-going-past-the-column", {
    environment: "preview-feature-branch-with-a-long-name-2534",
    hosts: [LONG_URL.replace(/^https?:\/\//, "").slice(0, 200)],
    restricted: true,
    groups: ["a-group-name-with-no-break-anywhere-in-it-at-all-0123456789"],
    users: ["someone.with.a.really.long.address@subdomain.of.a.long.example.com"],
    source: "ui",
    setBy: "someone.with.a.really.long.address@subdomain.of.a.long.example.com",
    setAt: "2026-10-26T13:58:40Z",
    status: "enforced",
  }),
];

export const APP_ACCESS: Omit<AppAccessOverviewScreenProps, "list"> = {
  rows: EDGE_ACCESS_ROWS,
  person: null,
  loading: false,
  stale: false,
  error: null,
  onRetry: noop,
  nextCursor: "next",
  totalCount: 42,
};
