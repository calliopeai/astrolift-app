import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";
import { ProjectTopologyScreen } from "./ProjectTopologyScreen";
import {
  PROJECT,
  WORKFLOWS,
  AGENTS,
  APPS,
  LIVE_AGENTS,
  WORKFLOW_RUNS,
} from "./projects-detail.fixtures";
const meta: Meta<typeof ProjectTopologyScreen> = {
  title: "Screens/Projects/ProjectTopologyScreen",
  component: ProjectTopologyScreen,
  parameters: { layout: "fullscreen" },
  args: {
    slug: PROJECT.slug,
    project: PROJECT,
    workflows: WORKFLOWS,
    agents: AGENTS,
    apps: APPS,
    liveAgents: LIVE_AGENTS,
    workflowRuns: WORKFLOW_RUNS,
    loading: false,
    partialError: false,
    focus: "",
    selectedId: "",
    onFocus: () => {},
    onSelect: () => {},
  },
};
export default meta;
type Story = StoryObj<typeof ProjectTopologyScreen>;
export const WholeProject: Story = {};
export const AgentSelected: Story = { args: { selectedId: `agent:${AGENTS[0].id}` } };
export const WorkflowStages: Story = {
  args: {
    focus: WORKFLOWS[0].guid,
    selectedId: `stage:${WORKFLOWS[0].guid}:${WORKFLOWS[0].stages[0].guid}`,
  },
};
export const Empty: Story = { args: { workflows: [], agents: [], apps: [] } };
export const Loading: Story = { args: { loading: true } };
export const PartialError: Story = { args: { partialError: true } };
export const Narrow: Story = { globals: { viewport: { value: "tablet", isRotated: false } } };
export const InspectorLinks: Story = {
  ...AgentSelected,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole("link", { name: /See more/ })).toHaveAttribute(
      "href",
      `/agents/${AGENTS[0].slug}/observe`
    );
    await userEvent.click(canvas.getByRole("button", { name: "Component list" }));
    await expect(canvas.getByRole("button", { name: `Agent · ${AGENTS[0].name}` })).toBeVisible();
  },
};
