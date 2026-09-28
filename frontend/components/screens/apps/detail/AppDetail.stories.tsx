import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { LayersIcon } from "lucide-react";

import { EmptyState } from "@/components/EmptyState";

import { AppDetailScreen } from "./AppDetail";
import {
  ACTIVITY,
  DETAIL,
  FAILED_APP,
  LATEST,
  LONG,
  LONG_APP,
  QUICK_LINKS,
  TABS,
} from "./app-detail-shell.fixtures";
import { AppTabsView } from "./AppTabs";
import { DeployActivityStrip } from "./DeployActivityStrip";
import { LatestDeploymentRow } from "./LatestDeploymentRow";
import { QuickLinksGrid } from "./QuickLinksGrid";

/**
 * The route fills the other slots (banners, URL card, deployment panel,
 * controls, insights) with containers owned by other screens; the stories
 * fill only the pieces that live in this folder.
 */
const meta: Meta<typeof AppDetailScreen> = {
  title: "Screens/Apps/Detail/AppDetail",
  component: AppDetailScreen,
  parameters: { layout: "fullscreen" },
  args: {
    ...DETAIL,
    tabs: <AppTabsView {...TABS} />,
    slots: {
      deployment: (
        <>
          <DeployActivityStrip {...ACTIVITY} />
          <LatestDeploymentRow {...LATEST} />
        </>
      ),
      links: <QuickLinksGrid {...QUICK_LINKS} />,
    },
  },
};
export default meta;

type Story = StoryObj<typeof AppDetailScreen>;

export const Full: Story = {};

export const Loading: Story = {
  args: { loading: true, app: null },
};

/** No app with this slug, or no permission to see it. */
export const NotFound: Story = {
  args: { app: null, slug: "no-such-app" },
};

/** Provisioning failed: the error box under the tabs. */
export const ProvisioningFailed: Story = {
  args: { app: FAILED_APP },
};

/** No deploy history: the header Deploy links to environments instead. */
export const NoDeployHistory: Story = {
  args: {
    latestDeploy: null,
    workloads: [],
    slots: {
      deployment: (
        <>
          <DeployActivityStrip {...ACTIVITY} deployments={[]} />
          <LatestDeploymentRow {...LATEST} latest={null} />
        </>
      ),
      links: <QuickLinksGrid {...QUICK_LINKS} count={0} />,
    },
  },
};

/** A primitive-native home replaces the overview once the app loads. */
export const PrimitiveHome: Story = {
  args: {
    home: (
      <EmptyState
        icon={<LayersIcon className="size-5" />}
        title="Primitive home"
        description="Bundle, cronjob, task or function home."
      />
    ),
  },
};

export const LongStrings: Story = {
  args: {
    app: LONG_APP,
    slug: LONG,
    appHref: `/apps/${LONG}`,
    tabs: <AppTabsView {...TABS} slug={LONG} />,
  },
};
