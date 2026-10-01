import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { INGRESS_AUTH, LONG } from "./fixtures";
import { IngressAuthView } from "./IngressAuth";

const meta: Meta = { title: "Screens/Clusters/Settings/IngressAuth" };
export default meta;

type Story = StoryObj;

export const Enabled: Story = { render: () => <IngressAuthView {...INGRESS_AUTH} /> };

/** No saved Cognito configuration does not establish whether traffic is protected. */
export const Empty: Story = {
  render: () => <IngressAuthView {...INGRESS_AUTH} existing={null} />,
};

export const Editing: Story = { render: () => <IngressAuthView {...INGRESS_AUTH} editing /> };

export const PoolsLoading: Story = {
  render: () => (
    <IngressAuthView {...INGRESS_AUTH} existing={null} editing poolId="" pools={[]} poolsLoading />
  ),
};

/** The cluster's role cannot list pools; paste mode is the way out. */
export const PoolsError: Story = {
  render: () => (
    <IngressAuthView
      {...INGRESS_AUTH}
      existing={null}
      editing
      poolId=""
      pools={[]}
      poolsErrored
      poolsError="RAW_POOL_READ_ERROR"
    />
  ),
};

export const Saving: Story = {
  render: () => <IngressAuthView {...INGRESS_AUTH} busy reconciling={false} />,
};

export const Applying: Story = {
  render: () => <IngressAuthView {...INGRESS_AUTH} busy reconciling />,
};

export const NotAlb: Story = {
  render: () => <IngressAuthView {...INGRESS_AUTH} ingressClass="envoy" />,
};

export const OtherProvider: Story = {
  render: () => <IngressAuthView {...INGRESS_AUTH} providerPluginSlug="gcp" isAws={false} />,
};

export const LongStrings: Story = {
  render: () => (
    <IngressAuthView
      {...INGRESS_AUTH}
      existing={{
        user_pool_arn: `arn:aws:cognito-idp:us-west-2:123456789012:userpool/${LONG}`,
        user_pool_client_id: LONG,
        user_pool_domain: LONG,
      }}
    />
  ),
};

export const ClientsUnavailable: Story = {
  render: () => (
    <IngressAuthView {...INGRESS_AUTH} editing clients={[]} clientsError="RAW_CLIENT_READ_ERROR" />
  ),
};
export const FutureProvider: Story = {
  render: () => (
    <IngressAuthView {...INGRESS_AUTH} providerPluginSlug="FUTURE_PROVIDER_LITERAL" isAws={false} />
  ),
};
