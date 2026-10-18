/**
 * The create wizard's state and rules (#2427), the same request as
 * `astro brain create`: where the layer's repository lives, what the layer
 * is called and who owns it, then Mimwell's substrate. The checks here only
 * keep an obviously incomplete request from being sent; the server checks
 * everything again and its refusals land on the field and step they name.
 * Pure.
 */
import type { CreateBrainInput, RepoVisibility } from "@/graphql/__generated__/operations";

import {
  type CellMatrix,
  DEFAULT_SUBSTRATE,
  SUBSTRATE_FIELDS,
  substrateInput,
  substrateProblems,
  type SubstrateDraft,
} from "../substrate-cells";

export type NewBrainStep = 1 | 2 | 3 | 4;

/** Label and description are `brains.wizard.steps.<key>`. */
export const NEW_BRAIN_STEPS: readonly { n: NewBrainStep; key: StepKey }[] = [
  { n: 1, key: "source" },
  { n: 2, key: "details" },
  { n: 3, key: "substrate" },
  { n: 4, key: "review" },
];
export type StepKey = "source" | "details" | "substrate" | "review";

export interface NewBrainDraft {
  connectionId: string;
  /** `owner/name`: a repository that does not exist yet, or an empty one. */
  repo: string;
  visibility: RepoVisibility;
  /** Blank: the install's pinned Mimwell edition. */
  templateRef: string;
  name: string;
  slug: string;
  /** The slug was typed, so the name no longer suggests it. */
  slugTouched: boolean;
  teamId: string;
  projectId: string;
  environmentName: string;
  deploy: boolean;
  substrate: SubstrateDraft;
}

export const EMPTY_DRAFT: NewBrainDraft = {
  connectionId: "",
  repo: "",
  visibility: "PRIVATE",
  templateRef: "",
  name: "",
  slug: "",
  slugTouched: false,
  teamId: "",
  projectId: "",
  environmentName: "production",
  deploy: true,
  substrate: DEFAULT_SUBSTRATE,
};

export type DraftField =
  | "connectionId"
  | "repo"
  | "visibility"
  | "templateRef"
  | "name"
  | "slug"
  | "teamId"
  | "projectId"
  | "environmentName"
  | "substrate";

/** Each is a `brains.wizard.problems` message. */
export type Problem =
  | "required"
  | "repoFormat"
  | "refFormat"
  | "slugFormat"
  | "needsAppSlug"
  | "envFormat"
  | "long";

/**
 * The rules the server applies first (`schema/mutations/brains.py`). The
 * slug is Mimwell's app slug (`brain_substrate.APP_SLUG`): a letter first.
 */
const SLUG = /^[a-z]([a-z0-9-]{0,61}[a-z0-9])?$/;
const REPO = /^[A-Za-z0-9_.-]{1,100}\/[A-Za-z0-9_.-]{1,100}$/;
const ENV = /^[a-z0-9]([a-z0-9-]{0,30}[a-z0-9])?$/;
const REF = /^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$/;

const STEP_OF: Record<DraftField, NewBrainStep> = {
  connectionId: 1,
  repo: 1,
  visibility: 1,
  templateRef: 1,
  name: 2,
  slug: 2,
  teamId: 2,
  projectId: 2,
  environmentName: 2,
  substrate: 3,
};

export const STEP_FIELDS: Record<NewBrainStep, DraftField[]> = {
  1: ["connectionId", "repo", "visibility", "templateRef"],
  2: ["name", "slug", "teamId", "projectId", "environmentName"],
  3: ["substrate"],
  4: [],
};

/** A source host from a connection kind (`github_pat` is github). */
export function hostOf(kind: string): "github" | "gitlab" | "bitbucket" | "gitea" | "" {
  return (
    (["github", "gitlab", "bitbucket", "gitea"] as const).find((h) => kind.startsWith(h)) ?? ""
  );
}

/** Internal visibility exists on GitHub and GitLab only. */
export function visibilitiesFor(kind: string): RepoVisibility[] {
  const host = hostOf(kind);
  return host === "github" || host === "gitlab"
    ? ["PRIVATE", "INTERNAL", "PUBLIC"]
    : ["PRIVATE", "PUBLIC"];
}

