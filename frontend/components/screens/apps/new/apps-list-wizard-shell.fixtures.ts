import type { AstroliftProject, AstroliftTeam } from "@/graphql/identity/identity.types";

import type { AppDetailsStepViewProps } from "./AppDetailsStep";
import type { AppDetailsFields } from "./use-app-details-step";
import type { WizardShellProps, WizardStep } from "./WizardShell";

/**
 * Hand-typed fixtures for the new-app flow (components/screens/apps/new/):
 * the wizard shell and the details step. The list's own live in ../list.
 */

const noop = () => {};

export const LONG =
  "platform-team-shared-production-workloads-us-west-2-with-a-deliberately-long-name-that-keeps-going";

const ORG = { id: "org-1", slug: "conflict", name: "CONFLICT" };

// ---------------------------------------------------------------- wizard

export const WIZARD_STEPS: WizardStep[] = [
  { key: "repo", label: "Repository", description: "Pick the Git repository to deploy." },
  { key: "manifest", label: "Manifest", description: "Review or write astrolift.toml." },
  { key: "details", label: "Details", description: "Name the app and pick its project." },
  { key: "strategy", label: "Deploy strategy", description: "How and when the app deploys." },
  { key: "review", label: "Review", description: "Check everything, then register." },
];

export const WIZARD: Omit<WizardShellProps, "children"> = {
  step: 3,
  steps: WIZARD_STEPS,
  onStepClick: noop,
  onBack: noop,
  onNext: noop,
  onCancel: noop,
};

export const LONG_WIZARD_STEPS: WizardStep[] = WIZARD_STEPS.map((s) => ({
  ...s,
  label: `${s.label} ${LONG}`,
  description: `${s.description} ${LONG} ${LONG}`,
}));

const TEAMS: AstroliftTeam[] = [
  { id: "t1", slug: "platform", name: "Platform", organization: ORG },
  { id: "t2", slug: "growth", name: "Growth", organization: ORG },
] as unknown as AstroliftTeam[];

const PROJECTS: AstroliftProject[] = [
  {
    id: "p1",
    slug: "core",
    name: "Core services",
    organization: ORG,
    team: { id: "t1", slug: "platform", name: "Platform" },
  },
  {
    id: "p2",
    slug: "web",
    name: "Web properties",
    organization: ORG,
    team: { id: "t2", slug: "growth", name: "Growth" },
  },
] as unknown as AstroliftProject[];

export const DETAILS_STATE: AppDetailsFields = {
  name: "API Gateway",
  slug: "api-gateway",
  slugTouched: false,
  description: "Edge router for every public API.",
  projectId: "p1",
};

export const DETAILS: Omit<AppDetailsStepViewProps<AppDetailsFields>, "state" | "setState"> = {
  allTeams: TEAMS,
  allProjects: PROJECTS,
  slugValid: true,
  projectsLoading: false,
};

export const LONG_DETAILS_STATE: AppDetailsFields = {
  name: LONG,
  slug: LONG.slice(0, 40),
  slugTouched: true,
  description: `${LONG} ${LONG} ${LONG}`,
  projectId: "p-long",
};

export const LONG_DETAILS: typeof DETAILS = {
  allTeams: [
    ...TEAMS,
    { id: "t-long", slug: LONG.slice(0, 40), name: LONG, organization: ORG },
  ] as unknown as AstroliftTeam[],
  allProjects: [
    ...PROJECTS,
    {
      id: "p-long",
      slug: LONG.slice(0, 40),
      name: LONG,
      organization: ORG,
      team: { id: "t-long", slug: LONG.slice(0, 40), name: LONG },
    },
  ] as unknown as AstroliftProject[],
  slugValid: true,
  projectsLoading: false,
};
