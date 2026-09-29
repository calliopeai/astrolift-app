/**
 * The projects rail's tree (spec 44 §4.2) from the existing astroliftNavTree.
 *
 * The nav tree is every project the viewer may see, not only the ones they
 * are in: spec 44's "my projects" needs a `mine` scope on the query (backend
 * follow-up). Until then the rail shows what the tree gives, which for most
 * people is the same list.
 */
import type { ProjectEntity, ProjectNode } from "@/components/shell/ProjectsRail";
import type {
  AstroliftAppStatus,
  AstroliftAppSummary,
  AstroliftNavTree,
} from "@/graphql/identity/identity.types";
import type { ModuleKey } from "@/lib/shell/nav-model";

const STATUS: Record<AstroliftAppStatus, NonNullable<ProjectEntity["status"]>> = {
  ready: "ok",
  provisioning: "pending",
  pending: "muted",
  failed: "error",
};

/** Where a workload's page lives, by its nav primitive (as the old NavTree routed it). */
function entityFor(app: AstroliftAppSummary): ProjectEntity {
  const isAgent = app.primitiveKind === "agent";
  const isWorkflow = app.primitiveKind === "workflow";
  const prefix = isAgent ? "/agents" : isWorkflow ? "/workflows" : "/apps";
  return {
    kind: isAgent ? "agent" : isWorkflow ? "workflow" : "app",
    key: app.id,
    name: app.name,
    href: `${prefix}/${app.primitiveSlug}`,
    status: STATUS[app.status] ?? "muted",
  };
}

export function projectsFromNavTree(
  tree: AstroliftNavTree | null | undefined,
  canView: (module: ModuleKey) => boolean
): ProjectNode[] {
  if (!tree) return [];
  const byKind = (e: ProjectEntity) =>
    e.kind === "agent"
      ? canView("agents")
      : e.kind === "workflow"
        ? canView("workflows")
        : canView("apps");
  return tree.teams.flatMap((team) =>
    team.projects.map((node) => {
      const entities: ProjectEntity[] = [
        ...node.apps.map(entityFor),
        ...node.standaloneAgents.map(entityFor),
        ...node.workflows.map((w) => ({
          kind: "workflow" as const,
          key: w.id,
          name: w.name,
          href: `/workflows/${encodeURIComponent(w.slug)}`,
          status: (w.isEnabled ? "ok" : "muted") as ProjectEntity["status"],
        })),
      ].filter(byKind);
      return { slug: node.project.slug, name: node.project.name, entities };
    })
  );
}
