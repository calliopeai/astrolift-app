"use client";

import { CommandRunDetail } from "@/components/screens/jobs/CommandRunDetail";
import { useCommandRunDetail } from "@/components/screens/jobs/use-command-run-detail";

/** Command (one-off exec) run detail (#1106, #1118). */
export function CommandRunDetailClient({ id }: { id: string }) {
  return <CommandRunDetail id={id} {...useCommandRunDetail(id)} />;
}
