import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { WorkflowTemplatesScreen } from "./WorkflowTemplatesScreen";
import { LONG_DEFINITION, TEMPLATES, loadError, templatesProps } from "./workflows-list.fixtures";

const meta: Meta = {
  title: "Screens/Workflows/List/WorkflowTemplatesScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

export const Full: Story = {
  render: () => <WorkflowTemplatesScreen {...templatesProps()} />,
};

export const Loading: Story = {
  render: () => <WorkflowTemplatesScreen {...templatesProps({ loading: true, templates: [] })} />,
};

export const Empty: Story = {
  render: () => <WorkflowTemplatesScreen {...templatesProps({ templates: [] })} />,
};

export const LoadFailed: Story = {
  render: () => (
    <WorkflowTemplatesScreen
      {...templatesProps({
        templates: [],
        error: loadError("workflowDefinitions: permission denied"),
      })}
    />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <WorkflowTemplatesScreen
      {...templatesProps({
        templates: [
          { ...LONG_DEFINITION, isGlobal: true, patternKind: "custom_pattern" },
          ...TEMPLATES,
        ],
      })}
    />
  ),
};

export const Cloning: Story = {
  render: () => (
    <WorkflowTemplatesScreen {...templatesProps({ cloningSlugs: [TEMPLATES[0].slug] })} />
  ),
};

export const ReadOnly: Story = {
  render: () => <WorkflowTemplatesScreen {...templatesProps({ canCreate: false })} />,
};
