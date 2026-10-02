import { NextIntlClientProvider } from "next-intl";

import en from "@/messages/en.json";
import es from "@/messages/es.json";
import de from "@/messages/de.json";
import fr from "@/messages/fr.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import pt from "@/messages/pt-BR.json";
import zh from "@/messages/zh-Hans.json";

import { useState } from "react";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { BoundedStageOptions, type BoundedStageOptionsProps } from "./BoundedStageOptions";

const meta: Meta<typeof BoundedStageOptions> = {
  title: "Workflows/BoundedStageOptions",
  component: BoundedStageOptions,
  parameters: { layout: "padded" },
};
export default meta;
type Story = StoryObj<typeof meta>;

function Editor(args: BoundedStageOptionsProps) {
  const [value, setValue] = useState(args);
  return (
    <div className="w-full max-w-md">
      <BoundedStageOptions
        {...value}
        onChange={(patch) =>
          setValue((previous) => ({
            ...previous,
            ...patch,
            valueJson: patch.backEdgeValueJson ?? previous.valueJson,
          }))
        }
      />
    </div>
  );
}

export const Review: Story = {
  args: {
    kind: "human_gate",
    maxAttempts: 3,
    backEdge: { to: "draft", when: "gate_rejected", max_rounds: 5, on_exhausted: "escalate" },
    targets: ["draft"],
    disabled: false,
    onChange: () => {},
  },
  render: (args) => <Editor {...args} />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByLabelText("Maximum rounds (1–20)")).toHaveValue(5);
    await userEvent.selectOptions(canvas.getByLabelText("At round limit"), "fail");
    await expect(canvas.getByLabelText("At round limit")).toHaveValue("fail");
  },
};
export const AgentFailure: Story = {
  ...Review,
  args: {
    ...Review.args!,
    kind: "agent_dispatch",
    maxAttempts: 4,
    backEdge: { to: "draft", when: "stage_failed", max_rounds: 3, on_exhausted: "fail" },
  },
  play: undefined,
};
export const OutputCondition: Story = {
  ...Review,
  args: {
    ...Review.args!,
    kind: "checkpoint",
    backEdge: {
      to: "draft",
      when: "output_equals",
      path: "tests.passed",
      value: false,
      max_rounds: 3,
      on_exhausted: "fail",
    },
  },
  play: undefined,
};
export const ReadOnly: Story = {
  ...Review,
  args: { ...Review.args!, disabled: true },
  play: undefined,
};
export const NoEarlierOutputKey: Story = {
  ...Review,
  args: { ...Review.args!, backEdge: {}, targets: [] },
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
            <Editor
              {...(Review.args as BoundedStageOptionsProps)}
              targets={["draft", "assessment"]}
            />
          </NextIntlClientProvider>
        </section>
      ))}
    </div>
  ),
};
