"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { LIST_AGENT_LIVE_STATUS, LIST_AGENT_WORKLOADS } from "@/graphql/agents/agents.queries";
import type {
  AstroliftAgentListItem,
  AstroliftAgentLiveStatus,
} from "@/graphql/agents/agents.types";
import { SOFT_DELETE_PROJECT } from "@/graphql/identity/identity.mutations";
import { LIST_MEMBERS, LIST_PROJECTS } from "@/graphql/identity/identity.queries";
import type {
  AstroliftMember,
  AstroliftProject,
  MutationResult,
} from "@/graphql/identity/identity.types";
import { LIST_APPS } from "@/graphql/registry/registry.queries";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";
import { LIST_PROJECT_RESOURCES } from "@/graphql/services/services.queries";
import { useModules } from "@/graphql/user/user.hooks";
import {
  LIST_TIERED_WORKFLOW_DEFINITIONS,
  LIST_WORKFLOW_DEFINITION_RUNS,
} from "@/graphql/workflows/tiered.queries";
import type {
  WorkflowDefinitionRun,
  WorkflowDefinitionSummary,
} from "@/graphql/workflows/tiered.types";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

interface ProjectsResp {
  astroliftProjects: AstroliftProject[];
}
interface AppsResp {
  astroliftApps: AstroliftRegisteredApp[];
}
interface MembersResp {
  astroliftMembers: AstroliftMember[];
}
interface AgentWorkloadsResp {
  agentWorkloads: AstroliftAgentListItem[];
}
interface AgentLiveStatusResp {
  agentLiveStatus: AstroliftAgentLiveStatus[];
}
interface ProjectWorkflowsResp {
  workflowDefinitions: WorkflowDefinitionSummary[];
}
interface ProjectWorkflowRunsResp {
  workflowDefinitionRuns: WorkflowDefinitionRun[];
}
interface ProjectResourcesResp {
  astroliftProjectManagedServicesPage: { totalCount: number };
  astroliftProjectSecretBundles: { id: string; keyCount: number }[];
}

/** The project fields the detail screen reads. */
export type ProjectDetailProject = Pick<
  AstroliftProject,
  "id" | "slug" | "name" | "createdAt" | "organization" | "team"
>;
/** The app fields the detail screen reads. */
export type ProjectDetailApp = Pick<
  AstroliftRegisteredApp,
  "id" | "slug" | "name" | "provisioningStatus" | "sourceRepo" | "createdAt"
>;
/** The member fields the detail screen reads. */
export type ProjectDetailMember = Pick<AstroliftMember, "id" | "joinedAt" | "scopeKind"> & {
  user: Pick<AstroliftMember["user"], "username" | "email">;
};

/**
 * Everything the project overview reads from the server (project, apps,
 * agents + live status, workflows + runs, members, resource counts) and
 * the soft-delete mutation. The data half of ProjectDetailScreen.
 */
