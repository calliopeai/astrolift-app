import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { type ListState, useLocalListState } from "@/components/list/use-list-state";

import { type ModelEndpoint, MODELS_LIST } from "./models-list";
import {
  LONG_MODELS,
  MANY_MODELS,
  MODELS,
  serveModels,
  SHA_MODELS,
} from "./models-providers.fixtures";
import { ModelsScreen, type ModelsScreenProps } from "./ModelsScreen";

const meta: Meta = {
  title: "Screens/Models/ModelsScreen",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

type Props = Partial<Omit<ModelsScreenProps, "list">> & {
  models?: ModelEndpoint[];
  initial?: Partial<ListState>;
};

/** The screen over fixture models, filtered and paged the way the server does it. */
function Models({ models = MODELS, initial, ...patch }: Props) {
  const list = useLocalListState(MODELS_LIST, initial);
  const { rows, totalCount } = serveModels(models, {
    filters: list.filters,
    q: list.state.q,
    sort: list.state.sort,
    page: list.state.page,
    pageSize: list.state.pageSize,
  });
  return (
    <ModelsScreen
      list={list}
      rows={rows}
      totalCount={totalCount}
      loading={false}
      stale={false}
      error={null}
      onRetry={() => {}}
      {...patch}
    />
  );
}

export const Full: Story = {
  render: () => <Models />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText("Qwen/Qwen3-8B")).toBeInTheDocument();
    await expect(canvas.getByText("cloud")).toBeInTheDocument();
    for (const view of ["All", "Mine", "Endpoints", "Hosted (GPU)"]) {
      await expect(canvas.getByRole("link", { name: view })).toBeInTheDocument();
    }
    await expect(canvas.getByRole("link", { name: "Deploy model" })).toHaveAttribute(
      "href",
      "/models/deploy"
    );
  },
};

export const Loading: Story = { render: () => <Models models={[]} loading /> };

export const Empty: Story = {
  render: () => <Models models={[]} />,
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getByText("No models yet")).toBeInTheDocument();
  },
};

export const LoadError: Story = {
  render: () => <Models models={[]} error={{ message: "Network error: failed to fetch" }} />,
};

/** Cloud-served endpoints only. */
export const Endpoints: Story = { render: () => <Models initial={{ view: "endpoints" }} /> };

/** vLLM and KServe on the org's own GPUs. */
export const Hosted: Story = { render: () => <Models initial={{ view: "hosted" }} /> };

/** Mine: the endpoints the viewer deployed. */
export const Mine: Story = { render: () => <Models initial={{ view: "mine" }} /> };

export const EmptyFiltered: Story = {
  render: () => <Models initial={{ filters: { cluster: "gke-eu-west-4" } }} />,
};

/** Forty models: numbered pages. */
export const Paged: Story = { render: () => <Models models={MANY_MODELS} initial={{ page: 2 }} /> };

/** A 64-char SHA, a 200-char ARN, an unbroken URL, and long owner names. */
export const LongStrings: Story = {
  render: () => <Models models={[...SHA_MODELS, ...LONG_MODELS, ...MODELS]} />,
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <Models models={[...SHA_MODELS, ...LONG_MODELS, ...MANY_MODELS]} />
    </div>
  ),
};
