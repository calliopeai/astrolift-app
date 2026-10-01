"use client";

import { useTranslations } from "next-intl";

import type { AstroliftDeployment } from "@/graphql/lifecycle/lifecycle.types";

import type { FailingItem, FailingPanelViewProps } from "./FailingPanel";
import {
  combineReads,
  type HomeAgentTask,
  type HomeRead,
  useHomeAgentRuns,
  useHomeDeployments,
  usePanelAccess,
} from "./home-reads";
import {
  agentRunHref,
  deployHref,
  deployReason,
  failingDeploys,
  runReason,
} from "./apps-agents-model";

export function failedDeployItem(
  d: AstroliftDeployment,
  t?: ReturnType<typeof useTranslations<"home">>
): FailingItem {
  return {
    key: `deploy:${d.id}`,
    kind: "deploy",
    reason: deployReason(
      d,
      t ? { rolledBack: t("copy.rolledBack"), missing: t("copy.noFailureReason") } : undefined
    ),
    subject: `${d.registeredAppSlug} · ${d.environmentName || (t ? t("operations.noEnvironment") : "no environment")}`,
    at: d.failedAt ?? d.endedAt ?? d.startedAt ?? d.createdAt,
    href: deployHref(d.id),
  };
}

export function failedRunItem(t: HomeAgentTask, missing?: string): FailingItem {
  return {
    key: `run:${t.id}`,
    kind: "run",
    reason: runReason(t.failureMessage, missing),
    subject: t.agentName || t.agentSlug,
    at: t.finishedAt ?? t.startedAt ?? t.createdAt,
    href: agentRunHref(t.id),
  };
}

/**
 * Failing's data: the apps and environments whose newest deploy failed,
 * from the approvals queue's deployments read (it carries the reason), and
 * the newest failed agent runs, the same read Failed runs makes. Each only
 * when the viewer may see that module.
 */
export function useFailing(): Omit<FailingPanelViewProps, "panel"> {
  const t = useTranslations("home");
  const { canView } = usePanelAccess();
  const appsOn = canView("apps");
  const agentsOn = canView("agents");
  const deploys = useHomeDeployments({ skip: !appsOn });
  const runs = useHomeAgentRuns("failed", { skip: !agentsOn });

  const failing = appsOn ? failingDeploys(deploys.deployments) : [];
  const items = [
    ...failing.map((d) => failedDeployItem(d, t)),
    ...(agentsOn ? runs.runs.map((r) => failedRunItem(r, t("copy.noRunReason"))) : []),
  ];
  const sources: HomeRead[] = [];
  if (appsOn) sources.push(deploys);
  if (agentsOn) sources.push(runs);
  // Deploys are counted within the newest 100; runs by the server's total.
  const count = agentsOn
    ? runs.total === null
      ? null
      : failing.length + runs.total
    : failing.length;
  return { items, count, ...combineReads(sources, items.length > 0) };
}
