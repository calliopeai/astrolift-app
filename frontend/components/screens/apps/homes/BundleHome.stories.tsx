import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { BUNDLE, LONG, workload } from "./app-homes.fixtures";
import { BundleHomeScreen } from "./BundleHome";

const meta: Meta = {
  title: "Screens/Apps/Homes/BundleHome",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

export const Full: Story = { render: () => <BundleHomeScreen {...BUNDLE} /> };

/** The bundle arrives already loaded with the app; still provisioning is the closest state. */
export const Provisioning: Story = {
  render: () => <BundleHomeScreen {...BUNDLE} status="provisioning" />,
};

export const Empty: Story = {
  render: () => <BundleHomeScreen {...BUNDLE} workloads={[]} />,
};

/** No fetch of its own to fail; a failed app is the closest error state. */
export const Failed: Story = {
  render: () => <BundleHomeScreen {...BUNDLE} status="failed" />,
};

export const LongStrings: Story = {
  render: () => (
    <BundleHomeScreen
      {...BUNDLE}
      name={LONG}
      workloads={[
        workload({ id: "l1", slug: LONG, name: LONG, kind: "cronjob", schedule: "*/5 * * * *" }),
        workload({ id: "l2", slug: "api", name: `${LONG}-api`, isPublic: true, replicas: 1 }),
      ]}
    />
  ),
};

export const At768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <BundleHomeScreen {...BUNDLE} />
    </div>
  ),
};
