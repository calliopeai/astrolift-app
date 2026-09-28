import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { DeployModelSheetView } from "./DeployModelSheet";
import { ModelReplicasView } from "./ModelReplicas";
import { type ModelEndpoint, ModelsScreen } from "./ModelsScreen";
import { ModelTestDialogView } from "./ModelTestDialog";
import {
  DEPLOY,
  LONG_MODELS,
  MODELS_SCREEN,
  REPLICAS,
  TEST_DIALOG,
} from "./models-providers.fixtures";
import { replicasOf } from "./use-model-replicas";

const meta: Meta = {
  title: "Screens/Models/ModelsScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

const slots = {
  renderReplicas: (m: ModelEndpoint) => (
    <ModelReplicasView {...REPLICAS} name={m.name} replicas={replicasOf(m.config)} />
  ),
  renderTest: (m: ModelEndpoint) => <ModelTestDialogView {...TEST_DIALOG} name={m.name} />,
  renderDeploySheet: ({
    open,
    onOpenChange,
  }: {
    open: boolean;
    onOpenChange: (open: boolean) => void;
  }) => <DeployModelSheetView {...DEPLOY} open={open} onOpenChange={onOpenChange} />,
};

export const Full: Story = {
  render: () => <ModelsScreen {...MODELS_SCREEN} {...slots} />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText("Qwen/Qwen3-8B")).toBeInTheDocument();
    await expect(canvas.getByText("cloud")).toBeInTheDocument();
  },
};

export const Loading: Story = {
  render: () => <ModelsScreen {...MODELS_SCREEN} {...slots} models={[]} loading />,
};

export const Empty: Story = {
  render: () => <ModelsScreen {...MODELS_SCREEN} {...slots} models={[]} />,
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getByText("No models yet")).toBeInTheDocument();
  },
};

export const LoadError: Story = {
  render: () => (
    <ModelsScreen
      {...MODELS_SCREEN}
      {...slots}
      models={[]}
      error={new globalThis.Error("Network error: failed to fetch")}
    />
  ),
};

export const LongStrings: Story = {
  render: () => <ModelsScreen {...MODELS_SCREEN} {...slots} models={LONG_MODELS} />,
};
