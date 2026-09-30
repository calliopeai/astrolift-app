"use client";

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
  const podTarget = usePodTarget(slug);
  const appSlug = shell.app?.slug ?? slug;

  return (
    <ShellScreen
      {...shell}
      {...podTarget}
      deploymentsHref={appPath(chrome, appSlug, "deployments")}
      tokensHref={appPath(chrome, appSlug, "tokens")}
      tabs={<AppTabs slug={appSlug} />}
    />
  );
}
