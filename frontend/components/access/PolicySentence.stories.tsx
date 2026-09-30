import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { NextIntlClientProvider } from "next-intl";
import ja from "@/messages/ja.json";
import es from "@/messages/es.json";
import * as React from "react";

import { AFTER_HOURS, EVERY_KIND, INVALID_POLICY, LONG_POLICY, SECRETS_OFFICE } from "./fixtures";
import type { PolicyShape } from "./policy-model";
import { PolicySentence } from "./PolicySentence";

/**
 * An ABAC policy as a sentence, read and edited: "Deny app.deploy on
 * anything in production for everyone unless it is Mon to Fri 09:00 to
 * 18:00 America/Los_Angeles."
 */
const meta: Meta<typeof PolicySentence> = {
  title: "Access/PolicySentence",
  component: PolicySentence,
  parameters: { layout: "padded" },
  args: { policy: AFTER_HOURS },
  decorators: [(Story) => <div className="max-w-3xl">{Story()}</div>],
};
export default meta;

type Story = StoryObj<typeof PolicySentence>;

export const AfterHours: Story = {};

export const Contractors: Story = { args: { policy: SECRETS_OFFICE } };

/** Every condition kind, and one the pickers do not know (kept verbatim). */
export const EveryKind: Story = { args: { policy: EVERY_KIND } };

/** No conditions, no resource, no actor: the widest rule. */
export const Empty: Story = {
  args: {
    policy: {
      effect: "DENY",
      actionPattern: "*",
      resource: {},
      conditions: [],
      actor: { groups: [], role: "" },
    },
  },
};

/** As the Policies list shows them: one sentence per row. */
export const List: Story = {
  render: () => (
    <ul className="divide-y rounded-md border">
      {[AFTER_HOURS, SECRETS_OFFICE, EVERY_KIND].map((p, i) => (
        <li key={i} className="p-3">
          <PolicySentence policy={p} />
        </li>
      ))}
    </ul>
  ),
};

function Editor({ start }: { start: PolicyShape }) {
  const [policy, setPolicy] = React.useState(start);
  return <PolicySentence policy={policy} onChange={setPolicy} />;
}

export const Edit: Story = { render: () => <Editor start={AFTER_HOURS} /> };

export const EditEveryKind: Story = { render: () => <Editor start={EVERY_KIND} /> };

/** Errors sit beside the condition they belong to. */
export const EditInvalid: Story = { render: () => <Editor start={INVALID_POLICY} /> };

export const LongStrings: Story = { args: { policy: LONG_POLICY } };

export const EditLongStrings: Story = { render: () => <Editor start={LONG_POLICY} /> };

export const Width768: Story = {
  decorators: [
    (Story) => (
      <div style={{ width: 768 }} className="overflow-hidden border p-4">
        {Story()}
      </div>
    ),
  ],
  render: () => <Editor start={EVERY_KIND} />,
};

export const JapaneseEveryKind: Story = {
  args: { policy: EVERY_KIND },
  decorators: [
    (Story) => (
      <NextIntlClientProvider locale="ja" messages={ja}>
        <Story />
      </NextIntlClientProvider>
    ),
  ],
};
export const SpanishEditor: Story = {
  render: () => <Editor start={EVERY_KIND} />,
  decorators: [
    (Story) => (
      <NextIntlClientProvider locale="es" messages={es}>
        <Story />
      </NextIntlClientProvider>
    ),
  ],
};
