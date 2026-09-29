import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { AppChromeProvider } from "@/app/(app)/apps/[slug]/components/app-chrome-context";
import { AppDoctorPanelView } from "@/components/screens/apps/homes/AppDoctorPanel";
import { DOCTOR_REPORT, TASK } from "@/components/screens/apps/homes/app-homes.fixtures";
import { TaskHomeScreen } from "@/components/screens/apps/homes/TaskHome";
import { ActivityTimelineView } from "@/components/screens/apps/overview/ActivityTimeline";
import { AutowireStatusBannerView } from "@/components/screens/apps/overview/AutowireStatusBanner";
import { ConfigDriftBannerView } from "@/components/screens/apps/overview/ConfigDriftBanner";
import { LatestDeployPanel } from "@/components/screens/apps/overview/LatestDeployPanel";
import { ManagedServicesPanel } from "@/components/screens/apps/overview/ManagedServicesPanel";
import { OwnershipPanel } from "@/components/screens/apps/overview/OwnershipPanel";
import { UptimeCardView } from "@/components/screens/apps/overview/UptimeCard";
import { UrlCardView } from "@/components/screens/apps/overview/UrlCard";
import { UrlHealthBadgeView } from "@/components/screens/apps/overview/UrlHealthBadge";
import {
  AUTOWIRE,
  CONFIG_DRIFT,
} from "@/components/screens/apps/overview/app-overview-banners.fixtures";
import {
  UPTIME,
  URL_CARD,
  URL_HEALTH,
} from "@/components/screens/apps/overview/app-overview-cards-a.fixtures";
import {
  ACTIVITY as EVENTS,
  MANAGED_SERVICES_HREF,
  SERVICES,
} from "@/components/screens/apps/overview/app-overview-cards-b.fixtures";
import {
  DEPLOY_LONG,
  LATEST_DEPLOY,
  LATEST_DEPLOY_FAILED,
  OWNERSHIP,
  OWNERSHIP_LONG,
} from "@/components/screens/apps/overview/app-overview-panels.fixtures";
import { TopologyScreen } from "@/components/screens/apps/tools/TopologyScreen";
import { TOPOLOGY } from "@/components/screens/apps/tools/app-observability-shell-topology-commands.fixtures";

import { AppDetailScreen, type AppDetailSlots } from "./AppDetail";
import { ACTIVITY, DETAIL, FAILED_APP, LONG, LONG_APP } from "./app-detail-shell.fixtures";
import { AppFrame } from "./AppFrame";
import { FAILING_APP, FRAME, LONG_FRAME_APP } from "./app-frame.fixtures";
import { DeployActivityStrip } from "./DeployActivityStrip";

/**
 * The Overview tab inside the app frame (spec 44 §5.2). The route fills each
 * slot with a container; the stories fill them with the same views over
 * fixtures, so the whole page reads as it ships.
 */
const SLOTS: AppDetailSlots = {
  configDrift: <ConfigDriftBannerView {...CONFIG_DRIFT} />,
  autowire: <AutowireStatusBannerView {...AUTOWIRE} />,
  latestDeploy: <LatestDeployPanel {...LATEST_DEPLOY} />,
  health: <UptimeCardView {...UPTIME} />,
  url: <UrlCardView {...URL_CARD} healthBadge={<UrlHealthBadgeView {...URL_HEALTH} />} />,
  topology: <TopologyScreen {...TOPOLOGY} />,
  activity: <ActivityTimelineView {...EVENTS} strip={<DeployActivityStrip {...ACTIVITY} />} />,
  managedServices: (
    <ManagedServicesPanel
      loading={false}
      services={SERVICES}
      managedServicesHref={MANAGED_SERVICES_HREF}
    />
  ),
  ownership: <OwnershipPanel {...OWNERSHIP} />,
  doctor: <AppDoctorPanelView {...DOCTOR_REPORT} />,
};

const meta: Meta<typeof AppDetailScreen> = {
  title: "Screens/Apps/Detail/AppDetail",
  component: AppDetailScreen,
  parameters: { layout: "padded" },
  args: { ...DETAIL, slots: SLOTS },
  decorators: [
    (Story, ctx) => (
      <div style={ctx.parameters.width ? { width: ctx.parameters.width } : undefined}>
        <AppChromeProvider framed>
          <AppFrame {...FRAME} {...(ctx.parameters.frame ?? {})}>
            <Story />
          </AppFrame>
        </AppChromeProvider>
      </div>
    ),
  ],
};
export default meta;

type Story = StoryObj<typeof AppDetailScreen>;

/** Every panel, with a drift notice and an autowire notice in the one slot. */
export const Full: Story = {};

export const Loading: Story = {
  args: { loading: true, app: null },
};

/** No app with this slug, or no permission to see it. */
export const NotFound: Story = {
  args: { app: null, slug: "no-such-app" },
};

/**
 * A failed deploy: its reason leads the first panel. The app's own
 * provisioning failure is the frame's first alert, above the body.
 */
export const DeployFailed: Story = {
  args: {
    app: FAILED_APP,
    slots: { ...SLOTS, latestDeploy: <LatestDeployPanel {...LATEST_DEPLOY_FAILED} /> },
  },
  parameters: { frame: { app: FAILING_APP } },
};

/** Never deployed, no workloads, no notices. */
export const Empty: Story = {
  args: {
    workloads: [],
    slots: {
      ...SLOTS,
      configDrift: null,
      autowire: null,
      latestDeploy: <LatestDeployPanel {...LATEST_DEPLOY} current={null} />,
      topology: <TopologyScreen {...TOPOLOGY} nodes={[]} edges={[]} />,
      activity: (
        <ActivityTimelineView
          events={[]}
          loading={false}
          strip={<DeployActivityStrip {...ACTIVITY} deployments={[]} />}
        />
      ),
      managedServices: (
        <ManagedServicesPanel
          loading={false}
          services={[]}
          managedServicesHref={MANAGED_SERVICES_HREF}
        />
      ),
    },
  },
};

/** Panels whose queries failed keep their frame and offer Retry. */
export const PanelErrors: Story = {
  args: {
    slots: {
      ...SLOTS,
      health: (
        <UptimeCardView uptime={null} loading={false} error="Network error: upstream timed out" />
      ),
      activity: (
        <ActivityTimelineView
          events={[]}
          loading={false}
          error="Network error: upstream timed out"
        />
      ),
      ownership: <OwnershipPanel {...OWNERSHIP} accesses={[]} error="Forbidden: app.read" />,
    },
  },
};

/** A task's Overview: the per-kind variant, notices still first. */
export const PrimitiveHome: Story = {
  args: { home: <TaskHomeScreen {...TASK} /> },
};

export const LongStrings: Story = {
  args: {
    app: LONG_APP,
    slug: LONG,
    slots: {
      ...SLOTS,
      latestDeploy: <LatestDeployPanel {...LATEST_DEPLOY} current={DEPLOY_LONG} />,
      ownership: <OwnershipPanel {...OWNERSHIP_LONG} />,
    },
  },
  parameters: { frame: { app: LONG_FRAME_APP, slug: LONG, pathname: `/apps/${LONG}` } },
};

export const At768: Story = {
  args: LongStrings.args,
  parameters: { ...LongStrings.parameters, width: 768 },
};
