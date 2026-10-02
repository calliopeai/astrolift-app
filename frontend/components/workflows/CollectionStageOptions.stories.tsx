import { useState } from "react";
import { NextIntlClientProvider } from "next-intl";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import en from "@/messages/en.json";
import es from "@/messages/es.json";
import de from "@/messages/de.json";
import fr from "@/messages/fr.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import pt from "@/messages/pt-BR.json";
import zh from "@/messages/zh-Hans.json";

import { CollectionStageOptions, type CollectionStageOptionsProps } from "./CollectionStageOptions";

const meta: Meta<typeof CollectionStageOptions> = {
  title: "Workflows/CollectionStageOptions",
  component: CollectionStageOptions,
  parameters: { layout: "padded" },
};
export default meta;
type Story = StoryObj<typeof meta>;

function Editor(args: CollectionStageOptionsProps) {
  const [iteration, setIteration] = useState(args.iteration);
  return (
    <div className="w-full max-w-md min-w-0">
      <CollectionStageOptions {...args} iteration={iteration} onChange={setIteration} />
      <output className="block text-xs break-all" aria-label="iteration JSON">
        {JSON.stringify(iteration)}
      </output>
    </div>
  );
}

export const Native: Story = {
  args: {
    kind: "collection",
    iteration: { max_items: 10, body_end: "review", items_path: "items" },
    targets: ["review", "final"],
    disabled: false,
    onChange: () => {},
  },
  render: (args) => <Editor {...args} />,
};
export const Imported: Story = {
  ...Native,
  args: {
    ...Native.args!,
    iteration: {
      source_format: "langflow_loop",
      max_items: 3,
      body_end: "parser",
      items: [{ text: "first" }, { text: "second" }],
    },
    targets: ["parser"],
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByLabelText(en.workflowCollections.bodyEnd)).toBeDisabled();
    const cap = canvas.getByLabelText(en.workflowCollections.cap);
    await userEvent.clear(cap);
    await userEvent.type(cap, "4");
    await expect(canvas.getByLabelText("iteration JSON")).toHaveTextContent('"max_items":4');
    await expect(canvas.getByLabelText("iteration JSON")).toHaveTextContent('"text":"second"');
  },
};
export const Formatter: Story = {
  ...Native,
  args: {
    ...Native.args!,
    kind: "format_record",
    iteration: { source_format: "langflow_parser", pattern: "Item: {text}", separator: "\n" },
  },
};
export const Invalid: Story = {
  ...Native,
  args: {
    ...Native.args!,
    iteration: { max_items: 51, body_end: "not-a-target", items_path: "items" },
  },
};
export const ReadOnly: Story = {
  ...Imported,
  args: { ...Imported.args!, disabled: true },
  play: undefined,
};
export const Translations: Story = {
  render: () => (
    <div className="grid w-full max-w-3xl grid-cols-1 gap-6 md:grid-cols-2">
      {(
        [
          ["en", en],
          ["es", es],
          ["de", de],
          ["fr", fr],
          ["ja", ja],
          ["ko", ko],
          ["pt-BR", pt],
          ["zh-Hans", zh],
        ] as const
      ).map(([locale, messages]) => (
        <section key={locale} data-locale={locale} className="min-w-0">
          <h2>{locale}</h2>
          <NextIntlClientProvider locale={locale} messages={messages} timeZone="UTC">
            <Editor {...(Imported.args as CollectionStageOptionsProps)} />
          </NextIntlClientProvider>
        </section>
      ))}
    </div>
  ),
};
