import type { BrainStatus, BrainSummary } from "./brain-status-model";

/** Hand-typed `astroliftBrainStatus` answers, one per state the screens draw. */

const FILES_SUBSTRATE: BrainStatus["substrate"] = {
  authority: "files",
  blobs: "fs",
  vectors: "local",
  graph: "edges-table",
  cell: "authority=files, blobs=fs, vectors=local, graph=edges-table",
  persistence: [],
  replicas: 1,
  image: "",
  port: 8080,
  adopted: false,
  adoptedStore: "",
  graduationNeeded: false,
  resources: [],
};

export const POSTGRES_SUBSTRATE: BrainStatus["substrate"] = {
  authority: "postgres",
  blobs: "s3",
  vectors: "in-db",
  graph: "age",
  cell: "authority=postgres, blobs=s3, vectors=in-db, graph=age",
  persistence: ["postgres", "s3"],
  replicas: 2,
  image: "",
  port: 8080,
  adopted: true,
  adoptedStore: "postgres",
  graduationNeeded: false,
  resources: [
    {
      kind: "postgres",
      name: "brain",
      environment: "production",
      state: "active",
      detail: "DATABASE_URL",
    },
    {
      kind: "s3",
      name: "blobs",
      environment: "",
      state: "unbound",
      detail:
        "Astrolift has no bucket binding yet; bind the bucket's secret bundle outside the manifest",
    },
  ],
};

export const HEALTHY: BrainStatus = {
  slug: "platform-brain",
  environmentName: "production",
  url: "https://platform-brain.apps.example.test",
  served: true,
  deployedCommit: "4f1c2a9e7b3d5c8f0a1b2c3d4e5f60718293a4b5",
  behind: false,
  compiledAt: "2026-10-15T06:12:00Z",
  warnings: [],
  deployment: {
    id: "d0000000-0000-4000-8000-000000000001",
    status: "running",
    imageTag: "4f1c2a9",
    commitSha: "4f1c2a9e7b3d5c8f0a1b2c3d4e5f60718293a4b5",
    createdAt: "2026-10-15T06:20:00Z",
  },
  sourceHead: {
    branch: "main",
    commitSha: "4f1c2a9e7b3d5c8f0a1b2c3d4e5f60718293a4b5",
    committedAt: "2026-10-15T06:10:00Z",
  },
  lastIngest: {
    status: "completed",
    conclusion: "success",
    url: "https://github.example.test/acme/platform-brain/actions/runs/101",
    createdAt: "2026-10-15T06:00:00Z",
  },
  substrate: FILES_SUBSTRATE,
};

/** The branch moved past what runs, the last ingest failed, and a host read failed. */
export const BEHIND: BrainStatus = {
  ...HEALTHY,
  slug: "sales-brain",
  behind: true,
  sourceHead: {
    branch: "main",
    commitSha: "9a8b7c6d5e4f30211223344556677889900aabbc",
    committedAt: "2026-10-16T09:30:00Z",
  },
  lastIngest: {
    status: "completed",
    conclusion: "failure",
    url: "https://github.example.test/acme/sales-brain/actions/runs/88",
    createdAt: "2026-10-16T09:00:00Z",
  },
  warnings: ["compiled at: the repository could not be read (HTTP 502)"],
  compiledAt: null,
};

export const DEPLOY_FAILED: BrainStatus = {
  ...HEALTHY,
  slug: "ops-brain",
  deployedCommit: "",
  behind: null,
  compiledAt: null,
  deployment: {
    id: "d0000000-0000-4000-8000-000000000002",
    status: "failed",
    imageTag: "b7e1d03",
    commitSha: "b7e1d03aa1b2c3d4e5f60718293a4b5c6d7e8f90",
    createdAt: "2026-10-16T08:45:00Z",
  },
  lastIngest: null,
};

export const DEPLOYING: BrainStatus = {
  ...HEALTHY,
  slug: "research-brain",
  served: false,
  url: "",
  deployedCommit: "",
  behind: null,
  compiledAt: null,
  deployment: {
    id: "d0000000-0000-4000-8000-000000000003",
    status: "deploying",
    imageTag: "",
    commitSha: "c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f6",
    createdAt: "2026-10-16T10:01:00Z",
  },
  lastIngest: {
    status: "in_progress",
    conclusion: "",
    url: "https://github.example.test/acme/research-brain/actions/runs/3",
    createdAt: "2026-10-16T10:00:00Z",
  },
};

