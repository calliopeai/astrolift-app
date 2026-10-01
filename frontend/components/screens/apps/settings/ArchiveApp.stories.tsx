import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";
import { NextIntlClientProvider } from "next-intl";
import fr from "@/messages/fr.json";
import ja from "@/messages/ja.json";

import { ArchiveAppView } from "./ArchiveApp";
import { ARCHIVE, LONG } from "./app-settings-members.fixtures";

const meta: Meta<typeof ArchiveAppView> = {
  title: "Screens/Apps/Settings/ArchiveApp",
  component: ArchiveAppView,
  args: ARCHIVE,
};
export default meta;

type Story = StoryObj<typeof ArchiveAppView>;

/** Not archived. */
export const Full: Story = {};

export const Archived: Story = {
  args: { isArchived: true, archivedAt: "2026-09-27T18:00:00Z" },
};

/** A restore in flight. */
export const Loading: Story = {
  args: { isArchived: true, archivedAt: "2026-09-27T18:00:00Z", restoring: true },
};

/** Archived with no timestamp recorded. */
export const Empty: Story = { args: { isArchived: true, archivedAt: null } };

/** No error state; a failed archive is a toast. Shown: the confirm dialog. */
export const ConfirmArchive: Story = {
  play: async ({ canvasElement }) => {
    await userEvent.click(within(canvasElement).getByRole("button", { name: /Archive/ }));
    await expect(await within(document.body).findByRole("alertdialog")).toBeInTheDocument();
  },
};

export const LongStrings: Story = {
  args: { appName: LONG },
  play: async ({ canvasElement }) => {
    await userEvent.click(within(canvasElement).getByRole("button", { name: /Archive/ }));
    await expect(await within(document.body).findByRole("alertdialog")).toBeInTheDocument();
  },
};

export const Width768: Story = {
  args: { appName: LONG, isArchived: true, archivedAt: "2026-09-27T18:00:00Z" },
  render: (args) => (
    <div style={{ width: 768 }}>
      <ArchiveAppView {...args} />
    </div>
  ),
};

export const French: Story = {
  decorators: [
    (Story) => (
      <NextIntlClientProvider
        locale="fr"
        messages={fr}
        timeZone="Europe/Paris"
        now={new Date("2026-09-30T12:00:00Z")}
      >
        <Story />
      </NextIntlClientProvider>
    ),
  ],
};

export const JapaneseArchived: Story = {
  args: { isArchived: true, archivedAt: "2026-09-27T18:00:00Z" },
  decorators: [
    (Story) => (
      <NextIntlClientProvider
        locale="ja"
        messages={ja}
        timeZone="Asia/Tokyo"
        now={new Date("2026-09-30T12:00:00Z")}
      >
        <Story />
      </NextIntlClientProvider>
    ),
  ],
};

export const Refused: Story = {
  args: { onArchive: async () => false },
  play: async ({ canvasElement }) => {
    await userEvent.click(within(canvasElement).getByRole("button", { name: "Archive" }));
    const dialog = await within(document.body).findByRole("alertdialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Archive" }));
    await expect(dialog).toBeInTheDocument();
  },
};
