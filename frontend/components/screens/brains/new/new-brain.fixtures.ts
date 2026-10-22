import { CELLS } from "../brain-cells.fixtures";
import { BRAINS } from "../brains.fixtures";
import type { BrainCreation } from "./BrainCreationProgress";
import type { WizardConnection } from "./BrainSourceStep";
import { EMPTY_DRAFT, type NewBrainDraft, type WizardProject } from "./new-brain-model";
import type { NewBrainWizardProps } from "./NewBrainWizard";

export const CONNECTIONS: WizardConnection[] = [
  {
    id: "c0000000-0000-4000-8000-000000000001",
    name: "Platform GitHub",
    kind: "github_pat",
    accountLogin: "acme-platform",
  },
  {
    id: "c0000000-0000-4000-8000-000000000002",
    name: "Acme GitHub App",
    kind: "github_app_install",
    accountLogin: "acme",
  },
  {
    id: "c0000000-0000-4000-8000-000000000003",
    name: "Internal Gitea",
    kind: "gitea_pat",
    accountLogin: null,
  },
];

export const PROJECTS: WizardProject[] = [
  {
    id: "p0000000-0000-4000-8000-000000000001",
    slug: "core",
    name: "Core",
    team: { id: "t0000000-0000-4000-8000-000000000001", slug: "platform", name: "Platform" },
  },
  {
    id: "p0000000-0000-4000-8000-000000000002",
    slug: "knowledge",
    name: "Knowledge",
    team: { id: "t0000000-0000-4000-8000-000000000001", slug: "platform", name: "Platform" },
  },
  {
    id: "p0000000-0000-4000-8000-000000000003",
    slug: "gtm",
    name: "Go to market",
    team: { id: "t0000000-0000-4000-8000-000000000002", slug: "sales", name: "Sales" },
  },
];

/** A request filled in on every step. */
export const DRAFT: NewBrainDraft = {
  ...EMPTY_DRAFT,
  connectionId: CONNECTIONS[0]!.id,
  repo: "acme-platform/platform-brain",
  name: "Platform knowledge",
  slug: "platform-brain",
  slugTouched: false,
  teamId: PROJECTS[0]!.team.id,
  projectId: PROJECTS[0]!.id,
};

export const POSTGRES_DRAFT: NewBrainDraft = {
  ...DRAFT,
  substrate: {
    cell: { authority: "postgres", blobs: "s3", vectors: "in-db", graph: "age" },
    persistence: [],
    replicas: 2,
    image: "",
    port: 8080,
  },
};

const STEP_PROPS = {
  denied: false,
  onStep: () => {},
  onContinue: () => {},
  continueDisabled: false,
  busy: false,
  onCancel: () => {},
  problems: {},
  serverErrors: {},
  substrateError: null,
  matrix: CELLS,
  pinnedRef: "v2026.10.1",
  generalError: null,
  connections: CONNECTIONS,
  connectionsLoading: false,
  connectionsError: null,
  onRetryConnections: () => {},
  projects: PROJECTS,
  projectsLoading: false,
  projectsError: null,
  onRetryProjects: () => {},
  canDeploy: true,
  connectionName: CONNECTIONS[0]!.name,
  teamSlug: "platform",
  projectSlug: "core",
};

/** The wizard on one step, with a filled-in draft. */
export function wizardProps(
  step: NewBrainWizardProps["step"],
  patch: Partial<NewBrainWizardProps> = {}
): NewBrainWizardProps {
  return {
    ...STEP_PROPS,
    progress: null,
    step,
    onBack: step > 1 ? () => {} : undefined,
    draft: DRAFT,
    onChange: () => {},
    ...patch,
  };
}

export const CREATION: BrainCreation = {
  id: "e0000000-0000-4000-8000-000000000001",
  slug: "platform-brain",
  state: "SCAFFOLDED",
  repo: "acme-platform/platform-brain",
  templateRef: "v1.4.0",
  commitSha: "4f1c2a9e7b3d5c8f0a1b2c3d4e5f60718293a4b5",
  deploymentId: null,
  app: null,
  error: null,
};

export const CREATION_DEPLOYING: BrainCreation = {
  ...CREATION,
  state: "DEPLOYING",
  deploymentId: "d0000000-0000-4000-8000-000000000001",
  app: { ...BRAINS[0]!, slug: "platform-brain", name: "Platform knowledge" },
};

export const CREATION_READY: BrainCreation = { ...CREATION_DEPLOYING, state: "READY" };

export const CREATION_FAILED: BrainCreation = {
  ...CREATION,
  state: "FAILED",
  commitSha: null,
  error: {
    code: "PRECONDITION",
    message:
      "acme/platform-brain does not exist and the GitHub App cannot create repositories; create the empty repository first, or use a user OAuth or PAT connection",
    field: "repo",
  },
};
