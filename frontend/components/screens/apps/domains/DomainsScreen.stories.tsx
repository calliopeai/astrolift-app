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

export const Full: Story = { render: () => <DomainsScreen {...base} /> };

/** First load: environments not back yet, domain list skeleton. */
export const Loading: Story = {
  render: () => <DomainsScreen {...base} loading domains={[]} environments={[]} />,
};

export const Empty: Story = {
  render: () => <DomainsScreen {...base} domains={[]} environments={[ENV_PROD]} />,
};

/**
 * The tab has no page-level error state (query and mutation errors surface
 * as toasts); the closest real one is every domain failing validation or
 * issuance.
 */
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
