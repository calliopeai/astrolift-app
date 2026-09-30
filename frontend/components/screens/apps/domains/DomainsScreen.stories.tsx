import { NextIntlClientProvider } from "next-intl";
import localizedMessages from "@/messages/es.json";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { useLocalListState } from "@/components/list/use-list-state";
import { AppTabsView } from "@/components/screens/apps/detail/AppTabs";

import {
  APP_DOMAINS,
  DOMAIN_ACTIVE,
  DOMAIN_CERT_FAILED,
  DOMAIN_LONG,
  DOMAIN_PENDING,
  ENV_PROD,
  ENV_STAGING_PAUSED,
  LONG,
} from "./app-domains.fixtures";
import { APP_DOMAINS_LIST, selectDomains } from "./domains-list";
import { DomainsScreen, type DomainsScreenProps } from "./DomainsScreen";

const meta: Meta = { title: "Screens/Apps/Domains/DomainsScreen" };
export default meta;

type Story = StoryObj;

const base: Omit<DomainsScreenProps, "list" | "rows" | "totalCount"> = {
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

/** The screen with in-memory list state, its rows answered as the hook would. */
function Domains(props: Partial<typeof base>) {
  const list = useLocalListState(APP_DOMAINS_LIST);
  const all = props.domains ?? base.domains;
  const { rows, totalCount } = selectDomains(all, list.filters, list.state);
  return <DomainsScreen {...base} {...props} list={list} rows={rows} totalCount={totalCount} />;
}

/** The first domain still waiting on DNS is shown under the list with its records to add. */
export const Full: Story = { render: () => <Domains /> };

/** Every domain validated: the first one's panel shows its certificate and routing. */
export const AllValidated: Story = {
  render: () => <Domains domains={base.domains.map((d) => ({ ...d, certState: "validated" }))} />,
};

/** First load: environments not back yet, domain list skeleton. */
export const Loading: Story = {
  render: () => <Domains loading domains={[]} environments={[]} />,
};

export const Empty: Story = {
  render: () => <Domains domains={[]} environments={[ENV_PROD]} />,
};

/** The domains query failed with nothing cached: the error and Retry sit in the table's frame. */
export const LoadError: Story = {
  render: () => (
    <Domains
      domains={[]}
      error={{ name: "ApolloError", message: "Network request failed: 502 Bad Gateway" }}
    />
  ),
};

/** Every domain failing validation or issuance; the first one's panel leads with the error. */
export const FailingDomains: Story = {
  render: () => <Domains domains={[DOMAIN_CERT_FAILED, DOMAIN_PENDING]} />,
};

/** A mutation in flight: every action disabled, the ingress toggle spinning. */
export const Busy: Story = { render: () => <Domains busy /> };

export const IngressPaused: Story = {
  render: () => <Domains environments={[ENV_STAGING_PAUSED]} />,
};

export const LongStrings: Story = {
  render: () => (
    <Domains
      slug={LONG}
      domains={[DOMAIN_LONG]}
      environments={[{ ...ENV_PROD, name: LONG, url: `https://${LONG}.apps.example.com` }]}
    />
  ),
};

export const W768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <Domains
        slug={LONG}
        domains={[DOMAIN_LONG, DOMAIN_PENDING]}
        environments={[{ ...ENV_PROD, name: LONG, url: `https://${LONG}.apps.example.com` }]}
      />
    </div>
  ),
};

/** Forty domains: the list pages, and a picked one leads the handshake panel. */
export const ManyDomains: Story = {
  render: () => (
    <Domains
      domains={Array.from({ length: 40 }, (_, i) => ({
        ...DOMAIN_ACTIVE,
        id: `dom-many-${i}`,
        hostname: `shop-${String(i).padStart(2, "0")}.acme.example.com`,
        certState: i % 7 === 0 ? "pending" : "validated",
      }))}
      pickedId="dom-many-7"
    />
  ),
};

export const Localized: Story = {
  render: () => <Domains />,
  decorators: [
    (Story) => (
      <NextIntlClientProvider locale="es" messages={localizedMessages} timeZone="UTC">
        <Story />
      </NextIntlClientProvider>
    ),
  ],
};
