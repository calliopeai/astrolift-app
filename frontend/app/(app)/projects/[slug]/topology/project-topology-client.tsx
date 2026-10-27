"use client";
import { useRouter, useSearchParams } from "next/navigation";
import { ProjectTopologyScreen } from "@/components/screens/projects/ProjectTopologyScreen";
import { useProjectDetail } from "@/components/screens/projects/use-project-detail";
export function ProjectTopologyClient({ slug }: { slug: string }) {
  const data = useProjectDetail(slug);
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
      loading={
        data.loading ||
        data.modulesLoading ||
        (data.appsLoading && !data.appsLoaded) ||
        (data.agentsLoading && !data.agentsLoaded) ||
        (data.workflowsLoading && !data.workflowsLoaded)
      }
      partialError={data.appsError || data.agentsError || data.workflowsError}
      focus={search.get("workflow") ?? ""}
      selectedId={search.get("node") ?? ""}
      onFocus={(id) => change("workflow", id)}
      onSelect={(id) => change("node", id)}
    />
  );
}
