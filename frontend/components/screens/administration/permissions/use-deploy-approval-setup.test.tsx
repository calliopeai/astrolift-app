import { MockedProvider } from "@apollo/client/testing/react";
import { renderHook, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { LIST_TEAMS } from "@/graphql/identity/identity.queries";
import { DEPLOY_APPROVAL_APPS } from "@/graphql/permissions/astrolift.queries";
import { UPDATE_APP_DEPLOY_APPROVAL } from "@/graphql/permissions/deploy-approval.mutations";
import messages from "@/messages/en.json";

import { useDeployApprovalSetup } from "./use-deploy-approval-setup";

vi.mock("@/graphql/identity/identity.hooks", () => ({
  useActiveOrg: () => ({ org: { id: "org-1" }, loading: false, error: null }),
}));

const app = (id: string, viewerPermissions: string[], approverUserIds: string[] = []) => ({
  __typename: "AstroliftRegisteredApp",
  id,
  slug: id,
  name: id,
  requiresApproval: false,
  approverTeamId: null,
  approverUserIds,
  minimumApprovals: 1,
  version: 3,
  viewerPermissions,
});

const org = (id: string) => ({ __typename: "AstroliftOrganization", id, slug: id, name: id });
const team = (id: string, orgId: string, deletedAt: string | null = null) => ({
  __typename: "AstroliftTeam",
  id,
  slug: id,
  name: id,
  organization: org(orgId),
  createdAt: "2026-10-01T00:00:00Z",
  updatedAt: "2026-10-01T00:00:00Z",
  deletedAt,
});

const appsMock = {
  request: { query: DEPLOY_APPROVAL_APPS },
  result: {
    data: {
      astroliftApps: [app("checkout", ["app.read", "app.update"]), app("ledger", ["app.read"])],
    },
  },
  maxUsageCount: 5,
};
const teamsMock = {
  request: { query: LIST_TEAMS },
  result: {
    data: {
      astroliftTeams: [
        team("payments", "org-1"),
        team("gone", "org-1", "2026-10-02T00:00:00Z"),
        team("elsewhere", "org-2"),
      ],
    },
  },
};

function wrapper(mocks: unknown[]) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <MockedProvider mocks={mocks as never}>
        <NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
          {children}
        </NextIntlClientProvider>
      </MockedProvider>
    );
  };
}

describe("useDeployApprovalSetup (#2272)", () => {
  it("offers changes only where the viewer holds app.update, and only this org's live teams", async () => {
    const { result } = renderHook(useDeployApprovalSetup, {
      wrapper: wrapper([appsMock, teamsMock]),
    });
    await waitFor(() => expect(result.current.apps).not.toBeNull());
    expect(result.current.apps!.map((a) => [a.id, a.canUpdate])).toEqual([
      ["checkout", true],
      ["ledger", false],
    ]);
    await waitFor(() =>
      expect(result.current.teams).toEqual([{ id: "payments", name: "payments" }])
    );
  });

  it("saves through updateApp and reports the server's refusal", async () => {
    const input = {
      id: "checkout",
      requiresApproval: true,
      approverTeamId: "payments",
      minimumApprovals: 2,
      ifMatchVersion: 3,
    };
    const refused = {
      request: { query: UPDATE_APP_DEPLOY_APPROVAL, variables: { input } },
      result: {
        data: {
          updateApp: {
            __typename: "MutationResult",
            ok: false,
            errors: [
              {
                __typename: "MutationError",
                code: "FORBIDDEN",
                message: "SERVER_DENIAL: app.update",
                field: null,
              },
            ],
            data: null,
          },
        },
      },
    };
    const { result } = renderHook(useDeployApprovalSetup, {
      wrapper: wrapper([appsMock, teamsMock, refused]),
    });
    await waitFor(() => expect(result.current.apps).not.toBeNull());
    const checkout = result.current.apps![0]!;
    expect(
      await result.current.onSave(checkout, {
        requiresApproval: true,
        minimumApprovals: 2,
        approverTeamId: "payments",
      })
    ).toBe("SERVER_DENIAL: app.update");
  });
});
