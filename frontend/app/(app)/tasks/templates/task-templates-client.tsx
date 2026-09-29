"use client";

import { TaskTemplatesScreen } from "@/components/screens/tasks/TaskTemplatesScreen";
import { useTaskTemplates } from "@/components/screens/tasks/use-tasks";

/** Task templates: the hook walks the cursor and runs a task, the screen draws the list. */
export function TaskTemplatesClient() {
  return <TaskTemplatesScreen {...useTaskTemplates()} />;
}
