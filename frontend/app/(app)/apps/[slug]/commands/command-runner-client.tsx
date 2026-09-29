"use client";

import { CommandRunnerScreen } from "@/components/screens/apps/tools/CommandRunnerScreen";
import { useCommandRunner } from "@/components/screens/apps/tools/use-command-runner";

import { AppTabs } from "../components/app-tabs";

export function CommandRunnerClient({ slug }: { slug: string }) {
  const runner = useCommandRunner(slug);
  return <CommandRunnerScreen {...runner} tabs={<AppTabs slug={slug} active="deployments" />} />;
}
