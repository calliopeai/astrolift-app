import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";
import {
  emptyContainer,
  emptyService,
  emptyWorkload,
  type ManifestModel,
} from "@/lib/manifest/model";
import type { ManifestErr } from "@/lib/manifest/schema";

import type { ConfigEditorScreenProps } from "./ConfigEditorScreen";

/**
 * Hand-typed fixtures for the app config tab: the editor screen, the visual
 * manifest builder, its pane, and its field primitives. The app record
 * carries only the fields these views read.
 */

const noop = () => {};
const asyncNoop = async () => {};

export const LONG =
  "platform-team-shared-production-checkout-service-with-a-deliberately-long-name-that-keeps-going";

export const DRAFT = `name = "checkout"

[env]
LOG_LEVEL = "info"
FEATURE_FLAGS = "new-cart"

[[workloads]]
name = "web"
kind = "deployment"
is_public = true
replicas = 2

[[workloads.containers]]
name = "web"
is_primary = true
image_ref = "ghcr.io/acme/checkout:1.4.2"
port = 8080

[[managed_services]]
kind = "postgres"
name = "db"
`;

export const EMPTY_DRAFT = "";

/** A draft the scoped TOML codec can't parse, so the Form view refuses it. */
export const UNSAFE_DRAFT = `name = "checkout\n[[workloads]]\n`;

export const APP = {
  id: "app-1",
  slug: "checkout",
  name: "Checkout",
  manifestPath: "astrolift.toml",
  deployBranch: "main",
  manifestSyncState: "db_ahead",
  updatedAt: "2026-09-27T14:05:00Z",
  sourceRepo: "acme/checkout",
  sourceUrl: "https://github.com/acme/checkout",
  rawManifest: DRAFT,
  rawManifestStaged: "",
  rawManifestStagedHash: "",
  stagedEnvChanges: [],
} as unknown as AstroliftRegisteredApp; // only the fields the config editor reads

/** No source connection: Apply staged instead of Push to repo. */
export const NO_REPO_APP = {
  ...APP,
  sourceRepo: "",
  sourceUrl: "",
  rawManifestStaged: DRAFT,
  rawManifestStagedHash: "digest",
  stagedEnvChanges: ["env.FEATURE_FLAGS"],
} as AstroliftRegisteredApp;

export const LONG_APP = {
  ...APP,
  name: LONG,
  slug: LONG,
  manifestPath: `deploy/${LONG}/astrolift.toml`,
  deployBranch: `release/${LONG}`,
  sourceRepo: `acme/${LONG}`,
  sourceUrl: `https://github.com/acme/${LONG}`,
  manifestSyncState: "diverged",
} as AstroliftRegisteredApp;

export const RENDERED: ConfigEditorScreenProps["rendered"] = {
  appSlug: "checkout",
  namespace: "app-checkout",
  resources: {
    "Deployment/web": { replicas: 2, image: "ghcr.io/acme/checkout:1.4.2" },
    "Service/web": { port: 8080 },
  },
  error: null,
};

export const RENDER_FAILED: ConfigEditorScreenProps["rendered"] = {
  appSlug: "checkout",
  namespace: "app-checkout",
  resources: {},
  error: "workloads[0].containers[0].port must be an integer",
  errorPath: "astrolift.toml",
  errorLine: 18,
  errorColumn: 8,
};

export const EDITOR: ConfigEditorScreenProps = {
  slug: "checkout",
  app: APP,
  loading: false,
  draft: DRAFT,
  setDraft: noop,
  schemaFamily: "app",
  isDirty: false,
  rendered: RENDERED,
  renderedLoading: false,
  busy: false,
  saving: false,
  applying: false,
  changedEnvKeys: [],
  conflict: null,
  handleSave: asyncNoop,
  handleSync: asyncNoop,
  handleApply: asyncNoop,
  handlePush: asyncNoop,
  dismissConflictKeepMine: noop,
  adoptTheirs: noop,
  closeConflict: noop,
};

export const CONFLICT: ConfigEditorScreenProps["conflict"] = {
  theirs: DRAFT.replace("replicas = 2", "replicas = 4"),
  serverUpdatedAt: "2026-09-27T14:09:00Z",
};

export const AGENT_DRAFT = `astrolift_version = "1"

[skills.triage]
description = "Triage incoming tickets"
`;

// ─── Builder model ───────────────────────────────────────────────────────────

export const MODEL: ManifestModel = {
  name: "checkout",
  env: [
    { key: "LOG_LEVEL", value: "info" },
    { key: "DATABASE_URL", value: { from: "db" } },
  ],
  workloads: [
    {
      ...emptyWorkload("web"),
      is_public: true,
      replicas: 2,
      containers: [
        {
          ...emptyContainer("web"),
          image_ref: "ghcr.io/acme/checkout:1.4.2",
          port: 8080,
          command: ["node"],
          args: ["server.js"],
          healthcheck: { kind: "http", value: "/health", port: 8080 },
        },
      ],
    },
    { ...emptyWorkload("nightly-report"), kind: "cronjob", schedule: "0 2 * * *" },
  ],
  managedServices: [
    { ...emptyService("postgres"), name: "db", config: [{ key: "version", value: "16" }] },
  ],
  extra: { astrolift_version: "1" },
};

export const EMPTY_MODEL: ManifestModel = {
  name: "",
  env: [],
  workloads: [],
  managedServices: [],
  extra: {},
};

export const MODEL_ERRORS: ManifestErr[] = [
  { path: "name", message: "name is required" },
  { path: "workloads[0].name", message: "workload names must be unique" },
  { path: "workloads[1].schedule", message: "schedule must be a cron expression" },
];

export const LONG_MODEL: ManifestModel = {
  name: LONG,
  env: [{ key: `${LONG.toUpperCase().replace(/-/g, "_")}`, value: LONG }],
  workloads: [
    {
      ...emptyWorkload(LONG),
      containers: [{ ...emptyContainer(LONG), image_ref: `ghcr.io/acme/${LONG}:${LONG}` }],
    },
  ],
  managedServices: [{ ...emptyService("postgres"), name: LONG, variant: LONG }],
  extra: { [LONG]: "kept" },
};
