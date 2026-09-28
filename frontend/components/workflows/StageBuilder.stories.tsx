import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { BUILDER, DEFINITION } from "@/components/workflows/fixtures";
import { StageBuilder } from "@/components/workflows/StageBuilder";

const meta: Meta = {
  title: "Screens/Workflows/StageBuilder",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

export const Editable: Story = { render: () => <StageBuilder {...BUILDER} /> };

export const NoStagesYet: Story = { render: () => <StageBuilder {...BUILDER} stages={[]} /> };

export const StagesLoading: Story = {
  render: () => <StageBuilder {...BUILDER} stages={[]} stagesLoading />,
};

/** A platform template: read-only with a clone banner. */
export const PlatformTemplate: Story = {
  render: () => (
    <StageBuilder
      {...BUILDER}
      definition={{ ...DEFINITION, isGlobal: true, organizationGuid: null }}
    />
  ),
};

/** Owned by a repository: read-only, edit the file and sync. */
export const RepositoryManaged: Story = {
  render: () => (
    <StageBuilder
      {...BUILDER}
      definition={{
        ...DEFINITION,
        sourceRepo: "calliopeai/company-agents",
        sourcePath: "workflows/outbound.toml",
        sourceRef: "f1f9f11a0c2e4b7d",
      }}
    />
  ),
};

export const NoManageAccess: Story = {
  render: () => <StageBuilder {...BUILDER} canManage={false} />,
};

export const DefinitionLoading: Story = {
  render: () => <StageBuilder {...BUILDER} definition={null} defLoading />,
};

export const DefinitionNotFound: Story = {
  render: () => <StageBuilder {...BUILDER} definition={null} />,
};

/** The TOML manifest view. */
export const CodeView: Story = {
  render: () => <StageBuilder {...BUILDER} />,
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await userEvent.click(c.getByRole("button", { name: /Code/ }));
    await expect(await c.findByLabelText("Workflow manifest TOML")).toBeInTheDocument();
  },
};

export const GraphView: Story = {
  render: () => <StageBuilder {...BUILDER} />,
  play: async ({ canvasElement }) => {
    await userEvent.click(within(canvasElement).getByRole("button", { name: /Graph/ }));
  },
};
