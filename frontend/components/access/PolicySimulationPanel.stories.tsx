import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { LONG, SIMULATION, SIMULATION_EMPTY } from "./fixtures";
import { PolicySimulationPanel } from "./PolicySimulationPanel";

/**
 * A draft policy run against today's holders and the recorded decisions
 * (`astroliftPolicySimulation`), on the policy editor's review step.
 */
const meta: Meta<typeof PolicySimulationPanel> = {
  title: "Access/PolicySimulationPanel",
  component: PolicySimulationPanel,
  parameters: { layout: "padded" },
  args: { simulation: SIMULATION, personHref: (id) => `/administration/access/people/${id}` },
  decorators: [(Story) => <div className="max-w-3xl">{Story()}</div>],
};
export default meta;

type Story = StoryObj<typeof PolicySimulationPanel>;

/** Denied holders, one it cannot decide for, and the recorded decisions that would flip. */
export const Full: Story = {
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getByTestId("simulation-summary")).toHaveTextContent(
      "3 of 41 holders would be denied"
    );
  },
};

/** Nothing recorded and nobody holds it: says so rather than showing empty lists. */
export const Empty: Story = { args: { simulation: SIMULATION_EMPTY } };

export const Loading: Story = { args: { simulation: null, loading: true } };

export const Error: Story = {
  args: { simulation: null, error: "upstream timed out after 30s", onRetry: () => {} },
};

/** The server refused the draft: its reasons in place. */
export const Refused: Story = {
  args: {
    simulation: { ...SIMULATION, ok: false, errors: ["actionPattern matches no permission"] },
    onRetry: () => {},
  },
};

export const LongStrings: Story = {
  args: {
    simulation: {
      ...SIMULATION,
      actions: [`app.${LONG}`, "app.deploy"],
      holders: SIMULATION.holders.map((h) => ({
        ...h,
        user: { ...h.user, username: LONG, email: `${LONG}@example.com` },
        detail: LONG,
      })),
    },
  },
};

export const Width768: Story = {
  decorators: [(Story) => <div style={{ width: 768 }}>{Story()}</div>],
};
