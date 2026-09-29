import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { BootstrapPlanView } from "./BootstrapPlan";
import { LONG, PLAN } from "./fixtures";

const meta: Meta = { title: "Screens/Clusters/Settings/BootstrapPlan" };
export default meta;

type Story = StoryObj;

export const Full: Story = { render: () => <BootstrapPlanView {...PLAN} /> };

export const Loading: Story = {
  render: () => <BootstrapPlanView {...PLAN} plan={null} loading />,
};

/** The driver ships no recipe. */
export const Empty: Story = { render: () => <BootstrapPlanView {...PLAN} plan={null} /> };

export const Installing: Story = { render: () => <BootstrapPlanView {...PLAN} installing /> };

export const LongStrings: Story = {
  render: () => (
    <BootstrapPlanView
      {...PLAN}
      plan={{
        ...PLAN.plan!,
        providerPluginSlug: LONG,
        components: PLAN.plan!.components.map((c) => ({
          ...c,
          title: `${c.title} ${LONG}`,
          rationale: `${c.rationale} ${LONG}`,
          requires: c.requires.map((r) => `${r}-${LONG}`),
        })),
      }}
    />
  ),
};
