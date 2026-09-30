"use client";

import { usePathname, useSearchParams } from "next/navigation";

import {
  DnsRecordsCard,
  EndpointMetricsPanel,
  GoldenSignalsPanel,
  ManagedServiceMetricsList,
  PromqlQueryPanel,
  TlsCertificatesCard,
  TraceExplorerPanel,
  WorkloadIdentityCard,
} from "@/components/observability";
import { useDnsRecords } from "@/components/observability/use-dns-records";
import { useEndpointMetrics } from "@/components/observability/use-endpoint-metrics";
import { useGoldenSignals } from "@/components/observability/use-golden-signals";
import { usePromql } from "@/components/observability/use-promql";
import { useTlsCertificates } from "@/components/observability/use-tls-certificates";
import { useTraceExplorer } from "@/components/observability/use-trace-explorer";
import { useWorkloadIdentity } from "@/components/observability/use-workload-identity";
import type { MetricsPanel } from "@/components/screens/apps/tools/metrics-panels";
import {
  AlertEventsListView,
  AlertRulesPanelView,
  ObservabilityScreen,
} from "@/components/screens/apps/tools/ObservabilityScreen";
import { useAlertEvents, useAlertRules } from "@/components/screens/apps/tools/use-alert-rules";
import { useAppObservability } from "@/components/screens/apps/tools/use-app-observability";

import { appPath, useAppChrome } from "@/lib/app-chrome-context";
import { AppTabs } from "../components/app-tabs";
import { ManagedServiceMetrics } from "../components/managed-service-metrics";
import { ObservabilitySection } from "../components/observability-section";

/**
 * Logs & metrics › Metrics, one panel at a time. The screen owns the markup;
 * each panel's content is a container here, rendered only on its panel, so
 * a panel's hooks (and their queries) run only while it is on screen.
 */
export function ObservabilityClient({ slug, panel }: { slug: string; panel: MetricsPanel }) {
  const chrome = useAppChrome();
  const obs = useAppObservability(slug, panel);
  const a = obs.app;
  const appSlug = a?.slug ?? slug;

  return (
    <ObservabilityScreen
      {...obs}
      deploymentsHref={appPath(chrome, appSlug, "deployments")}
      tabs={<AppTabs slug={appSlug} active="observability" />}
      signals={
        <SignalsPanels
          appSlug={appSlug}
          env={obs.scopedEnv}
          workload={obs.scopedWorkload}
          managedServices={obs.managedServices}
        />
      }
      network={<NetworkPanels appSlug={appSlug} />}
      alertRules={a ? <AlertRulesPanel appId={a.id} appName={a.name} /> : null}
    />
  );
}

/** Signals: the deploy summary (from the Overview) and the metric panels, in the picked scope. */
function SignalsPanels({
  appSlug,
  env,
  workload,
  managedServices,
}: {
  appSlug: string;
  env: string | null;
  workload: string | null;
  managedServices: { id: string; kind: string }[];
}) {
  const traces = useTraceExplorer(appSlug, env);
  const promql = usePromql(appSlug, env);
  const goldenSignals = useGoldenSignals(appSlug, env, workload);
  const endpointMetrics = useEndpointMetrics({
    appSlug,
    environmentName: env,
    workloadSlug: workload,
  });
  return (
    <>
      <ObservabilitySection appSlug={appSlug} />
      <GoldenSignalsPanel {...goldenSignals} />
      <EndpointMetricsPanel {...endpointMetrics} />
      <TraceExplorerPanel {...traces} />
      <PromqlQueryPanel {...promql} />
      <ManagedServiceMetricsList
        managedServices={managedServices}
        renderPanel={(id) => <ManagedServiceMetrics managedServiceId={id} />}
      />
    </>
  );
}

/** DNS & TLS: the records, certificates and workload identity. */
function NetworkPanels({ appSlug }: { appSlug: string }) {
  const dns = useDnsRecords(appSlug);
  const tls = useTlsCertificates(appSlug);
  const identity = useWorkloadIdentity(appSlug);
  return (
    <>
      <DnsRecordsCard appSlug={appSlug} {...dns} />
      <TlsCertificatesCard appSlug={appSlug} {...tls} />
      <WorkloadIdentityCard appSlug={appSlug} {...identity} />
    </>
  );
}

function AlertRulesPanel({ appId, appName }: { appId: string; appName: string }) {
  // The picked rule is `?rule=<id>`; a rule row links there.
  const params = useSearchParams();
  const pathname = usePathname() ?? "";
  const qs = params?.toString() ?? "";
  return (
    <AlertRulesPanelView
      {...useAlertRules(appId)}
      appName={appName}
      pickedRuleId={params?.get("rule") ?? null}
      ruleHref={(rule) => {
        const next = new URLSearchParams(qs);
        next.set("rule", rule.id);
        return `${pathname}?${next.toString()}`;
      }}
      renderEvents={(ruleId) => <AlertEventsList ruleId={ruleId} />}
    />
  );
}

function AlertEventsList({ ruleId }: { ruleId: string }) {
  return <AlertEventsListView {...useAlertEvents(ruleId)} />;
}
