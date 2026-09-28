import type { Meta, StoryObj } from "@storybook/nextjs-vite";

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
  AlertEventsListView,
  AlertRulesPanelView,
  ObservabilityScreen,
} from "./ObservabilityScreen";

const meta: Meta = {
  title: "Screens/Apps/Tools/ObservabilityScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

const panels = (
  <>
    <GoldenSignalsPanel {...GOLDEN_SIGNALS} />
    <EndpointMetricsPanel {...ENDPOINT_METRICS} />
    <TraceExplorerPanel {...TRACES} />
    <PromqlQueryPanel {...PROMQL} />
    <DnsRecordsCard {...DNS} />
    <TlsCertificatesCard {...TLS} />
    <WorkloadIdentityCard {...IDENTITY} />
  </>
);

const alertRules = (
  <AlertRulesPanelView
    {...ALERT_RULES_PANEL}
    renderEvents={() => <AlertEventsListView {...ALERT_EVENTS_LIST} />}
  />
);

export const Full: Story = {
  render: () => <ObservabilityScreen {...OBSERVABILITY} panels={panels} alertRules={alertRules} />,
};

export const Loading: Story = {
  render: () => <ObservabilityScreen {...OBSERVABILITY} app={null} loading />,
};

/** No app with this slug, or no permission to see it. */
export const NotFound: Story = {
  render: () => <ObservabilityScreen {...OBSERVABILITY} slug="no-such-app" app={null} />,
};

/** Pods, events and alert rules still loading after the app resolved. */
export const PodsLoading: Story = {
  render: () => (
    <ObservabilityScreen
      {...OBSERVABILITY}
      pods={[]}
      podsLoading
      selectedPod={null}
      logBuffer={[]}
      streaming={false}
      appEvents={[]}
      eventsLoading
      alertRules={
        <AlertRulesPanelView {...ALERT_RULES_PANEL} rules={[]} loading renderEvents={() => null} />
      }
    />
  ),
};

/** Nothing running yet: no pods, no log lines, no events, no alert rules. */
export const Empty: Story = {
  render: () => (
    <ObservabilityScreen
      {...OBSERVABILITY}
      pods={[]}
      selectedPod={null}
      podContainers={[]}
      selectedContainer={null}
      logBuffer={[]}
      streaming={false}
      allReplicas={false}
      appEvents={[]}
      alertRules={
        <AlertRulesPanelView {...ALERT_RULES_PANEL} rules={[]} renderEvents={() => null} />
      }
    />
  ),
};

/**
 * The screen has no query-error state of its own; the closest real one is a
 * historical window on a cluster with no log aggregator wired.
 */
export const HistoricalUnavailable: Story = {
  render: () => (
    <ObservabilityScreen
      {...OBSERVABILITY}
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
  render: () => <ObservabilityScreen {...OBSERVABILITY} allReplicas={false} />,
};

export const LongStrings: Story = {
  render: () => (
    <ObservabilityScreen
      {...OBSERVABILITY}
      slug={LONG_APP.slug}
      app={LONG_APP}
      pods={LONG_PODS}
      selectedPod={LONG_PODS[0].name}
      podContainers={[LONG_PODS[0].containerStatuses[0].name]}
      selectedContainer={LONG_PODS[0].containerStatuses[0].name}
      allReplicas={false}
      logBuffer={LONG_LOG_LINES}
      alertRules={
        <AlertRulesPanelView
          {...ALERT_RULES_PANEL}
          appName={LONG_APP.name}
          rules={LONG_ALERT_RULES}
          renderEvents={() => null}
        />
      }
    />
  ),
};

export const AlertEvents: Story = {
  render: () => <AlertEventsListView {...ALERT_EVENTS_LIST} />,
};

export const AlertEventsLoading: Story = {
  render: () => <AlertEventsListView {...ALERT_EVENTS_LIST} events={[]} loading />,
};

export const AlertEventsEmpty: Story = {
  render: () => <AlertEventsListView {...ALERT_EVENTS_LIST} events={[]} />,
};
