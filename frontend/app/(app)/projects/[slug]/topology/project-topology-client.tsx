"use client";
import { useQuery } from "@apollo/client/react";
import { LIST_CONFIGURED_WORKFLOWS } from "@/graphql/workflows/tiered.queries";
import type { ConfiguredWorkflow } from "@/graphql/workflows/tiered.types";
import { useRouter, useSearchParams } from "next/navigation";
import { ProjectTopologyScreen } from "@/components/screens/projects/ProjectTopologyScreen";
import { useProjectDetail } from "@/components/screens/projects/use-project-detail";
export function ProjectTopologyClient({ slug }: { slug: string }) {
  const data = useProjectDetail(slug);
  const configured = useQuery<{ workflows: ConfiguredWorkflow[] }>(LIST_CONFIGURED_WORKFLOWS, {
    variables: { orgId: data.project?.organization.id },
    skip: !data.project || !data.canViewWorkflows,
    fetchPolicy: "cache-and-network",
    pollInterval: 15000,
  });
  const router = useRouter();
  const search = useSearchParams();
  const change = (key: string, value: string) => {
    const params = new URLSearchParams(search.toString());
    if (value) params.set(key, value);
    else params.delete(key);
    if (key === "workflow") params.delete("node");
    router.replace(`/projects/${encodeURIComponent(slug)}/topology?${params}`, { scroll: false });
  };
  return (
    <ProjectTopologyScreen
      slug={slug}
      project={data.project}
      workflows={data.workflows}
      agents={data.agents}
      apps={data.apps}
      liveAgents={data.liveAgents}
      workflowRuns={data.workflowRuns}
      configuredWorkflows={configured.data?.workflows ?? []}
      loading={
        data.loading ||
        data.modulesLoading ||
        (data.appsLoading && !data.appsLoaded) ||
        (data.agentsLoading && !data.agentsLoaded) ||
        (data.workflowsLoading && !data.workflowsLoaded) ||
        (configured.loading && !configured.data)
      }
      partialError={
        data.appsError || data.agentsError || data.workflowsError || Boolean(configured.error)
      }
      focus={search.get("workflow") ?? ""}
      selectedId={search.get("node") ?? ""}
      onFocus={(id) => change("workflow", id)}
      onSelect={(id) => change("node", id)}
    />
  );
}