/** A slug suggestion from a name: a letter first, then lowercase letters, digits and single dashes. */
export function slugFromName(name: string): string {
  return name
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^[^a-z]+/, "")
    .slice(0, 63)
    .replace(/-+$/, "");
}

/** The draft with a new name, and the slug it suggests until the slug is typed. */
export function withName(draft: NewBrainDraft, name: string): NewBrainDraft {
  return { ...draft, name, slug: draft.slugTouched ? draft.slug : slugFromName(name) };
}

export interface WizardProject {
  id: string;
  slug: string;
  name: string;
  team: { id: string; slug: string; name: string };
}

/** The teams the viewer's projects belong to, once each, by slug. */
export function teamsOf(projects: readonly WizardProject[]): WizardProject["team"][] {
  const byId = new Map(projects.map((p) => [p.team.id, p.team]));
  return [...byId.values()].sort((a, b) => a.slug.localeCompare(b.slug));
}

export interface WizardContext {
  /** Connections that can act for the caller. */
  connections: readonly { id: string; kind: string }[];
  /** The caller's projects, each with its team. */
  projects: readonly WizardProject[];
  /** Mimwell's matrix (`astroliftBrainCells`); null until it arrives. */
  matrix?: CellMatrix | null;
  /**
   * Whether the edition the draft names has `deploy.app_slug`
   * (`astroliftBrainEdition`, #2444); null while unknown.
   */
  supportsAppSlug?: boolean | null;
}

export function draftProblems(
  draft: NewBrainDraft,
  context: WizardContext
): Partial<Record<DraftField, Problem>> {
  const out: Partial<Record<DraftField, Problem>> = {};
  const connection = context.connections.find((c) => c.id === draft.connectionId);
  if (!connection) out.connectionId = "required";
  const repo = draft.repo.trim();
  if (!repo) out.repo = "required";
  else if (!REPO.test(repo) || repo.includes("..")) out.repo = "repoFormat";
  if (connection && !visibilitiesFor(connection.kind).includes(draft.visibility))
    out.visibility = "required";
  const ref = draft.templateRef.trim();
  if (ref && (!REF.test(ref) || ref.includes(".."))) out.templateRef = "refFormat";
  if (draft.name.trim().length > 255) out.name = "long";
  if (!draft.slug) out.slug = "required";
  else if (!SLUG.test(draft.slug)) out.slug = "slugFormat";
  else if (context.supportsAppSlug === false && !draft.slug.endsWith("-brain"))
    out.slug = "needsAppSlug";
  if (!draft.teamId) out.teamId = "required";
  const project = context.projects.find((p) => p.id === draft.projectId);
  if (!project || project.team.id !== draft.teamId) out.projectId = "required";
  if (!ENV.test(draft.environmentName.trim())) out.environmentName = "envFormat";
  if (substrateProblems(draft.substrate, context.matrix ?? null).length > 0)
    out.substrate = "required";
  return out;
}

/** Whether a step can be left with Continue. */
export function stepReady(
  step: NewBrainStep,
  problems: Partial<Record<DraftField, Problem>>
): boolean {
  return STEP_FIELDS[step].every((field) => !problems[field]);
}

/** The first step with something to fix, for a jump back from Review. */
export function firstStepWithProblem(
  problems: Partial<Record<DraftField, Problem>>
): NewBrainStep | null {
  return ([1, 2, 3] as const).find((step) => !stepReady(step, problems)) ?? null;
}

/** The draft field a server error names (`sourceConnectionId`, `replicas`, …), if any. */
export function draftFieldOf(field: string | null | undefined): DraftField | null {
  if (!field) return null;
  if (field === "sourceConnectionId") return "connectionId";
  if (SUBSTRATE_FIELDS.includes(field)) return "substrate";
  return field in STEP_OF ? (field as DraftField) : null;
}

/** Where a server refusal is shown: its field's step, or Review for one it does not name. */
export function stepOfServerField(field: string | null | undefined): NewBrainStep {
  const draftField = draftFieldOf(field);
  return draftField ? STEP_OF[draftField] : 4;
}

