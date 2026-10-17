import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";
import { expect, fn, userEvent, within } from "storybook/test";

import { BrainSourceStep, type BrainSourceStepProps } from "./BrainSourceStep";
import { CONNECTIONS, DRAFT, PROJECTS } from "./new-brain.fixtures";
import { draftProblems, EMPTY_DRAFT, type NewBrainDraft, withDefaults } from "./new-brain-model";

const meta: Meta<typeof BrainSourceStep> = {
  title: "Screens/Brains/New/BrainSourceStep",
  component: BrainSourceStep,
  parameters: { layout: "padded" },
  args: {
    draft: DRAFT,
    onChange: fn(),
    connections: CONNECTIONS,
    connectionsLoading: false,
    connectionsError: null,
    onRetryConnections: fn(),
    problems: {},
    serverErrors: {},
  },
};
export default meta;

type Story = StoryObj<typeof BrainSourceStep>;

/** The step as the wizard drives it: edits land in the draft, checks follow. */
function Live(props: Partial<BrainSourceStepProps> & { initial?: NewBrainDraft }) {
  const [own, setOwn] = React.useState(props.initial ?? EMPTY_DRAFT);
  const context = { connections: props.connections ?? CONNECTIONS, projects: PROJECTS };
  const draft = withDefaults(own, context);
  return (
    <BrainSourceStep
      {...(meta.args as BrainSourceStepProps)}
      {...props}
      draft={draft}
      onChange={(patch) => setOwn({ ...draft, ...patch })}
      problems={draftProblems(draft, context)}
    />
  );
}

export const Filled: Story = {};

/** A repository in the wrong shape says so as it is typed. */
export const RepositoryFormat: Story = {
  render: () => <Live />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.type(canvas.getByLabelText(/Repository/), "acme platform");
    await expect(await canvas.findByRole("alert")).toHaveTextContent("owner/name");
    await userEvent.clear(canvas.getByLabelText(/Repository/));
    await userEvent.type(canvas.getByLabelText(/Repository/), "acme/platform-brain");
    await expect(canvas.queryByRole("alert")).not.toBeInTheDocument();
  },
};

/** A GitHub App install cannot create repositories: the step says so before the server does. */
export const AppInstall: Story = {
  args: { draft: { ...DRAFT, connectionId: CONNECTIONS[1]!.id } },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText(/cannot create repositories/)).toBeInTheDocument();
  },
};

/** Gitea has no internal visibility. */
export const Gitea: Story = { args: { draft: { ...DRAFT, connectionId: CONNECTIONS[2]!.id } } };

/** The server's refusal, on the field it names. */
export const RepositoryNotEmpty: Story = {
  args: {
    serverErrors: {
      repo: "acme-platform/platform-brain already has commits; use an empty or new repository",
    },
  },
};

export const TemplateRefFormat: Story = {
  args: { draft: { ...DRAFT, templateRef: "v1 ..2" }, problems: { templateRef: "refFormat" } },
};

export const ConnectionsLoading: Story = {
  args: { connections: [], connectionsLoading: true, draft: EMPTY_DRAFT },
};

export const ConnectionsFailed: Story = {
  args: { connections: [], connectionsError: "Network error", draft: EMPTY_DRAFT },
  play: async ({ args, canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getByRole("button", { name: "Retry" }));
    await expect(args.onRetryConnections).toHaveBeenCalled();
  },
};

/** No connection can act for the viewer: the step links to where one is made. */
export const NoConnections: Story = {
  args: { connections: [], draft: EMPTY_DRAFT },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole("link", { name: "Connect an account" })).toHaveAttribute(
      "href",
      "/providers#source"
    );
  },
};

export const Width768: Story = {
  render: (args) => (
    <div style={{ width: 768 }}>
      <BrainSourceStep {...args} />
    </div>
  ),
};
