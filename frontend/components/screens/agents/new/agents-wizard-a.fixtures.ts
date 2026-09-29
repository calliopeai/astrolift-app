import type { AstroliftDiscoveredAgentManifest } from "@/graphql/agents/agents.types";
import type { AstroliftProject, AstroliftTeam } from "@/graphql/identity/identity.types";

import type { AgentDiscoveryStepViewProps } from "./AgentDiscoveryStep";
import type { AgentProjectStepViewProps } from "./AgentProjectStep";
import type { AgentDiscoveryFields } from "./use-agent-discovery-step";
import type { AgentProjectFields } from "./use-agent-project-step";

/**
 * Hand-typed fixtures for New agent's discovery and project parts
 * (components/screens/agents/new/, group agents-wizard-a).
 */

const noop = () => {};

export const LONG =
  "platform-team-shared-production-agents-us-west-2-with-a-deliberately-long-name-that-keeps-going";

// ---------------------------------------------------------------- discovery

export const DISCOVERED: AstroliftDiscoveredAgentManifest[] = [
  {
    manifestPath: "agents/triage/astrolift.toml",
    name: "Triage",
    slug: "triage",
    workloadKind: "agent",
    alreadyRegistered: false,
  },
  {
    manifestPath: "agents/reviewer/astrolift.toml",
    name: "Reviewer",
    slug: "reviewer",
    workloadKind: "agent",
    alreadyRegistered: false,
  },
  {
    manifestPath: "agents/nightly-report/astrolift.toml",
    name: "Nightly report",
    slug: "nightly-report",
    workloadKind: "scheduled_agent",
    alreadyRegistered: true,
  },
];

const DISCOVERY_BASE: AgentDiscoveryFields = {
  sourceKind: "github",
  sourceRepo: "conflict/agents",
  defaultBranch: "main",
  ref: "main",
  scanned: false,
  scanError: null,
  discoveredAgents: [],
  selectedManifestPaths: [],
};

const DISCOVERY_HANDLERS = { toggle: noop, rescan: noop };

export const DISCOVERY_SCANNING: AgentDiscoveryStepViewProps = {
  ...DISCOVERY_HANDLERS,
  state: DISCOVERY_BASE,
  fetchState: "scanning",
  fetchError: "",
  newAgentCount: 0,
};

export const DISCOVERY_EMPTY: AgentDiscoveryStepViewProps = {
  ...DISCOVERY_HANDLERS,
  state: { ...DISCOVERY_BASE, scanned: true },
  fetchState: "empty",
  fetchError: "",
  newAgentCount: 0,
};

export const DISCOVERY_ERROR: AgentDiscoveryStepViewProps = {
  ...DISCOVERY_HANDLERS,
  state: { ...DISCOVERY_BASE, scanError: "repository not found or not accessible" },
  fetchState: "error",
  fetchError: "repository not found or not accessible",
  newAgentCount: 0,
};

export const DISCOVERY_FOUND: AgentDiscoveryStepViewProps = {
  ...DISCOVERY_HANDLERS,
  state: {
    ...DISCOVERY_BASE,
    scanned: true,
    discoveredAgents: DISCOVERED,
    selectedManifestPaths: DISCOVERED.filter((a) => !a.alreadyRegistered).map(
      (a) => a.manifestPath
    ),
  },
  fetchState: "found",
  fetchError: "",
  newAgentCount: 2,
};

const LONG_AGENT: AstroliftDiscoveredAgentManifest = {
  manifestPath: `agents/${LONG}/astrolift.toml`,
  name: `${LONG} ${LONG}`,
  slug: LONG.slice(0, 40),
  workloadKind: "scheduled_agent",
  alreadyRegistered: false,
};

export const DISCOVERY_LONG: AgentDiscoveryStepViewProps = {
  ...DISCOVERY_HANDLERS,
  state: {
    ...DISCOVERY_BASE,
    sourceRepo: `conflict/${LONG}`,
    ref: `release/${LONG}`,
    scanned: true,
    discoveredAgents: [LONG_AGENT, { ...DISCOVERED[2], name: LONG }],
    selectedManifestPaths: [LONG_AGENT.manifestPath],
  },
  fetchState: "error",
  fetchError: `${LONG} ${LONG} ${LONG}`,
  newAgentCount: 1,
};

// ---------------------------------------------------------------- project

const ORG = { id: "org-1", slug: "conflict", name: "CONFLICT" };

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
    slug: "agents",
    name: "Company agents",
    organization: ORG,
    team: { id: "t2", slug: "growth", name: "Growth" },
  },
] as unknown as AstroliftProject[];

type ProjectData = Omit<AgentProjectStepViewProps<AgentProjectFields>, "state" | "setState">;

export const PROJECT_STATE: AgentProjectFields = { projectId: "p1" };

export const PROJECT: ProjectData = { allTeams: TEAMS, allProjects: PROJECTS };

export const LONG_PROJECT_STATE: AgentProjectFields = { projectId: "p-long" };

export const LONG_PROJECT: ProjectData = {
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
};
