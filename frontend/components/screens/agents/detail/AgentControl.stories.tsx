import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { AgentControlScreen } from "./AgentControl";
import {
  CONTROL,
  CONTROL_AGENT,
  CONTROL_LONG,
  CONTROL_SAVES_SERVICE,
  pendingSave,
  PERSISTED_SERVICE_SPEC,
  rejectField,
} from "./agent-control.fixtures";
import { EDITOR } from "./agent-trigger-editor.fixtures";
import { TriggerBindingEditorView } from "./TriggerBindingEditor";

const trigger = <TriggerBindingEditorView {...EDITOR} />;

const meta: Meta<typeof AgentControlScreen> = {
  title: "Screens/Agents/Detail/AgentControl",
  component: AgentControlScreen,
  args: { ...CONTROL, triggerEditor: trigger },
};
export default meta;

type Story = StoryObj<typeof AgentControlScreen>;

/**
 * A Service with scheduled scaling, after a save has read the persisted spec
 * back (replicas + scaling crons seeded, no "save to set" notes).
 */
export const Full: Story = {
  args: { ...CONTROL_SAVES_SERVICE, agent: { ...CONTROL_AGENT, ...PERSISTED_SERVICE_SPEC } },
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await userEvent.click(c.getByRole("button", { name: /Save run spec/ }));
    await expect(await c.findByDisplayValue("0 8 * * 1-5")).toBeInTheDocument();
  },
};

/** Nothing configured yet: a Task that runs Once, not paused. */
export const Empty: Story = {};

/**
 * The editor has no loading state of its own (the agent shell loads the row
 * before this renders); the closest real one is a save in flight.
 */
export const Loading: Story = {
  args: { saving: true, onSave: pendingSave },
};

export const Schedule: Story = {
  args: { agent: { ...CONTROL_AGENT, runMode: "schedule", runCronExpression: "0 3 * * *" } },
};

export const InvalidCron: Story = {
  args: { agent: { ...CONTROL_AGENT, runMode: "schedule", runCronExpression: "every day" } },
};

/** The server rejects the cron; the message renders under its control. */
export const SaveRejected: Story = {
  args: {
    agent: { ...CONTROL_AGENT, runMode: "schedule", runCronExpression: "0 3 * * *" },
    onSave: rejectField("runCronExpression", "Cron expression is not valid for the platform."),
  },
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await userEvent.click(c.getByRole("button", { name: /Save run spec/ }));
    await expect(
      await c.findByText("Cron expression is not valid for the platform.")
    ).toBeInTheDocument();
  },
};

/** A server error on a field with no inline control falls to the banner. */
export const SaveRejectedUnmapped: Story = {
  args: {
    onSave: rejectField("agentSlug", "You don't have permission to edit this agent."),
  },
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await userEvent.click(c.getByRole("button", { name: /Save run spec/ }));
    await expect(
      await c.findByText("You don't have permission to edit this agent.")
    ).toBeInTheDocument();
  },
};

export const Loop: Story = {
  args: { agent: { ...CONTROL_AGENT, runMode: "loop", runMaxParallel: 0 } },
};

export const Trigger: Story = {
  args: { agent: { ...CONTROL_AGENT, runMode: "trigger" } },
};

/** Stored Service replicas are visible before any save. */
export const Service: Story = {
  args: { agent: { ...CONTROL_AGENT, runFamily: "service", runPaused: true, replicas: 7 } },
};

export const LongStrings: Story = {
  args: {
    agent: {
      ...CONTROL_AGENT,
      name: CONTROL_LONG,
      slug: CONTROL_LONG,
      runMode: "schedule",
      runCronExpression: "0,5,10,15,20,25,30,35,40,45,50,55 0-23 1-31 1-12 0-6",
    },
    onSave: rejectField("runCronExpression", `${CONTROL_LONG} ${CONTROL_LONG}`),
  },
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await userEvent.click(c.getByRole("button", { name: /Save run spec/ }));
    await expect(await c.findByText(`${CONTROL_LONG} ${CONTROL_LONG}`)).toBeInTheDocument();
  },
};
