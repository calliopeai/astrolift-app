"use client";

import { useQuery } from "@apollo/client/react";
import * as React from "react";
import { LIST_ENVIRONMENTS, GET_APP_EXEC_TARGET } from "@/graphql/lifecycle/lifecycle.queries";
import { isReviewedExecTarget } from "@/lib/exec-session";

import { ShellScreen } from "@/components/screens/apps/tools/ShellScreen";
import { useAppShell } from "@/components/screens/apps/tools/use-app-shell";

import { appPath, useAppChrome } from "@/lib/app-chrome-context";
import { AppTabs } from "../components/app-tabs";
import { usePodTarget } from "../components/use-pod-target";

/**
 * Control › Shell — the acting half of what used to be the Console tab
 * (#1247). An interactive root shell on a running pod, the script upload that
 * feeds it, and the paste-ready CLI equivalents.
 */
export function ShellClient({ slug }: { slug: string }) {
  const chrome = useAppChrome();
  const shell = useAppShell(slug);
  const environmentRead = useQuery<{
    astroliftEnvironments: { id: string; name: string; clusterSlug: string }[];
  }>(LIST_ENVIRONMENTS, {
    variables: { appSlug: slug },
    fetchPolicy: "network-only",
  });
  const environments = environmentRead.data?.astroliftEnvironments ?? [];
  const [pickedEnvironment, setPickedEnvironment] = React.useState<string | null>(null);
  const environmentId = pickedEnvironment ?? environments[0]?.id ?? null;
  const environment = environments.find((row) => row.id === environmentId) ?? null;
  const podTarget = usePodTarget(slug, {
    environmentName: environment?.name,
    enabled: !!environment,
    strictSelection: true,
  });
  const selectedWorkload = podTarget.podRows.find(
    (pod) => pod.name === podTarget.selectedPod
  )?.workload;
  const review = useQuery<{ astroliftAppExecTarget: unknown }>(GET_APP_EXEC_TARGET, {
    variables: {
      appSlug: slug,
      workloadSlug: selectedWorkload ?? "",
      environmentId: environmentId ?? "",
      podName: podTarget.selectedPod ?? "",
      container: podTarget.selectedContainer ?? "",
    },
    skip:
      !environment || !selectedWorkload || !podTarget.selectedPod || !podTarget.selectedContainer,
    fetchPolicy: "network-only",
  });
  const observed = review.data?.astroliftAppExecTarget;
  const execTarget =
    isReviewedExecTarget(observed) &&
    observed.environmentId === environmentId &&
    observed.podName === podTarget.selectedPod &&
    observed.container === podTarget.selectedContainer &&
    !review.loading &&
    !review.error
      ? observed
      : null;
  const appSlug = shell.app?.slug ?? slug;

  return (
    <ShellScreen
      {...shell}
      {...podTarget}
      environments={environments}
      environmentId={environmentId}
      setEnvironmentId={setPickedEnvironment}
      execTarget={execTarget}
      deploymentsHref={appPath(chrome, appSlug, "deployments")}
      tokensHref={appPath(chrome, appSlug, "tokens")}
      tabs={<AppTabs slug={appSlug} />}
    />
  );
}
