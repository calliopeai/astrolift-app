import { NextIntlClientProvider } from "next-intl";
import localizedMessages from "@/messages/de.json";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import {
  DOMAIN_BYO,
  DOMAIN_CERT_FAILED,
  DOMAIN_LONG,
  DOMAIN_NO_RECORDS,
  DOMAIN_PENDING,
  DOMAIN_WILDCARD,
  HANDSHAKE_CARD,
} from "./app-domains.fixtures";
import { DomainHandshakeCard } from "./DomainHandshakeCard";
import { DNS_WITHHELD } from "../../domains/domains-environments.fixtures";

const meta: Meta = { title: "Screens/Apps/Domains/DomainHandshakeCard" };
export default meta;

type Story = StoryObj;

/** Validated, cert active, redirects and path routes configured. */
export const Full: Story = { render: () => <DomainHandshakeCard {...HANDSHAKE_CARD} /> };

/** DNS records not yet propagated; no redirects or routes. */
export const Pending: Story = {
  render: () => <DomainHandshakeCard {...HANDSHAKE_CARD} domain={DOMAIN_PENDING} />,
};

/** The card has no loading state of its own; the closest is a queued cert with no records yet. */
export const Queued: Story = {
  render: () => <DomainHandshakeCard {...HANDSHAKE_CARD} domain={DOMAIN_NO_RECORDS} />,
};

/** No handshake records returned and nothing configured. */
export const Empty: Story = {
  render: () => (
    <DomainHandshakeCard
      {...HANDSHAKE_CARD}
      domain={{ ...DOMAIN_NO_RECORDS, certState: "pending", certificateState: "not_requested" }}
      workloadOptions={[]}
    />
  ),
};

/** Validation error plus failed issuance: the upload-cert escape hatch. */
export const CertFailed: Story = {
  render: () => <DomainHandshakeCard {...HANDSHAKE_CARD} domain={DOMAIN_CERT_FAILED} />,
};

export const BringYourOwnCert: Story = {
  render: () => <DomainHandshakeCard {...HANDSHAKE_CARD} domain={DOMAIN_BYO} />,
};

export const WildcardIssuing: Story = {
  render: () => <DomainHandshakeCard {...HANDSHAKE_CARD} domain={DOMAIN_WILDCARD} />,
};

/**
 * calliope-installer#447: DNS withheld on a platform-managed zone. The records
 * are not written for the operator, so the card gives the reason instead of
 * "no operator action needed". Recheck, a probe, stays. PlatformZoneDnsUnset
 * is the same domain without the restriction.
 */
const platformHelp = /No operator action needed/;
export const PlatformZoneDnsWithheld: Story = {
  render: () => (
    <DomainHandshakeCard
      {...HANDSHAKE_CARD}
      domain={DOMAIN_WILDCARD}
      dnsRestriction={DNS_WITHHELD}
    />
  ),
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText(/DNS is withheld/)).toBeVisible();
    await expect(canvas.queryByText(platformHelp)).toBeNull();
  },
};
export const PlatformZoneDnsUnset: Story = {
  render: () => (
    <DomainHandshakeCard {...HANDSHAKE_CARD} domain={DOMAIN_WILDCARD} dnsRestriction={null} />
  ),
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText(platformHelp)).toBeVisible();
    await expect(canvas.queryByText(/DNS is withheld/)).toBeNull();
  },
};

export const Busy: Story = { render: () => <DomainHandshakeCard {...HANDSHAKE_CARD} busy /> };

export const LongStrings: Story = {
  render: () => <DomainHandshakeCard {...HANDSHAKE_CARD} domain={DOMAIN_LONG} />,
};

export const W768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <DomainHandshakeCard {...HANDSHAKE_CARD} domain={DOMAIN_LONG} />
    </div>
  ),
};

export const Localized: Story = {
  render: () => <DomainHandshakeCard {...HANDSHAKE_CARD} />,
  decorators: [
    (Story) => (
      <NextIntlClientProvider locale="de" messages={localizedMessages} timeZone="UTC">
        <Story />
      </NextIntlClientProvider>
    ),
  ],
};
