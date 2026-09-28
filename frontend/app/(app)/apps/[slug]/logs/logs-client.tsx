"use client";

import { useLogExport } from "@/components/observability/use-log-export";
import { AppLogsScreen } from "@/components/screens/apps/deployments/AppLogsScreen";
import { useAppLogs } from "@/components/screens/apps/deployments/use-app-logs";

import { appPath, useAppChrome } from "../components/app-chrome-context";
import { AppTabs } from "../components/app-tabs";
import { usePodTarget } from "../components/use-pod-target";

/** Observe › Logs route: resolves the pod target, tails its log, renders the screen. */
export function LogsClient({ slug }: { slug: string }) {
  const chrome = useAppChrome();
  const pod = usePodTarget(slug);
  const logs = useAppLogs({
    slug,
    selectedPod: pod.selectedPod,
    selectedContainer: pod.selectedContainer,
  });
  const logExport = useLogExport({
    appSlug: slug,
    podName: pod.selectedPod,
    container: pod.selectedContainer,
  });
  const a = logs.app;

  return (
    <AppLogsScreen
      {...pod}
      {...logs}
      slug={slug}
      logExport={logExport}
      deploymentsHref={appPath(chrome, a?.slug ?? slug, "deployments")}
      tabs={a ? <AppTabs slug={a.slug} active="console" /> : null}
    />
  );
}
