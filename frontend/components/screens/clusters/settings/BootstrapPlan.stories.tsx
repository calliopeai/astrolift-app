import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { BootstrapPlanView } from "./BootstrapPlan";
import { LONG, PLAN } from "./fixtures";

const meta: Meta = { title: "Screens/Clusters/Settings/BootstrapPlan" };
export default meta;

type Story = StoryObj;

export const Full: Story = { render: () => <BootstrapPlanView {...PLAN} /> };

export const Loading: Story = {
  render: () => <BootstrapPlanView {...PLAN} plan={null} loading />,
};

export const Empty: Story = {
  render: () => <BootstrapPlanView {...PLAN} plan={{ ...PLAN.plan!, components: [] }} />,
};

export const Installing: Story = { render: () => <BootstrapPlanView {...PLAN} installing /> };

export const ReadFailed: Story = {
  render: () => (
    <BootstrapPlanView
      {...PLAN}
      error="LITERAL_BOOTSTRAP_READ_DIAGNOSTIC"
      onRetry={() => undefined}
    />
  ),
};

export const ReadOnly: Story = { render: () => <BootstrapPlanView {...PLAN} readOnly /> };

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

/** calliope-installer#447: a controller acting for a withheld capability is offered disabled, with the reason. */
export const WithheldController: Story = {
  render: () => (
    <BootstrapPlanView
      {...PLAN}
      plan={{
        ...PLAN.plan!,
        components: PLAN.plan!.components.map((c) =>
          c.key === "aws_lb_controller"
            ? {
                ...c,
                withheldReason:
                  "Load balancers are withheld from Astrolift on this install: it makes no load balancers and runs no AWS Load Balancer Controller of its own.",
              }
            : c
        ),
      }}
    />
  ),
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    const box = canvas.getByRole("checkbox", { name: /AWS Load Balancer Controller/i });
    await expect(box).toBeDisabled();
    await expect(box).not.toBeChecked();
    await expect(canvas.getByText(/Load balancers are withheld/)).toBeVisible();
  },
};
