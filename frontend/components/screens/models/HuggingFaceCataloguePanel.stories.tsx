import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";
import { NextIntlClientProvider } from "next-intl";
import fr from "@/messages/fr.json";
import zh from "@/messages/zh-Hans.json";
import { HuggingFaceCataloguePanel } from "./HuggingFaceCataloguePanel";
import { hfCatalogueList } from "./hf-catalogue-list";
import { hfCatalogueProps } from "./shared-model.fixtures";
const meta = {
  title: "Screens/Models/HuggingFaceCataloguePanel",
  component: HuggingFaceCataloguePanel,
  parameters: { layout: "padded" },
} satisfies Meta<typeof HuggingFaceCataloguePanel>;
export default meta;
type Story = StoryObj<typeof meta>;
export const Catalogue: Story = {
  args: hfCatalogueProps,
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await expect(c.getByText(/Runtime compatibility unknown/)).toBeInTheDocument();
    await expect(c.getByText("Qwen/Qwen3-8B")).toBeInTheDocument();
  },
};
export const Resolving: Story = {
  args: { ...hfCatalogueProps, selectedRepoId: "Qwen/Qwen3-8B", resolving: true },
};
export const PinnedRevision: Story = {
  args: {
    ...hfCatalogueProps,
    selectedRepoId: "Qwen/Qwen3-8B",
    resolvedModel: { ...hfCatalogueProps.page.rows[0], revisionSha: "a".repeat(40) },
  },
};
export const RateLimited: Story = {
  args: {
    ...hfCatalogueProps,
    state: "RATE_LIMITED",
    retryAfterSeconds: 45,
    page: { ...hfCatalogueProps.page, rows: [] },
  },
};
export const Unavailable: Story = {
  args: { ...hfCatalogueProps, state: "UNAVAILABLE", page: { ...hfCatalogueProps.page, rows: [] } },
};
export const Empty: Story = {
  args: { ...hfCatalogueProps, state: "NO_DATA", page: { ...hfCatalogueProps.page, rows: [] } },
};
export const Loading: Story = {
  args: {
    ...hfCatalogueProps,
    state: null,
    page: { ...hfCatalogueProps.page, loading: true, rows: [] },
  },
};
export const French: Story = {
  args: {
    ...PinnedRevision.args,
    page: {
      ...hfCatalogueProps.page,
      list: {
        ...hfCatalogueProps.page.list,
        definition: hfCatalogueList({
          ...fr.models.shared.catalogue,
          sorts: fr.models.shared.catalogue.sort,
        }),
      },
    },
  },
  decorators: [
    (Story) => (
      <NextIntlClientProvider locale="fr" messages={fr} timeZone="UTC">
        <Story />
      </NextIntlClientProvider>
    ),
  ],
};
export const Chinese: Story = {
  args: {
    ...PinnedRevision.args,
    page: {
      ...hfCatalogueProps.page,
      list: {
        ...hfCatalogueProps.page.list,
        definition: hfCatalogueList({
          ...zh.models.shared.catalogue,
          sorts: zh.models.shared.catalogue.sort,
        }),
      },
    },
  },
  decorators: [
    (Story) => (
      <NextIntlClientProvider locale="zh-Hans" messages={zh} timeZone="UTC">
        <Story />
      </NextIntlClientProvider>
    ),
  ],
};
export const Width768: Story = {
  args: {
    ...hfCatalogueProps,
    page: {
      ...hfCatalogueProps.page,
      rows: [
        {
          ...hfCatalogueProps.page.rows[0],
          repoId:
            "a-very-long-publisher/a-very-long-model-name-with-a-complete-immutable-revision-and-unknown-compatibility",
        },
      ],
    },
  },
  render: (args) => (
    <div style={{ width: 768 }}>
      <HuggingFaceCataloguePanel {...args} />
    </div>
  ),
};
