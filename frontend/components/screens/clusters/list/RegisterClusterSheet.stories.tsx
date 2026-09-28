import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { REGISTER } from "./fixtures";
import { RegisterClusterSheet } from "./RegisterClusterSheet";

const meta: Meta = {
  title: "Screens/Clusters/RegisterClusterSheet",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

const sheet = (patch: Partial<React.ComponentProps<typeof RegisterClusterSheet>> = {}) => (
  <RegisterClusterSheet {...REGISTER} open onOpenChange={() => {}} {...patch} />
);

export const Open: Story = { render: () => sheet() };

export const RegionsLoading: Story = {
  render: () => sheet({ regions: [], regionsLoading: true }),
};

/** No provider plugins registered: the submit stays disabled. */
export const NoPlugins: Story = { render: () => sheet({ plugins: [], pluginSlug: "" }) };

/** k8s_native has no region concept, so the picker is hidden. */
export const KubernetesNative: Story = { render: () => sheet({ pluginSlug: "k8s_native" }) };

export const Registering: Story = { render: () => sheet({ registering: true }) };

export const LongStrings: Story = {
  render: () =>
    sheet({
      plugins: [
        {
          id: "pl-long",
          slug: "custom-provider-plugin-with-a-deliberately-long-slug",
          name: "A custom provider plugin with a deliberately long display name",
          version: "0.0.1",
          isEnabled: true,
          capabilitiesManifest: {},
        },
      ],
      pluginSlug: "custom-provider-plugin-with-a-deliberately-long-slug",
      regions: [
        {
          id: "ap-southeast-4-melbourne-local-zone-1a",
          label: "Asia Pacific (Melbourne) local zone with a long label",
          continent: "oceania",
        },
      ],
    }),
};
