import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { ModelDetailScreen, type ModelDetailScreenProps } from "./ModelDetailScreen";
import { ModelReplicasView } from "./ModelReplicas";
import {
  LONG_MODELS,
  MODELS,
  REPLICAS,
  SHA_MODELS,
  TEST_DIALOG,
} from "./models-providers.fixtures";
import { ModelTestDialogView } from "./ModelTestDialog";
import { replicasOf } from "./use-model-replicas";

const meta: Meta = {
  title: "Screens/Models/ModelDetailScreen",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

function Detail({ model = MODELS[0], ...patch }: Partial<ModelDetailScreenProps>) {
  return (
    <ModelDetailScreen
      model={model}
      loading={false}
      error={null}
      onRetry={() => {}}
      replicas={
        model && (
          <ModelReplicasView
            {...REPLICAS}
            name={model.name}
            replicas={replicasOf((model.config ?? {}) as Record<string, unknown>)}
          />
        )
      }
      test={model && <ModelTestDialogView {...TEST_DIALOG} name={model.name} />}
      {...patch}
    />
  );
}

/** A hosted vLLM model: Test in the title row, replicas in their own panel. */
export const Hosted: Story = {
  render: () => <Detail />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole("button", { name: "Test" })).toBeInTheDocument();
    await expect(canvas.getByRole("button", { name: "Scale qwen up" })).toBeInTheDocument();
  },
};

/** Stopped: replicas 0, the toggle reads Start. */
export const Stopped: Story = { render: () => <Detail model={MODELS[1]} /> };

/** KServe: hosted, but it scales itself. */
export const KServe: Story = { render: () => <Detail model={MODELS[2]} /> };

/** A cloud endpoint that failed: the reason leads the first panel. */
export const Failed: Story = { render: () => <Detail model={MODELS[3]} /> };

export const Loading: Story = { render: () => <Detail model={null} loading /> };

export const NotFound: Story = { render: () => <Detail model={null} /> };

export const LoadError: Story = {
  render: () => <Detail model={null} error={{ message: "Network error: failed to fetch" }} />,
};

/** A 64-char SHA name and model id. */
export const LongStrings: Story = { render: () => <Detail model={SHA_MODELS[0]} /> };

/** A 200-char ARN failure, at the narrowest width. */
export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <Detail model={{ ...SHA_MODELS[1], name: LONG_MODELS[1].name }} />
    </div>
  ),
};
