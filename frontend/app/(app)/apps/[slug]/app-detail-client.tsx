"use client";

import { AppDetailScreen } from "@/components/screens/apps/detail/AppDetail";
import { useAppDetail } from "@/components/screens/apps/detail/use-app-detail";
import type { AstroliftRegisteredApp, AstroliftWorkload } from "@/graphql/registry/registry.types";
import { classifyPrimitive } from "@/lib/primitive";

import { ActivityTimeline } from "./components/activity-timeline";
import { appPath, useAppChrome } from "./components/app-chrome-context";
import { AppDoctorPanel } from "./components/app-doctor-panel";
import { AppTabs } from "./components/app-tabs";
import { AutowireStatusBanner } from "./components/autowire-status-banner";
import { ConfigDriftBanner } from "./components/config-drift-banner";
import { ControlsSection } from "./components/controls-section";
import { DeployActivityStrip } from "./components/deploy-activity-strip";
import { DeployStrategyCard } from "./components/deploy-strategy-card";
import { DeployTokenControl } from "./components/deploy-token-control";
import { DeploymentPanel } from "./components/deployment-panel";
import { DeregisterPendingBanner } from "./components/deregister-pending-banner";
import { GithubConnectCallout } from "./components/github-connect-callout";
import { BundleHome } from "./components/homes/bundle-home";
import { CronjobHome } from "./components/homes/cronjob-home";
import { FunctionHome } from "./components/homes/function-home";
import { TaskHome } from "./components/homes/task-home";
import { LatestDeploymentRow } from "./components/latest-deployment-row";
import { ObservabilitySection } from "./components/observability-section";
import { PendingDeployments } from "./components/pending-deployments";
import { ProvisioningProgressPanel } from "./components/provisioning-progress";
import { QuickLinksGrid } from "./components/quick-links-grid";
import { ReprovisionCallout } from "./components/reprovision-callout";
import { UptimeCard } from "./components/uptime-card";
import { UrlCard } from "./components/url-card";

/**
 * App overview. The screen owns the markup; every piece with data of its own
 * is a container passed in as a slot, so its query runs only when shown.
 */
export function AppDetailClient({ slug }: { slug: string }) {
  const chrome = useAppChrome();
  const detail = useAppDetail(slug);
  const a = detail.app;

  return (
    <AppDetailScreen
      {...detail}
      slug={slug}
      appHref={appPath(chrome, a?.slug ?? slug)}
      tabs={a ? <AppTabs slug={a.slug} active="overview" /> : undefined}
      home={a ? primitiveHome(slug, a, detail.workloads) : undefined}
      slots={a ? overviewSlots(a, detail.workloads) : undefined}
    />
  );
}

/**
 * Primitive-native homes: an app that presents as a distinct primitive gets a
 * landing screen shaped like that primitive rather than the generic app view.
 * Agents/workflows route to their own paths; here we specialize the /apps
 * surface for bundles (mixed workloads) and the one-off/scheduled kinds.
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

function overviewSlots(a: AstroliftRegisteredApp, wlList: AstroliftWorkload[]) {
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
    doctor: <AppDoctorPanel appSlug={a.slug} />,
    urlCard: (
      <UrlCard
        appId={a.id}
        appSlug={a.slug}
        subdomain={a.subdomain}
        managedHostname={a.managedHostname}
        primaryWorkloadSlug={wlList.find((w) => w.isPublic)?.slug ?? null}
        provisioningStatus={a.provisioningStatus}
      />
    ),
    deployment: (
      <>
        <DeployActivityStrip appSlug={a.slug} limit={20} />
        <DeploymentPanel appSlug={a.slug} />
        <PendingDeployments appSlug={a.slug} />
        <DeployStrategyCard app={a} />
        <LatestDeploymentRow appSlug={a.slug} />
      </>
    ),
    controls: (
      <>
        <ControlsSection appSlug={a.slug} deployBranch={a.deployBranch} />
        <DeployTokenControl appSlug={a.slug} />
      </>
    ),
    insights: (
      <>
        <UptimeCard appSlug={a.slug} />
        <ObservabilitySection appSlug={a.slug} />
        <ActivityTimeline appSlug={a.slug} limit={20} />
      </>
    ),
    links: <QuickLinksGrid appSlug={a.slug} />,
  };
}
