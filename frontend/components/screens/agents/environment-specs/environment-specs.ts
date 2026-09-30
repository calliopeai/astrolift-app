import { type ListDefinition, standardViews } from "@/components/list/list-state";

import { type NumberedListQuery, numberedPageVariables } from "../skills/catalog";

export interface EnvironmentSpec {
  id: string;
  slug: string;
  name: string;
  runtime: string;
  imageTag: string;
  agentType: string;
  toolPreset: string;
  vncEnabled: boolean;
  managedModel: boolean;
  modelGateway: boolean;
  runAsNonRoot: boolean;
  allowInstall: boolean;
  teamId: string | null;
  projectId: string | null;
  configRepo: string;
  configBranch: string;
  configManifestPath: string;
  secretRefs: unknown;
  updatedAt: string;
}

export const ENVIRONMENT_SPECS_LIST: ListDefinition = {
  id: "agents.environment-specs",
  fields: [
    {
      key: "agentType",
      label: "Agent type",
      options: [
        { value: "claude", label: "Claude" },
        { value: "codex", label: "Codex" },
      ],
    },
  ],
  searchPlaceholder: "Search names, slugs, images, runtimes, repos…",
  defaultSort: [{ key: "slug", dir: "asc" }],
  views: standardViews({ createdBy: "me" }, [], {
    mineNote:
      "Mine means recipes you created. Older recipes with no recorded creator appear in All.",
  }),
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

export function environmentSpecsVariables(q: NumberedListQuery) {
  const filter: { agentType?: string[]; createdBy?: string[] } = {};
  if (q.filters.agentType) filter.agentType = [q.filters.agentType];
  if (q.filters.createdBy) filter.createdBy = [q.filters.createdBy];
  return numberedPageVariables(q, filter, ENVIRONMENT_SPECS_LIST.defaultSort);
}

export function specOwner(spec: EnvironmentSpec): string {
  return spec.projectId ? "Project" : spec.teamId ? "Team" : "Organization shared";
}
