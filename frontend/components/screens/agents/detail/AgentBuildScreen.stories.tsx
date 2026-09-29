import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import {
  BUILD,
  BUILD_EMPTY,
  BUILD_ERROR,
  BUILD_LOADING,
  BUILD_LONG,
} from "./agent-build-run.fixtures";
import { AgentBuildScreen } from "./AgentBuildScreen";

const meta: Meta = {
  title: "Screens/Agents/Detail/AgentBuildScreen",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

/** Repo, primary container, workload, brief and three skills (one inactive, one without tools). */
export const Full: Story = { render: () => <AgentBuildScreen {...BUILD} /> };

export const Loading: Story = { render: () => <AgentBuildScreen {...BUILD_LOADING} /> };

/** A freshly registered agent: no repo, container, brief or skills. */
export const Empty: Story = { render: () => <AgentBuildScreen {...BUILD_EMPTY} /> };

/** The agent detail join failed; the brief and skills cards say so. */
export const LoadFailed: Story = { render: () => <AgentBuildScreen {...BUILD_ERROR} /> };

/** Long slugs, refs and descriptions, a non-primary container, an unknown adapter. */
export const LongStrings: Story = { render: () => <AgentBuildScreen {...BUILD_LONG} /> };

/** Inside the agent frame: skills are a summary with View all to the Skills & tools tab. */
export const SkillsSummary: Story = {
  render: () => <AgentBuildScreen {...BUILD} skillsHref="/agents/support-bot/skills" />,
};

export const SkillsSummaryEmpty: Story = {
  render: () => <AgentBuildScreen {...BUILD_EMPTY} skillsHref="/agents/support-bot/skills" />,
};