export function useProjectDetail(slug: string) {
  const router = useRouter();
  const modules = useModules();
  const permissions = useMyPermissions();
  const canViewApps = modules.canView("apps");
  const canCreateApp = modules.canCreate("apps");
  const canViewAgents = modules.canView("agents");
  const canCreateAgent = modules.canCreate("agents");
  const canViewWorkflows = modules.canView("workflows");
  const canManageMembers = permissions.can("org.manage_members");

  const projects = useQuery<ProjectsResp>(LIST_PROJECTS);
  const apps = useQuery<AppsResp>(LIST_APPS, {
    skip: modules.loading || !canViewApps,
  });
  const members = useQuery<MembersResp>(LIST_MEMBERS, {
    skip: permissions.loading || !canManageMembers,
  });

  const project = projects.data?.astroliftProjects.find((p) => p.slug === slug);
  const orgId = project?.organization.id ?? "";
  const agents = useQuery<AgentWorkloadsResp>(LIST_AGENT_WORKLOADS, {
    variables: { orgId, projectSlug: slug },
    skip: !orgId || modules.loading || !canViewAgents,
    fetchPolicy: "cache-and-network",
  });
  const liveAgents = useQuery<AgentLiveStatusResp>(LIST_AGENT_LIVE_STATUS, {
    variables: { orgId, projectSlug: slug, workloadId: null },
    skip: !orgId || modules.loading || !canViewAgents,
    fetchPolicy: "cache-and-network",
    pollInterval: 15_000,
  });
  const workflows = useQuery<ProjectWorkflowsResp>(LIST_TIERED_WORKFLOW_DEFINITIONS, {
    variables: { orgId, projectId: project?.id ?? null },
    skip: !orgId || !project?.id || modules.loading || !canViewWorkflows,
    fetchPolicy: "cache-and-network",
  });
  const workflowRuns = useQuery<ProjectWorkflowRunsResp>(LIST_WORKFLOW_DEFINITION_RUNS, {
    variables: { orgId, projectId: project?.id ?? null, status: null, limit: 50 },
    skip: !orgId || !project?.id || modules.loading || !canViewWorkflows,
    fetchPolicy: "cache-and-network",
    pollInterval: 10_000,
  });
  const projectResources = useQuery<ProjectResourcesResp>(LIST_PROJECT_RESOURCES, {
    variables: { projectId: project?.id ?? "" },
    skip: !project?.id,
    fetchPolicy: "cache-and-network",
  });

  const [softDelete, { loading: deleting }] = useMutation<{
    softDeleteProject: MutationResult<{ id: string; deleted: boolean }>;
  }>(SOFT_DELETE_PROJECT, {
    refetchQueries: [{ query: LIST_PROJECTS }],
    awaitRefetchQueries: true,
  });

  // Filter on the client. There's no singular astroliftProject(slug)
  // resolver yet, so we pull the list and pick — fine for the
  // typical org with O(10) projects. A focused project read will
  // matter for orgs with hundreds of projects (#TBD backend ticket).
  const projectApps: ProjectDetailApp[] =
    apps.data?.astroliftApps.filter((a) => a.projectSlug === slug) ?? [];
  const projectAgents = canViewAgents ? (agents.data?.agentWorkloads ?? []) : [];
  const projectWorkflows = canViewWorkflows ? (workflows.data?.workflowDefinitions ?? []) : [];

  // Members of this project: scopeKind=PROJECT and scopeId matches the
  // project's id. Org-wide members also inherit access — surface those
  // in a separate group below the explicit project members so it's
  // clear which are direct vs inherited.
  const directMembers: ProjectDetailMember[] =
    members.data?.astroliftMembers.filter(
      (m) => m.scopeKind === "PROJECT" && m.scopeId === project?.id
    ) ?? [];

  /** Throws on failure so the confirm dialog stays open with the error. */
  async function onDelete() {
    if (!project) return;
    const { data } = await softDelete({
      variables: { input: { id: project.id } },
    });
    if (data?.softDeleteProject.ok) {
      toast.success(`Deleted ${project.slug}`);
      router.push("/administration/projects");
    } else {
      throw new Error(data?.softDeleteProject.errors?.[0]?.message ?? "Delete failed");
    }
  }

  return {
    slug,
    project: (project ?? null) as ProjectDetailProject | null,
    loading: projects.loading && !project,
    modulesLoading: modules.loading,
    canViewApps,
    canCreateApp,
    canViewAgents,
    canCreateAgent,
    canViewWorkflows,
    canManageMembers,
    apps: projectApps,
    appsLoading: apps.loading,
    appsLoaded: Boolean(apps.data),
    appsError: Boolean(apps.error),
    agents: projectAgents,
    agentsLoading: agents.loading,
    agentsLoaded: Boolean(agents.data),
    agentsError: Boolean(agents.error),
    liveAgents: liveAgents.data?.agentLiveStatus ?? [],
    workflows: projectWorkflows,
    workflowsLoading: workflows.loading,
    workflowsLoaded: Boolean(workflows.data),
    workflowsError: Boolean(workflows.error),
    workflowRuns: workflowRuns.data?.workflowDefinitionRuns ?? [],
    directMembers,
    membersLoading: members.loading,
    resourceCount:
      (projectResources.data?.astroliftProjectManagedServicesPage?.totalCount ?? 0) +
      (projectResources.data?.astroliftProjectSecretBundles.length ?? 0),
    resourcesLoading: projectResources.loading && !projectResources.data,
    deleting,
    onDelete,
  };
}
