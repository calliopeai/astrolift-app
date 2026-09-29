import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import {
  AGENT,
  AGENTS,
  DISPATCH,
  LONG_AGENT,
  SECRETS,
  BUNDLES,
} from "./agents-dispatch-secrets.fixtures";
import { AgentSecretBundlesView } from "./AgentSecretBundles";
import { AgentSecretsView } from "./AgentSecrets";
import { DispatchTabView } from "./DispatchTab";

const meta: Meta = {
  title: "Screens/Agents/List/DispatchTab",
};
export default meta;

type Story = StoryObj;

/** Agents are loaded but none is picked yet. */
export const Full: Story = {
  render: () => <DispatchTabView {...DISPATCH} />,
};

/** An agent is picked: Once form, cadence strip, run-mode matrix. */
export const AgentSelected: Story = {
  render: () => (
    <DispatchTabView
      {...DISPATCH}
      defaultAgentSlug={AGENT.slug}
      renderSecretsDialog={({ spec, open, onOpenChange }) => (
        <AgentSecretsView
          {...SECRETS}
          envSpecName={spec.name}
          open={open}
          onOpenChange={onOpenChange}
          bundles={<AgentSecretBundlesView {...BUNDLES} />}
        />
      )}
    />
  ),
};

/** Advanced opened: environment spec override and timeout. */
export const AdvancedOpen: Story = {
  render: () => <DispatchTabView {...DISPATCH} defaultAgentSlug={AGENT.slug} />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getByRole("button", { name: /Advanced/ }));
    await expect(await canvas.findByLabelText("Timeout (seconds)")).toBeInTheDocument();
  },
};

/** Schedule picked: routes to the Control tab instead of dispatching. */
export const ScheduleMode: Story = {
  render: () => (
    <DispatchTabView {...DISPATCH} defaultAgentSlug={AGENT.slug} defaultRunMode="SCHEDULE" />
  ),
};

/** A paused agent with no schedule, picking Schedule. */
export const PausedNotScheduled: Story = {
  render: () => (
    <DispatchTabView {...DISPATCH} defaultAgentSlug={AGENTS[1].slug} defaultRunMode="SCHEDULE" />
  ),
};

export const Dispatching: Story = {
  render: () => <DispatchTabView {...DISPATCH} defaultAgentSlug={AGENT.slug} dispatching />,
};

export const Loading: Story = {
  render: () => <DispatchTabView {...DISPATCH} agents={[]} fleetLoading />,
};

export const Empty: Story = {
  render: () => <DispatchTabView {...DISPATCH} agents={[]} />,
};

/**
 * The tab has no error state: a failed fleet query renders as Empty and a
 * failed dispatch is a toast. This is the closest real state, the Once form
 * with an invalid timeout.
 */
export const InvalidTimeout: Story = {
  render: () => <DispatchTabView {...DISPATCH} defaultAgentSlug={AGENT.slug} />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getByRole("button", { name: /Advanced/ }));
    await userEvent.type(await canvas.findByLabelText("Timeout (seconds)"), "0");
    await expect(
      await canvas.findByText("Enter a whole number of seconds above zero.")
    ).toBeInTheDocument();
  },
};

export const LongStrings: Story = {
  render: () => (
    <DispatchTabView
      {...DISPATCH}
      agents={[LONG_AGENT, ...AGENTS]}
      defaultAgentSlug={LONG_AGENT.slug}
    />
  ),
};
