"use client";

import { AppDetailScreen, type AppDetailSlots } from "@/components/screens/apps/detail/AppDetail";
import { useAppDetail } from "@/components/screens/apps/detail/use-app-detail";
import type { AstroliftRegisteredApp, AstroliftWorkload } from "@/graphql/registry/registry.types";
import { classifyPrimitive } from "@/lib/primitive";

import { ActivityTimeline } from "./components/activity-timeline";
import { AppDoctorPanel } from "./components/app-doctor-panel";
import { AppTabs } from "./components/app-tabs";
import { AutowireStatusBanner } from "./components/autowire-status-banner";
import { ConfigDriftBanner } from "./components/config-drift-banner";
import { DeployActivityStrip } from "./components/deploy-activity-strip";
import { DeregisterPendingBanner } from "./components/deregister-pending-banner";
import { GithubConnectCallout } from "./components/github-connect-callout";
import { BundleHome } from "./components/homes/bundle-home";
import { CronjobHome } from "./components/homes/cronjob-home";
import { FunctionHome } from "./components/homes/function-home";
import { TaskHome } from "./components/homes/task-home";
import {
  LatestDeployPanel,
  ManagedServicesPanel,
  OwnershipPanel,
} from "./components/overview-panels";
import { PendingDeployments } from "./components/pending-deployments";
import { ProvisioningProgressPanel } from "./components/provisioning-progress";
import { ReprovisionCallout } from "./components/reprovision-callout";
import { UptimeCard } from "./components/uptime-card";
import { UrlCard } from "./components/url-card";
import { TopologyClient } from "./topology/topology-client";

/**
 * App overview (spec 44 §5.2). The screen owns the markup; every piece with
 * data of its own is a container passed in as a slot, so its query runs only
 * when shown.
 */
export function AppDetailClient({ slug }: { slug: string }) {
  const detail = useAppDetail(slug);
  const a = detail.app;

  return (
    <AppDetailScreen
      {...detail}
      slug={slug}
      tabs={a ? <AppTabs slug={a.slug} active="overview" /> : undefined}
      home={a ? primitiveHome(slug, a, detail.workloads) : undefined}
      slots={a ? overviewSlots(a, detail.workloads) : undefined}
    />
  );
}

/**
 * The Overview per kind: an app that presents as a distinct primitive gets
 * panels shaped like that primitive rather than the generic grid. Agents
 * and workflows route to their own paths; here we specialise /apps for
 * bundles (mixed workloads) and the one-off and scheduled kinds.
 */
function primitiveHome(slug: string, a: AstroliftRegisteredApp, wlList: AstroliftWorkload[]) {
  const primitive = classifyPrimitive(wlList.map((w) => w.kind));
  const primaryWl = wlList[0];
  if (primitive === "bundle") {
    return (
      <BundleHome slug={slug} name={a.name} status={a.provisioningStatus} workloads={wlList} />
    );
  }
  if (primitive === "cronjob" && primaryWl) {
    return <CronjobHome slug={slug} name={a.name} workload={primaryWl} />;
  }
  if (primitive === "task" && primaryWl) {
    return <TaskHome slug={slug} name={a.name} workload={primaryWl} />;
  }
  if (primitive === "function" && primaryWl) {
    return (
      <FunctionHome
        slug={slug}
        name={a.name}
        workload={primaryWl}
        host={a.managedHostname || a.subdomain || null}
      />
    );
  }
  return undefined;
}

function overviewSlots(a: AstroliftRegisteredApp, wlList: AstroliftWorkload[]): AppDetailSlots {
  return {
    deregisterBanner: <DeregisterPendingBanner appSlug={a.slug} />,
    provisioningProgress: <ProvisioningProgressPanel app={a} />,
    reprovisionCallout: <ReprovisionCallout appSlug={a.slug} reprovision={a.reprovision} />,
    configDrift: a.configDrift?.hasDrift ? (
      <ConfigDriftBanner appSlug={a.slug} drift={a.configDrift} />
    ) : null,
    githubConnect: <GithubConnectCallout sourceKind={a.sourceKind} />,
    autowire: (
      <AutowireStatusBanner
        appSlug={a.slug}
        sourceKind={a.sourceKind}
        sourceRepo={a.sourceRepo}
        autowire={a.autowire}
      />
    ),
    latestDeploy: (
      <LatestDeployPanel appSlug={a.slug} pending={<PendingDeployments appSlug={a.slug} />} />
    ),
    health: <UptimeCard appSlug={a.slug} />,
    url: (
      <UrlCard
        appId={a.id}
        appSlug={a.slug}
        subdomain={a.subdomain}
        managedHostname={a.managedHostname}
        primaryWorkloadSlug={wlList.find((w) => w.isPublic)?.slug ?? null}
        provisioningStatus={a.provisioningStatus}
      />
    ),
    topology: <TopologyClient slug={a.slug} />,
    activity: (
      <ActivityTimeline
        appSlug={a.slug}
        strip={<DeployActivityStrip appSlug={a.slug} limit={20} />}
      />
    ),
    managedServices: <ManagedServicesPanel appSlug={a.slug} />,
    ownership: (
      <OwnershipPanel appSlug={a.slug} projectName={a.projectName} teamName={a.teamName} />
    ),
    doctor: <AppDoctorPanel appSlug={a.slug} />,
  };
}