/** The request `createBrain` takes. Deploy is only asked for when the caller may deploy. */
export function createInput(
  draft: NewBrainDraft,
  { canDeploy }: { canDeploy: boolean }
): CreateBrainInput {
  return {
    projectId: draft.projectId,
    slug: draft.slug,
    sourceConnectionId: draft.connectionId,
    repo: draft.repo.trim(),
    name: draft.name.trim() || null,
    teamId: draft.teamId || null,
    visibility: draft.visibility,
    templateRef: draft.templateRef.trim() || null,
    environmentName: draft.environmentName.trim() || "production",
    deploy: canDeploy && draft.deploy,
    substrate: substrateInput(draft.substrate),
  };
}

/**
 * The work a creation does, in order. Each step but the last names the
 * state that finishes it (`REPO_READY` finishes the repository step); the
 * deploy runs while the state is DEPLOYING and finishes at READY. Labels are
 * `brains.creation.steps.<step>`.
 */
export const CREATION_STEPS = ["REPO_READY", "SCAFFOLDED", "REGISTERED", "DEPLOYING"] as const;
export type CreationStep = (typeof CREATION_STEPS)[number];
export type CreationStepState = "done" | "current" | "todo" | "failed";

const ORDER = ["PENDING", ...CREATION_STEPS, "READY"];

/**
 * Which step a failed creation stopped at. The row does not record it, so
 * it is read from what the creation had produced (a scaffold commit, an
 * app, a deployment), or from the last state this page saw it in.
 */
export function failedStep(
  creation: { commitSha?: string | null; app?: unknown; deploymentId?: string | null },
  lastSeen: string | null
): CreationStep {
  const produced: CreationStep = creation.deploymentId
    ? "DEPLOYING"
    : creation.app
      ? "DEPLOYING"
      : creation.commitSha
        ? "REGISTERED"
        : "REPO_READY";
  const seen = lastSeen ? ORDER.indexOf(lastSeen) : -1;
  const next = seen >= 0 && seen < CREATION_STEPS.length ? CREATION_STEPS[seen] : null;
  return next && CREATION_STEPS.indexOf(next) > CREATION_STEPS.indexOf(produced) ? next : produced;
}

/**
 * Each step for the progress list. A layer created without a deploy skips
 * DEPLOYING; READY finishes every step; FAILED marks the step it stopped at.
 */
export function creationSteps(
  state: string,
  { deploy, failed }: { deploy: boolean; failed?: CreationStep | null }
): { step: CreationStep; state: CreationStepState }[] {
  const steps = CREATION_STEPS.filter((s) => deploy || s !== "DEPLOYING");
  if (state === "FAILED") {
    const at = steps.indexOf(failed ?? "REPO_READY");
    return steps.map((step, i) => ({
      step,
      state: i < at ? "done" : i === at ? "failed" : "todo",
    }));
  }
  // A step is done once the state that finishes it is reached; the first
  // one not done is the one in progress.
  const reached = state === "READY" ? ORDER.length : ORDER.indexOf(state);
  const finishedAt = (step: CreationStep) => ORDER.indexOf(step === "DEPLOYING" ? "READY" : step);
  const current = steps.find((step) => finishedAt(step) > reached);
  return steps.map((step) => ({
    step,
    state: finishedAt(step) <= reached ? "done" : step === current ? "current" : "todo",
  }));
}

export function creationFinished(state: string): boolean {
  return state === "READY" || state === "FAILED";
}

/**
 * The draft with the obvious picks made until the person makes their own:
 * the first connection, the first team and that team's first project.
 * Derived on every render rather than written back, so data that arrives
 * later still fills in.
 */
export function withDefaults(draft: NewBrainDraft, context: WizardContext): NewBrainDraft {
  const connectionId = context.connections.some((c) => c.id === draft.connectionId)
    ? draft.connectionId
    : (context.connections[0]?.id ?? "");
  const teams = teamsOf(context.projects).map((team) => team.id);
  const teamId = teams.includes(draft.teamId) ? draft.teamId : (teams[0] ?? "");
  const projectId = context.projects.some((p) => p.id === draft.projectId && p.team.id === teamId)
    ? draft.projectId
    : (context.projects.find((p) => p.team.id === teamId)?.id ?? "");
  return { ...draft, connectionId, teamId, projectId };
}
