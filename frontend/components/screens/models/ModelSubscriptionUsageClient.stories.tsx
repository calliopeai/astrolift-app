import { ApolloClient, ApolloLink, InMemoryCache, Observable } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { useMemo } from "react";
import { expect, within } from "storybook/test";
import { ActiveOrgProvider } from "@/graphql/identity/identity.hooks";
import { ModelSubscriptionUsageClient } from "./ModelSubscriptionUsageClient";
import { sharedModelDetailProps } from "./shared-model-detail.fixtures";
import { observation } from "./model-observations.fixtures";
import type { ModelSubscription } from "./ModelSubscriptionsPanel";
import type { GetModelSubscriptionMetricsQuery } from "@/graphql/__generated__/operations";
import messages from "@/messages/en.json";

const model = sharedModelDetailProps.model!;
const subscription: ModelSubscription = {
  id: "subscription-one",
  version: 2,
  alias: "chat",
  bindingPrefix: "MODEL_CHAT_",
  appSlug: "storefront",
  environmentName: "production",
  status: "active",
  desiredRevision: 3,
  appliedRevision: 3,
  reason: null,
  canRevoke: true,
};

const meta = {
  title: "Screens/Models/ModelSubscriptionUsageClient",
  component: ModelSubscriptionUsageClient,
  args: { model, subscription, onClose: () => {} },
  decorators: [
    function ScopedReads(Story, context) {
      const available = context.parameters.metricState !== "NO_DATA";
      const client = useMemo(
        () =>
          new ApolloClient({
            cache: new InMemoryCache(),
            link: new ApolloLink(
              (operation) =>
                new Observable((observer) => {
                  let data: Record<string, unknown>;
                  if (operation.operationName === "Me") {
                    data = { me: { id: "owner", profile: null, modules: [] } };
                  } else if (operation.operationName === "ListOrganizations") {
                    data = {
                      astroliftOrganizations: [
                        {
                          id: model.organizationId,
                          slug: "demo",
                          name: "Demo",
                          website: "",
                          scimEnabled: false,
                          auditLogRetentionDays: 30,
                          appearanceDefault: {},
                          appearanceLocked: false,
                          restrictedSettingsDefault: "editable",
                          previewMaxActiveDefault: 3,
                          logRetentionDaysDefault: 7,
                          allowUserProfileEdit: true,
                          onboardingCompletedAt: null,
                          createdAt: "2026-10-01T00:00:00Z",
                          updatedAt: "2026-10-01T00:00:00Z",
                          deletedAt: null,
                        },
                      ],
                    };
                  } else if (operation.operationName === "GetModelSubscriptionMetrics") {
                    data = {
                      astroliftModelSubscriptionMetrics: {
                        serviceId: model.id,
                        clusterId: model.clusterId,
                        subscriptionId: subscription.id,
                        start: operation.variables.start,
                        end: operation.variables.end,
                        retrievedAt: operation.variables.end,
                        stepSeconds: 30,
                        scope: "authenticated_subscription",
                        metrics: [
                          observation("requests_per_second", "requests/s", 2),
                          observation("error_requests_per_second", "requests/s", 0),
                          observation("response_bytes_per_second", "bytes/s", 500),
                          observation("latency_p95", "seconds", 0.4),
                        ].map((metric) => ({
                          ...metric,
                          source: "authenticated_model_subscription",
                          state: available ? "AVAILABLE" : "NO_DATA",
                          value: available ? metric.value : null,
                          observedAt: available ? operation.variables.end : null,
                          samples: [],
                        })),
                      },
                    } satisfies GetModelSubscriptionMetricsQuery;
                  } else {
                    observer.error(
                      new Error(`Unexpected story operation ${operation.operationName}`)
                    );
                    return;
                  }
                  observer.next({ data });
                  observer.complete();
                })
            ),
          }),
        [available]
      );
      return (
        <ApolloProvider client={client}>
          <ActiveOrgProvider>
            <Story />
          </ActiveOrgProvider>
        </ApolloProvider>
      );
    },
  ],
} satisfies Meta<typeof ModelSubscriptionUsageClient>;
export default meta;
type Story = StoryObj<typeof meta>;
export const Available: Story = {
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).findByText("500")).resolves.toBeVisible();
  },
};
export const NoData: Story = {
  parameters: { metricState: "NO_DATA" },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).findByText(messages.models.shared.subscriptionUsage.empty)
    ).resolves.toBeVisible();
  },
};
