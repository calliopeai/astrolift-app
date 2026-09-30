import { NextIntlClientProvider } from "next-intl";
import localizedMessages from "@/messages/fr.json";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { DOMAIN_LONG, UPLOAD_SHEET } from "./app-domains.fixtures";
import { UploadCertSheet } from "./UploadCertSheet";

const meta: Meta = { title: "Screens/Apps/Domains/UploadCertSheet" };
export default meta;

type Story = StoryObj;

export const Open: Story = { render: () => <UploadCertSheet {...UPLOAD_SHEET} /> };

/** No domain picked yet: the fallback title. The sheet has no loading or empty state. */
export const NoDomain: Story = {
  render: () => <UploadCertSheet {...UPLOAD_SHEET} domain={null} />,
};

/**
 * The sheet has no error state of its own (a rejected upload toasts and the
 * sheet stays open); the closest real one is the upload in flight.
 */
export const Uploading: Story = { render: () => <UploadCertSheet {...UPLOAD_SHEET} busy /> };

export const LongStrings: Story = {
  render: () => <UploadCertSheet {...UPLOAD_SHEET} domain={DOMAIN_LONG} />,
};

export const Localized: Story = {
  render: () => <UploadCertSheet {...UPLOAD_SHEET} />,
  decorators: [
    (Story) => (
      <NextIntlClientProvider locale="fr" messages={localizedMessages} timeZone="UTC">
        <Story />
      </NextIntlClientProvider>
    ),
  ],
};
