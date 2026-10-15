import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { NextIntlClientProvider } from "next-intl";
import { expect, userEvent, within } from "storybook/test";
import ja from "@/messages/ja.json";

import { ADD_ENVIRONMENT, LONG } from "./app-settings-members.fixtures";
import { AddEnvironmentView } from "./AddEnvironment";

const meta: Meta<typeof AddEnvironmentView> = {
  title: "Screens/Apps/Settings/AddEnvironment",
  component: AddEnvironmentView,
  args: ADD_ENVIRONMENT,
};
export default meta;

type Story = StoryObj<typeof AddEnvironmentView>;

/** A cluster choice besides the app's default. */
export const Full: Story = {};

/** No cluster the viewer can see: the environment goes on the app's default. */
export const Empty: Story = { args: { clusters: [] } };

/** A name that is not one DNS label leaves the button disabled. */
export const InvalidName: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.type(canvas.getByRole("textbox"), "Staging Env");
    await expect(canvas.getByRole("button", { name: /add environment/i })).toBeDisabled();
  },
};

/** No error state; a refused create is a toast. Shown: the create running. */
export const Loading: Story = { args: { creating: true } };

export const LongStrings: Story = {
  args: { clusters: [{ slug: "long", name: LONG }] },
};

export const JapaneseWidth768: Story = {
  render: (args) => (
    <NextIntlClientProvider locale="ja" messages={ja} timeZone="UTC">
      <div style={{ width: 768 }}>
        <AddEnvironmentView {...args} />
      </div>
    </NextIntlClientProvider>
  ),
};
