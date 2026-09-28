import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { AppTabsView } from "@/components/screens/apps/detail/AppTabs";

import {
  APP_DOMAINS,
  DOMAIN_CERT_FAILED,
  DOMAIN_LONG,
  DOMAIN_PENDING,
  ENV_PROD,
  ENV_STAGING_PAUSED,
  LONG,
} from "./app-domains.fixtures";
import { DomainsScreen, type DomainsScreenProps } from "./DomainsScreen";

const meta: Meta = { title: "Screens/Apps/Domains/DomainsScreen" };
export default meta;

type Story = StoryObj;

const base: DomainsScreenProps = {
  ...APP_DOMAINS,
  slug: "storefront",
  tabs: (
    <AppTabsView
      slug="storefront"
      basePath="/apps"
      pathname="/apps/storefront/domains"
      active="domains"
    />
  ),
};

/** The first domain still waiting on DNS is shown under the list with its records to add. */
export const Full: Story = { render: () => <DomainsScreen {...base} /> };

/** Every domain validated: the first one's panel shows its certificate and routing. */
export const AllValidated: Story = {
  render: () => (
    <DomainsScreen
      {...base}
      domains={base.domains.map((d) => ({ ...d, certState: "validated" }))}
    />
  ),
};

/** First load: environments not back yet, domain list skeleton. */
export const Loading: Story = {
  render: () => <DomainsScreen {...base} loading domains={[]} environments={[]} />,
};

export const Empty: Story = {
  render: () => <DomainsScreen {...base} domains={[]} environments={[ENV_PROD]} />,
};

/** The domains query failed with nothing cached: the error and Retry sit in the table's frame. */
export const LoadError: Story = {
  render: () => (
    <DomainsScreen
      {...base}
      domains={[]}
      error={{ name: "ApolloError", message: "Network request failed: 502 Bad Gateway" }}
    />
  ),
};

/** Every domain failing validation or issuance; the first one's panel leads with the error. */
export const FailingDomains: Story = {
  render: () => <DomainsScreen {...base} domains={[DOMAIN_CERT_FAILED, DOMAIN_PENDING]} />,
};

/** A mutation in flight: every action disabled, the ingress toggle spinning. */
export const Busy: Story = { render: () => <DomainsScreen {...base} busy /> };

export const IngressPaused: Story = {
  render: () => <DomainsScreen {...base} environments={[ENV_STAGING_PAUSED]} />,
};

export const LongStrings: Story = {
  render: () => (
    <DomainsScreen
      {...base}
      slug={LONG}
      domains={[DOMAIN_LONG]}
      environments={[{ ...ENV_PROD, name: LONG, url: `https://${LONG}.apps.example.com` }]}
    />
  ),
};

export const W768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <DomainsScreen
        {...base}
        slug={LONG}
        domains={[DOMAIN_LONG, DOMAIN_PENDING]}
        environments={[{ ...ENV_PROD, name: LONG, url: `https://${LONG}.apps.example.com` }]}
      />
    </div>
  ),
};
