import type { Meta, StoryObj } from "@storybook/nextjs-vite";

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
