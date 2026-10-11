import { NextIntlClientProvider } from "next-intl";
import en from "@/messages/en.json";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { MockedProvider } from "@apollo/client/testing/react";
import { ActiveOrgProvider } from "@/graphql/identity/identity.hooks";
import { LIST_ORGANIZATIONS } from "@/graphql/identity/identity.queries";
import { GET_ME } from "@/graphql/user/user.queries";
import { InstallAlertMailClient } from "./InstallAlertMailClient";
import {
  INSTALL_ALERT_MAIL_SUPPORT,
  INSTALL_ALERT_MAIL_HISTORY,
} from "@/graphql/operations/install-alert-mail.queries";
import { ALERT_PANEL } from "./InstallAlertMailPanel.stories";
const meta: Meta<typeof InstallAlertMailClient> = {
  title: "Screens/Settings/Install alert email adapter",
  component: InstallAlertMailClient,
  render: () => (
    <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
      <InstallAlertMailClient />
    </NextIntlClientProvider>
  ),
  decorators: [
    (Story) => (
      <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
        <MockedProvider
          mocks={[
            {
              request: { query: LIST_ORGANIZATIONS },
              result: {
                data: {
                  astroliftOrganizations: [
                    {
                      id: "a0000000-0000-4000-8000-000000000001",
                      slug: "example",
                      name: "Example",
                      website: null,
                      scimEnabled: false,
                      auditLogRetentionDays: 30,
                      appearanceDefault: {},
                      appearanceLocked: [],
                      restrictedSettingsDefault: "show",
                      previewMaxActiveDefault: 5,
                      logRetentionDaysDefault: 7,
                      allowUserProfileEdit: true,
                      onboardingCompletedAt: null,
                      createdAt: "2026-10-04T12:00:00Z",
                      updatedAt: "2026-10-04T12:00:00Z",
                      deletedAt: null,
                    },
                  ],
                },
              },
            },
            {
              request: { query: GET_ME },
              result: {
                data: {
                  me: { id: "a0000000-0000-4000-8000-000000000002", profile: null, modules: [] },
                },
              },
            },
            {
              request: {
                query: INSTALL_ALERT_MAIL_SUPPORT,
                variables: { eventKind: "deploy.failed" },
              },
              result: { data: { installAlertMailSupport: ALERT_PANEL.support } },
            },
            {
              request: {
                query: INSTALL_ALERT_MAIL_HISTORY,
                variables: { eventKind: "deploy.failed", after: null, limit: 25 },
              },
              result: {
                data: { installAlertMailTestsPage: { items: [], totalCount: 0, nextCursor: null } },
              },
            },
          ]}
        >
          <ActiveOrgProvider>
            <Story />
          </ActiveOrgProvider>
        </MockedProvider>
      </NextIntlClientProvider>
    ),
  ],
};
export default meta;
export const CurrentSource: StoryObj<typeof meta> = {};
