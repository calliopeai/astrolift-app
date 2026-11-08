import { PROVIDER_REQUEST_PROPS } from "./NvkeProviderRequestPanel.stories";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { fakeController } from "@/components/data-table/fixtures";
import { NvkeSiteScreen } from "./NvkeSiteScreen";
import accessStory from "./NvkeWorkloadAccessPanel.stories";
import temporalStory from "./NvkeTemporalPanel.stories";
import metricsStory from "./NvkeMetricsPanel.stories";
import { SITE, SITE_DETAIL } from "./fixtures";

export default {
  title: "Screens/NVKE/Site",
  component: NvkeSiteScreen,
  args: SITE_DETAIL,
} satisfies Meta<typeof NvkeSiteScreen>;
type Story = StoryObj<typeof NvkeSiteScreen>;
export const ImportedAccess: Story = {
  args: { workloadAccess: { ...accessStory.args, candidate: null } },
};
export const Full: Story = {};
export const NativeTemporal: Story = { args: { temporal: temporalStory.args } };
export const Metrics: Story = { args: { metrics: metricsStory.args } };
export const Loading: Story = { args: { site: null, loading: true } };
export const Empty: Story = {
  args: {
    operations: fakeController({ state: "empty", searchEnabled: false }),
    site: { ...SITE, connections: [] },
  },
};
export const LoadError: Story = { args: { site: null, error: "NVKE data could not be read." } };
export const CachedReadError: Story = { args: { error: "Refresh to verify current authority." } };
export const ReadOnly: Story = {
  args: { access: { read: true, manage: false, admin: false, loading: false } },
};
export const Disabled: Story = { args: { site: { ...SITE, isActive: false } } };
export const LongStrings: Story = {
  args: {
    site: {
      ...SITE,
      name: "Long research site name ".repeat(8),
      organizationNamespace: "long-organization-namespace-".repeat(2),
      connections: SITE.connections.map((c) => ({
        ...c,
        endpoint: "https://provider.example.test/" + "very-long-provider-path/".repeat(12),
      })),
    },
  },
};
export const Width768: Story = {
  render: (args) => (
    <div style={{ width: 768 }}>
      <NvkeSiteScreen {...args} />
    </div>
  ),
};

export const ProviderCollection: Story = { args: { providerRequest: PROVIDER_REQUEST_PROPS } };
