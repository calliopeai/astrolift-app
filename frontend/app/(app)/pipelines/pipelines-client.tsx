"use client";

import {
  PipelineListView,
  PipelinesScreen,
  RunHistoryView,
} from "@/components/screens/pipelines/PipelinesScreen";
import {
  type Pipeline,
  usePipelineList,
  usePipelines,
  useRunHistory,
} from "@/components/screens/pipelines/use-pipelines";

/**
 * /pipelines (#106, #107). The screen owns the markup; each tab's table walk
 * sits in a container here so its hook runs only while that tab is shown.
 */
export function PipelinesClient() {
  const { tab, onTabChange, onTrigger, triggering, startDialog } = usePipelines();
  return (
    <>
      {startDialog}
      <PipelinesScreen
        tab={tab}
        onTabChange={onTabChange}
        pipelinesTab={<PipelineListTab onTrigger={onTrigger} triggering={triggering} />}
        runsTab={<RunHistoryTab />}
      />
    </>
  );
}

function PipelineListTab(props: {
  onTrigger: (p: Pipeline) => Promise<void>;
  triggering: boolean;
}) {
  return <PipelineListView {...usePipelineList()} {...props} />;
}

function RunHistoryTab() {
  return <RunHistoryView {...useRunHistory()} />;
}
