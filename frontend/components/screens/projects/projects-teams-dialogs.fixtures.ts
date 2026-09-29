import type { AstroliftProject, AstroliftTeam } from "@/graphql/identity/identity.types";

import type { CreateTeamSheetProps } from "../teams/CreateTeamSheet";
import type { EditTeamSheetProps } from "../teams/EditTeamSheet";

import type { CreateProjectSheetProps } from "./CreateProjectSheet";
import type { EditProjectSheetProps } from "./EditProjectSheet";
import type { ProjectsScreenProps } from "./ProjectsScreen";

/**
 * Hand-typed fixtures for the projects list, the project create / edit
 * sheets and the team create / edit sheets.
 */

const noop = () => {};
const resolvedTrue = async () => true;
const org = { id: "org-1", slug: "acme", name: "Acme" };

export function team(slug: string, patch: Partial<AstroliftTeam> = {}): AstroliftTeam {
  return {
    id: `team-${slug}`,
    slug,
    name: slug.charAt(0).toUpperCase() + slug.slice(1),
    organization: org,
    createdAt: "2026-08-01T12:00:00Z",
    updatedAt: "2026-08-01T12:00:00Z",
    deletedAt: null,
    ...patch,
  };
}

export const TEAMS: AstroliftTeam[] = [team("backend"), team("platform"), team("data")];

export const LONG_TEAM = team("platform-reliability-engineering-and-developer-experience", {
  name: "Platform reliability engineering and developer experience working group",
  organization: {
    ...org,
    slug: "acme-holdings-international-subsidiary-europe",
  },
});

export function project(
  slug: string,
  owner: AstroliftTeam = TEAMS[0],
  patch: Partial<AstroliftProject> = {}
): AstroliftProject {
  return {
    id: `proj-${slug}`,
    slug,
    name: slug.charAt(0).toUpperCase() + slug.slice(1).replace(/-/g, " "),
    organization: org,
    team: { id: owner.id, slug: owner.slug, name: owner.name },
    createdAt: "2026-09-01T12:00:00Z",
    updatedAt: "2026-09-01T12:00:00Z",
    deletedAt: null,
    ...patch,
  };
}

export const PROJECTS: AstroliftProject[] = [
  project("api-gateway", TEAMS[0], { name: "API gateway" }),
  project("billing", TEAMS[0]),
  project("ingest", TEAMS[2], { createdAt: "2026-07-14T08:30:00Z" }),
  project("observability", TEAMS[1]),
];

export const LONG_PROJECT = project("customer-facing-realtime-analytics-pipeline-eu", LONG_TEAM, {
  name: "Customer-facing realtime analytics pipeline for the EU data residency region",
});

/** The screen's props less the list controller, which the story builds. */
export function projectsProps(
  patch: Partial<Omit<ProjectsScreenProps, "list">> = {}
): Omit<ProjectsScreenProps, "list"> {
  return {
    rows: PROJECTS,
    totalCount: PROJECTS.length,
    nextCursor: null,
    loading: false,
    error: null,
    onRetry: noop,
    teams: TEAMS,
    teamsLoading: false,
    noTeams: false,
    autoOpenCreate: false,
    deleting: false,
    deleteProject: async () => {},
    renderCreateDialog: () => null,
    renderEditDialog: () => null,
    ...patch,
  };
}

export function createProjectProps(
  patch: Partial<CreateProjectSheetProps> = {}
): CreateProjectSheetProps {
  return {
    open: true,
    onOpenChange: noop,
    teams: TEAMS,
    creating: false,
    createProject: resolvedTrue,
    ...patch,
  };
}

export function editProjectProps(
  patch: Partial<EditProjectSheetProps> = {}
): EditProjectSheetProps {
  const p = PROJECTS[0];
  return {
    open: true,
    onOpenChange: noop,
    project: p,
    name: p.name,
    setName: noop,
    slug: p.slug,
    setSlug: noop,
    slugStatus: "unchanged",
    canSubmit: false,
    saving: false,
    generateSlug: noop,
    save: resolvedTrue,
    ...patch,
  };
}

export function createTeamProps(patch: Partial<CreateTeamSheetProps> = {}): CreateTeamSheetProps {
  return {
    open: true,
    onOpenChange: noop,
    hasOrg: true,
    creating: false,
    createTeam: resolvedTrue,
    ...patch,
  };
}

export function editTeamProps(patch: Partial<EditTeamSheetProps> = {}): EditTeamSheetProps {
  const t = TEAMS[0];
  return {
    open: true,
    onOpenChange: noop,
    team: t,
    name: t.name,
    setName: noop,
    slug: t.slug,
    setSlug: noop,
    slugStatus: "unchanged",
    canSubmit: false,
    saving: false,
    generateSlug: noop,
    save: resolvedTrue,
    ...patch,
  };
}
