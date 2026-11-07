import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { fakeController } from "@/components/data-table/fixtures";
import { NvkeSitesScreen } from "./NvkeSitesScreen";
import { SITE, SITES } from "./fixtures";

export default {
  title: "Screens/NVKE/Sites",
  component: NvkeSitesScreen,
  args: SITES,
} satisfies Meta<typeof NvkeSitesScreen>;
type Story = StoryObj<typeof NvkeSitesScreen>;
export const Full: Story = {};
export const Loading: Story = {
  args: { table: fakeController({ state: "loading", searchEnabled: false }) },
};
export const Empty: Story = {
  args: { table: fakeController({ state: "empty", searchEnabled: false }) },
};
export const LoadError: Story = {
  args: {
    table: fakeController({
      state: "error",
      error: new Error("Fixture read failure"),
      searchEnabled: false,
    }),
  },
};
export const ReadOnly: Story = {
  args: { access: { read: true, manage: false, admin: false, loading: false } },
};
export const LongStrings: Story = {
  args: {
    table: fakeController({
      rows: [
        {
          ...SITE,
          name: "A research site with a very long descriptive name ".repeat(4),
          slug: "research-".repeat(18),
          organizationNamespace: "research-namespace-".repeat(3),
        },
      ],
      searchEnabled: false,
      sortEnabled: false,
    }),
  },
};
export const Width768: Story = {
  render: (args) => (
    <div style={{ width: 768 }}>
      <NvkeSitesScreen {...args} />
    </div>
  ),
};