export const NOT_DEPLOYED: BrainStatus = {
  ...HEALTHY,
  slug: "draft-brain",
  served: false,
  url: "",
  deployedCommit: "",
  behind: null,
  compiledAt: null,
  deployment: null,
  sourceHead: null,
  lastIngest: null,
};

/** Mimwell says the cell is outgrown; the brain has not adopted a store yet. */
export const GRADUATION_NEEDED: BrainStatus = {
  ...HEALTHY,
  substrate: {
    ...FILES_SUBSTRATE,
    graduationNeeded: true,
  },
};

export const ON_POSTGRES: BrainStatus = { ...HEALTHY, substrate: POSTGRES_SUBSTRATE };

/** A non-files cell provisioned but not yet taken up by the brain's own cutover. */
export const NOT_ADOPTED: BrainStatus = {
  ...HEALTHY,
  substrate: {
    ...POSTGRES_SUBSTRATE,
    adopted: false,
    adoptedStore: "",
    resources: [
      {
        kind: "postgres",
        name: "brain",
        environment: "production",
        state: "provisioning",
        detail: "",
      },
      ...POSTGRES_SUBSTRATE.resources.slice(1),
    ],
  },
};

export const WITH_SQLITE: BrainStatus = {
  ...HEALTHY,
  substrate: {
    ...FILES_SUBSTRATE,
    authority: "sqlite",
    cell: "authority=sqlite, blobs=fs, vectors=local, graph=edges-table",
    persistence: ["sqlite-volume", "efs"],
    replicas: 3,
    adopted: true,
    adoptedStore: "sqlite",
    resources: [
      {
        kind: "sqlite-volume",
        name: "brain-data",
        environment: "",
        state: "declared",
        detail: "/data",
      },
      { kind: "efs", name: "brain-shared", environment: "", state: "declared", detail: "/shared" },
    ],
  },
};

/** A value this build does not know, as a newer backend might send. */
export const UNKNOWN_VALUES: BrainStatus = {
  ...HEALTHY,
  deployment: { ...HEALTHY.deployment!, status: "paused_by_operator" },
  substrate: {
    ...FILES_SUBSTRATE,
    authority: "duckdb",
    cell: "authority=duckdb, blobs=fs, vectors=local, graph=edges-table",
    resources: [{ kind: "duckdb", name: "brain", environment: "", state: "warming", detail: "" }],
  },
};

/**
 * The summary a list row carries for `status` (#2444): its deployment now,
 * and what the last status read found, unless `checked` is false (no read
 * has run, or it expired).
 */
export function summaryOf(status: BrainStatus | null, checked = true): BrainSummary | null {
  if (!status) return null;
  return {
    environmentName: status.environmentName,
    deployment: status.deployment,
    compiledAt: checked ? status.compiledAt : null,
    behind: checked ? status.behind : null,
    lastIngest: checked ? status.lastIngest : null,
    checkedAt: checked ? "2026-10-16T08:00:00Z" : null,
  };
}

/** A substrate as the API sends it, `__typename`s and all, for Apollo mocks. */
export function typedSubstrate(substrate: BrainStatus["substrate"]) {
  return {
    __typename: "AstroliftBrainSubstrate",
    ...substrate,
    resources: substrate.resources.map((r) => ({ __typename: "AstroliftBrainResource", ...r })),
  };
}

/** `astroliftBrainStatus` as the API sends it, for Apollo mocks. */
export function statusData(status: BrainStatus | null) {
  return {
    astroliftBrainStatus: status && {
      __typename: "AstroliftBrainStatus",
      ...status,
      deployment: status.deployment && {
        __typename: "AstroliftBrainDeploymentState",
        ...status.deployment,
      },
      sourceHead: status.sourceHead && {
        __typename: "AstroliftBrainSourceHead",
        ...status.sourceHead,
      },
      lastIngest: status.lastIngest && {
        __typename: "AstroliftBrainIngestRun",
        ...status.lastIngest,
      },
      substrate: typedSubstrate(status.substrate),
    },
  };
}
