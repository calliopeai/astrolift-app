import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { ConnectedAccountsSection } from "@/components/ConnectedAccountsSection";
import type { AstroliftMyConnectedAccount } from "@/graphql/identity/identity.types";

/** Settings › Profile: the person's linked source-provider identities (#395). */
const meta: Meta = { title: "Patterns/Settings/ConnectedAccountsSection" };
export default meta;

const account = (over: Partial<AstroliftMyConnectedAccount>): AstroliftMyConnectedAccount =>
  ({
    providerConfigId: "gh",
    providerKind: "github_app",
    providerLabel: "GitHub",
    isConnected: true,
    reauthRequired: false,
    linkedAccountLogin: "leo-conflict",
    ...over,
  }) as AstroliftMyConnectedAccount;

const ACTIONS = {
  loading: false,
  connecting: false,
  disconnecting: false,
  onConnect: async () => {},
  onDisconnect: async () => {},
};

export const States: StoryObj = {
  render: () => (
    <div className="max-w-lg">
      <ConnectedAccountsSection
        {...ACTIONS}
        accounts={[
          account({}),
          account({
            providerConfigId: "gl",
            providerKind: "gitlab_oauth",
            providerLabel: "GitLab",
            reauthRequired: true,
          }),
          account({
            providerConfigId: "gh2",
            providerLabel: "GitHub Enterprise",
            isConnected: false,
            linkedAccountLogin: null,
          }),
        ]}
      />
    </div>
  ),
};
