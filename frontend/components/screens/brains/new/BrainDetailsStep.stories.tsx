import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";
import { expect, fn, userEvent, within } from "storybook/test";

import { BrainDetailsStep, type BrainDetailsStepProps } from "./BrainDetailsStep";
import { CONNECTIONS, DRAFT, PROJECTS } from "./new-brain.fixtures";
import { draftProblems, EMPTY_DRAFT, type NewBrainDraft, withDefaults } from "./new-brain-model";

const meta: Meta<typeof BrainDetailsStep> = {
  title: "Screens/Brains/New/BrainDetailsStep",
  component: BrainDetailsStep,
  parameters: { layout: "padded" },
  args: {
    draft: DRAFT,
    onChange: fn(),
    projects: PROJECTS,
    projectsLoading: false,
    projectsError: null,
    onRetryProjects: fn(),
    canDeploy: true,
    problems: {},
    serverErrors: {},
  },
};
export default meta;

type Story = StoryObj<typeof BrainDetailsStep>;

function Live(props: Partial<BrainDetailsStepProps> & { initial?: NewBrainDraft }) {
  const [own, setOwn] = React.useState(props.initial ?? EMPTY_DRAFT);
  const context = { connections: CONNECTIONS, projects: PROJECTS };
  const draft = withDefaults(own, context);
  return (
    <BrainDetailsStep
      {...(meta.args as BrainDetailsStepProps)}
      {...props}
      draft={draft}
      onChange={setOwn}
      problems={draftProblems(draft, context)}
    />
  );
}

export const Filled: Story = {};

/** The name suggests the slug until the slug is typed. */
export const SlugFromName: Story = {
  render: () => <Live />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.type(canvas.getByLabelText("Name"), "Sales Playbooks");
    await expect(canvas.getByLabelText(/Slug/)).toHaveValue("sales-playbooks");
    await userEvent.clear(canvas.getByLabelText(/Slug/));
    await userEvent.type(canvas.getByLabelText(/Slug/), "Sales_Brain");
    await expect(await canvas.findByRole("alert")).toHaveTextContent("Lowercase letters");
    await userEvent.type(canvas.getByLabelText("Name"), " 2");
    await expect(canvas.getByLabelText(/Slug/)).toHaveValue("Sales_Brain");
  },
};

/** Without app.deploy the layer is created without a first deploy, and the step says why. */
export const NoDeployPermission: Story = {
  args: { canDeploy: false },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole("checkbox", { name: "Deploy now" })).toBeDisabled();
    await expect(canvas.getByText(/needs app.deploy/)).toBeInTheDocument();
  },
};

/** The server refused the slug; the message sits on the field. */
export const SlugTaken: Story = {
  args: {
    serverErrors: { slug: "app with slug 'platform-brain' already exists in this organization" },
  },
};

export const BadEnvironment: Story = {
  args: {
    draft: { ...DRAFT, environmentName: "Production Main" },
    problems: { environmentName: "envFormat" },
  },
};

export const ProjectsLoading: Story = { args: { projects: [], projectsLoading: true } };

export const ProjectsFailed: Story = {
  args: { projects: [], projectsError: "Network error" },
  play: async ({ args, canvasElement }) => {
    await userEvent.click(within(canvasElement).getByRole("button", { name: "Retry" }));
    await expect(args.onRetryProjects).toHaveBeenCalled();
  },
};

export const NoProjects: Story = { args: { projects: [], draft: EMPTY_DRAFT } };

export const LongNames: Story = {
  args: {
    draft: {
      ...DRAFT,
      name: "A semantic layer with a name long enough to wrap in every locale the console ships",
      slug: "a-semantic-layer-with-a-slug-long-enough-to-reach-the-limit-63",
    },
  },
};
