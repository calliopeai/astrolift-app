"use client";

import {
  TaskRunsTable,
  TaskTemplatesTable,
  TasksScreen,
} from "@/components/screens/tasks/TasksScreen";
import {
  useTaskRuns,
  useTaskTemplates,
  useTasks,
  type TaskWorkload,
} from "@/components/screens/tasks/use-tasks";

/**
 * Tasks. The screen owns the markup; each tab's table gets a container
 * here so its cursor walk runs only while that tab is shown.
 */
export function TasksClient() {
  const tasks = useTasks();

  return (
    <TasksScreen
      tab={tasks.tab}
      setTab={tasks.setTab}
      templates={
        <TemplatesTab
          runningWorkloadId={tasks.runningWorkloadId}
          onRunNow={tasks.onRunNow}
          mutationLoading={tasks.mutationLoading}
        />
      }
      recent={<RecentRunsTab />}
      history={<HistoryRunsTab status={tasks.statusFilter} onStatusChange={tasks.onStatusChange} />}
    />
  );
}

function TemplatesTab(props: {
  runningWorkloadId: string | null;
  onRunNow: (w: TaskWorkload) => void;
  mutationLoading: boolean;
}) {
  return <TaskTemplatesTable {...props} {...useTaskTemplates()} />;
}

function RecentRunsTab() {
  return <TaskRunsTable kind="recent" {...useTaskRuns("recent")} />;
}

function HistoryRunsTab({
  status,
  onStatusChange,
}: {
  status: string;
  onStatusChange: (next: string) => void;
}) {
  return (
    <TaskRunsTable
      kind="history"
      status={status}
      onStatusChange={onStatusChange}
      {...useTaskRuns("hist", status)}
    />
  );
}
