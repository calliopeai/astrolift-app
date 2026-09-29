import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { AgentProjectStepView } from "./AgentProjectStep";
import {
  LONG_PROJECT,
  LONG_PROJECT_STATE,
  PROJECT,
  PROJECT_STATE,
} from "./agents-wizard-a.fixtures";
import type { AgentProjectFields } from "./use-agent-project-step";

/**
 * The project step has no error UI of its own (a failed teams/projects query
 * renders as empty selects), so Empty is the closest "error".
 */
const meta: Meta = {
  title: "Screens/Agents/New/AgentProjectStep",
};
export default meta;

type Story = StoryObj;

/** Holds the wizard slice the step writes to, so the selects are usable. */
function Stateful({ initial, data }: { initial: AgentProjectFields; data: typeof PROJECT }) {
  const [state, setState] = React.useState(initial);
  return <AgentProjectStepView {...data} state={state} setState={setState} />;
}

export const Full: Story = {
  render: () => <Stateful initial={PROJECT_STATE} data={PROJECT} />,
};

/** Teams and projects not back yet: the closest real "loading" state. */
export const Loading: Story = {
  render: () => <Stateful initial={{ projectId: "" }} data={{ allTeams: [], allProjects: [] }} />,
};

/** No projects the viewer can register into. */
export const Empty: Story = {
  render: () => <Stateful initial={{ projectId: "" }} data={{ ...PROJECT, allProjects: [] }} />,
};

export const LongStrings: Story = {
  render: () => <Stateful initial={LONG_PROJECT_STATE} data={LONG_PROJECT} />,
};
