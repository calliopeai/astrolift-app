"use client";

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
import {
  AlertEventsListView,
  AlertRulesPanelView,
  ObservabilityScreen,
} from "@/components/screens/apps/tools/ObservabilityScreen";
import { useAlertEvents, useAlertRules } from "@/components/screens/apps/tools/use-alert-rules";
import { useAppObservability } from "@/components/screens/apps/tools/use-app-observability";

import { appPath, useAppChrome } from "../components/app-chrome-context";
import { AppTabs } from "../components/app-tabs";
import { ManagedServiceMetrics } from "../components/managed-service-metrics";

/**
 * App › Observability. The screen owns the markup; each metric panel is wired
 * to its own hook here, and the alert-rules panel (and each expanded rule's
 * events) gets a container so its queries run only when it is rendered.
 */
export function ObservabilityClient({ slug }: { slug: string }) {
  const chrome = useAppChrome();
  const obs = useAppObservability(slug);
  const { scopedEnv, scopedWorkload } = obs;

  const dns = useDnsRecords(slug);
  const tls = useTlsCertificates(slug);
  const identity = useWorkloadIdentity(slug);
  const traces = useTraceExplorer(slug, scopedEnv);
  const promql = usePromql(slug, scopedEnv);
  const goldenSignals = useGoldenSignals(slug, scopedEnv, scopedWorkload);
  const endpointMetrics = useEndpointMetrics({
    appSlug: slug,
    environmentName: scopedEnv,
    workloadSlug: scopedWorkload,
  });

  const a = obs.app;
  const appSlug = a?.slug ?? slug;

  return (
    <ObservabilityScreen
      {...obs}
      deploymentsHref={appPath(chrome, appSlug, "deployments")}
      tabs={<AppTabs slug={appSlug} active="observability" />}
      panels={
        <>
          <GoldenSignalsPanel {...goldenSignals} />
          <EndpointMetricsPanel {...endpointMetrics} />
          <TraceExplorerPanel {...traces} />
          <PromqlQueryPanel {...promql} />
          <ManagedServiceMetricsList
            managedServices={obs.managedServices}
            renderPanel={(id) => <ManagedServiceMetrics managedServiceId={id} />}
          />
          <DnsRecordsCard appSlug={appSlug} {...dns} />
          <TlsCertificatesCard appSlug={appSlug} {...tls} />
          <WorkloadIdentityCard appSlug={appSlug} {...identity} />
        </>
      }
      alertRules={a ? <AlertRulesPanel appId={a.id} appName={a.name} /> : null}
    />
  );
}

function AlertRulesPanel({ appId, appName }: { appId: string; appName: string }) {
  return (
    <AlertRulesPanelView
      {...useAlertRules(appId)}
      appName={appName}
      renderEvents={(ruleId) => <AlertEventsList ruleId={ruleId} />}
    />
  );
}

function AlertEventsList({ ruleId }: { ruleId: string }) {
  return <AlertEventsListView {...useAlertEvents(ruleId)} />;
}
