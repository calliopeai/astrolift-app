import { activeSection, type SearchParams } from "@/components/screens/apps/detail/app-tabs-model";
import { AppTabSections } from "@/components/screens/apps/detail/AppTabSections";
import { LIST_DEPLOYMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import { LIST_EVENTS } from "@/graphql/operations/operations.queries";
import { GET_APP, LIST_WORKLOADS } from "@/graphql/registry/registry.queries";
import { PreloadQuery } from "@/lib/apollo";

import { CommandRunnerClient } from "../commands/command-runner-client";
import { ObservabilityClient } from "../observability/observability-client";
import { ShellClient } from "../shell/shell-client";
import { LogsClient } from "./logs-client";

export const metadata = {
  title: "Logs & metrics · Astrolift",
};

/**
 * The Logs & metrics tab (spec 44 §5.2, §10.3): Logs, Metrics (the former
 * observability page), Console (the shell) and Commands, one section at a
 * time by `?section=`. `/observability`, `/shell`, `/console` and
 * `/commands` redirect here with their query (`?pod=`) kept.
 */
export default async function AppLogsPage({
  params,
  searchParams,
}: {
  params: Promise<{ slug: string }>;
  searchParams: Promise<SearchParams>;
}) {
  const { slug } = await params;
  const section = activeSection("logs", await searchParams);
  return (
    <AppTabSections slug={slug} tab="logs" active={section}>
      {section === "metrics" ? (
        <PreloadQuery query={GET_APP} variables={{ slug }}>
          <PreloadQuery query={LIST_DEPLOYMENTS} variables={{ appSlug: slug, limit: 50 }}>
            <PreloadQuery query={LIST_EVENTS} variables={{ limit: 200 }}>
              <ObservabilityClient slug={slug} />
            </PreloadQuery>
          </PreloadQuery>
        </PreloadQuery>
      ) : section === "console" ? (
        <PreloadQuery query={GET_APP} variables={{ slug }}>
          <ShellClient slug={slug} />
        </PreloadQuery>
      ) : section === "commands" ? (
        <PreloadQuery query={LIST_WORKLOADS} variables={{ appSlug: slug }}>
          <CommandRunnerClient slug={slug} />
        </PreloadQuery>
      ) : (
        <PreloadQuery query={GET_APP} variables={{ slug }}>
          <LogsClient slug={slug} />
        </PreloadQuery>
      )}
    </AppTabSections>
  );
}
