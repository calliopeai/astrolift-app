import { NextIntlClientProvider } from "next-intl";
import french from "@/messages/fr.json";

import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { IDENTITY, IDENTITY_LONG } from "./app-settings-members.fixtures";
import { AppIdentityView } from "./AppIdentity";

const meta: Meta<typeof AppIdentityView> = {
  title: "Screens/Apps/Settings/AppIdentity",
  component: AppIdentityView,
  args: IDENTITY,
};
export default meta;

type Story = StoryObj<typeof AppIdentityView>;

export const Full: Story = {};

/** A save in flight: the fields and buttons are disabled. */
export const Loading: Story = { args: { saving: true } };

/** No description and no repository connected. */
export const Empty: Story = {
  args: { description: "", sourceRepo: "", sourceUrl: "", defaultBranch: "" },
};

/** A clear name is refused in place, beside the field. */
export const NameRequired: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.clear(canvas.getByLabelText("Name"));
    await expect(canvas.getByRole("alert")).toHaveTextContent("The app needs a name.");
  },
};

/** A rejected save keeps the edits and says why. */
export const SaveFailed: Story = {
  args: {
    onSave: async () => {
      throw new Error("App was modified by another session. Refresh and retry.");
    },
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.type(canvas.getByLabelText("Name"), " v2");
    await userEvent.click(canvas.getByRole("button", { name: "Save" }));
    await expect(await canvas.findByRole("alert")).toHaveTextContent(/modified by another/);
  },
};

export const LongStrings: Story = { args: IDENTITY_LONG };

export const Width768: Story = {
  args: IDENTITY_LONG,
  render: (args) => (
    <div style={{ width: 768 }}>
      <AppIdentityView {...args} />
    </div>
  ),
};

export const FrenchWidth768: Story = {
  args: IDENTITY_LONG,
  render: (args) => (
    <NextIntlClientProvider locale="fr" messages={french}>
      <div style={{ width: 768 }}>
        <AppIdentityView {...args} />
      </div>
    </NextIntlClientProvider>
  ),
};
