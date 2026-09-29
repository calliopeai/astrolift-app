import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { useLocalListState } from "@/components/list/use-list-state";

import { PipelineSecretsView as View, type PipelineSecretsViewProps } from "./PipelineSecrets";
import { PIPELINE_SECRETS_LIST } from "./pipelines-list";
import { LONG_SECRET, secretsProps } from "./pipelines-previews.fixtures";

const meta: Meta = {
  title: "Screens/Pipelines/PipelineSecrets",
};
export default meta;

type Story = StoryObj;

/** The view with its list state in memory, as the hook keeps it. */
function PipelineSecretsView(props: PipelineSecretsViewProps) {
  const list = useLocalListState(PIPELINE_SECRETS_LIST);
  return <View {...props} list={list} />;
}

export const Full: Story = { render: () => <PipelineSecretsView {...secretsProps()} /> };

export const Loading: Story = {
  render: () => <PipelineSecretsView {...secretsProps({ secrets: [], loading: true })} />,
};

export const Empty: Story = {
  render: () => <PipelineSecretsView {...secretsProps({ secrets: [] })} />,
};

/**
 * The secrets query surfaces no error state here: a failed fetch renders as
 * the empty list. A failed delete shows inline in the confirm dialog.
 */
export const ErrorState: Story = {
  render: () => <PipelineSecretsView {...secretsProps({ secrets: [] })} />,
};

/** A delete in flight: every row's delete action is disabled. */
export const Deleting: Story = {
  render: () => <PipelineSecretsView {...secretsProps({ deleting: true })} />,
};

/** Add secret opens the inline, write-only form. */
export const AddForm: Story = {
  render: () => <PipelineSecretsView {...secretsProps()} />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getByRole("button", { name: /add secret/i }));
    await expect(canvas.getByLabelText("Value")).toHaveAttribute("type", "password");
  },
};

export const LongStrings: Story = {
  render: () => <PipelineSecretsView {...secretsProps({ secrets: [LONG_SECRET] })} />,
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <PipelineSecretsView {...secretsProps({ secrets: [LONG_SECRET] })} />
    </div>
  ),
};
