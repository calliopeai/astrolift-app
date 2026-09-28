import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import {
  ENV_LONG,
  ENV_PROD,
  ENV_STAGING_PAUSED,
  ENVIRONMENTS,
  LONG,
} from "@/components/screens/domains/domains-environments.fixtures";
import { AppTabsView } from "@/components/screens/apps/detail/AppTabs";

import { EnvironmentsScreen } from "./EnvironmentsScreen";

const meta: Meta = {
  title: "Screens/Environments/EnvironmentsScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

/** The global /environments list: rows open the environment detail. */
export const Full: Story = { render: () => <EnvironmentsScreen {...ENVIRONMENTS} /> };

/** An app's environments tab: scoped description, the app tab bar, inert rows. */
export const ForApp: Story = {
  render: () => (
    <EnvironmentsScreen
      {...ENVIRONMENTS}
      appSlug="storefront"
      environments={[ENV_PROD, ENV_STAGING_PAUSED]}
      tabs={
        <AppTabsView
          slug="storefront"
          basePath="/apps"
          pathname="/apps/storefront/environments"
          active="deployments"
        />
      }
    />
  ),
};

export const Loading: Story = {
  render: () => <EnvironmentsScreen {...ENVIRONMENTS} loading environments={[]} />,
};

export const Empty: Story = {
  render: () => <EnvironmentsScreen {...ENVIRONMENTS} environments={[]} />,
};

/**
 * The list has no error state (query and mutation errors surface as
 * toasts); the closest real one is deploys paused everywhere.
 */
export const AllPaused: Story = {
  render: () => (
    <EnvironmentsScreen
      {...ENVIRONMENTS}
      environments={[ENV_STAGING_PAUSED, { ...ENV_PROD, deploysPaused: true }]}
    />
  ),
};

/** No app.deploy permission: the pause / resume column is empty. */
export const ReadOnly: Story = {
  render: () => <EnvironmentsScreen {...ENVIRONMENTS} canPause={false} />,
};

/** A pause or resume in flight: every action disabled. */
export const Busy: Story = { render: () => <EnvironmentsScreen {...ENVIRONMENTS} busy /> };

export const LongStrings: Story = {
  render: () => (
    <EnvironmentsScreen {...ENVIRONMENTS} appSlug={LONG} environments={[ENV_LONG, ENV_PROD]} />
  ),
};
