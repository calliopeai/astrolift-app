import { useEffect, useMemo } from "react";
import type { Decorator } from "@storybook/nextjs-vite";
import { ApolloClient, ApolloLink, InMemoryCache, Observable } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { ActiveOrgProvider } from "@/graphql/identity/identity.hooks";
import { connectionPolicyProps, connectionRequestRow } from "./model-connection.fixtures";
export const connectionStoryDecorator: Decorator = (Story) => (
  <ConnectionStoryProvider>
    <Story />
  </ConnectionStoryProvider>
);
function ConnectionStoryProvider({ children }: { children: React.ReactNode }) {
  const client = useMemo(
    () =>
      new ApolloClient({
        cache: new InMemoryCache(),
        devtools: { enabled: false },
        link: new ApolloLink(
          (operation) =>
            new Observable((observer) => {
              const page = {
                page: operation.variables.page,
                pageSize: operation.variables.pageSize,
                totalCount: 1,
                nextCursor: null,
                items: [connectionRequestRow],
              };
              const data =
                operation.operationName === "ListOrganizations"
                  ? {
                      astroliftOrganizations: [
                        {
                          id: "org",
                          slug: "sample-org",
                          name: "Sample organization",
                          website: null,
                          scimEnabled: false,
                          auditLogRetentionDays: 365,
                          appearanceDefault: null,
                          appearanceLocked: false,
                          restrictedSettingsDefault: null,
                          previewMaxActiveDefault: 1,
                          logRetentionDaysDefault: 7,
                          allowUserProfileEdit: false,
                          onboardingCompletedAt: null,
                          createdAt: null,
                          updatedAt: null,
                          deletedAt: null,
                        },
                      ],
                    }
                  : operation.operationName === "Me"
                    ? {
                        me: {
                          id: "sample-owner",
                          profile: { id: "sample-profile", username: "sample-owner" },
                          modules: [],
                        },
                      }
                    : operation.operationName === "GetModelConnectionCapabilities"
                      ? { astroliftServerInfo: { capabilities: ["models.connection_approvals"] } }
                      : operation.operationName === "ListModelConnectionRequests"
                        ? { [operation.variables.review ? "inbox" : "own"]: page }
                        : operation.operationName === "GetModelConnectionRequest"
                          ? { [operation.variables.review ? "inbox" : "own"]: connectionRequestRow }
                          : operation.operationName === "GetOrganizationModelConnectionPolicy"
                            ? { organizationModelConnectionPolicy: connectionPolicyProps.policy }
                            : operation.operationName === "GetModelHostingAction"
                              ? {
                                  modelHostingAction: {
                                    allowed: false,
                                    reason: "Sample read-only viewer",
                                  },
                                }
                              : null;
              if (data) observer.next({ data });
              else observer.next({ errors: [{ message: "Story transport does not write" }] });
              observer.complete();
            })
        ),
      }),
    []
  );
  useEffect(() => () => client.stop(), [client]);
  return (
    <ApolloProvider client={client}>
      <ActiveOrgProvider>{children}</ActiveOrgProvider>
    </ApolloProvider>
  );
}
