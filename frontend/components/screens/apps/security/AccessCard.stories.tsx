import { NextIntlClientProvider } from "next-intl";
import fr from "@/messages/fr.json";
import ja from "@/messages/ja.json";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { AccessCardView, AccessEditorView } from "./AccessCard";
import {
  ACCESS_LONG,
  ACCESS_MANIFEST,
  ACCESS_OPEN,
  ACCESS_RESTRICTED,
  EDITOR,
  EDITOR_CHANGED,
} from "./app-security-previews.fixtures";

const meta: Meta<typeof AccessCardView> = {
  title: "Screens/Apps/Security/AccessCard",
  component: AccessCardView,
  args: { ...ACCESS_RESTRICTED, editor: <AccessEditorView {...EDITOR} /> },
};
export default meta;

type Story = StoryObj<typeof AccessCardView>;

/** A restricted rule an operator with `app.access` can edit. */
export const Full: Story = {};

export const Loading: Story = { args: { access: null, loading: true } };

/** No rule: every signed-in user can enter. */
export const Empty: Story = {
  args: { ...ACCESS_OPEN, editor: <AccessEditorView {...EDITOR} groups={[]} users={[]} /> },
};

/** An edited draft with the live preview naming who would lose access. */
export const Changed: Story = { args: { editor: <AccessEditorView {...EDITOR_CHANGED} /> } };

/** Set in astrolift.toml and not on the Envoy edge yet: read-only summary. */
export const Manifest: Story = { args: { ...ACCESS_MANIFEST } };

/**
 * The card has no error state (a failed query renders nothing). Closest real
 * state: the save in flight.
 */
export const Saving: Story = {
  args: { editor: <AccessEditorView {...EDITOR_CHANGED} saving /> },
};

export const LongStrings: Story = { args: { ...ACCESS_LONG } };

export const French: Story = {
  decorators: [
    (Story) => (
      <NextIntlClientProvider locale="fr" messages={fr} timeZone="UTC">
        <Story />
      </NextIntlClientProvider>
    ),
  ],
};
export const Japanese: Story = {
  decorators: [
    (Story) => (
      <NextIntlClientProvider locale="ja" messages={ja} timeZone="UTC">
        <Story />
      </NextIntlClientProvider>
    ),
  ],
};
