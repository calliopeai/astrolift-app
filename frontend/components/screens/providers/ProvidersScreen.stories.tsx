import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { CloudIcon } from "lucide-react";

import { EmptyState } from "@/components/EmptyState";

import { CLOUD_PROVIDERS } from "../models/models-providers.fixtures";

import { CloudProvidersPanelView } from "./CloudProvidersPanel";
import { ProvidersScreen } from "./ProvidersScreen";

const meta: Meta<typeof ProvidersScreen> = {
  title: "Screens/Providers/ProvidersScreen",
  component: ProvidersScreen,
  parameters: { layout: "fullscreen" },
  args: {
    tab: "cloud",
    selectTab: () => {},
    // Source and Identity are their own screens (Screens/Settings/*); a
    // stand-in marks where they mount.
    panels: {
      cloud: <CloudProvidersPanelView {...CLOUD_PROVIDERS} />,
      source: <EmptyState icon={<CloudIcon className="size-5" />} title="Source panel" />,
      identity: <EmptyState icon={<CloudIcon className="size-5" />} title="Identity panel" />,
    },
  },
};
export default meta;

type Story = StoryObj<typeof ProvidersScreen>;

export const Cloud: Story = {};

export const Source: Story = { args: { tab: "source" } };

export const Identity: Story = { args: { tab: "identity" } };

export const Loading: Story = {
  args: {
    panels: {
      cloud: <CloudProvidersPanelView {...CLOUD_PROVIDERS} loading />,
      source: null,
      identity: null,
    },
  },
};

export const Empty: Story = {
  args: {
    panels: {
      cloud: (
        <CloudProvidersPanelView
          {...CLOUD_PROVIDERS}
          pluginCount={0}
          configured={[]}
          available={[]}
        />
      ),
      source: null,
      identity: null,
    },
  },
};
