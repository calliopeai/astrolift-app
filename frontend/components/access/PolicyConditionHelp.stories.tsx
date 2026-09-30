import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { NextIntlClientProvider } from "next-intl";
import es from "@/messages/es.json";
import { expect, within } from "storybook/test";

import { CONDITION_CATALOG, LONG } from "./fixtures";
import { PolicyConditionHelp } from "./PolicyConditionHelp";

/** Each condition a draft uses, from the server's catalog, with what a check needs to answer it. */
const meta: Meta<typeof PolicyConditionHelp> = {
  title: "Access/PolicyConditionHelp",
  component: PolicyConditionHelp,
  parameters: { layout: "padded" },
  args: { catalog: CONDITION_CATALOG, kinds: ["time_window", "ip_allowlist"] },
  decorators: [(Story) => <div className="max-w-2xl">{Story()}</div>],
};
export default meta;

type Story = StoryObj<typeof PolicyConditionHelp>;

export const TwoConditions: Story = {
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getByText(/cannot answer it, so it denies/)).toBeVisible();
  },
};

export const EveryKind: Story = { args: { kinds: CONDITION_CATALOG.map((c) => c.kind) } };

/** A kind the server does not know: said to deny. */
export const UnknownKind: Story = { args: { kinds: ["geo_fence"] } };

export const Loading: Story = { args: { loading: true } };

export const CatalogFailed: Story = {
  args: { catalog: [], error: { message: "upstream timed out" } },
};

/** No conditions: nothing to say. */
export const NoConditions: Story = { args: { kinds: [] } };

export const LongStrings: Story = {
  args: {
    catalog: [{ ...CONDITION_CATALOG[0], label: LONG, description: LONG }],
    kinds: ["time_window"],
  },
};

export const Spanish: Story = {
  args: { catalog: CONDITION_CATALOG, kinds: ["time_window", "ip_allowlist", "geo_fence"] },
  decorators: [
    (Story) => (
      <NextIntlClientProvider locale="es" messages={es}>
        <Story />
      </NextIntlClientProvider>
    ),
  ],
};
