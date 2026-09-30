import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { NextIntlClientProvider } from "next-intl";
import fr from "@/messages/fr.json";
import ja from "@/messages/ja.json";

import { AccessCompare, AccessExplainer } from "./AccessExplainer";
import {
  CHECKOUT,
  COMPARISON,
  DIAGNOSIS_LONG,
  DIAGNOSIS_NO,
  DIAGNOSIS_POLICY_DENIED,
  DIAGNOSIS_SUPERUSER,
  DIAGNOSIS_YES,
  LONG,
} from "./fixtures";

/**
 * "Can <who> <do what>?" answered with the resolver's reasoning chain from
 * `permissionDiagnose`, and Compare two people from `permissionCompare`.
 */
const meta: Meta<typeof AccessExplainer> = {
  title: "Access/AccessExplainer",
  component: AccessExplainer,
  parameters: { layout: "padded" },
  args: { diagnosis: DIAGNOSIS_YES },
  decorators: [(Story) => <div className="max-w-2xl">{Story()}</div>],
};
export default meta;

type Story = StoryObj<typeof AccessExplainer>;

export const Yes: Story = {
  args: { bindingHref: (b) => `/administration/access/roles/${b.role}` },
};

/** Held on a team, asked at org scope: the #1717 shape. */
export const No: Story = { args: { diagnosis: DIAGNOSIS_NO } };

/** Asked about an app: the answer is for that app. */
export const WithTarget: Story = { args: { diagnosis: DIAGNOSIS_NO, target: CHECKOUT } };

/** On an app: the role grants it there and a policy denies it anyway; the chain says which. */
export const PolicyDenied: Story = {
  args: { diagnosis: DIAGNOSIS_POLICY_DENIED, target: CHECKOUT },
};

export const Superuser: Story = { args: { diagnosis: DIAGNOSIS_SUPERUSER } };

/** Nothing asked yet. */
export const Empty: Story = { args: { diagnosis: null } };

export const Loading: Story = { args: { diagnosis: null, loading: true } };

export const Error: Story = {
  args: {
    diagnosis: null,
    error: { message: "Only superusers or the user themselves can diagnose permissions." },
    onRetry: () => {},
  },
};

export const LongStrings: Story = {
  args: { diagnosis: DIAGNOSIS_LONG, target: { kind: "PROJECT", id: "p", name: LONG } },
};

export const Compare: Story = { render: () => <AccessCompare comparison={COMPARISON} /> };

export const CompareIdentical: Story = {
  render: () => (
    <AccessCompare
      comparison={{ ...COMPARISON, onlyA: [], onlyB: [], shared: COMPARISON.shared }}
    />
  ),
};

export const CompareEmpty: Story = { render: () => <AccessCompare comparison={null} /> };

export const CompareLoading: Story = { render: () => <AccessCompare comparison={null} loading /> };

export const CompareError: Story = {
  render: () => (
    <AccessCompare comparison={null} error={{ message: "User not found" }} onRetry={() => {}} />
  ),
};

export const CompareLongStrings: Story = {
  render: () => (
    <AccessCompare
      comparison={{
        userAUsername: LONG,
        userBUsername: `${LONG}-b`,
        onlyA: [`app.${LONG}`],
        onlyB: [],
        shared: Array.from({ length: 40 }, (_, i) => `resource_${i}.read`),
      }}
    />
  ),
};

export const Width768: Story = {
  decorators: [
    (Story) => (
      <div style={{ width: 768 }} className="overflow-hidden border p-4">
        {Story()}
      </div>
    ),
  ],
  render: () => (
    <div className="flex flex-col gap-6">
      <AccessExplainer diagnosis={DIAGNOSIS_NO} target={CHECKOUT} />
      <AccessCompare comparison={COMPARISON} />
    </div>
  ),
};

export const FrenchPolicyDenied: Story = {
  args: { diagnosis: DIAGNOSIS_POLICY_DENIED, target: CHECKOUT },
  decorators: [
    (Story) => (
      <NextIntlClientProvider locale="fr" messages={fr}>
        <Story />
      </NextIntlClientProvider>
    ),
  ],
};
export const JapaneseComparison: Story = {
  render: () => <AccessCompare comparison={COMPARISON} />,
  decorators: [
    (Story) => (
      <NextIntlClientProvider locale="ja" messages={ja}>
        <Story />
      </NextIntlClientProvider>
    ),
  ],
};
