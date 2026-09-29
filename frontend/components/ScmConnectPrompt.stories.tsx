import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { ScmEmptyConnectAction, ScmReauthAction } from "@/components/ScmConnectPrompt";
import type { UseScmConnect } from "@/components/use-scm-connect";

const meta: Meta = { title: "Patterns/Scm/ScmConnectPrompt" };
export default meta;

const account = (over: Partial<UseScmConnect["accounts"][number]> = {}) =>
  ({
    providerConfigId: "gh",
    providerKind: "github_app",
    providerLabel: "GitHub",
    isConnected: true,
    reauthRequired: false,
    ...over,
  }) as UseScmConnect["accounts"][number];

const scm = (accounts: UseScmConnect["accounts"], loading = false): UseScmConnect => ({
  accounts,
  loading,
  starting: false,
  startConnect: async () => {},
});

export const Reconnect: StoryObj = {
  render: () => <ScmReauthAction connectionKind="github_app" scm={scm([account()])} />,
};
export const ReconnectFallback: StoryObj = {
  render: () => <ScmReauthAction connectionKind="gitlab_oauth" scm={scm([])} />,
};
export const EmptyConnectable: StoryObj = {
  render: () => (
    <ScmEmptyConnectAction
      scm={scm([
        account({ isConnected: false }),
        account({
          providerConfigId: "gl",
          providerKind: "gitlab_oauth",
          providerLabel: "GitLab",
          isConnected: false,
        }),
      ])}
    />
  ),
};
export const EmptyNoProvider: StoryObj = { render: () => <ScmEmptyConnectAction scm={scm([])} /> };
