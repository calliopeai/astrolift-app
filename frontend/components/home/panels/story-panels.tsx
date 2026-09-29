/**
 * Home's stories draw each registered panel from its fixtures instead of its
 * hook: the registered components fetch (and need the org and Apollo the
 * app provides), a story has neither. `withStoryPanels` swaps every panel
 * listed in storyPanels for its fixture-backed view and every other one for
 * the placeholder, so no panel fetches in a story. Add a panel here when it
 * gets a real component.
 */

import type * as React from "react";

import { PlaceholderPanel } from "../PlaceholderPanel";
import type { HomePanelDef, HomePanelKey, HomePanelProps } from "../registry";
import { ActivityPanelView } from "./ActivityPanel";
import { AgentRunsPanelView } from "./AgentRunsPanel";
import { AlertsPanelView } from "./AlertsPanel";
import * as AA from "./apps-agents.fixtures";
import {
  ACTIVITY,
  AGENT_RUNS,
  ALERTS,
  BUDGET,
  CLUSTERS,
  FORECAST,
  KPIS,
  PLATFORM_RUNS,
  READY,
} from "./builder-operator.fixtures";
import { clustersWorstFirst } from "./builder-operator-model";
import { ClustersPanelView } from "./ClustersPanel";
import { FailedRunsPanelView } from "./FailedRunsPanel";
import { FailingPanelView } from "./FailingPanel";
import { KpisPanelView } from "./KpisPanel";
import { MyAgentsPanelView } from "./MyAgentsPanel";
import { MyAppsPanelView } from "./MyAppsPanel";
import { PlatformActivityPanelView } from "./PlatformActivityPanel";
import { RecentDeploymentsPanelView } from "./RecentDeploymentsPanel";
import { RunningNowPanelView } from "./RunningNowPanel";
import { RunsSpendPanelView } from "./RunsSpendPanel";
import { SpendQuotaPanelView } from "./SpendQuotaPanel";
import { TrafficErrorsPanelView } from "./TrafficErrorsPanel";
import { WaitingPanelView } from "./WaitingPanel";

export const storyPanels: Partial<Record<HomePanelKey, React.ComponentType<HomePanelProps>>> = {
  waiting: ({ panel }) => (
    <WaitingPanelView
      panel={panel}
      items={AA.WAITING}
      approving={false}
      onApprove={async () => {}}
      {...AA.READY}
    />
  ),
  failing: ({ panel }) => (
    <FailingPanelView panel={panel} items={AA.FAILING} count={7} {...AA.READY} />
  ),
  "my-apps": ({ panel }) => (
    <MyAppsPanelView
      panel={panel}
      items={AA.MY_APPS}
      count={9}
      mineNote={AA.MINE_NOTE}
      {...AA.READY}
    />
  ),
  "recent-deployments": ({ panel }) => (
    <RecentDeploymentsPanelView panel={panel} items={AA.RECENT_DEPLOYS} count={214} {...AA.READY} />
  ),
  deployments: ({ panel }) => (
    <RecentDeploymentsPanelView panel={panel} items={AA.RECENT_DEPLOYS} count={214} {...AA.READY} />
  ),
  "traffic-errors": ({ panel }) => (
    <TrafficErrorsPanelView
      panel={panel}
      apps={AA.APPS_PICK}
      appSlug={AA.APPS_PICK[0]!.slug}
      onAppChange={() => {}}
      traffic={AA.TRAFFIC}
      errors={AA.ERRORS}
      reason="OK"
      {...AA.READY}
    />
  ),
  "failed-runs": ({ panel }) => (
    <FailedRunsPanelView panel={panel} items={AA.FAILED_RUNS} count={12} {...AA.READY} />
  ),
  "running-now": ({ panel }) => (
    <RunningNowPanelView panel={panel} snapshot={AA.RUNNING_SNAPSHOT} {...AA.READY} />
  ),
  "my-agents": ({ panel }) => (
    <MyAgentsPanelView
      panel={panel}
      items={AA.MY_AGENTS}
      count={4}
      mineNote={AA.AGENTS_NOTE}
      {...AA.READY}
    />
  ),
  "runs-spend": ({ panel }) => (
    <RunsSpendPanelView
      panel={panel}
      days={AA.RUN_DAYS}
      capped={false}
      spend={AA.SPEND}
      {...AA.READY}
    />
  ),
  activity: ({ panel }) => <ActivityPanelView panel={panel} {...ACTIVITY} />,
  kpis: ({ panel }) => <KpisPanelView panel={panel} {...KPIS} />,
  "agent-runs": ({ panel }) => (
    <AgentRunsPanelView panel={panel} rows={AGENT_RUNS} count={212} {...READY} />
  ),
  alerts: ({ panel }) => <AlertsPanelView panel={panel} rows={ALERTS} count={2} {...READY} />,
  clusters: ({ panel }) => (
    <ClustersPanelView
      panel={panel}
      rows={clustersWorstFirst(CLUSTERS)}
      count={CLUSTERS.length}
      {...READY}
    />
  ),
  "platform-activity": ({ panel }) => (
    <PlatformActivityPanelView panel={panel} items={PLATFORM_RUNS} hasMore {...READY} />
  ),
  "spend-quota": ({ panel }) => (
    <SpendQuotaPanelView panel={panel} forecast={FORECAST} budget={BUDGET} {...READY} />
  ),
};

/** The panels drawn from fixtures where storyPanels has them, else placeholders. */
export function withStoryPanels(panels: HomePanelDef[]): HomePanelDef[] {
  return panels.map((p) => {
    const story = storyPanels[p.key];
    return { ...p, component: story ?? PlaceholderPanel };
  });
}
