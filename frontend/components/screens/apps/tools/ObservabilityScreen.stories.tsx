import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { useLocalListState } from "@/components/list/use-list-state";
import {
  DnsRecordsCard,
  EndpointMetricsPanel,
  GoldenSignalsPanel,
  PromqlQueryPanel,
  TlsCertificatesCard,
  TraceExplorerPanel,
  WorkloadIdentityCard,
} from "@/components/observability";

import {
  ALERT_EVENTS,
  ALERT_EVENTS_LIST,
  ALERT_RULES_PANEL,
  DNS,
  ENDPOINT_METRICS,
  GOLDEN_SIGNALS,
  IDENTITY,
  LONG_ALERT_RULES,
  LONG_APP,
  LONG_LOG_LINES,
  LONG_PODS,
  OBSERVABILITY,
  PROMQL,
  TLS,
  TRACES,
} from "./app-observability-shell-topology-commands.fixtures";
import {
  APP_ALERT_RULES_LIST,
  APP_PODS_LIST,
  selectAlertRules,
  selectPods,
} from "./metrics-panels";
import {
  AlertEventsListView,
  AlertRulesPanelView,
  type AlertRulesPanelViewProps,
  ObservabilityScreen,
  type ObservabilityScreenProps,
} from "./ObservabilityScreen";

const meta: Meta = {
  title: "Screens/Apps/Tools/ObservabilityScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

const signals = (
  <>
    <GoldenSignalsPanel {...GOLDEN_SIGNALS} />
    <EndpointMetricsPanel {...ENDPOINT_METRICS} />
    <TraceExplorerPanel {...TRACES} />
    <PromqlQueryPanel {...PROMQL} />
  </>
);

const network = (
  <>
    <DnsRecordsCard {...DNS} />
    <TlsCertificatesCard {...TLS} />
    <WorkloadIdentityCard {...IDENTITY} />
  </>
);

/** The alert rules panel with in-memory list state, as the hook would pass it. */
function Rules(props: Partial<Omit<AlertRulesPanelViewProps, "list" | "rows" | "totalCount">>) {
  const list = useLocalListState(APP_ALERT_RULES_LIST);
  const all = props.rules ?? ALERT_RULES_PANEL.rules;
  const page = selectAlertRules(all, list.filters, list.state);
  return (
    <AlertRulesPanelView
      {...ALERT_RULES_PANEL}
      renderEvents={() => <AlertEventsListView {...ALERT_EVENTS_LIST} />}
      {...props}
      list={list}
      rows={page.rows}
      totalCount={page.totalCount}
    />
  );
}

/** The screen with in-memory pod list state and every panel's slot filled. */
function Screen(
  props: Partial<Omit<ObservabilityScreenProps, "podsList" | "podRows" | "podTotal">>
) {
  const podsList = useLocalListState(APP_PODS_LIST);
  const all = props.pods ?? OBSERVABILITY.pods;
  const page = selectPods(all, podsList.filters, podsList.state);
  return (
    <ObservabilityScreen
      {...OBSERVABILITY}
      signals={signals}
      network={network}
      alertRules={<Rules />}
      {...props}
      podsList={podsList}
      podRows={page.rows}
      podTotal={page.totalCount}
    />
  );
}

/** Pods: the list, the picked pod's usage, its scoped log and the platform events. */
export const Full: Story = { render: () => <Screen /> };

/** Signals: the metric scope and the metric panels. */
export const Signals: Story = { render: () => <Screen panel="signals" /> };

/** Alert rules: the list, and the picked rule's events as a feed under it. */
export const Alerts: Story = { render: () => <Screen panel="alerts" /> };

/** DNS & TLS: records, certificates and workload identity. */
export const Network: Story = { render: () => <Screen panel="network" /> };

export const Loading: Story = {
  render: () => <Screen app={null} loading />,
};

/** No app with this slug, or no permission to see it. */
export const NotFound: Story = {
  render: () => <Screen slug="no-such-app" app={null} />,
};

/** Pods and events still loading after the app resolved. */
export const PodsLoading: Story = {
  render: () => (
    <Screen
      pods={[]}
      podsLoading
      selectedPod={null}
      logBuffer={[]}
      streaming={false}
      appEvents={[]}
      eventsLoading
    />
  ),
};

/** Nothing running yet: no pods, no log lines, no events. */
export const Empty: Story = {
  render: () => (
    <Screen
      pods={[]}
      selectedPod={null}
      podContainers={[]}
      selectedContainer={null}
      logBuffer={[]}
      streaming={false}
      allReplicas={false}
      appEvents={[]}
    />
  ),
};

/** No alert rules yet. */
export const AlertsEmpty: Story = {
  render: () => <Screen panel="alerts" alertRules={<Rules rules={[]} pickedRuleId={null} />} />,
};

export const AlertsLoading: Story = {
  render: () => (
    <Screen panel="alerts" alertRules={<Rules rules={[]} loading pickedRuleId={null} />} />
  ),
};

/**
 * The screen has no query-error state of its own; the closest real one is a
 * historical window on a cluster with no log aggregator wired.
 */
export const HistoricalUnavailable: Story = {
  render: () => (
    <Screen
      historicalRange="6h"
      isHistorical
      historicalUnavailable
      logBuffer={[]}
      streaming={false}
    />
  ),
};

/** Per-pod tail: one replica picked, its container picker shown in the log viewer. */
export const PerPodTail: Story = {
  render: () => <Screen allReplicas={false} />,
};

const long = {
  slug: LONG_APP.slug,
  app: LONG_APP,
  pods: LONG_PODS,
  selectedPod: LONG_PODS[0].name,
  podContainers: [LONG_PODS[0].containerStatuses[0].name],
  selectedContainer: LONG_PODS[0].containerStatuses[0].name,
  allReplicas: false,
  logBuffer: LONG_LOG_LINES,
};

export const LongStrings: Story = { render: () => <Screen {...long} /> };

export const AlertsLongStrings: Story = {
  render: () => (
    <Screen
      {...long}
      panel="alerts"
      alertRules={
        <Rules appName={LONG_APP.name} rules={LONG_ALERT_RULES} pickedRuleId="rule-long" />
      }
    />
  ),
};

export const W768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <Screen {...long} />
    </div>
  ),
};

export const AlertEvents: Story = {
  render: () => <AlertEventsListView {...ALERT_EVENTS_LIST} />,
};

/** Many events with older pages on the cursor: the feed scrolls in its frame. */
export const AlertEventsLongHistory: Story = {
  render: () => (
    <AlertEventsListView
      {...ALERT_EVENTS_LIST}
      events={Array.from({ length: 30 }, (_, i) => ({
        ...ALERT_EVENTS[i % ALERT_EVENTS.length]!,
        id: `aev-many-${i}`,
        firedAt: new Date(Date.UTC(2026, 8, 28, 12) - i * 3_600_000).toISOString(),
      }))}
      hasMore
    />
  ),
};

export const AlertEventsLoading: Story = {
  render: () => <AlertEventsListView {...ALERT_EVENTS_LIST} events={[]} loading />,
};

export const AlertEventsEmpty: Story = {
  render: () => <AlertEventsListView {...ALERT_EVENTS_LIST} events={[]} />,
};

export const AlertEventsError: Story = {
  render: () => (
    <AlertEventsListView
      {...ALERT_EVENTS_LIST}
      events={[]}
      error="Network error: upstream timed out"
      onRetry={() => {}}
    />
  ),
};
