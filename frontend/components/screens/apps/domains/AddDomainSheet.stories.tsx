import { NextIntlClientProvider } from "next-intl";
import localizedMessages from "@/messages/ja.json";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { AddDomainSheet } from "./AddDomainSheet";
import { ADD_SHEET, CERTS } from "./app-domains.fixtures";

const meta: Meta = { title: "Screens/Apps/Domains/AddDomainSheet" };
export default meta;

type Story = StoryObj;

export const Open: Story = { render: () => <AddDomainSheet {...ADD_SHEET} /> };

/** Wildcard on a cluster whose provider cannot list certs: free-text SNI ref. */
export const WildcardFreeText: Story = {
  render: () => <AddDomainSheet {...ADD_SHEET} isWildcard clusterProviderSlug="k8s_native" />,
};

/** Wildcard with the provider-backed cert picker. */
export const WildcardCertPicker: Story = {
  render: () => <AddDomainSheet {...ADD_SHEET} isWildcard showCertCombobox certs={CERTS} />,
};

export const CertsLoading: Story = {
  render: () => <AddDomainSheet {...ADD_SHEET} isWildcard showCertCombobox certsLoading />,
};

/** The provider supports listing but returned no certs. */
export const CertsEmpty: Story = {
  render: () => <AddDomainSheet {...ADD_SHEET} isWildcard showCertCombobox />,
};

/**
 * The sheet has no error state of its own (a rejected add toasts and the
 * sheet stays open); the closest real one is the submit in flight.
 */
export const Submitting: Story = { render: () => <AddDomainSheet {...ADD_SHEET} busy /> };

export const LongStrings: Story = {
  render: () => (
    <AddDomainSheet
      {...ADD_SHEET}
      isWildcard
      showCertCombobox
      certs={CERTS.map((c) => ({
        ...c,
        domainName: `*.${"very-long-tenant-subdomain-".repeat(4)}acme.com`,
        arn: `${c.arn}-${"f".repeat(80)}`,
      }))}
    />
  ),
};

export const Localized: Story = {
  render: () => <AddDomainSheet {...ADD_SHEET} />,
  decorators: [
    (Story) => (
      <NextIntlClientProvider locale="ja" messages={localizedMessages} timeZone="UTC">
        <Story />
      </NextIntlClientProvider>
    ),
  ],
};
